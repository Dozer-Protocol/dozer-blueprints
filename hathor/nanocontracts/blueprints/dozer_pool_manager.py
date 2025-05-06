# Copyright 2025 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

## TODO:
# - Include cross pool swap
# - Include signed pools for listing in Dozer dApp
# - Include select HTR-USDT pool for price returns
# - Include HTR pool-token map to help on price calculation

from typing import Any, NamedTuple

from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    TokenUid,
    NCAction,
    NCActionType,
    public,
    view,
)
import logging

logger = logging.getLogger(__name__)

PRECISION = 10**20


# Custom error classes
class PoolExists(NCFail):
    """Raised when trying to create a pool that already exists."""

    pass


class PoolNotFound(NCFail):
    """Raised when trying to use a pool that doesn't exist."""

    pass


class InvalidTokens(NCFail):
    """Raised when invalid tokens are provided."""

    pass


class InvalidFee(NCFail):
    """Raised when an invalid fee is provided."""

    pass


class InvalidAction(NCFail):
    """Raised when an invalid token action is provided."""

    pass


class Unauthorized(NCFail):
    """Raised when an unauthorized address tries to perform an action."""

    pass


class InvalidPath(NCFail):
    """Raised when an invalid swap path is provided."""

    pass


class InsufficientLiquidity(NCFail):
    """Raised when there is insufficient liquidity for an operation."""

    pass


class SwapResult(NamedTuple):
    """Result for an executed swap with the details of the execution.

    Notice that the results are presented for tokens in and tokens out.
    So one must check which one is Token A and which one is Token B."""

    amount_in: Amount
    slippage_in: Amount
    token_in: TokenUid
    amount_out: Amount
    token_out: TokenUid


