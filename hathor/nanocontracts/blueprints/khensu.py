from typing import Optional
from hathor.conf.get_settings import HathorSettings
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Address,
    Amount,
    NCAction,
    TokenUid,
    ContractId,
    BlueprintId,
    NCActionType,
    public,
    view,
)
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.exception import NCFail
from hathor.types import Timestamp

settings = HathorSettings()


# Custom exceptions
class InsufficientAmount(NCFail):
    pass


class Unauthorized(NCFail):
    pass


class InvalidState(NCFail):
    pass


class MigrationFailed(NCFail):
    pass


# Constants
MIN_PURCHASE = Amount(100_00)  # 100 HTR
MAX_PURCHASE = Amount(50000_00)  # 50,000 HTR
INITIAL_VIRTUAL_POOL = Amount(15000_00)  # 15,000 HTR
INITIAL_TOKEN_RESERVE = Amount(1073000191)
BASIS_POINTS = 10000
HTR_UID = settings.HATHOR_TOKEN_UID  # type: ignore


class Khensu(Blueprint):
    # Administrative state
    admin_address: Address
    is_paused: bool

    # Core state
    virtual_pool: Amount
    token_reserve: Amount
    token_uid: TokenUid
    lp_contract: ContractId
    dozer_pool_blueprint_id: BlueprintId

    # Fee management
    buy_fee_rate: int  # Basis points (e.g., 200 for 2%)
    sell_fee_rate: int  # Basis points (e.g., 500 for 5%)
    collected_buy_fees: Amount
    collected_sell_fees: Amount

    # Migration state
    is_migrated: bool
    target_market_cap: Amount
    liquidity_amount: Amount
    graduation_fee: Amount
    graduation_fee_withdrawn: bool

    # Token minting simulation
    minted_supply: Amount
    total_supply: Amount

    # User balances that comes from slippage
    user_balances: dict[Address, dict[TokenUid, Amount]]

    # Pool statistics
    total_volume: Amount
    transaction_count: int
    last_activity_timestamp: Timestamp

    @public
    def initialize(
        self,
        ctx: Context,
        admin_address: Address,
        token_uid: TokenUid,
        dozer_pool_blueprint_id: BlueprintId,
        buy_fee_rate: int,
        sell_fee_rate: int,
        target_market_cap: Amount,
        liquidity_amount: Amount,
        graduation_fee: Amount,
    ) -> None:
        if not admin_address or not token_uid or not dozer_pool_blueprint_id:
            raise NCFail("Invalid initialization parameters")

        if buy_fee_rate > 1000 or sell_fee_rate > 1000:
            raise NCFail("Fee rates cannot exceed 1000 basis points")
        self.admin_address = admin_address
        self.token_uid = token_uid
        self.dozer_pool_blueprint_id = dozer_pool_blueprint_id
        self.lp_contract = ContractId(b'')  # Will be set when migrated
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.target_market_cap = target_market_cap
        self.liquidity_amount = liquidity_amount
        self.graduation_fee = graduation_fee
        self.graduation_fee_withdrawn = False

        # Validate token deposit
        action_htr, action_token = self._get_actions_in_in(ctx)
        if action_htr.token_uid != HTR_UID:
            raise NCFail("Invalid HTR deposit")
        if action_token.token_uid != token_uid:
            raise NCFail("Invalid token deposit")
        if action_token.amount != INITIAL_TOKEN_RESERVE:
            raise NCFail("Invalid initial token supply")

        # Initialize state variables
        self.is_paused = False
        self.is_migrated = False
        self.virtual_pool = INITIAL_VIRTUAL_POOL
        self.token_reserve = INITIAL_TOKEN_RESERVE
        self.collected_buy_fees = Amount(0)
        self.collected_sell_fees = Amount(0)
        self.minted_supply = Amount(0)
        self.total_supply = INITIAL_TOKEN_RESERVE
        self.total_volume = Amount(0)
        self.transaction_count = 0
        self.last_activity_timestamp = ctx.block.timestamp

    def _only_admin(self, ctx: Context) -> None:
        if ctx.address != self.admin_address:
            raise Unauthorized("Only admin can call this method")

    def _validate_not_paused(self) -> None:
        if self.is_paused:
            raise InvalidState("Contract is paused")

    def _validate_not_migrated(self) -> None:
        if self.is_migrated:
            raise InvalidState("Contract has already migrated")

    def _calculate_tokens_out(self, htr_amount: Amount) -> Amount:
        """Calculate tokens to return for a given HTR input using bonding curve"""
        # Using bonding curve formula: T = TR * (1 - VP/(VP + H))
        # where T = tokens out, TR = total reserve, VP = virtual pool, H = HTR in
        numerator = htr_amount * self.token_reserve
        denominator = self.virtual_pool + htr_amount
        if denominator == 0:
            return Amount(0)
        return numerator // denominator

    def _calculate_htr_out(self, token_amount: Amount) -> Amount:
        """Calculate HTR to return for a given token input using bonding curve"""
        # Using inverse bonding curve: H = (TR * VP) / (TR - T) - VP
        # where H = HTR out, TR = total reserve, VP = virtual pool, T = tokens in
        if token_amount >= self.token_reserve:
            return Amount(0)
        numerator = token_amount * self.virtual_pool
        denominator = self.token_reserve - token_amount
        if denominator == 0:
            return Amount(0)
        return numerator // denominator

    def _get_actions_in_in(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Return token_a and token_b actions. It also validates that both are deposits."""
        action_htr, action_token = self._get_actions_HTR_token(ctx)
        if action_htr.type != NCActionType.DEPOSIT:
            raise NCFail("only deposits allowed for token_a")
        if action_token.type != NCActionType.DEPOSIT:
            raise NCFail("only deposits allowed for token_b")
        return action_htr, action_token

    def _get_actions_HTR_token(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Return token_a and token_b actions."""
        if set(ctx.actions.keys()) != {HTR_UID, self.token_uid}:
            raise NCFail("only token_a and token_b are allowed")
        action_htr = ctx.actions[HTR_UID]
        action_token = ctx.actions[self.token_uid]
        self.last_activity_timestamp = ctx.block.timestamp
        return action_htr, action_token

    def _get_action(self, ctx: Context, action_type: NCActionType) -> NCAction:
        """Get and validate single action"""
        if len(ctx.actions) != 1:
            raise NCFail("Expected single action")
        action = list(ctx.actions.values())[0]
        if action.type != action_type:
            raise NCFail(f"Expected {action_type} action")
        return action

    def _mint(self, amount: Amount) -> None:
        """Simulate token minting"""
        if self.minted_supply + amount > self.total_supply:
            raise NCFail("Would exceed total supply")
        self.minted_supply += amount

    def _get_actions_in_out(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Get and validate deposit/withdrawal pair"""
        if len(ctx.actions) != 2:
            raise NCFail("Expected deposit and withdrawal")

        action_in = None
        action_out = None

        for action in ctx.actions.values():
            if action.type == NCActionType.DEPOSIT:
                action_in = action
            elif action.type == NCActionType.WITHDRAWAL:
                action_out = action

        if not action_in or not action_out:
            raise NCFail("Must have one deposit and one withdrawal")

        return action_in, action_out

    def _calculate_price_impact(self, amount_in: Amount, amount_out: Amount) -> float:
        """Calculate price impact percentage"""
        if amount_out == 0:
            return 0

        # Calculate expected output without impact
        expected_out = (amount_in * self.virtual_pool) // (
            self.virtual_pool + amount_in
        )

        # Calculate impact percentage
        impact = 100 * (expected_out - amount_out) / amount_out
        return max(0, min(impact, 100))

    @public
    def buy_tokens(self, ctx: Context) -> None:
        self._validate_not_paused()
        self._validate_not_migrated()

        action_in, action_out = self._get_actions_in_out(ctx)
        if action_in.token_uid != HTR_UID:
            raise NCFail("Input token must be HTR")

        if action_in.amount < MIN_PURCHASE or action_in.amount > MAX_PURCHASE:
            raise InsufficientAmount("Amount outside allowed range")

        # Calculate and apply buy fee
        fee_amount = (action_in.amount * self.buy_fee_rate) // BASIS_POINTS
        net_amount = action_in.amount - fee_amount
        self.collected_buy_fees += fee_amount

        # Calculate tokens to return
        tokens_out = self._calculate_tokens_out(net_amount)
        if tokens_out > self.token_reserve:
            raise InsufficientAmount("Insufficient token reserve")

        # Handle slippage return if user requested less than available
        slippage = tokens_out - action_out.amount
        if slippage > 0:
            user_balance = self.user_balances.get(ctx.address, {})
            user_balance[self.token_uid] = (
                user_balance.get(self.token_uid, 0) + slippage
            )
            self.user_balances[ctx.address] = user_balance

        # Update state
        self.virtual_pool += net_amount
        self.token_reserve -= action_out.amount
        self.total_volume += action_in.amount
        self.transaction_count += 1
        self.last_activity_timestamp = ctx.block.timestamp

        # Check migration threshold
        # Only attempt migration if we've reached the target market cap
        if self.virtual_pool >= self.target_market_cap and not self.is_migrated:
            try:
                self.migrate_liquidity(ctx)
            except NCFail:
                # If migration fails, continue with the buy operation
                pass

    @public
    def sell_tokens(self, ctx: Context) -> None:
        self._validate_not_paused()
        self._validate_not_migrated()

        action_in, action_out = self._get_actions_in_out(ctx)
        if action_in.token_uid != self.token_uid:
            raise NCFail("Input token must be token_uid")

        # Calculate HTR return
        htr_out = self._calculate_htr_out(action_in.amount)

        # Apply sell fee
        fee_amount = (htr_out * self.sell_fee_rate) // BASIS_POINTS
        net_amount = htr_out - fee_amount
        self.collected_sell_fees += fee_amount

        # Handle slippage return if user requested less than available
        slippage = net_amount - action_out.amount
        if slippage > 0:
            user_balance = self.user_balances.get(ctx.address, {})
            user_balance[HTR_UID] = user_balance.get(HTR_UID, 0) + slippage
            self.user_balances[ctx.address] = user_balance

        # Update state
        self.virtual_pool -= action_out.amount
        self.token_reserve += action_in.amount
        self.total_volume += action_in.amount
        self.transaction_count += 1
        self.last_activity_timestamp = ctx.block.timestamp

    @public
    def withdraw_fees(self, ctx: Context) -> None:
        self._only_admin(ctx)

        total_fees = self.collected_buy_fees + self.collected_sell_fees
        if total_fees == 0:
            raise InvalidState("No fees to withdraw")

        # Reset fee counters
        self.collected_buy_fees = Amount(0)
        self.collected_sell_fees = Amount(0)

    @public
    def withdraw_graduation_fee(self, ctx: Context) -> None:
        """Allow admin to withdraw the graduation fee after migration"""
        self._only_admin(ctx)
        if not self.is_migrated:
            raise InvalidState("Contract not yet migrated")

        if self.graduation_fee_withdrawn:
            raise InvalidState("Graduation fee already withdrawn")

        action = self._get_action(ctx, NCActionType.WITHDRAWAL)
        if action.token_uid != HTR_UID:
            raise NCFail("Can only withdraw HTR")
        if action.amount != self.graduation_fee:
            raise NCFail("Invalid withdrawal amount")

        self.graduation_fee_withdrawn = True

    @public
    def transfer_admin(self, ctx: Context, new_admin: Address) -> None:
        self._only_admin(ctx)
        if not new_admin:
            raise NCFail("Invalid admin address")
        self.admin_address = new_admin

    @public
    def pause(self, ctx: Context) -> None:
        self._only_admin(ctx)
        if self.is_paused:
            raise InvalidState("Already paused")
        self.is_paused = True

    @public
    def unpause(self, ctx: Context) -> None:
        self._only_admin(ctx)
        if not self.is_paused:
            raise InvalidState("Not paused")
        self.is_paused = False

    @public
    def migrate_liquidity(self, ctx: Context) -> None:
        self._validate_not_migrated()

        if self.virtual_pool < self.target_market_cap:
            raise InvalidState("Market cap threshold not reached")

        try:
            # Validate balances
            if self.token_reserve == 0:
                raise NCFail("No tokens to migrate")
            if self.virtual_pool < self.liquidity_amount + self.graduation_fee:
                raise NCFail("Insufficient HTR for migration")

            # Create the Dozer pool contract with the required liquidity
            # Generate a unique salt based on the token pair
            salt = self.token_uid + HTR_UID + bytes(str(ctx.block.timestamp), 'utf-8')
            
            # Prepare token actions for the new pool
            actions = [
                NCAction(NCActionType.DEPOSIT, HTR_UID, self.liquidity_amount),
                NCAction(NCActionType.DEPOSIT, self.token_uid, self.token_reserve),
            ]
            
            # Create the Dozer pool contract
            pool_id, _ = self.create_contract(
                self.dozer_pool_blueprint_id, 
                salt, 
                actions, 
                HTR_UID,                    # token_a
                self.token_uid,             # token_b
                0,                          # fee (0%)
                50,                         # protocol_fee (50%)
            )
            
            # Update state
            self.lp_contract = pool_id
            self.is_migrated = True

        except Exception as e:
            raise MigrationFailed(f"Migration failed: {str(e)}")

    @public
    def admin_migrate_liquidity(self, ctx: Context) -> None:
        self._only_admin(ctx)
        if self.is_migrated:
            raise InvalidState("Already migrated")
        self.migrate_liquidity(ctx)

    @public
    def post_migration_buy(self, ctx: Context) -> None:
        if not self.is_migrated:
            raise InvalidState("Not migrated")
        self._validate_not_paused()

        action_in, action_out = self._get_actions_in_out(ctx)
        if action_in.token_uid != HTR_UID:
            raise NCFail("Input token must be HTR")

        # Calculate and apply 1% fee
        fee_amount = (action_in.amount * 100) // BASIS_POINTS
        net_amount = action_in.amount - fee_amount

        # Route to Dozer pool with fee-adjusted amount
        actions = [
            NCAction(NCActionType.DEPOSIT, HTR_UID, net_amount),
            NCAction(NCActionType.WITHDRAWAL, self.token_uid, action_out.amount),
        ]

        self.call_public_method(
            self.lp_contract, "swap_exact_tokens_for_tokens", actions
        )

        self.collected_buy_fees += fee_amount
        self.total_volume += action_in.amount
        self.transaction_count += 1
        self.last_activity_timestamp = ctx.block.timestamp

    @public
    def post_migration_sell(self, ctx: Context) -> None:
        if not self.is_migrated:
            raise InvalidState("Not migrated")
        self._validate_not_paused()

        action_in, action_out = self._get_actions_in_out(ctx)
        if action_in.token_uid != self.token_uid:
            raise NCFail("Input token must be token_uid")

        # Calculate and apply 1% fee
        expected_out = self.call_view_method(
            self.lp_contract,
            "front_quote_exact_tokens_for_tokens",
            action_in.amount,
            action_in.token_uid,
        )["amount_out"]

        fee_amount = (expected_out * 100) // BASIS_POINTS
        net_amount = expected_out - fee_amount

        # Route to Dozer pool with adjusted amount
        actions = [
            NCAction(NCActionType.DEPOSIT, action_in.token_uid, action_in.amount),
            NCAction(NCActionType.WITHDRAWAL, HTR_UID, net_amount),
        ]

        self.call_public_method(
            self.lp_contract, "swap_exact_tokens_for_tokens", actions
        )

        self.collected_sell_fees += fee_amount
        self.total_volume += action_in.amount
        self.transaction_count += 1
        self.last_activity_timestamp = ctx.block.timestamp

    @view
    def quote_buy(self, htr_amount: Amount) -> dict[str, float]:
        """Quote buying tokens with HTR"""
        if self.is_migrated:
            raise InvalidState("Contract has migrated")

        fee_amount = (htr_amount * self.buy_fee_rate) // BASIS_POINTS
        net_amount = htr_amount - fee_amount
        tokens_out = self._calculate_tokens_out(net_amount)
        price_impact = self._calculate_price_impact(htr_amount, tokens_out)

        return {"amount_out": tokens_out, "price_impact": price_impact}

    @view
    def quote_sell(self, token_amount: Amount) -> dict[str, float]:
        """Quote selling tokens for HTR"""
        if self.is_migrated:
            raise InvalidState("Contract has migrated")

        htr_out = self._calculate_htr_out(token_amount)
        fee_amount = (htr_out * self.sell_fee_rate) // BASIS_POINTS
        net_amount = htr_out - fee_amount
        price_impact = self._calculate_price_impact(token_amount, net_amount)

        return {"amount_out": net_amount, "price_impact": price_impact}

    @view
    def front_quote_exact_tokens_for_tokens(
        self, amount_in: Amount, token_in: TokenUid
    ) -> dict[str, float]:
        """Post-migration quote using Dozer pool"""
        if not self.is_migrated:
            raise InvalidState("Not migrated")

        return self.call_view_method(
            self.lp_contract, "front_quote_exact_tokens_for_tokens", amount_in, token_in
        )

    @view
    def front_quote_tokens_for_exact_tokens(
        self, amount_out: Amount, token_in: TokenUid
    ) -> dict[str, float]:
        """Post-migration quote using Dozer pool"""
        if not self.is_migrated:
            raise InvalidState("Not migrated")

        return self.call_view_method(
            self.lp_contract,
            "front_quote_tokens_for_exact_tokens",
            amount_out,
            token_in,
        )

    @view
    def calculate_buy_return(self, htr_amount: Amount) -> Amount:
        if self.is_migrated:
            raise InvalidState("Contract has migrated")

        fee_amount = (htr_amount * self.buy_fee_rate) // BASIS_POINTS
        net_amount = htr_amount - fee_amount
        return self._calculate_tokens_out(net_amount)

    @view
    def calculate_sell_return(self, token_amount: Amount) -> Amount:
        if self.is_migrated:
            raise InvalidState("Contract has migrated")

        htr_out = self._calculate_htr_out(token_amount)
        fee_amount = (htr_out * self.sell_fee_rate) // BASIS_POINTS
        return htr_out - fee_amount
