from typing import Optional
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Address,
    Amount,
    NCAction,
    TokenUid,
    ContractId,
    NCActionType,
    public,
    view,
)
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.exception import NCFail


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
HTR_UID = HathorSettings().HATHOR_TOKEN_UID  # type: ignore


class KhensuCurve(Blueprint):
    # Administrative state
    admin_address: Address
    is_paused: bool

    # Core state
    virtual_pool: Amount
    token_reserve: Amount
    token_uid: TokenUid
    lp_contract: ContractId

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

    # Token minting simulation
    minted_supply: Amount
    total_supply: Amount

    @public
    def initialize(
        self,
        ctx: Context,
        admin_address: Address,
        token_uid: TokenUid,
        lp_contract: ContractId,
        buy_fee_rate: int,
        sell_fee_rate: int,
        target_market_cap: Amount,
        liquidity_amount: Amount,
        graduation_fee: Amount,
    ) -> None:
        if not admin_address or not token_uid or not lp_contract:
            raise NCFail("Invalid initialization parameters")

        if buy_fee_rate > 1000 or sell_fee_rate > 1000:
            raise NCFail("Fee rates cannot exceed 1000 basis points")

        # Validate token deposit
        action_htr, action_token = self._get_actions_in_in(ctx)
        if action_htr.token_uid != HTR_UID:
            raise NCFail("Invalid HTR deposit")
        if action_token.token_uid != token_uid:
            raise NCFail("Invalid token deposit")
        if action_token.amount != INITIAL_TOKEN_RESERVE:
            raise NCFail("Invalid initial token supply")

        self.admin_address = admin_address
        self.token_uid = token_uid
        self.lp_contract = lp_contract
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.target_market_cap = target_market_cap
        self.liquidity_amount = liquidity_amount
        self.graduation_fee = graduation_fee

        # Initialize state variables
        self.is_paused = False
        self.is_migrated = False
        self.virtual_pool = INITIAL_VIRTUAL_POOL
        self.token_reserve = INITIAL_TOKEN_RESERVE
        self.collected_buy_fees = Amount(0)
        self.collected_sell_fees = Amount(0)
        self.minted_supply = Amount(0)
        self.total_supply = INITIAL_TOKEN_RESERVE

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
        tokens_before = self.token_reserve
        virtual_pool_after = self.virtual_pool + htr_amount

        # Using bonding curve formula: y = 1073000191 - (32190005730 / (15000 + x))
        constant = Amount(32190005730)
        tokens_after = INITIAL_TOKEN_RESERVE - (constant // virtual_pool_after)

        return tokens_before - tokens_after

    def _calculate_htr_out(self, token_amount: Amount) -> Amount:
        """Calculate HTR to return for a given token input using bonding curve"""
        tokens_after = self.token_reserve + token_amount
        virtual_pool_before = self.virtual_pool

        # Using inverse bonding curve to find required virtual pool
        constant = Amount(32190005730)
        virtual_pool_after = constant // (INITIAL_TOKEN_RESERVE - tokens_after)

        return virtual_pool_before - virtual_pool_after

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
        self.last_activity_timestamp = ctx.timestamp
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

    @public
    def buy_tokens(self, ctx: Context, htr_amount: Amount) -> None:
        self._validate_not_paused()
        self._validate_not_migrated()

        if htr_amount < MIN_PURCHASE or htr_amount > MAX_PURCHASE:
            raise InsufficientAmount("Amount outside allowed range")

        # Calculate and apply buy fee
        fee_amount = (htr_amount * self.buy_fee_rate) // BASIS_POINTS
        net_amount = htr_amount - fee_amount
        self.collected_buy_fees += fee_amount

        # Calculate tokens to return
        tokens_out = self._calculate_tokens_out(net_amount)
        if tokens_out > self.token_reserve:
            raise InsufficientAmount("Insufficient token reserve")

        # Update state
        self.virtual_pool += net_amount
        self.token_reserve -= tokens_out

        # Check migration threshold
        if self.virtual_pool >= self.target_market_cap:
            self.migrate_liquidity(ctx)

    @public
    def sell_tokens(self, ctx: Context, token_amount: Amount) -> None:
        self._validate_not_paused()
        self._validate_not_migrated()

        # Calculate HTR return
        htr_out = self._calculate_htr_out(token_amount)

        # Apply sell fee
        fee_amount = (htr_out * self.sell_fee_rate) // BASIS_POINTS
        net_amount = htr_out - fee_amount
        self.collected_sell_fees += fee_amount

        # Update state
        self.virtual_pool -= net_amount
        self.token_reserve += token_amount

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

        action = self._get_action(ctx, NCActionType.WITHDRAWAL)
        if action.token_uid != HTR_UID:
            raise NCFail("Can only withdraw HTR")
        if action.amount != self.graduation_fee:
            raise NCFail("Invalid withdrawal amount")

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

            # Add liquidity to Dozer pool
            actions = [
                NCAction(NCActionType.DEPOSIT, HTR_UID, self.liquidity_amount),
                NCAction(NCActionType.DEPOSIT, self.token_uid, self.token_reserve),
            ]

            self.call_public_method(self.lp_contract, "add_liquidity", actions)

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
    def post_migration_buy(self, ctx: Context, htr_amount: Amount) -> None:
        if not self.is_migrated:
            raise InvalidState("Not migrated")
        self._validate_not_paused()

        action_in, action_out = self._get_actions_in_out(ctx)

        # Apply 1% fee
        fee_amount = (htr_amount * 100) // BASIS_POINTS
        net_amount = htr_amount - fee_amount
        self.collected_buy_fees += fee_amount

        # Route to Dozer pool
        actions = [
            NCAction(NCActionType.DEPOSIT, action_in.token_uid, net_amount),
            NCAction(NCActionType.WITHDRAWAL, action_out.token_uid, action_out.amount),
        ]

        result = self.call_public_method(
            self.lp_contract, "swap_exact_tokens_for_tokens", actions
        )

    @public
    def post_migration_sell(self, ctx: Context, token_amount: Amount) -> None:
        if not self.is_migrated:
            raise InvalidState("Not migrated")
        self._validate_not_paused()

        action_in, action_out = self._get_actions_in_out(ctx)

        # Route tokens to Dozer pool
        actions = [
            NCAction(NCActionType.DEPOSIT, action_in.token_uid, action_in.amount),
            NCAction(NCActionType.WITHDRAWAL, action_out.token_uid, action_out.amount),
        ]

        result = self.call_public_method(
            self.lp_contract, "swap_tokens_for_exact_tokens", actions
        )

        # Apply 1% fee on HTR returned
        fee_amount = (result.amount_out * 100) // BASIS_POINTS
        self.collected_sell_fees += fee_amount

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