class DozerPoolManager(Blueprint):
    """Singleton manager for multiple liquidity pools inspired by Uniswap v2.

    This contract manages multiple liquidity pools in a single contract.
    Each pool is identified by a composite key of token_a:token_b:fee.

    The swap methods are:
    - swap_exact_tokens_for_tokens()
    - swap_tokens_for_exact_tokens()

    Features:
    - Multiple pools in a single contract
    - Protocol fee collection
    - Liquidity management
    - Pool statistics tracking
    """

    # Administrative state
    owner: Address
    default_fee: Amount
    default_protocol_fee: Amount

    # Pool registry - token_a:token_b:fee -> exists
    pool_exists: dict[str, bool]

    # Token registry
    all_pools: list[str]  # List of all pool keys
    token_to_pools: dict[TokenUid, list[str]]  # Token -> list of pool keys

    # Pool data - using composite keys (token_a:token_b:fee)
    # Every pool data structure follows similar organization to Dozer_Pool_v1_1

    # Token information per pool
    pool_token_a: dict[str, TokenUid]  # pool_key -> token_a
    pool_token_b: dict[str, TokenUid]  # pool_key -> token_b

    # Pool reserves
    pool_reserve_a: dict[str, Amount]  # pool_key -> reserve_a
    pool_reserve_b: dict[str, Amount]  # pool_key -> reserve_b

    # Pool-specific fees
    pool_fee_numerator: dict[str, int]  # pool_key -> fee_numerator
    pool_fee_denominator: dict[str, int]  # pool_key -> fee_denominator

    # Liquidity tracking
    pool_total_liquidity: dict[str, Amount]  # pool_key -> total_liquidity
    pool_user_liquidity: dict[
        str, dict[Address, Amount]
    ]  # pool_key -> user -> liquidity

    # User balances (for slippage)
    pool_balance_a: dict[str, dict[Address, Amount]]  # pool_key -> user -> balance_a
    pool_balance_b: dict[str, dict[Address, Amount]]  # pool_key -> user -> balance_b
    pool_total_balance_a: dict[str, Amount]  # pool_key -> total_balance_a
    pool_total_balance_b: dict[str, Amount]  # pool_key -> total_balance_b

    # Pool statistics
    pool_accumulated_fee: dict[
        str, dict[TokenUid, Amount]
    ]  # pool_key -> token -> amount
    pool_transactions: dict[str, int]  # pool_key -> transaction count
    pool_last_activity: dict[str, int]  # pool_key -> last activity timestamp
    pool_volume_a: dict[str, Amount]  # pool_key -> volume_a
    pool_volume_b: dict[str, Amount]  # pool_key -> volume_b

    # Protocol fee accumulation (all pools)
    protocol_fee_balance: dict[TokenUid, Amount]  # token -> accumulated protocol fee

    @public
    def initialize(self, ctx: Context) -> None:
        """Initialize the DozerPoolManager contract.

        Sets up the initial state for the contract.
        """
        self.owner = ctx.address
        self.default_fee = 3  # 0.3%
        self.default_protocol_fee = 10  # 10% of fees

        # No need to initialize collections as they are already initialized by the Blueprint system

    def _get_pool_key(self, token_a: TokenUid, token_b: TokenUid, fee: Amount) -> str:
        """Create a standardized pool key from tokens and fee.

        Args:
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool

        Returns:
            A composite key in the format token_a:token_b:fee
        """
        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        # Create composite key
        return f"{token_a.hex()}/{token_b.hex()}/{fee}"

    def _validate_pool_exists(self, pool_key: str) -> None:
        """Check if a pool exists, raising error if not.

        Args:
            pool_key: The pool key to check

        Raises:
            PoolNotFound: If the pool does not exist
        """
        if not self.pool_exists.get(pool_key, False):
            raise PoolNotFound(f"Pool does not exist: {pool_key}")

    def _get_actions_a_b(
        self, ctx: Context, pool_key: str
    ) -> tuple[NCAction, NCAction]:
        """Get and validate token actions for a specific pool.

        Args:
            ctx: The transaction context
            pool_key: The pool key

        Returns:
            A tuple of (action_a, action_b)

        Raises:
            InvalidTokens: If the actions don't match the pool tokens
        """
        token_a = self.pool_token_a[pool_key]
        token_b = self.pool_token_b[pool_key]

        if set(ctx.actions.keys()) != {token_a, token_b}:
            raise InvalidTokens("Only token_a and token_b are allowed")

        action_a = ctx.actions[token_a]
        action_b = ctx.actions[token_b]

        # Update last activity timestamp
        self.pool_last_activity[pool_key] = ctx.timestamp

        return action_a, action_b

    def _get_actions_in_in(
        self, ctx: Context, pool_key: str
    ) -> tuple[NCAction, NCAction]:
        """Return token_a and token_b actions. It also validates that both are deposits.

        Args:
            ctx: The transaction context
            pool_key: The pool key

        Returns:
            A tuple of (action_a, action_b) both deposits

        Raises:
            InvalidAction: If any action is not a deposit
        """
        action_a, action_b = self._get_actions_a_b(ctx, pool_key)
        if action_a.type != NCActionType.DEPOSIT:
            raise InvalidAction("Only deposits allowed for token_a")
        if action_b.type != NCActionType.DEPOSIT:
            raise InvalidAction("Only deposits allowed for token_b")
        return action_a, action_b

    def _get_actions_out_out(
        self, ctx: Context, pool_key: str
    ) -> tuple[NCAction, NCAction]:
        """Return token_a and token_b actions. It also validates that both are withdrawals.

        Args:
            ctx: The transaction context
            pool_key: The pool key

        Returns:
            A tuple of (action_a, action_b) both withdrawals

        Raises:
            InvalidAction: If any action is not a withdrawal
        """
        action_a, action_b = self._get_actions_a_b(ctx, pool_key)
        if action_a.type != NCActionType.WITHDRAWAL:
            raise InvalidAction("Only withdrawals allowed for token_a")
        if action_b.type != NCActionType.WITHDRAWAL:
            raise InvalidAction("Only withdrawals allowed for token_b")
        return action_a, action_b

    def _get_actions_in_out(
        self, ctx: Context, pool_key: str
    ) -> tuple[NCAction, NCAction]:
        """Return action_in and action_out, where action_in is a deposit and action_out is a withdrawal.

        Args:
            ctx: The transaction context
            pool_key: The pool key

        Returns:
            A tuple of (action_in, action_out)

        Raises:
            InvalidAction: If there isn't exactly one deposit and one withdrawal
        """
        action_a, action_b = self._get_actions_a_b(ctx, pool_key)

        if action_a.type == NCActionType.DEPOSIT:
            action_in = action_a
            action_out = action_b
        else:
            action_in = action_b
            action_out = action_a

        if action_in.type != NCActionType.DEPOSIT:
            raise InvalidAction("Must have one deposit and one withdrawal")
        if action_out.type != NCActionType.WITHDRAWAL:
            raise InvalidAction("Must have one deposit and one withdrawal")

        return action_in, action_out

    def _update_balance(
        self, address: Address, amount: Amount, token: TokenUid, pool_key: str
    ) -> None:
        """Update balance for a given change.

        Args:
            address: The user address
            amount: The amount to update
            token: The token
            pool_key: The pool key
        """
        if amount == 0:
            return

        token_a = self.pool_token_a[pool_key]

        if token == token_a:
            # Update balance_a using the partial approach
            partial_balance_a = self.pool_balance_a.get(pool_key, {})
            partial_balance_a[address] = partial_balance_a.get(address, 0) + amount
            self.pool_balance_a[pool_key] = partial_balance_a

            # Update total balance
            pool_total_balance_a = self.pool_total_balance_a.get(pool_key, 0)
            pool_total_balance_a += amount
            self.pool_total_balance_a[pool_key] = pool_total_balance_a
        else:
            # Update balance_b using the partial approach
            partial_balance_b = self.pool_balance_b.get(pool_key, {})
            partial_balance_b[address] = partial_balance_b.get(address, 0) + amount
            self.pool_balance_b[pool_key] = partial_balance_b

            # Update total balance
            pool_total_balance_b = self.pool_total_balance_b.get(pool_key, 0)
            pool_total_balance_b += amount
            self.pool_total_balance_b[pool_key] = pool_total_balance_b

    def _get_reserve(self, token_uid: TokenUid, pool_key: str) -> Amount:
        """Get the reserve for a token in a pool.

        Args:
            token_uid: The token
            pool_key: The pool key

        Returns:
            The reserve amount

        Raises:
            InvalidTokens: If the token is not part of the pool
        """
        if token_uid == self.pool_token_a[pool_key]:
            return self.pool_reserve_a[pool_key]
        elif token_uid == self.pool_token_b[pool_key]:
            return self.pool_reserve_b[pool_key]
        else:
            raise InvalidTokens("Token not in pool")

    def _update_reserve(
        self, amount: Amount, token_uid: TokenUid, pool_key: str
    ) -> None:
        """Update reserve for a token in a pool.

        Args:
            amount: The amount to update
            token_uid: The token
            pool_key: The pool key

        Raises:
            InvalidTokens: If the token is not part of the pool
        """
        if token_uid == self.pool_token_a[pool_key]:
            self.pool_reserve_a[pool_key] += amount
        elif token_uid == self.pool_token_b[pool_key]:
            self.pool_reserve_b[pool_key] += amount
        else:
            raise InvalidTokens("Token not in pool")

    @view
    def quote(self, amount_a: Amount, reserve_a: Amount, reserve_b: Amount) -> Amount:
        """Return amount_b such that amount_b/amount_a = reserve_b/reserve_a = k

        Args:
            amount_a: The amount of token A
            reserve_a: The reserve of token A
            reserve_b: The reserve of token B

        Returns:
            The equivalent amount of token B
        """
        amount_b = (amount_a * reserve_b) // reserve_a
        return amount_b

    @view
    def get_amount_out(
        self,
        amount_in: Amount,
        reserve_in: Amount,
        reserve_out: Amount,
        fee_numerator: int,
        fee_denominator: int,
    ) -> Amount:
        """Return the maximum amount_out for an exact amount_in.

        Args:
            amount_in: The input amount
            reserve_in: The input reserve
            reserve_out: The output reserve
            fee_numerator: The fee numerator
            fee_denominator: The fee denominator

        Returns:
            The output amount
        """
        a = fee_denominator - fee_numerator
        b = fee_denominator
        amount_out = (reserve_out * amount_in * a) // (reserve_in * b + amount_in * a)
        if amount_out > reserve_out:
            amount_out = int(reserve_out * 0.99)
        return amount_out

    @view
    def get_amount_in(
        self,
        amount_out: Amount,
        reserve_in: Amount,
        reserve_out: Amount,
        fee_numerator: int,
        fee_denominator: int,
    ) -> Amount:
        """Return the minimum amount_in for an exact amount_out.

        Args:
            amount_out: The output amount
            reserve_in: The input reserve
            reserve_out: The output reserve
            fee_numerator: The fee numerator
            fee_denominator: The fee denominator

        Returns:
            The input amount
        """
        a = fee_denominator - fee_numerator
        b = fee_denominator
        if amount_out >= reserve_out:
            amount_in = self.quote(amount_out, reserve_out, reserve_in)
        else:
            amount_in = (reserve_in * amount_out * b) // (
                (reserve_out - amount_out) * a
            )
        return amount_in

    @view
    def _get_protocol_liquidity_increase(
        self, protocol_fee_amount: Amount, token: TokenUid, pool_key: str
    ) -> int:
        """Calculate the liquidity increase equivalent to a defined percentage of the
        collected fee to be minted to the owner address.

        Args:
            protocol_fee_amount: The protocol fee amount
            token: The token
            pool_key: The pool key

        Returns:
            The liquidity increase
        """
        if token == self.pool_token_a[pool_key]:
            liquidity_increase = (
                self.pool_total_liquidity[pool_key]
                * protocol_fee_amount
                // (self.pool_reserve_a[pool_key] * 2)
            )
        else:
            optimal_a = self.quote(
                protocol_fee_amount,
                self.pool_reserve_b[pool_key],
                self.pool_reserve_a[pool_key],
            )
            liquidity_increase = (
                self.pool_total_liquidity[pool_key]
                * optimal_a
                // (self.pool_reserve_a[pool_key] * 2)
            )
        return liquidity_increase

    @public
    def create_pool(
        self,
        ctx: Context,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> str:
        """Create a new liquidity pool with initial deposits.

        Args:
            ctx: The transaction context
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool (default: use default_fee)

        Returns:
            The pool key

        Raises:
            InvalidTokens: If tokens are invalid
            PoolExists: If the pool already exists
            InvalidFee: If the fee is invalid
        """
        # Use default fee if not specified
        if fee is None:
            fee = self.default_fee

        # Validate tokens
        if token_a == token_b:
            raise InvalidTokens("token_a cannot be equal to token_b")

        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        # Create pool key
        pool_key = self._get_pool_key(token_a, token_b, fee)

        # Check if pool already exists
        if self.pool_exists.get(pool_key, False):
            raise PoolExists("Pool already exists")

        # Validate fee
        if fee > 50:
            raise InvalidFee("Fee too high")
        if fee < 0:
            raise InvalidFee("Invalid fee")

        # Get initial deposits
        if set(ctx.actions.keys()) != {token_a, token_b}:
            raise InvalidTokens("Only token_a and token_b are allowed")

        action_a = ctx.actions[token_a]
        action_b = ctx.actions[token_b]

        if (
            action_a.type != NCActionType.DEPOSIT
            or action_b.type != NCActionType.DEPOSIT
        ):
            raise InvalidAction("Only deposits allowed for initial liquidity")

        # Initialize pool data
        self.pool_exists[pool_key] = True
        self.pool_token_a[pool_key] = token_a
        self.pool_token_b[pool_key] = token_b
        self.pool_reserve_a[pool_key] = action_a.amount
        self.pool_reserve_b[pool_key] = action_b.amount

        # Set up fees
        self.pool_fee_numerator[pool_key] = fee
        self.pool_fee_denominator[pool_key] = 1000

        # Initialize liquidity
        initial_liquidity = PRECISION * action_a.amount
        self.pool_total_liquidity[pool_key] = initial_liquidity

        # Initialize user liquidity for this pool
        if pool_key not in self.pool_user_liquidity:
            self.pool_user_liquidity[pool_key] = {}
        self.pool_user_liquidity[pool_key][ctx.address] = initial_liquidity

        # Initialize statistics
        self.pool_accumulated_fee[pool_key] = {}
        self.pool_accumulated_fee[pool_key][token_a] = 0
        self.pool_accumulated_fee[pool_key][token_b] = 0
        self.pool_transactions[pool_key] = 0
        self.pool_volume_a[pool_key] = 0
        self.pool_volume_b[pool_key] = 0
        self.pool_total_balance_a[pool_key] = 0
        self.pool_total_balance_b[pool_key] = 0
        self.pool_last_activity[pool_key] = ctx.timestamp

        # Initialize balance dictionaries
        self.pool_balance_a[pool_key] = {}
        self.pool_balance_b[pool_key] = {}

        # Update registry
        # all_pools should already be initialized by the Blueprint system
        self.all_pools.append(pool_key)

        # Update token to pools mapping
        partial = self.token_to_pools.get(token_a, [])
        partial.append(pool_key)
        self.token_to_pools[token_a] = partial

        # For token_b
        partial = self.token_to_pools.get(token_b, [])
        partial.append(pool_key)
        self.token_to_pools[token_b] = partial

        return pool_key

    @public
    def add_liquidity(
        self,
        ctx: Context,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> tuple[TokenUid, Amount]:
        """Add liquidity to an existing pool.

        Args:
            ctx: The transaction context
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool (default: use default_fee)

        Returns:
            A tuple of (token, change_amount)

        Raises:
            PoolNotFound: If the pool does not exist
            InvalidAction: If the actions are invalid
        """
        # Use default fee if not specified
        if fee is None:
            fee = self.default_fee

        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        action_a, action_b = self._get_actions_in_in(ctx, pool_key)

        # This logic mirrors Dozer_Pool_v1_1.add_liquidity
        reserve_a = self.pool_reserve_a[pool_key]
        reserve_b = self.pool_reserve_b[pool_key]

        optimal_b = self.quote(action_a.amount, reserve_a, reserve_b)
        if optimal_b <= action_b.amount:
            change = action_b.amount - optimal_b
            self._update_balance(
                ctx.address, change, self.pool_token_b[pool_key], pool_key
            )

            # Calculate liquidity increase
            liquidity_increase = (
                self.pool_total_liquidity[pool_key] * action_a.amount // reserve_a
            )

            # Update user liquidity
            partial = self.pool_user_liquidity.get(pool_key, {})
            partial[ctx.address] = partial.get(ctx.address, 0) + liquidity_increase
            self.pool_user_liquidity[pool_key] = partial

            # Update total liquidity
            self.pool_total_liquidity[pool_key] += liquidity_increase

            # Update reserves
            self.pool_reserve_a[pool_key] += action_a.amount
            self.pool_reserve_b[pool_key] += optimal_b

            return (self.pool_token_b[pool_key], change)
        else:
            optimal_a = self.quote(action_b.amount, reserve_b, reserve_a)

            # Validate optimal_a is not greater than action_a.amount
            if optimal_a > action_a.amount:
                raise InvalidAction("Insufficient token A amount")

            change = action_a.amount - optimal_a
            self._update_balance(
                ctx.address, change, self.pool_token_a[pool_key], pool_key
            )

            # Calculate liquidity increase
            liquidity_increase = (
                self.pool_total_liquidity[pool_key] * optimal_a // reserve_a
            )

            # Update user liquidity
            partial = self.pool_user_liquidity.get(pool_key, {})
            partial[ctx.address] = partial.get(ctx.address, 0) + liquidity_increase
            self.pool_user_liquidity[pool_key] = partial

            # Update total liquidity
            self.pool_total_liquidity[pool_key] += liquidity_increase

            # Update reserves
            self.pool_reserve_a[pool_key] += optimal_a
            self.pool_reserve_b[pool_key] += action_b.amount

            return (self.pool_token_a[pool_key], change)

    @public
    def remove_liquidity(
        self,
        ctx: Context,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> None:
        """Remove liquidity from a pool.

        Args:
            ctx: The transaction context
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool (default: use default_fee)

        Raises:
            PoolNotFound: If the pool does not exist
            InvalidAction: If the user has no liquidity or insufficient liquidity
        """
        # Use default fee if not specified
        if fee is None:
            fee = self.default_fee

        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        action_a, action_b = self._get_actions_out_out(ctx, pool_key)

        # Check if user has liquidity
        if (
            ctx.address not in self.pool_user_liquidity[pool_key]
            or self.pool_user_liquidity[pool_key][ctx.address] == 0
        ):
            raise InvalidAction("No liquidity to remove")

        # Calculate maximum withdrawal
        max_withdraw = (
            self.pool_user_liquidity[pool_key][ctx.address]
            * self.pool_reserve_a[pool_key]
            // self.pool_total_liquidity[pool_key]
        )

        if max_withdraw < action_a.amount:
            raise InvalidAction(
                f"Insufficient liquidity: {max_withdraw} < {action_a.amount}"
            )

        optimal_b = self.quote(
            action_a.amount,
            self.pool_reserve_a[pool_key],
            self.pool_reserve_b[pool_key],
        )

        if optimal_b < action_b.amount:
            raise InvalidAction("Insufficient token B amount")

        change = optimal_b - action_b.amount

        self._update_balance(ctx.address, change, self.pool_token_b[pool_key], pool_key)

        # Calculate liquidity decrease
        liquidity_decrease = (
            self.pool_total_liquidity[pool_key]
            * action_a.amount
            // self.pool_reserve_a[pool_key]
        )

        # Update user liquidity
        partial = self.pool_user_liquidity.get(pool_key, {})
        partial[ctx.address] = partial.get(ctx.address, 0) - liquidity_decrease
        self.pool_user_liquidity[pool_key] = partial

        # Update total liquidity
        self.pool_total_liquidity[pool_key] -= liquidity_decrease

        # Update reserves
        self.pool_reserve_a[pool_key] -= action_a.amount
        self.pool_reserve_b[pool_key] -= optimal_b

    @public
    def swap_exact_tokens_for_tokens(
        self,
        ctx: Context,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> SwapResult:
        """Swap an exact amount of input tokens for as many output tokens as possible.

        Args:
            ctx: The transaction context
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool (default: use default_fee)

        Returns:
            SwapResult with details of the swap

        Raises:
            PoolNotFound: If the pool does not exist
            InvalidAction: If the actions are invalid
            InsufficientLiquidity: If there is insufficient liquidity
        """
        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        action_in, action_out = self._get_actions_in_out(ctx, pool_key)
        reserve_in = self._get_reserve(action_in.token_uid, pool_key)
        reserve_out = self._get_reserve(action_out.token_uid, pool_key)

        amount_in = action_in.amount
        fee_amount = (
            amount_in
            * self.pool_fee_numerator[pool_key]
            // self.pool_fee_denominator[pool_key]
        )

        # Update accumulated fee using the partial approach
        partial_fee = self.pool_accumulated_fee.get(pool_key, {})
        partial_fee[action_in.token_uid] = (
            partial_fee.get(action_in.token_uid, 0) + fee_amount
        )
        self.pool_accumulated_fee[pool_key] = partial_fee

        # Calculate protocol fee
        protocol_fee_amount = fee_amount * self.default_protocol_fee // 100

        # Add to protocol fee balance using safe access
        self.protocol_fee_balance[action_in.token_uid] = (
            self.protocol_fee_balance.get(action_in.token_uid, 0) + protocol_fee_amount
        )

        # Calculate liquidity increase for protocol fee
        liquidity_increase = self._get_protocol_liquidity_increase(
            protocol_fee_amount, action_in.token_uid, pool_key
        )

        # Add liquidity to owner using the partial approach
        partial_liquidity = self.pool_user_liquidity.get(pool_key, {})
        partial_liquidity[self.owner] = (
            partial_liquidity.get(self.owner, 0) + liquidity_increase
        )
        self.pool_user_liquidity[pool_key] = partial_liquidity

        # Update total liquidity
        self.pool_total_liquidity[pool_key] += liquidity_increase

        # Calculate amount out
        amount_out = self.get_amount_out(
            action_in.amount,
            reserve_in,
            reserve_out,
            self.pool_fee_numerator[pool_key],
            self.pool_fee_denominator[pool_key],
        )

        # Check if there are sufficient funds
        if reserve_out < amount_out:
            raise InsufficientLiquidity("Insufficient funds")

        # Check if the requested amount is too high
        if action_out.amount > amount_out:
            raise InvalidAction("Amount out is too high")

        # Calculate slippage
        slippage_in = amount_out - action_out.amount

        # Update user balance for slippage
        self._update_balance(ctx.address, slippage_in, action_out.token_uid, pool_key)

        # Update reserves
        self._update_reserve(amount_in, action_in.token_uid, pool_key)
        self._update_reserve(-amount_out, action_out.token_uid, pool_key)

        # Update statistics
        self.pool_transactions[pool_key] += 1

        if action_in.token_uid == self.pool_token_a[pool_key]:
            self.pool_volume_a[pool_key] += amount_in
        else:
            self.pool_volume_b[pool_key] += amount_in

        return SwapResult(
            action_in.amount,
            slippage_in,
            action_in.token_uid,
            amount_out,
            action_out.token_uid,
        )

    @public
    def swap_tokens_for_exact_tokens(
        self,
        ctx: Context,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> SwapResult:
        """Receive an exact amount of output tokens for as few input tokens as possible.

        Args:
            ctx: The transaction context
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool (default: use default_fee)

        Returns:
            SwapResult with details of the swap

        Raises:
            PoolNotFound: If the pool does not exist
            InvalidAction: If the actions are invalid
            InsufficientLiquidity: If there is insufficient liquidity
        """
        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        action_in, action_out = self._get_actions_in_out(ctx, pool_key)
        reserve_in = self._get_reserve(action_in.token_uid, pool_key)
        reserve_out = self._get_reserve(action_out.token_uid, pool_key)

        amount_out = action_out.amount

        # Check if there are sufficient funds
        if reserve_out < amount_out:
            raise InsufficientLiquidity("Insufficient funds")

        # Calculate amount in
        amount_in = self.get_amount_in(
            amount_out,
            reserve_in,
            reserve_out,
            self.pool_fee_numerator[pool_key],
            self.pool_fee_denominator[pool_key],
        )

        # Calculate fee amount
        fee_amount = (
            amount_in
            * self.pool_fee_numerator[pool_key]
            // self.pool_fee_denominator[pool_key]
        )

        # Update accumulated fee using the partial approach
        partial_fee = self.pool_accumulated_fee.get(pool_key, {})
        partial_fee[action_in.token_uid] = (
            partial_fee.get(action_in.token_uid, 0) + fee_amount
        )
        self.pool_accumulated_fee[pool_key] = partial_fee

        # Calculate protocol fee
        protocol_fee_amount = fee_amount * self.default_protocol_fee // 100

        # Add to protocol fee balance using safe access
        self.protocol_fee_balance[action_in.token_uid] = (
            self.protocol_fee_balance.get(action_in.token_uid, 0) + protocol_fee_amount
        )

        # Calculate liquidity increase for protocol fee
        liquidity_increase = self._get_protocol_liquidity_increase(
            protocol_fee_amount, action_in.token_uid, pool_key
        )

        # Add liquidity to owner using the partial approach
        partial_liquidity = self.pool_user_liquidity.get(pool_key, {})
        partial_liquidity[self.owner] = (
            partial_liquidity.get(self.owner, 0) + liquidity_increase
        )
        self.pool_user_liquidity[pool_key] = partial_liquidity

        # Update total liquidity
        self.pool_total_liquidity[pool_key] += liquidity_increase

        # Check if the provided amount is sufficient
        if action_in.amount < amount_in:
            raise InvalidAction("Amount in is too low")

        # Calculate slippage
        slippage_in = action_in.amount - amount_in

        # Update user balance for slippage
        self._update_balance(ctx.address, slippage_in, action_in.token_uid, pool_key)

        # Update reserves
        self._update_reserve(amount_in, action_in.token_uid, pool_key)
        self._update_reserve(-amount_out, action_out.token_uid, pool_key)

        # Update statistics
        self.pool_transactions[pool_key] += 1

        if action_in.token_uid == self.pool_token_a[pool_key]:
            self.pool_volume_a[pool_key] += amount_in
        else:
            self.pool_volume_b[pool_key] += amount_in

        return SwapResult(
            action_in.amount,
            slippage_in,
            action_in.token_uid,
            amount_out,
            action_out.token_uid,
        )

    @public
    def withdraw_cashback(
        self,
        ctx: Context,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> None:
        """Withdraw cashback from a pool.

        Args:
            ctx: The transaction context
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool

        Raises:
            PoolNotFound: If the pool does not exist
            InvalidAction: If there is not enough cashback
        """
        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        action_a, action_b = self._get_actions_out_out(ctx, pool_key)

        # Check if user has enough cashback
        if action_a.amount > self.pool_balance_a.get(pool_key, {}).get(ctx.address, 0):
            raise InvalidAction("Not enough cashback for token A")

        if action_b.amount > self.pool_balance_b.get(pool_key, {}).get(ctx.address, 0):
            raise InvalidAction("Not enough cashback for token B")

        # Update user balances
        if pool_key not in self.pool_balance_a:
            self.pool_balance_a[pool_key] = {}
        if ctx.address not in self.pool_balance_a[pool_key]:
            self.pool_balance_a[pool_key][ctx.address] = 0
        self.pool_balance_a[pool_key][ctx.address] -= action_a.amount

        if pool_key not in self.pool_balance_b:
            self.pool_balance_b[pool_key] = {}
        if ctx.address not in self.pool_balance_b[pool_key]:
            self.pool_balance_b[pool_key][ctx.address] = 0
        self.pool_balance_b[pool_key][ctx.address] -= action_b.amount

        # Update total balances
        if pool_key not in self.pool_total_balance_a:
            self.pool_total_balance_a[pool_key] = 0
        self.pool_total_balance_a[pool_key] -= action_a.amount

        if pool_key not in self.pool_total_balance_b:
            self.pool_total_balance_b[pool_key] = 0
        self.pool_total_balance_b[pool_key] -= action_b.amount

    @public
    def withdraw_protocol_fees(self, ctx: Context, token: TokenUid) -> Amount:
        """Withdraw accumulated protocol fees for a specific token.

        Args:
            ctx: The transaction context
            token: The token to withdraw fees for

        Returns:
            The amount withdrawn

        Raises:
            Unauthorized: If the caller is not the owner
            InvalidAction: If there are no fees to withdraw
        """
        if ctx.address != self.owner:
            raise Unauthorized("Only owner can withdraw protocol fees")

        if token not in ctx.actions:
            raise InvalidAction("Token action required")

        action = ctx.actions[token]
        if action.type != NCActionType.WITHDRAWAL:
            raise InvalidAction("Only withdrawals allowed")

        # Check if there are fees to withdraw
        if (
            token not in self.protocol_fee_balance
            or self.protocol_fee_balance[token] == 0
        ):
            raise InvalidAction("No protocol fees to withdraw")

        # Check if the requested amount is valid
        if action.amount > self.protocol_fee_balance[token]:
            raise InvalidAction("Not enough protocol fees")

        # Update protocol fee balance
        self.protocol_fee_balance[token] -= action.amount

        return action.amount

    @public
    def change_default_fee(self, ctx: Context, new_fee: Amount) -> None:
        """Set the default fee for new pools.

        Args:
            ctx: The transaction context
            new_fee: The new default fee

        Raises:
            Unauthorized: If the caller is not the owner
            InvalidFee: If the fee is invalid
        """
        if ctx.address != self.owner:
            raise Unauthorized("Only owner can set default fee")

        if new_fee > 50:
            raise InvalidFee("Fee too high")
        if new_fee < 0:
            raise InvalidFee("Invalid fee")

        self.default_fee = new_fee

    @public
    def change_protocol_fee(self, ctx: Context, new_fee: Amount) -> None:
        """Set the protocol fee percentage.

        Args:
            ctx: The transaction context
            new_fee: The new protocol fee percentage

        Raises:
            Unauthorized: If the caller is not the owner
            InvalidFee: If the fee is invalid
        """
        if ctx.address != self.owner:
            raise Unauthorized("Only owner can set protocol fee")

        if new_fee > 50:
            raise InvalidFee("Protocol fee too high")
        if new_fee < 0:
            raise InvalidFee("Invalid protocol fee")

        self.default_protocol_fee = new_fee

    @public
    def change_owner(self, ctx: Context, new_owner: Address) -> None:
        """Change the owner of the contract.

        Args:
            ctx: The transaction context
            new_owner: The new owner address

        Raises:
            Unauthorized: If the caller is not the owner
        """
        if ctx.address != self.owner:
            raise Unauthorized("Only owner can change owner")

        self.owner = new_owner

    @view
    def get_reserves(
        self,
        token_a: TokenUid,
        token_b: TokenUid,
        fee: Amount,
    ) -> tuple[Amount, Amount]:
        """Get the reserves for a specific pool.

        Args:
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool

        Returns:
            A tuple of (reserve_a, reserve_b)

        Raises:
            PoolNotFound: If the pool does not exist
        """
        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        return (self.pool_reserve_a[pool_key], self.pool_reserve_b[pool_key])

    @view
    def get_all_pools(self) -> list[str]:
        """Get a list of all pools with their tokens and fees.

        Returns:
            A list of tuples (token_a, token_b, fee)
        """
        result = []
        for pool_key in self.all_pools:
            token_a = self.pool_token_a[pool_key].hex()
            token_b = self.pool_token_b[pool_key].hex()
            fee = self.pool_fee_numerator[pool_key]
            result.append(f"{token_a}/{token_b}/{fee}")
        return result

    @view
    def get_pools_for_token(self, token: TokenUid) -> list[str]:
        """Get all pools that contain a specific token.

        Args:
            token: The token to search for

        Returns:
            A list of tuples (token_a, token_b, fee)
        """
        if token not in self.token_to_pools:
            return []

        result = []
        for pool_key in self.token_to_pools[token]:
            token_a = self.pool_token_a[pool_key].hex()
            token_b = self.pool_token_b[pool_key].hex()
            fee = self.pool_fee_numerator[pool_key]
            result.append(f"{token_a}/{token_b}/{fee}")
        return result

    @view
    def liquidity_of(
        self,
        address: Address,
        pool_key: str,
    ) -> Amount:
        """Get the liquidity of an address in a specific pool.

        Args:
            address: The address to check
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool

        Returns:
            The liquidity amount

        Raises:
            PoolNotFound: If the pool does not exist
        """
        self._validate_pool_exists(pool_key)

        return self.pool_user_liquidity[pool_key].get(address, 0)

    @view
    def balance_of(
        self,
        address: Address,
        pool_key: str,
    ) -> tuple[Amount, Amount]:
        """Get the balance of an address in a specific pool.

        Args:
            address: The address to check
            pool_key: The pool key to check

        Returns:
            A tuple of (balance_a, balance_b)

        Raises:
            PoolNotFound: If the pool does not exist
        """
        self._validate_pool_exists(pool_key)

        balance_a = self.pool_balance_a.get(pool_key, {}).get(address, 0)
        balance_b = self.pool_balance_b.get(pool_key, {}).get(address, 0)

        return (balance_a, balance_b)

    @view
    def front_end_api_pool(
        self,
        pool_key: str,
    ) -> dict[str, float]:
        """Get pool information for frontend display.

        Args:
            pool_key: The pool key to check

        Returns:
            A dictionary with pool information

        Raises:
            PoolNotFound: If the pool does not exist
        """

        token_a, token_b, fee = pool_key.split("/")
        token_a = bytes.fromhex(token_a)
        token_b = bytes.fromhex(token_b)
        fee = int(fee)
        # Ensure tokens are ordered
        if token_a > token_b:
            token_a, token_b = token_b, token_a

        pool_key = self._get_pool_key(token_a, token_b, fee)
        self._validate_pool_exists(pool_key)

        return {
            "reserve0": self.pool_reserve_a[pool_key],
            "reserve1": self.pool_reserve_b[pool_key],
            "fee": self.pool_fee_numerator[pool_key]
            / self.pool_fee_denominator[pool_key],
            "volume": self.pool_volume_a[pool_key],
            "fee0": self.pool_accumulated_fee[pool_key].get(token_a, 0),
            "fee1": self.pool_accumulated_fee[pool_key].get(token_b, 0),
            "dzr_rewards": 1000,  # Placeholder as in original implementation
            "transactions": self.pool_transactions[pool_key],
        }

    @view
    def pool_info(
        self,
        pool_key: str,
    ) -> dict[str, Any]:
        """Get detailed information about a pool.

        Args:
            pool_key: The pool key to check

        Returns:
            A dictionary with pool information

        Raises:
            PoolNotFound: If the pool does not exist
        """
        self._validate_pool_exists(pool_key)

        return {
            "token_a": self.pool_token_a[pool_key],
            "token_b": self.pool_token_b[pool_key],
            "reserve_a": self.pool_reserve_a[pool_key],
            "reserve_b": self.pool_reserve_b[pool_key],
            "fee": self.pool_fee_numerator[pool_key]
            / self.pool_fee_denominator[pool_key],
            "total_liquidity": self.pool_total_liquidity[pool_key],
            "transactions": self.pool_transactions[pool_key],
            "volume_a": self.pool_volume_a[pool_key],
            "volume_b": self.pool_volume_b[pool_key],
            "last_activity": self.pool_last_activity[pool_key],
        }

    @view
    def user_info(
        self,
        address: Address,
        pool_key: str,
    ) -> dict[str, Any]:
        """Get detailed information about a user's position in a pool.

        Args:
            address: The address to check
            pool_key: The pool key to check

        Returns:
            A dictionary with user information

        Raises:
            PoolNotFound: If the pool does not exist
        """
        self._validate_pool_exists(pool_key)

        liquidity = self.pool_user_liquidity[pool_key].get(address, 0)
        balance_a = self.pool_balance_a.get(pool_key, {}).get(address, 0)
        balance_b = self.pool_balance_b.get(pool_key, {}).get(address, 0)

        # Calculate share
        share = 0
        if self.pool_total_liquidity[pool_key] > 0:
            share = liquidity * 100 / self.pool_total_liquidity[pool_key]

        # Calculate token amounts based on share
        token_a_amount = (
            self.pool_reserve_a[pool_key]
            * liquidity
            // self.pool_total_liquidity[pool_key]
        )
        token_b_amount = (
            self.pool_reserve_b[pool_key]
            * liquidity
            // self.pool_total_liquidity[pool_key]
        )

        return {
            "liquidity": liquidity,
            "share": share,
            "token_a_amount": token_a_amount,
            "token_b_amount": token_b_amount,
            "balance_a": balance_a,
            "balance_b": balance_b,
        }
