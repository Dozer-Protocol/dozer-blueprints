from typing import NamedTuple

from hathor import (
    Amount,
    Blueprint,
    CallerId,
    Context,
    HATHOR_TOKEN_UID,
    NCAction,
    NCDepositAction,
    NCFail,
    NCWithdrawalAction,
    Timestamp,
    TokenUid,
    ContractId,
    NCActionType,
    export,
    public,
    view,
)


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
FEE = 10  # 0.1%
PROTOCOL_FEE = 20  # 20% of the fee goes to the protocol


class TokenData(NamedTuple):
    """Consolidated token data stored in a single dict"""
    creator: CallerId
    token_name: str
    token_symbol: str
    image_link: str
    description: str
    twitter: str
    telegram: str
    website: str
    curve_constant: Amount
    initial_virtual_pool: Amount
    virtual_pool: Amount
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
    created_at: Timestamp


class TokenInfo(NamedTuple):
    """NamedTuple with data from a specific registered token for API responses"""

    creator: str
    token_name: str
    token_symbol: str
    image_link: str
    description: str
    twitter: str
    telegram: str
    website: str
    market_cap: Amount
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
    created_at: Timestamp


# Token data will be stored in separate dictionaries for each property
@export
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
    all_tokens: list[TokenUid]
    symbol_dict: dict[str, TokenUid]

    # Consolidated token data
    tokens: dict[TokenUid, TokenData]

    # User balances from slippage
    token_user_balances: dict[TokenUid, dict[CallerId, Amount]]

    # Platform statistics
    total_tokens_created: int
    total_tokens_migrated: int
    collected_buy_fees: Amount
    collected_sell_fees: Amount
    collected_graduation_fees: Amount

    # LRU Cache for most recently accessed tokens
    lru_prev: dict[TokenUid, TokenUid]  # Previous pointer in doubly-linked list
    lru_next: dict[TokenUid, TokenUid]  # Next pointer in doubly-linked list
    lru_head: TokenUid  # Most recently used (head of list)
    lru_tail: TokenUid  # Least recently used (tail of list)
    lru_cache_capacity: int  # Maximum cache size
    lru_cache_size: int  # Current cache size
    lru_null_token: TokenUid  # Sentinel value to represent null

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
        lru_cache_capacity: int,
    ) -> None:
        """Initialize the KhensuManager contract."""
        caller_address = ctx.get_caller_address()
        assert caller_address is not None, "Caller address must be set"
        self.admin_address = caller_address
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

        # LRU Cache initialization
        self.lru_null_token = HATHOR_TOKEN_UID  # type: ignore
        self.lru_head = self.lru_null_token
        self.lru_tail = self.lru_null_token
        self.lru_cache_capacity = lru_cache_capacity
        self.lru_cache_size = 0
        self.lru_prev = {}
        self.lru_next = {}

        # Initialize empty values
        self.all_tokens = []
        self.symbol_dict = {}
        self.tokens = {}
        self.token_user_balances = {}

    def _get_token_data(self, token_uid: TokenUid) -> TokenData:
        """Get the token data, raising error if not found."""
        if token_uid not in self.tokens:
            raise TokenNotFound(f"Token does not exist: {token_uid.hex()}")
        return self.tokens[token_uid]

    def _update_token_data(self, token_uid: TokenUid, **updates) -> None:
        """Update specific fields of token data."""
        token_data = self._get_token_data(token_uid)
        self.tokens[token_uid] = token_data._replace(**updates)

    def _get_token(self, token_uid: TokenUid) -> TokenInfo:
        """Get the token data formatted for API responses."""
        token_data = self._get_token_data(token_uid)
        market_cap = Amount(token_data.virtual_pool - token_data.initial_virtual_pool)

        return TokenInfo(
            token_data.creator.hex(),
            token_data.token_name,
            token_data.token_symbol,
            token_data.image_link,
            token_data.description,
            token_data.twitter,
            token_data.telegram,
            token_data.website,
            market_cap,
            token_data.curve_constant,
            token_data.initial_virtual_pool,
            token_data.token_reserve,
            token_data.pool_key,
            token_data.is_migrated,
            token_data.target_market_cap,
            token_data.liquidity_amount,
            token_data.minted_supply,
            token_data.total_supply,
            token_data.total_volume,
            token_data.transaction_count,
            token_data.last_activity,
            token_data.created_at,
        )

    def _validate_token_exists(self, token_uid: TokenUid) -> None:
        """Check if a token exists, raising error if not."""
        if token_uid not in self.tokens:
            raise TokenNotFound(f"Token does not exist: {token_uid.hex()}")

    def _only_admin(self, ctx: Context) -> None:
        """Validate that the caller is the platform admin."""
        if ctx.get_caller_address() != self.admin_address:
            raise Unauthorized("Only admin can call this method")

    def _validate_not_migrated(self, token_uid: TokenUid) -> None:
        """Validate that a token has not been migrated."""
        token_data = self._get_token_data(token_uid)
        if token_data.is_migrated:
            raise InvalidState("Token has already migrated")

    def _calculate_fee(self, amount: Amount, fee_rate: int) -> Amount:
        """Calculate fee using ceiling division."""
        return Amount((amount * fee_rate + BASIS_POINTS - 1) // BASIS_POINTS)

    def _validate_actions_in_out(
        self, ctx: Context, expected_in: TokenUid, expected_out: TokenUid
    ) -> tuple[NCDepositAction, NCWithdrawalAction]:
        """Get and validate deposit/withdrawal pair with expected token types."""
        action_in, action_out = self._get_actions_in_out(ctx)

        if action_in.token_uid != expected_in:
            raise InvalidState(f"Input token must be {expected_in.hex()}")
        if action_out.token_uid != expected_out:
            raise InvalidState(f"Output token must be {expected_out.hex()}")

        assert isinstance(action_in, NCDepositAction), "Invalid action type"
        assert isinstance(action_out, NCWithdrawalAction), "Invalid action type"

        return action_in, action_out

    def _calculate_tokens_out(self, token_uid: TokenUid, htr_amount: Amount) -> Amount:
        """Calculate tokens to return for a given HTR input using bonding curve."""
        # Using bonding curve formula: T = CC * H / (VP * (VP + H))
        # where T = tokens out, CC = curve constant, VP = virtual pool, H = HTR in
        token_data = self._get_token_data(token_uid)
        numerator = htr_amount * token_data.curve_constant
        denominator = (token_data.virtual_pool + htr_amount) * token_data.virtual_pool
        if denominator == 0:
            return Amount(0)
        return Amount(numerator // denominator)

    def _calculate_htr_needed(
        self, token_uid: TokenUid, token_amount: Amount
    ) -> Amount:
        """Calculate HTR needed for a given token input using bonding curve."""
        # Using inverse bonding curve: H = T * VP^2 / (CC - VP * T)
        # where H = HTR out, CC = curve constant, VP = virtual pool, T = tokens in
        token_data = self._get_token_data(token_uid)

        if token_amount >= token_data.token_reserve:
            return Amount(0)

        numerator = token_amount * token_data.virtual_pool**2
        denominator = token_data.curve_constant - token_data.virtual_pool * token_amount
        if denominator == 0:
            return Amount(0)
        # Ceiling division
        return Amount((numerator + denominator - 1) // denominator)

    def _calculate_htr_out(self, token_uid: TokenUid, token_amount: Amount) -> Amount:
        """Calculate HTR to return for a given token input using bonding curve."""
        # Using inverse bonding curve: H = T * VP^2 / (CC + VP * T)
        # where H = HTR out, CC = curve constant, VP = virtual pool, T = tokens in
        token_data = self._get_token_data(token_uid)

        if token_amount >= token_data.token_reserve:
            return Amount(0)

        numerator = token_amount * token_data.virtual_pool**2
        denominator = token_data.curve_constant + token_data.virtual_pool * token_amount
        if denominator == 0:
            return Amount(0)
        return Amount(numerator // denominator)

    def _calculate_tokens_needed(
        self, token_uid: TokenUid, htr_amount: Amount
    ) -> Amount:
        """Calculate tokens needed for a given HTR input using bonding curve."""
        # Using bonding curve formula: T = CC * H / (VP * (VP - H))
        # where T = tokens out, CC = curve constant, VP = virtual pool, H = HTR in
        token_data = self._get_token_data(token_uid)
        numerator = htr_amount * token_data.curve_constant
        denominator = (token_data.virtual_pool - htr_amount) * token_data.virtual_pool
        if denominator == 0:
            return Amount(0)
        # Ceiling division
        return Amount((numerator + denominator - 1) // denominator)
    
    def _get_action(self, ctx: Context, action_type: NCActionType) -> NCAction:
        """Get and validate single action"""
        if len(ctx.actions) != 1:
            raise NCFail("Expected single action")
        action_tuple = list(ctx.actions.values())[0]
        total_amount = Amount(0)
        for action in action_tuple:
            assert isinstance(action, NCDepositAction) or isinstance(action, NCWithdrawalAction), "Invalid action type"
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
                and action_tuple[0].token_uid == HATHOR_TOKEN_UID
            ):
                total_deposit_htr = Amount(0)
                for deposit in action_tuple:
                    assert isinstance(deposit, NCDepositAction), "Invalid action type"
                    total_deposit_htr += deposit.amount
                action_htr = NCDepositAction(
                    token_uid=action_tuple[0].token_uid, amount=total_deposit_htr
                )
            elif (
                action_tuple[0].type == NCActionType.DEPOSIT
                and action_tuple[0].token_uid != HATHOR_TOKEN_UID
            ):
                total_deposit_token = Amount(0)
                for deposit in action_tuple:
                    assert isinstance(deposit, NCDepositAction), "Invalid action type"
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
                    assert isinstance(deposit, NCDepositAction), "Invalid action type"
                    total_deposit += deposit.amount
                action_in = NCDepositAction(
                    token_uid=action_tuple[0].token_uid, amount=total_deposit
                )
            elif action_tuple[0].type == NCActionType.WITHDRAWAL:
                total_withdrawal = Amount(0)
                for withdrawal in action_tuple:
                    assert isinstance(withdrawal, NCWithdrawalAction), "Invalid action type"
                    total_withdrawal += withdrawal.amount
                action_out = NCWithdrawalAction(
                    token_uid=action_tuple[0].token_uid, amount=total_withdrawal
                )

        if not action_in or not action_out:
            raise InvalidState("Must have one deposit and one withdrawal")

        return action_in, action_out

    def _calculate_price_impact(
        self, token_uid: TokenUid, theoretical_amount: Amount, actual_amount: Amount
    ) -> int:
        """Calculate price impact percentage using standard AMM formula.

        Args:
            token_uid: The token identifier
            theoretical_amount: Expected amount without fees/slippage
            actual_amount: Actual amount the user receives

        Returns:
            Price impact as basis points (e.g., 250 = 2.5%)
        """
        if actual_amount == 0 or theoretical_amount == 0:
            return 0

        self._validate_token_exists(token_uid)

        # Standard AMM price impact formula: (theoretical - actual) / theoretical * 10000
        # This follows the same approach as Dozer and other AMMs
        if theoretical_amount <= actual_amount:
            return 0  # No negative impact

        impact = 10000 * (theoretical_amount - actual_amount) // theoretical_amount
        return max(0, min(impact, 10000))

    def _update_balance(
        self, token_uid: TokenUid, address: CallerId, amount: Amount
    ) -> None:
        """Update user balance for a token."""
        if token_uid not in self.token_user_balances:
            self.token_user_balances[token_uid] = {}

        user_balance = self.token_user_balances[token_uid]
        user_balance[address] = Amount(user_balance.get(address, Amount(0)) + amount)
        self.token_user_balances[token_uid] = user_balance

    def _evict_lru_tail(self) -> None:
        """Evict the least recently used token from cache (O(1) operation)."""
        if self.lru_cache_size == 0:
            return

        tail_uid = self.lru_tail
        new_tail = self.lru_prev.get(tail_uid, self.lru_null_token)

        # Remove tail from dictionaries
        if tail_uid in self.lru_prev:
            del self.lru_prev[tail_uid]
        if tail_uid in self.lru_next:
            del self.lru_next[tail_uid]

        # Update new tail's next pointer
        if new_tail != self.lru_null_token:
            self.lru_next[new_tail] = self.lru_null_token

        # Update tail pointer
        self.lru_tail = new_tail

        # If we evicted the only element, update head too
        if tail_uid == self.lru_head:
            self.lru_head = self.lru_null_token

        # Decrement size
        self.lru_cache_size -= 1

    def _remove_from_lru_list(self, token_uid: TokenUid) -> None:
        """Remove token from its current position in LRU list (O(1) operation)."""
        prev_node = self.lru_prev.get(token_uid, self.lru_null_token)
        next_node = self.lru_next.get(token_uid, self.lru_null_token)

        # Update previous node's next pointer
        if prev_node != self.lru_null_token:
            self.lru_next[prev_node] = next_node
        else:
            # This was the head
            self.lru_head = next_node

        # Update next node's prev pointer
        if next_node != self.lru_null_token:
            self.lru_prev[next_node] = prev_node
        else:
            # This was the tail
            self.lru_tail = prev_node

    def _add_to_lru_head(self, token_uid: TokenUid) -> None:
        """Add token to the head of LRU list (most recent position, O(1) operation)."""
        # Set token's pointers
        self.lru_prev[token_uid] = self.lru_null_token
        self.lru_next[token_uid] = self.lru_head

        # Update old head's prev pointer
        if self.lru_head != self.lru_null_token:
            self.lru_prev[self.lru_head] = token_uid

        # Update head pointer
        self.lru_head = token_uid

        # If this is the first element, update tail too
        if self.lru_tail == self.lru_null_token:
            self.lru_tail = token_uid

    def update_lru(self, token_uid: TokenUid) -> None:
        """Update LRU cache by moving token to head (most recent). O(1) operation with no loops."""
        # Check if token already in cache
        token_in_cache = token_uid in self.lru_prev

        if token_in_cache:
            # Remove from current position
            self._remove_from_lru_list(token_uid)
            # Move to head (will be added below)
        else:
            # New token - check capacity
            if self.lru_cache_size >= self.lru_cache_capacity:
                # Evict least recently used (tail)
                self._evict_lru_tail()

            # Increment size for new token
            self.lru_cache_size += 1

        # Add/move token to head (most recent)
        self._add_to_lru_head(token_uid)

    def migrate_liquidity(self, token_uid: TokenUid) -> None:
        """Migrate a token's liquidity to a DEX when threshold is reached."""
        token_data = self._get_token_data(token_uid)

        if token_data.is_migrated:
            raise InvalidState("Token has already migrated")

        # Get relevant token data
        market_cap = token_data.virtual_pool - token_data.initial_virtual_pool

        # Check if market cap threshold is reached
        if market_cap < token_data.target_market_cap:
            raise InvalidState("Market cap threshold not reached")

        # Validate balances
        if token_data.token_reserve == 0:
            raise NCFail("No tokens to migrate")
        if market_cap < token_data.liquidity_amount + self.graduation_fee:
            raise NCFail("Insufficient HTR for migration")

        # Add liquidity to Dozer pool
        # TODO: Check if it is token_reserve or if it should be minted.
        actions = [
            NCDepositAction(token_uid=HATHOR_TOKEN_UID, amount=token_data.liquidity_amount),
            NCDepositAction(token_uid=token_uid, amount=token_data.token_reserve),
        ]

        # Call Dozer Pool Manager to create pool
        # TODO: check FEES
        dozer = self.syscall.get_contract(self.dozer_pool_manager_id, blueprint_id=None)
        pool_key = dozer.public(*actions).create_pool(FEE)

        # Store the pool key
        self._update_token_data(token_uid, is_migrated=True, pool_key=pool_key)

        # Update collected graduation fees
        # (Prevents trapping residual money due to rounding on transactions)
        # TODO: Check if it is token_reserve or if it should be minted.
        self.collected_graduation_fees = Amount(self.collected_graduation_fees + market_cap - token_data.liquidity_amount)

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
        image_link: str,
    ) -> TokenUid:
        """Create a new token with the manager."""
        initial_token_reserve = self.default_token_total_supply

        token_uid = self.syscall.create_deposit_token(
            token_name=token_name,
            token_symbol=token_symbol,
            amount=initial_token_reserve,
        )

        # TODO: Validate no actions(but currently, it needs deposit of 1% HTR)

        caller_address = ctx.get_caller_address()
        assert caller_address is not None, "Caller address must be set"

        if twitter and not twitter.startswith("https://"):
            twitter = ""
        if telegram and not telegram.startswith("https://"):
            telegram = ""
        if website and not website.startswith("https://"):
            website = ""

        # Store image hash if provided
        if not image_link or len(image_link) < 32:
            image_link = ""

        self.tokens[token_uid] = TokenData(
            creator=caller_address,
            token_name=token_name,
            token_symbol=token_symbol,
            image_link=image_link,
            description=description,
            twitter=twitter,
            telegram=telegram,
            website=website,
            curve_constant=self.default_curve_constant,
            initial_virtual_pool=self.default_initial_virtual_pool,
            virtual_pool=self.default_initial_virtual_pool,
            token_reserve=initial_token_reserve,
            pool_key="",
            is_migrated=False,
            target_market_cap=self.default_target_market_cap,
            liquidity_amount=self.default_liquidity_amount,
            minted_supply=Amount(0),
            total_supply=initial_token_reserve,
            total_volume=Amount(0),
            transaction_count=0,
            last_activity=Timestamp(ctx.block.timestamp),
            created_at=Timestamp(ctx.block.timestamp),
        )

        self.symbol_dict[token_symbol] = token_uid
        self.all_tokens.append(token_uid)
        self.token_user_balances[token_uid] = {}
        self.total_tokens_created += 1
        self.update_lru(token_uid)

        return token_uid

    @public(allow_deposit=True, allow_withdrawal=True)
    def buy_tokens(self, ctx: Context, token_uid: TokenUid) -> None:
        """Buy tokens using HTR."""
        self._validate_not_migrated(token_uid)

        action_in, action_out = self._validate_actions_in_out(ctx, HATHOR_TOKEN_UID, token_uid)

        # Calculate and apply buy fee (operates with ceiling division)
        fee_amount = self._calculate_fee(Amount(action_in.amount), self.buy_fee_rate)
        net_amount = Amount(action_in.amount - fee_amount)

        # Verify if the payment is too low, making the fee equal to net_amount because of ceiling division
        if net_amount <= 0:
            raise TransactionDenied("Fee was not matched")

        token_data = self._get_token_data(token_uid)
        market_cap = Amount(token_data.virtual_pool - token_data.initial_virtual_pool)
        max_net_amount = Amount(token_data.target_market_cap - market_cap)

        # Calculate tokens to return
        tokens_out = self._calculate_tokens_out(token_uid, net_amount)
        if tokens_out > token_data.token_reserve:
            raise InsufficientAmount("Insufficient token reserve")

        if tokens_out == 0:
            raise TransactionDenied("Below minimum purchase")

        if action_out.amount > tokens_out:
            raise TransactionDenied("Payment does not match cost")

        # Validate if transaction is not beyond market cap
        tokens_to_graduate = self._calculate_tokens_out(token_uid, max_net_amount)
        if tokens_to_graduate == self._calculate_tokens_out(token_uid, Amount(max_net_amount - 1)):
            tokens_to_graduate += 1
        if tokens_out > tokens_to_graduate:
            raise InsufficientAmount("Transaction beyond market cap")

        # Handle slippage return if user requested less than available
        slippage = Amount(tokens_out - action_out.amount)
        caller_address = ctx.get_caller_address()
        assert caller_address is not None, "Caller address must be set"

        if slippage > 0:
            self._update_balance(token_uid, caller_address, slippage)

        # Update collected fees
        self.collected_buy_fees = Amount(self.collected_buy_fees + fee_amount)

        # Update token state

        self._update_token_data(
            token_uid,
            virtual_pool=Amount(token_data.virtual_pool + net_amount),
            token_reserve=Amount(token_data.token_reserve - action_out.amount),
            total_volume=Amount(token_data.total_volume + action_in.amount),
            transaction_count=token_data.transaction_count + 1,
            last_activity=Timestamp(ctx.block.timestamp),
        )

        # Update LRU cache
        self.update_lru(token_uid)

        # Check migration threshold
        # Only attempt migration if we've reached the target market cap
        token_data = self._get_token_data(token_uid)
        if (
            token_data.virtual_pool - token_data.initial_virtual_pool >= token_data.target_market_cap
            and not token_data.is_migrated
        ):
            self.migrate_liquidity(token_uid)

    @public(allow_deposit=True, allow_withdrawal=True)
    def sell_tokens(self, ctx: Context, token_uid: TokenUid) -> None:
        """Sell tokens for HTR."""
        self._validate_not_migrated(token_uid)

        action_in, action_out = self._validate_actions_in_out(ctx, token_uid, HATHOR_TOKEN_UID)

        if action_in.amount < 1:
            raise TransactionDenied("Below minimum sale")

        # Calculate HTR return
        htr_out = self._calculate_htr_out(token_uid, Amount(action_in.amount))

        # Apply sell fee
        fee_amount = self._calculate_fee(htr_out, self.sell_fee_rate)
        net_amount = htr_out - fee_amount

        # Verify if the amount sold is not too low, making the fee equal to net_amount because of ceiling division
        if net_amount <= 0:
            raise TransactionDenied("Fee was not matched")

        if net_amount < action_out.amount:
            raise TransactionDenied("Selling price was not matched")

        # Update collected fees
        self.collected_sell_fees = Amount(self.collected_sell_fees + fee_amount)

        # Handle slippage return if user requested less than available
        slippage = Amount(net_amount - action_out.amount)
        caller_address = ctx.get_caller_address()
        assert caller_address is not None, "Caller address must be set"
        if slippage > 0:
            self._update_balance(HATHOR_TOKEN_UID, caller_address, slippage)

        # Update token state
        token_data = self._get_token_data(token_uid)
        self._update_token_data(
            token_uid,
            virtual_pool=Amount(token_data.virtual_pool - htr_out),
            token_reserve=Amount(token_data.token_reserve + action_in.amount),
            total_volume=Amount(token_data.total_volume + htr_out),
            transaction_count=token_data.transaction_count + 1,
            last_activity=Timestamp(ctx.block.timestamp),
        )

        # Update LRU cache
        self.update_lru(token_uid)

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
        assert isinstance(action, NCWithdrawalAction), "Invalid action type"

        if action.token_uid != HATHOR_TOKEN_UID:
            raise NCFail("Can only withdraw HTR")
        withdraw_amount = action.amount
        if withdraw_amount > total_fees:
            raise NCFail("Invalid withdrawal amount")

        # Subtract fee counters
        remaining = withdraw_amount
        # Subtract from buy fees first
        deduct = min(remaining, self.collected_buy_fees)
        self.collected_buy_fees = Amount(self.collected_buy_fees - deduct)
        remaining -= deduct

        if remaining <= 0:
            return

        # Then subtract from sell fees
        deduct = min(remaining, self.collected_sell_fees)
        self.collected_sell_fees = Amount(self.collected_sell_fees - deduct)
        remaining -= deduct

        if remaining <= 0:
            return

        # Finally subtract from graduation fees
        deduct = min(remaining, self.collected_graduation_fees)
        self.collected_graduation_fees = Amount(self.collected_graduation_fees - deduct)
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

    @public
    def change_lru_capacity(self, ctx: Context, new_capacity: int) -> None:
        """Change the LRU cache capacity (admin only)."""
        self._only_admin(ctx)
        if new_capacity <= 0:
            raise InvalidParameters("LRU cache capacity must be positive")

        # Evict tokens if new capacity is smaller than current size
        while self.lru_cache_size > new_capacity:
            self._evict_lru_tail()

        self.lru_cache_capacity = new_capacity

    @view
    def get_token_uid(self, token_symbol: str) -> str:
        """Get the token UID for a given symbol."""
        if token_symbol not in self.symbol_dict:
            raise TokenNotFound(f"No token found for the symbol {token_symbol}")
        return self.symbol_dict.get(token_symbol, HATHOR_TOKEN_UID).hex()

    @view
    def get_token_info(self, token_uid: TokenUid) -> TokenInfo:
        """Get detailed information about a token."""
        return self._get_token(token_uid)

    @view
    def get_last_n_tokens(self, number: int, offset: TokenUid) -> str:
        """Get N most recently accessed tokens from LRU cache starting after offset."""
        number = max(number, 0)
        last_tokens = []
        current = self.lru_head
        if offset in self.tokens and offset != self.lru_null_token:
            current = self.lru_next.get(offset, self.lru_null_token)

        for _ in range(min(number, self.lru_cache_size)):
            if current == self.lru_null_token:
                break
            last_tokens.append(current.hex())
            current = self.lru_next.get(current, self.lru_null_token)

        return " ".join(last_tokens)

    @view
    def get_newest_n_tokens(self, number: int, offset: int) -> str:
        """Get N newly created tokens after a given offset (reverse order)"""
        number = max(number, 0)
        offset = max(offset, 0)
        newest_tokens = []
        n = len(self.all_tokens)

        for i in range(-1 - offset, -1 - offset - number, -1):
            if -n <= i < 0:
                newest_tokens.append(self.all_tokens[i].hex())

        return " ".join(newest_tokens)

    @view
    def get_oldest_n_tokens(self, number: int, offset: int) -> str:
        """Get N oldest tokens after a given offset"""
        number = max(number, 0)
        offset = max(offset, 0)
        oldest_tokens = []
        n = len(self.all_tokens)

        for i in range(offset, min(offset + number, n)):
            oldest_tokens.append(self.all_tokens[i].hex())

        return " ".join(oldest_tokens)

    @view
    def get_user_balance(self, address: CallerId, token_uid: TokenUid) -> Amount:
        """Get the balance of a user for a specific token."""
        if not token_uid == HATHOR_TOKEN_UID:
            self._validate_token_exists(token_uid)

        if token_uid not in self.token_user_balances:
            return Amount(0)

        return self.token_user_balances[token_uid].get(address, Amount(0))

    @view
    def quote_buy(self, token_uid: TokenUid, htr_amount: Amount) -> dict[str, int]:
        """
        Quote buying tokens with HTR.

        The "recommended_htr_amount" is how many htr should be payed to buy the "amount_out" returned.
        It only differs from "htr_amout" when the provided value is invalid.
        """
        token_data = self._get_token_data(token_uid)

        if token_data.is_migrated:
            raise InvalidState("Contract has migrated")

        # Calculate and apply buy fee using ceiling division
        fee_amount = self._calculate_fee(htr_amount, self.buy_fee_rate)
        net_amount = Amount(htr_amount - fee_amount)

        market_cap = Amount(token_data.virtual_pool - token_data.initial_virtual_pool)
        max_net_amount = Amount(token_data.target_market_cap - market_cap)

        tokens_out = self._calculate_tokens_out(token_uid, net_amount)
        tokens_to_graduate = self._calculate_tokens_out(token_uid, max_net_amount)
        # Guarantee that the market cap will be met in order to graduate:
        if tokens_to_graduate == self._calculate_tokens_out(token_uid, Amount(max_net_amount - 1)):
            tokens_to_graduate += 1

        if tokens_out >= tokens_to_graduate:
            tokens_out = Amount(tokens_to_graduate)

            # Calculate theoretical tokens without fees (full HTR amount)
            tokens_theoretical = self._calculate_tokens_out(token_uid, Amount(max_net_amount + fee_amount))
            price_impact = self._calculate_price_impact(token_uid, tokens_theoretical, tokens_out)
        else:
            tokens_out = self._calculate_tokens_out(token_uid, net_amount)
            # Calculate theoretical tokens without fees (full HTR amount, no fee deduction)
            tokens_theoretical = self._calculate_tokens_out(token_uid, htr_amount)
            price_impact = self._calculate_price_impact(token_uid, tokens_theoretical, tokens_out)

        # Check if less HTR are needed to buy this amount of tokens
        min_htr_needed = self._calculate_htr_needed(token_uid, tokens_out)
        numerator = min_htr_needed * BASIS_POINTS
        denominator = BASIS_POINTS - self.buy_fee_rate
        min_htr_amount = (numerator + denominator - 1) // denominator
        recommended_htr_amount = min(htr_amount, min_htr_amount)

        return {
            "amount_out": tokens_out,
            "price_impact": price_impact,
            "recommended_htr_amount": recommended_htr_amount,
        }

    @view
    def quote_sell(self, token_uid: TokenUid, token_amount: Amount) -> dict[str, int]:
        """Quote selling tokens for HTR."""
        token_data = self._get_token_data(token_uid)

        if token_data.is_migrated:
            raise InvalidState("Contract has migrated")

        htr_out = self._calculate_htr_out(token_uid, token_amount)

        # Apply sell fee
        fee_amount = self._calculate_fee(htr_out, self.sell_fee_rate)
        net_amount = Amount(htr_out - fee_amount)

        # Calculate price impact: theoretical (before fees) vs actual (after fees)
        price_impact = self._calculate_price_impact(token_uid, htr_out, net_amount)

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
        token_data = self._get_token_data(token_uid)
        return token_data.is_migrated

    @view
    def get_pool(self, token_uid: TokenUid) -> str:
        """Get the pool key for a migrated token."""
        token_data = self._get_token_data(token_uid)

        if not token_data.is_migrated:
            raise InvalidState("Token not migrated")

        return token_data.pool_key

    @view
    def front_quote_exact_tokens_for_tokens(
        self, token_uid: TokenUid, amount_in: Amount, token_in: TokenUid
    ) -> dict[str, Amount]:
        """Quote swap for exact input amount after token has migrated to Dozer pool.

        Args:
            token_uid: The token that was migrated
            amount_in: Exact amount of input tokens
            token_in: Token being swapped in (must be token_uid or HTR)

        Returns:
            Dict with 'amount_out' showing how many tokens will be received
        """
        token_data = self._get_token_data(token_uid)

        if not token_data.is_migrated:
            raise InvalidState("Token not migrated")

        if token_in not in (token_uid, HATHOR_TOKEN_UID):
            raise InvalidParameters("Invalid token to swap")

        # Get pool reserves from Dozer using the oasis pattern
        # Reserves are returned in sorted order by token UID
        reserve_a, reserve_b = self.syscall.get_contract(
            self.dozer_pool_manager_id, blueprint_id=None
        ).view().get_reserves(token_uid, HATHOR_TOKEN_UID, FEE)

        # Determine which reserve corresponds to which token based on sorting
        # If token_uid < HATHOR_TOKEN_UID, then reserve_a=token, reserve_b=HTR
        if token_uid < HATHOR_TOKEN_UID:
            reserve_token = reserve_a
            reserve_htr = reserve_b
        else:
            reserve_token = reserve_b
            reserve_htr = reserve_a

        # Determine which reserve is in/out based on token_in
        if token_in == token_uid:
            reserve_in, reserve_out = reserve_token, reserve_htr
        else:
            reserve_in, reserve_out = reserve_htr, reserve_token

        # Calculate amount out using Dozer's formula
        # fee_denominator is always 1000 in Dozer pools
        amount_out = self.syscall.get_contract(
            self.dozer_pool_manager_id, blueprint_id=None
        ).view().get_amount_out(
            amount_in,
            reserve_in,
            reserve_out,
            FEE,
            1000
        )

        return {"amount_out": amount_out}

    @view
    def front_quote_tokens_for_exact_tokens(
        self, token_uid: TokenUid, amount_out: Amount, token_in: TokenUid
    ) -> dict[str, Amount]:
        """Quote swap for exact output amount after token has migrated to Dozer pool.

        Args:
            token_uid: The token that was migrated
            amount_out: Exact amount of output tokens desired
            token_in: Token being swapped in (must be token_uid or HTR)

        Returns:
            Dict with 'amount_in' showing how many input tokens are needed
        """
        token_data = self._get_token_data(token_uid)

        if not token_data.is_migrated:
            raise InvalidState("Token not migrated")

        if token_in not in (token_uid, HATHOR_TOKEN_UID):
            raise InvalidParameters("Invalid token to swap")

        # Get pool reserves from Dozer using the oasis pattern
        # Reserves are returned in sorted order by token UID
        reserve_a, reserve_b = self.syscall.get_contract(
            self.dozer_pool_manager_id, blueprint_id=None
        ).view().get_reserves(token_uid, HATHOR_TOKEN_UID, FEE)

        # Determine which reserve corresponds to which token based on sorting
        # If token_uid < HATHOR_TOKEN_UID, then reserve_a=token, reserve_b=HTR
        if token_uid < HATHOR_TOKEN_UID:
            reserve_token = reserve_a
            reserve_htr = reserve_b
        else:
            reserve_token = reserve_b
            reserve_htr = reserve_a

        # Determine which reserve is in/out based on token_in
        if token_in == token_uid:
            reserve_in, reserve_out = reserve_token, reserve_htr
        else:
            reserve_in, reserve_out = reserve_htr, reserve_token

        # Calculate amount in needed using Dozer's formula
        # fee_denominator is always 1000 in Dozer pools
        amount_in = self.syscall.get_contract(
            self.dozer_pool_manager_id, blueprint_id=None
        ).view().get_amount_in(
            amount_out,
            reserve_in,
            reserve_out,
            FEE,
            1000
        )

        return {"amount_in": amount_in}
