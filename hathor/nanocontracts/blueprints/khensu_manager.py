from typing import NamedTuple

from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Amount,
    CallerId,
    NCAction,
    NCDepositAction,
    NCWithdrawalAction,
    Timestamp,
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


class TokenNotFound(NCFail):
    pass


class TokenExists(NCFail):
    pass


class InvalidParameters(NCFail):
    pass


class TransactionDenied(NCFail):
    pass


# Constants
BASIS_POINTS = 10000
HTR_UID = TokenUid(b"\x00")  # type: ignore
FEE = 10  # 0.1%
PROTOCOL_FEE = 20  # 20% of the fee goes to the protocol


class TokenInfo(NamedTuple):
    """NamedTuple with data from a specific registered token"""

    creator: str
    token_name: str
    token_symbol: str
    description: str
    twitter: str
    telegram: str
    website: str
    url_logo: str
    virtual_pool: Amount
    curve_constant: Amount
    initial_virtual_pool: Amount
    token_reserve: Amount
    pool_key: str
    is_migrated: bool
    target_market_cap: Amount
    liquidity_amount: Amount
    minted_supply: Amount
    total_supply: Amount
    total_volume: Amount
    transaction_count: int
    last_activity: Timestamp


# Token data will be stored in separate dictionaries for each property
class KhensuManager(Blueprint):
    """Singleton manager for Khensu token bonding curves."""

    # Administrative state
    admin_address: CallerId
    dozer_pool_manager_id: ContractId
    buy_fee_rate: int
    sell_fee_rate: int
    default_target_market_cap: Amount
    default_liquidity_amount: Amount
    default_initial_virtual_pool: Amount
    default_token_total_supply: Amount
    default_curve_constant: Amount
    graduation_fee: Amount

    # Token registry
    all_tokens: list[TokenUid]  # List of all registered tokens

    # Token admin data
    token_creators: dict[TokenUid, CallerId]  # Token creator addresses

    # Token Bounding Curve
    token_curve_constant: dict[TokenUid, Amount]  # Curve Constant during Token creation
    token_initial_virtual_pool: dict[TokenUid, Amount]

    # Token core state
    token_virtual_pools: dict[TokenUid, Amount]  # Virtual pool sizes
    token_reserves: dict[TokenUid, Amount]  # Token reserves
    token_pools: dict[TokenUid, str]  # Dozer pool for each token

    # Token migration parameters
    token_migrated: dict[TokenUid, bool]  # Migration states
    token_target_caps: dict[TokenUid, Amount]  # Target market caps
    token_liquidity_amounts: dict[TokenUid, Amount]  # Liquidity amounts

    # Token supply data
    token_minted_supplies: dict[TokenUid, Amount]  # Minted supplies
    token_total_supplies: dict[TokenUid, Amount]  # Total supplies

    # Token statistics
    token_volumes: dict[TokenUid, Amount]  # Trading volumes
    token_tx_counts: dict[TokenUid, int]  # Transaction counts
    token_last_activities: dict[TokenUid, Timestamp]  # Last activity timestamps

    # User balances that comes from slippage
    token_user_balances: dict[TokenUid, dict[CallerId, Amount]]

    # Token metadata
    token_names: dict[TokenUid, str]  # Token names
    token_symbols: dict[TokenUid, str]  # Token symbols  
    token_descriptions: dict[TokenUid, str]  # Token description
    token_twitters: dict[TokenUid, str]  # Token twitter url
    token_telegrams: dict[TokenUid, str]  # Token telegram url
    token_websites: dict[TokenUid, str]  # Token website url
    token_logos: dict[TokenUid, str]  # Token logo URLs

    # Platform statistics
    total_tokens_created: int
    total_tokens_migrated: int
    collected_buy_fees: Amount
    collected_sell_fees: Amount
    collected_graduation_fees: Amount

    @public
    def initialize(
        self,
        ctx: Context,
        dozer_pool_manager_id: ContractId,
        default_target_market_cap: Amount,
        default_liquidity_amount: Amount,
        default_initial_virtual_pool: Amount,
        default_curve_constant: Amount,
        default_token_total_supply: Amount,
        buy_fee_rate: Amount,
        sell_fee_rate: Amount,
        graduation_fee: Amount,
    ) -> None:
        """Initialize the KhensuManager contract."""
        self.admin_address = ctx.caller_id
        self.dozer_pool_manager_id = dozer_pool_manager_id

        # Default parameters for new tokens
        self.buy_fee_rate = buy_fee_rate
        self.sell_fee_rate = sell_fee_rate
        self.default_target_market_cap = default_target_market_cap
        self.default_liquidity_amount = default_liquidity_amount
        self.default_initial_virtual_pool = default_initial_virtual_pool
        self.default_curve_constant = default_curve_constant
        self.default_token_total_supply = default_token_total_supply
        self.graduation_fee = graduation_fee

        # Platform statistics
        self.total_tokens_created = 0
        self.total_tokens_migrated = 0
        self.collected_buy_fees = Amount(0)
        self.collected_sell_fees = Amount(0)
        self.collected_graduation_fees = Amount(0)

    def _get_token(self, token_uid: TokenUid) -> TokenInfo:
        """Get the token data for a given token uid as a dictionary."""
        if token_uid not in self.all_tokens:
            raise TokenNotFound(f"Token does not exist: {token_uid.hex()}")

        # Build token data structure
        return TokenInfo(
            self.token_creators.get(token_uid).hex(),
            self.token_names.get(token_uid),
            self.token_symbols.get(token_uid),
            self.token_descriptions.get(token_uid),
            self.token_twitters.get(token_uid),
            self.token_telegrams.get(token_uid),
            self.token_websites.get(token_uid),
            self.token_logos.get(token_uid),
            self.token_virtual_pools.get(token_uid),
            self.token_curve_constant.get(token_uid),
            self.token_initial_virtual_pool.get(token_uid),
            self.token_reserves.get(token_uid),
            self.token_pools.get(token_uid),
            self.token_migrated.get(token_uid, False),
            self.token_target_caps.get(token_uid),
            self.token_liquidity_amounts.get(token_uid),
            self.token_minted_supplies.get(token_uid, Amount(0)),
            self.token_total_supplies.get(token_uid),
            self.token_volumes.get(token_uid, Amount(0)),
            self.token_tx_counts.get(token_uid, 0),
            self.token_last_activities.get(token_uid),
        )

    def _validate_token_exists(self, token_uid: TokenUid) -> None:
        """Check if a token exists, raising error if not."""
        if token_uid not in self.all_tokens:
            raise TokenNotFound(f"Token does not exist: {token_uid.hex()}")

    def _only_admin(self, ctx: Context) -> None:
        """Validate that the caller is the platform admin."""
        if ctx.caller_id != self.admin_address:
            raise Unauthorized("Only admin can call this method")

    def _validate_not_migrated(self, token_uid: TokenUid) -> None:
        """Validate that a token has not been migrated."""
        self._validate_token_exists(token_uid)
        if self.token_migrated.get(token_uid, False):
            raise InvalidState("Token has already migrated")

    def _calculate_tokens_out(self, token_uid: TokenUid, htr_amount: Amount) -> Amount:
        """Calculate tokens to return for a given HTR input using bonding curve."""
        # Using bonding curve formula: T = CC * H / (VP * (VP + H))
        # where T = tokens out, CC = curve constant, VP = virtual pool, H = HTR in
        self._validate_token_exists(token_uid)
        virtual_pool = self.token_virtual_pools.get(token_uid)
        curve_constant = self.token_curve_constant.get(token_uid)

        numerator = htr_amount * curve_constant
        denominator = (virtual_pool + htr_amount) * virtual_pool
        if denominator == 0:
            return Amount(0)
        return numerator // denominator

    def _calculate_htr_needed(
        self, token_uid: TokenUid, token_amount: Amount
    ) -> Amount:
        """Calculate HTR needed for a given token input using bonding curve."""
        # Using inverse bonding curve: H = T * VP^2 / (CC - VP * T)
        # where H = HTR out, CC = curve constant, VP = virtual pool, T = tokens in
        self._validate_token_exists(token_uid)
        virtual_pool = self.token_virtual_pools.get(token_uid)
        token_reserve = self.token_reserves.get(token_uid)
        curve_constant = self.token_curve_constant.get(token_uid)

        if token_amount >= token_reserve:
            return Amount(0)

        numerator = token_amount * virtual_pool**2
        denominator = curve_constant - virtual_pool * token_amount
        if denominator == 0:
            return Amount(0)
        # Celiling division
        return (numerator + denominator - 1) // denominator

    def _calculate_htr_out(self, token_uid: TokenUid, token_amount: Amount) -> Amount:
        """Calculate HTR to return for a given token input using bonding curve."""
        # Using inverse bonding curve: H = T * VP^2 / (CC + VP * T)
        # where H = HTR out, CC = curve constant, VP = virtual pool, T = tokens in
        self._validate_token_exists(token_uid)
        virtual_pool = self.token_virtual_pools.get(token_uid)
        token_reserve = self.token_reserves.get(token_uid)
        curve_constant = self.token_curve_constant.get(token_uid)

        if token_amount >= token_reserve:
            return Amount(0)

        numerator = token_amount * virtual_pool**2
        denominator = curve_constant + virtual_pool * token_amount
        if denominator == 0:
            return Amount(0)
        return numerator // denominator

    def _calculate_tokens_needed(
        self, token_uid: TokenUid, htr_amount: Amount
    ) -> Amount:
        """Calculate tokens needed for a given HTR input using bonding curve."""
        # Using bonding curve formula: T = CC * H / (VP * (VP - H))
        # where T = tokens out, CC = curve constant, VP = virtual pool, H = HTR in
        self._validate_token_exists(token_uid)
        virtual_pool = self.token_virtual_pools.get(token_uid)
        curve_constant = self.token_curve_constant.get(token_uid)

        numerator = htr_amount * curve_constant
        denominator = (virtual_pool - htr_amount) * virtual_pool
        if denominator == 0:
            return Amount(0)
        # Celiling division
        return (numerator + denominator - 1) // denominator

    def _get_action(self, ctx: Context, action_type: NCActionType) -> NCAction:
        """Get and validate single action"""
        if len(ctx.actions) != 1:
            raise NCFail("Expected single action")
        action_tuple = list(ctx.actions.values())[0]
        total_amount = Amount(0)
        for action in action_tuple:
            total_amount += action.amount
            if action_tuple[0].type != action_type:
                raise NCFail(f"Expected {action_type} action")
        if action_type == NCActionType.DEPOSIT:
            return NCDepositAction(
                token_uid=action_tuple[0].token_uid, amount=total_amount
            )
        elif action_type == NCActionType.WITHDRAWAL:
            return NCWithdrawalAction(
                token_uid=action_tuple[0].token_uid, amount=total_amount
            )
        else:
            raise NCFail(f"Invalid action type: {action_type}")

    def _get_actions_in_in(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Return token_a and token_b actions. It also validates that both are deposits."""
        if len(ctx.actions) != 2:
            raise InvalidState("Expected exactly two tokens")

        action_htr = None
        action_token = None

        for action_tuple in ctx.actions.values():
            if (
                action_tuple[0].type == NCActionType.DEPOSIT
                and action_tuple[0].token_uid == HTR_UID
            ):
                total_deposit_htr = Amount(0)
                for deposit in action_tuple:
                    total_deposit_htr += deposit.amount
                action_htr = NCDepositAction(
                    token_uid=action_tuple[0].token_uid, amount=total_deposit_htr
                )
            elif (
                action_tuple[0].type == NCActionType.DEPOSIT
                and action_tuple[0].token_uid != HTR_UID
            ):
                total_deposit_token = Amount(0)
                for deposit in action_tuple:
                    total_deposit_token += deposit.amount
                action_token = NCDepositAction(
                    token_uid=action_tuple[0].token_uid, amount=total_deposit_token
                )

        if not action_htr or not action_token:
            raise InvalidState("Expected HTR and token deposits")

        return action_htr, action_token

    def _get_actions_in_out(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Get and validate deposit/withdrawal pair."""
        if len(ctx.actions) != 2:
            raise InvalidState("Expected deposit and withdrawal of 2 different tokens")

        action_in = None
        action_out = None

        for action_tuple in ctx.actions.values():
            if action_tuple[0].type == NCActionType.DEPOSIT:
                total_deposit = Amount(0)
                for deposit in action_tuple:
                    total_deposit += deposit.amount
                action_in = NCDepositAction(
                    token_uid=action_tuple[0].token_uid, amount=total_deposit
                )
            elif action_tuple[0].type == NCActionType.WITHDRAWAL:
                total_withdrawal = Amount(0)
                for withdrawal in action_tuple:
                    total_withdrawal += withdrawal.amount
                action_out = NCWithdrawalAction(
                    token_uid=action_tuple[0].token_uid, amount=total_withdrawal
                )

        if not action_in or not action_out:
            raise InvalidState("Must have one deposit and one withdrawal")

        return action_in, action_out

    def _calculate_price_impact(
        self, token_uid: TokenUid, amount_in: Amount, amount_out: Amount
    ) -> int:
        """Calculate price impact percentage."""
        if amount_out == 0:
            return 0

        self._validate_token_exists(token_uid)
        virtual_pool = self.token_virtual_pools.get(token_uid)

        # Calculate expected output without impact
        expected_out = (amount_in * virtual_pool) // (virtual_pool + amount_in)

        # Calculate impact percentage
        impact = 10000 * (expected_out - amount_out) // amount_out
        return max(0, min(impact, 10000))

    def _update_balance(
        self, token_uid: TokenUid, address: CallerId, amount: Amount
    ) -> None:
        """Update user balance for a token."""
        if token_uid not in self.token_user_balances:
            self.token_user_balances[token_uid] = {}

        user_balance = self.token_user_balances[token_uid]
        user_balance[address] = user_balance.get(address, 0) + amount
        self.token_user_balances[token_uid] = user_balance

    def migrate_liquidity(self, token_uid: TokenUid) -> None:
        """Migrate a token's liquidity to a DEX when threshold is reached."""
        self._validate_token_exists(token_uid)
        self._validate_not_migrated(token_uid)

        # Get relevant token data
        virtual_pool = self.token_virtual_pools[token_uid]
        target_market_cap = self.token_target_caps[token_uid]
        token_reserve = self.token_reserves[token_uid]
        liquidity_amount = self.token_liquidity_amounts[token_uid]

        # Check if market cap threshold is reached
        if virtual_pool < target_market_cap:
            raise InvalidState("Market cap threshold not reached")

        # Validate balances
        if token_reserve == 0:
            raise NCFail("No tokens to migrate")
        if virtual_pool < liquidity_amount + self.graduation_fee:
            raise NCFail("Insufficient HTR for migration")
        # Add liquidity to Dozer pool
        # TODO: Check if it is token_reserve or if it shuold be minted.
        actions = [
            NCDepositAction(token_uid=HTR_UID, amount=liquidity_amount),
            NCDepositAction(token_uid=token_uid, amount=token_reserve),
        ]
        # Call Dozer Pool Manager to create pool
        # TODO: check FEES
        pool_key = self.syscall.call_public_method(
            self.dozer_pool_manager_id, "create_pool", actions, FEE
        )
        self.token_migrated[token_uid] = True
        # Store the pool key
        self.token_pools[token_uid] = pool_key

        # Update collected graduation fees
        # (Prevents trapping residual money due to rounding on transactions)
        # TODO: Check if it is token_reserve or if it shuold be minted.
        self.collected_graduation_fees += virtual_pool - liquidity_amount
        # Update platform statistics
        self.total_tokens_migrated += 1

    # TODO: Remove once deposit of htr is not required anymore
    @public(allow_deposit=True)
    def register_token(
        self,
        ctx: Context,
        token_name: str,
        token_symbol: str,
        description: str,
        twitter: str,
        telegram: str,
        website: str,
        url_logo: str,
    ) -> TokenUid:
        """Create a new token with the manager."""

        initial_token_reserve = self.default_token_total_supply

        token_uid = self.syscall.create_token(
            token_name, token_symbol, initial_token_reserve, False, False
        )

        # TODO: Validate no actions(but currently, it needs deposit of 1% HTR)

        # Register token in all dictionaries
        # Admin data
        self.token_creators[token_uid] = ctx.caller_id

        # Token metadata
        self.token_names[token_uid] = token_name
        self.token_symbols[token_uid] = token_symbol
        self.token_descriptions[token_uid] = description

        if twitter != "" and not twitter.startswith("https://"):
            twitter = ""
        if telegram != "" and not telegram.startswith("https://"):
            telegram = ""
        if website != "" and not website.startswith("https://"):
            website = ""
        if url_logo != "" and not url_logo.startswith("https://"):
            url_logo = ""

        self.token_twitters[token_uid] = twitter
        self.token_telegrams[token_uid] = telegram
        self.token_websites[token_uid] = website
        self.token_logos[token_uid] = url_logo

        # Core state
        self.token_virtual_pools[token_uid] = self.token_initial_virtual_pool[
            token_uid
        ] = self.default_initial_virtual_pool

        self.token_curve_constant[token_uid] = self.default_curve_constant
        self.token_reserves[token_uid] = initial_token_reserve
        self.token_pools[token_uid] = ""

        # Migration parameters
        self.token_migrated[token_uid] = False
        self.token_target_caps[token_uid] = self.default_target_market_cap
        self.token_liquidity_amounts[token_uid] = self.default_liquidity_amount

        # Token supply data
        self.token_minted_supplies[token_uid] = Amount(0)
        self.token_total_supplies[token_uid] = initial_token_reserve

        # Statistics
        self.token_volumes[token_uid] = Amount(0)
        self.token_tx_counts[token_uid] = 0
        self.token_last_activities[token_uid] = ctx.timestamp

        # Register token in the list
        self.all_tokens.append(token_uid)

        # Initialize user balances
        self.token_user_balances[token_uid] = {}

        # Update platform statistics
        self.total_tokens_created += 1

        return token_uid

    @public(allow_deposit=True, allow_withdrawal=True)
    def buy_tokens(self, ctx: Context, token_uid: TokenUid) -> None:
        """Buy tokens using HTR."""
        self._validate_token_exists(token_uid)
        self._validate_not_migrated(token_uid)

        action_in, action_out = self._get_actions_in_out(ctx)

        # Ensure correct tokens
        if action_in.token_uid != HTR_UID:
            raise InvalidState("Input token must be HTR")
        if action_out.token_uid != token_uid:
            raise InvalidState("Output token must be token_uid")

        # Calculate and apply buy fee (operates with ceiling division)
        fee_amount = (
            action_in.amount * self.buy_fee_rate + BASIS_POINTS - 1
        ) // BASIS_POINTS
        net_amount = action_in.amount - fee_amount

        # Verify if the payment is too low, making the fee equal to net_amout because of ceiling division
        if net_amount <= 0:
            raise TransactionDenied("Fee was not matched")

        virtual_pool = self.token_virtual_pools.get(token_uid)
        target_market_cap = self.token_target_caps.get(token_uid)
        max_net_amount = target_market_cap - virtual_pool

        # Validade if transaction is not beyond market cap
        if net_amount > max_net_amount:
            raise InsufficientAmount("Transaction beyond market cap")

        # Calculate tokens to return
        tokens_out = self._calculate_tokens_out(token_uid, net_amount)
        if tokens_out > self.token_reserves[token_uid]:
            raise InsufficientAmount("Insufficient token reserve")

        if tokens_out == 0:
            raise TransactionDenied("Below minimum purchase")

        if action_out.amount > tokens_out:
            raise TransactionDenied("Payment does not match cost")

        # Handle slippage return if user requested less than available
        slippage = tokens_out - action_out.amount
        if slippage > 0:
            self._update_balance(token_uid, ctx.caller_id, slippage)

        # Update collected fees
        self.collected_buy_fees += fee_amount

        # Update token state
        self.token_virtual_pools[token_uid] += net_amount
        self.token_reserves[token_uid] -= action_out.amount

        # Update statistics
        self.token_volumes[token_uid] += action_in.amount
        self.token_tx_counts[token_uid] += 1
        self.token_last_activities[token_uid] = ctx.timestamp

        # Check migration threshold
        # Only attempt migration if we've reached the target market cap
        if (
            self.token_virtual_pools[token_uid] >= self.token_target_caps[token_uid]
        ) and not self.token_migrated[token_uid]:
            self.migrate_liquidity(token_uid)

    @public(allow_deposit=True, allow_withdrawal=True)
    def sell_tokens(self, ctx: Context, token_uid: TokenUid) -> None:
        """Sell tokens for HTR."""
        self._validate_token_exists(token_uid)
        self._validate_not_migrated(token_uid)

        action_in, action_out = self._get_actions_in_out(ctx)

        # Ensure correct tokens
        if action_in.token_uid != token_uid:
            raise InvalidState("Input token must be token_uid")
        if action_out.token_uid != HTR_UID:
            raise InvalidState("Output token must be HTR")

        if action_in.amount < 1:
            raise TransactionDenied("Below minimum sale")

        # Calculate HTR return
        htr_out = self._calculate_htr_out(token_uid, action_in.amount)

        # Apply sell fee
        fee_amount = (htr_out * self.sell_fee_rate + BASIS_POINTS - 1) // BASIS_POINTS
        net_amount = htr_out - fee_amount

        # Verify if the amount sold is not too low, making the fee equal to net_amout because of ceiling division
        if net_amount <= 0:
            raise TransactionDenied("Fee was not matched")

        if net_amount < action_out.amount:
            raise TransactionDenied("Selling price was not matched")

        # Update collected fees

        self.collected_sell_fees += fee_amount

        # Handle slippage return if user requested less than available
        slippage = net_amount - action_out.amount
        if slippage > 0:
            self._update_balance(HTR_UID, ctx.caller_id, slippage)

        # Update token state
        self.token_virtual_pools[token_uid] -= htr_out
        self.token_reserves[token_uid] += action_in.amount

        # Update statistics
        self.token_volumes[token_uid] += htr_out
        self.token_tx_counts[token_uid] += 1
        self.token_last_activities[token_uid] = ctx.timestamp

    @public(allow_withdrawal=True)
    def withdraw_fees(self, ctx: Context) -> None:
        """Withdraw collected fees for a token."""
        self._only_admin(ctx)

        total_fees = (
            self.collected_buy_fees
            + self.collected_sell_fees
            + self.collected_graduation_fees
        )
        if total_fees <= 0:
            raise InvalidState("No fees to withdraw")

        action = self._get_action(ctx, NCActionType.WITHDRAWAL)
        if action.token_uid != HTR_UID:
            raise NCFail("Can only withdraw HTR")
        withdraw_amount = action.amount
        if withdraw_amount > total_fees:
            raise NCFail("Invalid withdrawal amount")

        # Subtract fee counters
        remaining = withdraw_amount
        # Subtract from buy fees first
        deduct = min(remaining, self.collected_buy_fees)
        self.collected_buy_fees -= deduct
        remaining -= deduct

        if remaining <= 0:
            return

        # Then subtract from sell fees
        deduct = min(remaining, self.collected_sell_fees)
        self.collected_sell_fees -= deduct
        remaining -= deduct

        if remaining <= 0:
            return

        # Finally subtract from graduation fees
        deduct = min(remaining, self.collected_graduation_fees)
        self.collected_graduation_fees -= deduct
        remaining -= deduct

    @public
    def change_buy_fee_rate(self, ctx: Context, buy_fee_rate: int) -> None:
        """Change the buy fee rate for all tokens."""
        self._only_admin(ctx)
        if buy_fee_rate > 1000 or buy_fee_rate < 0:
            raise InvalidParameters("Invalid buy fee rate")
        self.buy_fee_rate = buy_fee_rate

    @public
    def change_sell_fee_rate(self, ctx: Context, sell_fee_rate: int) -> None:
        """Change the sell fee rate for all tokens."""
        self._only_admin(ctx)
        if sell_fee_rate > 1000 or sell_fee_rate < 0:
            raise InvalidParameters("Invalid sell fee rate")
        self.sell_fee_rate = sell_fee_rate

    @public
    def change_graduation_fee(self, ctx: Context, graduation_fee: Amount) -> None:
        """Change the graduation fee for all tokens."""
        self._only_admin(ctx)
        if graduation_fee < 0:
            raise InvalidParameters("Invalid graduation fee")
        self.graduation_fee = graduation_fee

    @public
    def change_bonding_curve(
        self,
        ctx: Context,
        target_market_cap: Amount,
        liquidity_amount: Amount,
        default_initial_virtual_pool: Amount,
        default_curve_constant: Amount,
        default_token_total_supply: Amount,
    ) -> None:
        """Change the bonding curve parameter for new tokens."""
        self._only_admin(ctx)
        if target_market_cap <= 0 or target_market_cap < liquidity_amount:
            raise InvalidParameters("Invalid target market cap")
        elif liquidity_amount <= 0:
            raise InvalidParameters("Invalid liquidity amount")
        elif default_initial_virtual_pool <= 0:
            raise InvalidParameters("Invalid virtual pool amount")
        elif default_curve_constant <= 0:
            raise InvalidParameters("Invalid curve constant")
        elif default_token_total_supply <= 0:
            raise InvalidParameters("Invalid token amount")
        self.default_target_market_cap = target_market_cap
        self.default_liquidity_amount = liquidity_amount
        self.default_initial_virtual_pool = default_initial_virtual_pool
        self.default_curve_constant = default_curve_constant
        self.default_token_total_supply = default_token_total_supply

    @public
    def transfer_admin(self, ctx: Context, new_admin: CallerId) -> None:
        """Transfers admin rights to a new address."""
        self._only_admin(ctx)
        self.admin_address = new_admin

    @view
    def get_token_info(self, token_uid: TokenUid) -> TokenInfo:
        """Get detailed information about a token."""
        return self._get_token(token_uid)

    @view
    def get_last_n_tokens(self, number: int) -> str:
        """Get a string with the UIDs of the last n tokens created by this contract."""
        number = max(number, 0)
        last_tokens = []
        for n in range(1, min(number, len(self.all_tokens)) + 1):
            last_tokens.append(self.all_tokens[-n].hex())
        return " ".join(map(str, last_tokens))

    @view
    def get_user_balance(self, address: CallerId, token_uid: TokenUid) -> Amount:
        """Get the balance of a user for a specific token."""
        if not token_uid == HTR_UID:
            self._validate_token_exists(token_uid)

        if token_uid not in self.token_user_balances:
            return Amount(0)

        return self.token_user_balances[token_uid].get(address, 0)

    @view
    def quote_buy(self, token_uid: TokenUid, htr_amount: Amount) -> dict[str, int]:
        """
        Quote buying tokens with HTR.

        The "recommended_htr_amount" is how many htr should be payed to buy the "amount_out" returned.
        It only differs from "htr_amout" when the provided value is invalid.
        """
        self._validate_token_exists(token_uid)

        if self.token_migrated.get(token_uid, False):
            raise InvalidState("Contract has migrated")

        # Calculate and apply buy fee using ceiling division
        fee_amount = (htr_amount * self.buy_fee_rate + BASIS_POINTS - 1) // BASIS_POINTS
        net_amount = htr_amount - fee_amount

        virtual_pool = self.token_virtual_pools.get(token_uid)
        target_market_cap = self.token_target_caps.get(token_uid)
        max_net_amount = target_market_cap - virtual_pool

        if net_amount > max_net_amount:
            tokens_out = self._calculate_tokens_out(token_uid, max_net_amount)

            numerator = max_net_amount * BASIS_POINTS
            denominator = BASIS_POINTS - self.buy_fee_rate
            recommended_htr_amount = (numerator + denominator - 1) // denominator

            price_impact = self._calculate_price_impact(
                token_uid, recommended_htr_amount, tokens_out
            )
        else:
            tokens_out = self._calculate_tokens_out(token_uid, net_amount)
            price_impact = self._calculate_price_impact(
                token_uid, htr_amount, tokens_out
            )
            recommended_htr_amount = htr_amount

        # Check if less HTR are needed to buy this amount of tokens
        recommended_htr_amount = min(
            recommended_htr_amount,
            self._calculate_htr_needed(token_uid, tokens_out) + fee_amount,
        )

        return {
            "amount_out": tokens_out,
            "price_impact": price_impact,
            "recommended_htr_amount": recommended_htr_amount,
        }

    @view
    def quote_sell(self, token_uid: TokenUid, token_amount: Amount) -> dict[str, int]:
        """Quote selling tokens for HTR."""
        self._validate_token_exists(token_uid)

        if self.token_migrated.get(token_uid, False):
            raise InvalidState("Contract has migrated")

        htr_out = self._calculate_htr_out(token_uid, token_amount)

        # Apply sell fee
        fee_amount = (htr_out * self.sell_fee_rate + BASIS_POINTS - 1) // BASIS_POINTS
        net_amount = htr_out - fee_amount

        price_impact = self._calculate_price_impact(token_uid, token_amount, net_amount)

        return {"amount_out": net_amount, "price_impact": price_impact}

    @view
    def get_platform_stats(self) -> dict[str, int]:
        """Get platform-wide statistics."""
        return {
            "total_tokens_created": self.total_tokens_created,
            "total_tokens_migrated": self.total_tokens_migrated,
            "platform_fees_collected": self.collected_buy_fees
            + self.collected_sell_fees
            + self.collected_graduation_fees,
        }

    @view
    def is_token_migrated(self, token_uid: TokenUid) -> bool:
        """Check if a token has been migrated to DEX."""
        self._validate_token_exists(token_uid)
        return self.token_migrated.get(token_uid, False)

    @view
    def get_pool(self, token_uid: TokenUid) -> ContractId:
        """Get the pool key for a migrated token."""
        self._validate_token_exists(token_uid)

        if not self.token_migrated.get(token_uid, False):
            raise InvalidState("Token not migrated")

        return self.token_pools.get(token_uid)

    @view
    def front_quote_exact_tokens_for_tokens(
        self, token_uid: TokenUid, amount_in: Amount, token_in: TokenUid
    ) -> dict[str, float]:
        """Post-migration quote using Dozer pool"""
        self._validate_token_exists(token_uid)

        if not self.token_migrated.get(token_uid, False):
            raise InvalidState("Token not migrated")

        if not token_in in (token_uid, HTR_UID):
            raise InvalidParameters("Invalid token to swap")

        token_out = HTR_UID if token_uid == token_in else token_in

        return self.syscall.call_view_method(
            self.dozer_pool_manager_id,
            "front_quote_exact_tokens_for_tokens",
            amount_in,
            token_in,
            token_out,
            FEE,
        )

    @view
    def front_quote_tokens_for_exact_tokens(
        self, token_uid: TokenUid, amount_out: Amount, token_in: TokenUid
    ) -> dict[str, float]:
        """Post-migration quote using Dozer pool"""
        self._validate_token_exists(token_uid)

        if not self.token_migrated.get(token_uid, False):
            raise InvalidState("Token not migrated")

        if not token_in in (token_uid, HTR_UID):
            raise InvalidParameters("Invalid token to swap")

        token_out = HTR_UID if token_uid == token_in else token_in

        return self.syscall.call_view_method(
            self.dozer_pool_manager_id,
            "front_quote_tokens_for_exact_tokens",
            amount_out,
            token_in,
            token_out,
            FEE,
        )


__blueprint__ = KhensuManager
