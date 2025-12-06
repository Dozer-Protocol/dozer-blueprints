from typing import NamedTuple

from hathor import (
    Amount,
    Blueprint,
    BlueprintId,
    Context,
    TokenUid,
    Timestamp,
    Address,
    ContractId,
    NCDepositAction,
    NCFail,
    NCWithdrawalAction,
    export,
    public,
    view,
)

# Constants
DAY_IN_SECONDS: int = 24 * 60 * 60  # 86400 seconds = 24 hours
MIN_PERIOD_DAYS: int = 30
MIN_STAKE_AMOUNT = 100  # Minimum stake amount
MAX_STAKE_AMOUNT = 1000000  # Maximum stake amount per address
PRECISION: int = 10**20  # For fixed-point arithmetic


class StakeUserInfo(NamedTuple):
    """User information for staking contract."""

    deposits: int


class StakeFrontEndInfo(NamedTuple):
    """Frontend API information for staking contract."""

    owner_balance: int
    total_staked: int
    rewards_per_share: int
    paused: bool


class StakingStats(NamedTuple):
    """Comprehensive staking pool statistics."""

    owner_balance: int
    total_staked: int
    rewards_per_share: int
    paused: bool
    earnings_per_day: int
    days_of_rewards_remaining: int
    estimated_apy: int
    owner_address: str  # hex-encoded address
    token_uid: str  # hex-encoded token UID


class UserStakingInfo(NamedTuple):
    """Comprehensive user staking information."""

    deposits: int
    pending_rewards: int
    max_withdrawal: int
    stake_timestamp: int
    days_until_unlock: int
    can_unstake: bool


class StakeValidationInfo(NamedTuple):
    """Validation parameters for stake/unstake operations."""

    min_stake_amount: int
    max_stake_amount: int
    min_period_days: int
    is_paused: bool


class InvalidVersion(NCFail):
    pass


@export
class StakeV2(Blueprint):
    """V2 Stake blueprint with enhanced features.

    The life cycle of contracts using this blueprint is the following:

    1. [Owner] Create a contract define earning per day.
    2. [Owner] `deposit()` or `withdraw()`.
    3. [User] `stake(...)`.
    4. [User] `unstake(...)`.

    New in V2:
    - reward_multiplier field for adjustable reward rates
    - migrate_v1_to_v2() method to initialize V2 fields
    """

    # Pool
    token_uid: TokenUid
    earnings_per_second: int  # Changed to int for fixed-point arithmetic
    total_staked: Amount
    rewards_per_share: int  # Changed to int for fixed-point arithmetic
    last_reward: int
    paused: bool  # For emergency pause

    # Owner
    owner_balance: Amount
    owner_address: bytes
    creator_contract_id: ContractId  # DozerTools contract that created this

    # User
    user_deposits: dict[Address, Amount]  # Includes deposits + auto-compounded rewards
    user_actual_stake: dict[Address, Amount]  # Only actual deposits (for total_staked tracking)
    user_debit: dict[Address, int]  # Changed to int for fixed-point arithmetic
    user_stake_timestamp: dict[Address, int]  # For timelock

    # Version tracking
    contract_version: str  # Semantic version string (e.g., "1.0.0")

    # NEW IN V2: Reward multiplier
    reward_multiplier: int  # Reward multiplier in basis points (10000 = 1x)

    def _validate_state(self) -> None:
        """Validate contract state invariants"""
        assert self.total_staked >= 0, "Invalid total staked"
        assert self.owner_balance >= 0, "Invalid owner balance"

    def _validate_address(self, address: Address) -> None:
        """Validate address format"""
        if not isinstance(address, bytes):
            raise InvalidInput("Invalid address format")

    def _validate_stake_amount(self, amount: Amount) -> None:
        """Validate stake amount against limits"""
        if amount < MIN_STAKE_AMOUNT:
            raise InvalidAmount("Below minimum stake amount")
        if amount > MAX_STAKE_AMOUNT:
            raise InvalidAmount("Exceeds maximum stake amount")

    def _validate_unstake_time(self, ctx: Context, address: Address) -> None:
        """Validate unstaking timelock"""
        if (
            int(ctx.block.timestamp)
            < self.user_stake_timestamp[address] + MIN_PERIOD_DAYS * DAY_IN_SECONDS
        ):
            raise InvalidTime("Staking period not completed")

    def _amount_check(self, amount: Amount, earnings_per_day: int) -> None:
        """Checks if the contract can run for at least one month"""
        if amount < MIN_PERIOD_DAYS * earnings_per_day:
            raise InsufficientBalance(f"keep enough for {MIN_PERIOD_DAYS} days")

    def _get_single_deposit_action(self, ctx: Context) -> NCDepositAction:
        """Get a single deposit action for the specified token."""
        action = ctx.get_single_action(self.token_uid)
        if not isinstance(action, NCDepositAction):
            raise InvalidActions("Expected deposit action")
        return action

    def _get_single_withdrawal_action(self, ctx: Context) -> NCWithdrawalAction:
        """Get a single withdrawal action for the specified token."""
        action = ctx.get_single_action(self.token_uid)
        if not isinstance(action, NCWithdrawalAction):
            raise InvalidActions("Expected withdrawal action")
        return action

    def _validate_owner_auth(self, ctx: Context) -> None:
        """Validate owner authorization"""
        if ctx.caller_id != self.owner_address:
            raise Unauthorized("Unauthorized")

    def _only_creator_contract(self, ctx: Context) -> None:
        if ContractId(ctx.caller_id) != self.creator_contract_id:
            raise NCFail("Only creator contract can call this method")

    def _safe_pay(self, amount: int, address: Address) -> None:
        """Safe payment handling"""
        if amount <= self.owner_balance:
            self.owner_balance = Amount(self.owner_balance - amount)
            self.user_deposits[address] = Amount(
                self.user_deposits.get(address, 0) + amount
            )
            self._validate_state()

    def _update_pool(self, ctx: Context):
        """Update pool with fixed-point arithmetic"""
        now = int(ctx.block.timestamp)
        if self.last_reward != 0 and now <= self.last_reward:
            return
        if self.total_staked == 0:
            self.last_reward = now
            return
        # Note: earnings_per_second already includes PRECISION factor
        # So we don't multiply time_diff by PRECISION here
        time_diff = now - self.last_reward
        self.rewards_per_share += (
            time_diff * self.earnings_per_second
        ) // self.total_staked
        self.last_reward = now

    def _pending_rewards(self, address: Address) -> Amount:
        """Calculate pending rewards with fixed-point arithmetic"""
        if address not in self.user_deposits:
            return Amount(0)
        return Amount(
            (self.user_deposits[address] * self.rewards_per_share) // PRECISION
            - self.user_debit.get(address, 0)
        )

    @public(allow_deposit=True)
    def initialize(
        self,
        ctx: Context,
        earnings_per_day: int,
        token_uid: TokenUid,
        creator_contract_id: ContractId,
    ) -> None:
        self.token_uid = token_uid
        action = self._get_single_deposit_action(ctx)
        self.earnings_per_second = (earnings_per_day * PRECISION) // DAY_IN_SECONDS
        self.owner_address = ctx.caller_id
        amount = Amount(action.amount)
        self._amount_check(amount, earnings_per_day)
        self.owner_balance = Amount(amount)
        self.total_staked = Amount(0)
        self.last_reward = 0
        self.rewards_per_share = 0
        self.paused = False
        # Initialize user tracking dictionaries
        self.user_deposits = {}
        self.user_actual_stake = {}
        self.user_debit = {}
        self.user_stake_timestamp = {}
        # Set creator_contract_id (for DozerTools routing)
        self.creator_contract_id = creator_contract_id
        # Initialize version (V2 starts at 2.0.0)
        self.contract_version = "2.0.0"
        # Initialize V2 fields (1x multiplier by default)
        self.reward_multiplier = 10000
        self._validate_state()

    @public
    def pause(self, ctx: Context) -> None:
        """Emergency pause functionality"""
        if ctx.caller_id != self.owner_address:
            raise Unauthorized("Only owner can pause")
        self.paused = True

    @public
    def unpause(self, ctx: Context) -> None:
        """Unpause functionality"""
        if ctx.caller_id != self.owner_address:
            raise Unauthorized("Only owner can unpause")
        self.paused = False

    @public(allow_withdrawal=True)
    def emergency_withdraw(self, ctx: Context) -> None:
        """Emergency withdrawal without timelock"""
        if not self.paused:
            raise InvalidState("Contract must be paused")
        action = self._get_single_withdrawal_action(ctx)
        address = Address(ctx.caller_id)
        if address not in self.user_deposits:
            raise Unauthorized("user not staked")
        amount = action.amount
        if amount > self.user_deposits[address]:
            raise InsufficientBalance("insufficient funds")
        self.user_deposits[address] = Amount(self.user_deposits[address] - amount)
        self.total_staked = Amount(self.total_staked - amount)
        self._validate_state()

    @public(allow_deposit=True)
    def owner_deposit(self, ctx: Context) -> None:
        action = self._get_single_deposit_action(ctx)
        self.owner_balance = Amount(self.owner_balance + action.amount)
        self._validate_state()

    @public(allow_withdrawal=True)
    def owner_withdraw(self, ctx: Context) -> None:
        action = self._get_single_withdrawal_action(ctx)
        self._validate_owner_auth(ctx)
        amount = action.amount
        if amount > self.owner_balance:
            raise InsufficientBalance("insufficient owner balance")
        self.owner_balance = Amount(self.owner_balance - amount)
        self._validate_state()

    @public(allow_deposit=True)
    def stake(self, ctx: Context) -> None:
        if self.paused:
            raise InvalidState("Contract is paused")
        if ctx.caller_id == self.owner_address:
            raise Unauthorized("admin, please use other address to stake")
        action = self._get_single_deposit_action(ctx)
        address = Address(ctx.caller_id)
        amount = Amount(action.amount)
        self._validate_stake_amount(amount)
        self._validate_address(address)

        # update pool parameters
        self._update_pool(ctx)
        # update rewards if user already have balance in pool
        pending = self._pending_rewards(address)
        # create entries for newcomers
        if pending == 0:
            if address not in self.user_deposits:
                self.user_deposits[address] = Amount(0)
                self.user_actual_stake[address] = Amount(0)
            self.user_stake_timestamp[address] = int(ctx.block.timestamp)

        self._safe_pay(pending, address)
        self.user_deposits[address] = Amount(self.user_deposits[address] + amount)
        # Track actual stake (deposits only, not auto-compounded rewards)
        self.user_actual_stake[address] = Amount(self.user_actual_stake[address] + amount)
        # Only add the new deposit amount to total_staked, NOT pending rewards
        # Pending rewards are already accounted for in owner_balance
        self.total_staked = Amount(self.total_staked + amount)
        self.user_debit[address] = (
            self.user_deposits[address] * self.rewards_per_share
        ) // PRECISION
        self._validate_state()

    @public(allow_withdrawal=True)
    def unstake(self, ctx: Context) -> None:
        if self.paused:
            raise InvalidState("Contract is paused")
        action = self._get_single_withdrawal_action(ctx)
        address = Address(ctx.caller_id)
        if address not in self.user_deposits:
            raise Unauthorized("user not staked")

        self._validate_unstake_time(ctx, address)
        amount = action.amount

        self._update_pool(ctx)
        pending = self._pending_rewards(address)

        # User can withdraw up to deposits + pending rewards
        max_withdrawal = self.user_deposits[address] + pending
        if amount > max_withdrawal:
            raise InsufficientBalance("insufficient funds")

        # Calculate how much comes from actual stake vs rewards
        # user_actual_stake tracks only deposits (not auto-compounded rewards)
        # For legacy users without user_actual_stake, use a conservative estimate
        if address not in self.user_actual_stake:
            # Initialize with current deposits (migration for existing users)
            self.user_actual_stake[address] = self.user_deposits[address]

        user_actual = self.user_actual_stake.get(address, 0)

        # Split withdrawal into: actual stake, compounded rewards (in deposits), pending rewards
        amount_from_deposits = min(amount, self.user_deposits[address])
        amount_from_pending = amount - amount_from_deposits

        # Of the amount from deposits, how much is actual stake vs compounded rewards?
        amount_from_stake = min(amount_from_deposits, user_actual)
        amount_from_compounded = amount_from_deposits - amount_from_stake

        total_rewards = amount_from_compounded + amount_from_pending

        # Deduct from deposits and actual stake
        self.user_deposits[address] = Amount(
            self.user_deposits[address] - amount_from_deposits
        )
        self.user_actual_stake[address] = Amount(
            self.user_actual_stake[address] - amount_from_stake
        )

        # Deduct from owner balance (for all rewards: compounded + pending)
        if total_rewards > 0:
            if total_rewards > self.owner_balance:
                raise InsufficientBalance("insufficient owner balance for rewards")
            self.owner_balance = Amount(self.owner_balance - total_rewards)

        # Update total_staked (only the actual stake portion affects total_staked)
        self.total_staked = Amount(self.total_staked - amount_from_stake)

        # Update user debit to reflect that pending rewards were paid out
        self.user_debit[address] = (
            self.user_deposits[address] * self.rewards_per_share
        ) // PRECISION

        self._validate_state()

    @view
    def get_max_withdrawal(self, address: Address, timestamp: Timestamp) -> Amount:
        if self.paused:
            return Amount(self.user_deposits.get(address, 0))

        rewards_per_share = self.rewards_per_share
        if (
            int(timestamp) >= self.last_reward
            and self.total_staked != 0
            and address in self.user_deposits
        ):
            time_diff = int(timestamp) - self.last_reward
            # Note: earnings_per_second already includes PRECISION factor
            # So we don't multiply time_diff by PRECISION here (unlike in _update_pool)
            rewards_increment = (time_diff * self.earnings_per_second) // self.total_staked
            rewards_per_share += rewards_increment

            user_total = (self.user_deposits[address] * rewards_per_share) // PRECISION
            user_debit_val = self.user_debit.get(address, 0)
            pending = Amount(user_total - user_debit_val)
            return Amount(self.user_deposits[address] + pending)
        else:
            return Amount(0)

    @view
    def get_user_info(self, address: Address) -> StakeUserInfo:
        return StakeUserInfo(
            deposits=self.user_deposits.get(address, 0),
        )

    @view
    def front_end_api(self) -> StakeFrontEndInfo:
        return StakeFrontEndInfo(
            owner_balance=self.owner_balance,
            total_staked=self.total_staked,
            rewards_per_share=self.rewards_per_share,
            paused=self.paused,
        )

    @view
    def get_staking_stats(self) -> StakingStats:
        """Get comprehensive staking pool statistics.

        Returns comprehensive stats including owner balance, total staked,
        rewards info, and calculated metrics like days remaining and APY.
        """
        # Calculate earnings per day
        earnings_per_day = (
            (self.earnings_per_second * DAY_IN_SECONDS) // PRECISION
            if PRECISION > 0
            else 0
        )

        # Calculate days of rewards remaining
        days_remaining = (
            self.owner_balance // earnings_per_day if earnings_per_day > 0 else 0
        )

        # Calculate estimated APY
        if self.total_staked > 0 and earnings_per_day > 0:
            annual_rewards = earnings_per_day * 365
            estimated_apy = (annual_rewards * 100) // self.total_staked
        else:
            estimated_apy = 0

        return StakingStats(
            owner_balance=self.owner_balance,
            total_staked=self.total_staked,
            rewards_per_share=self.rewards_per_share,
            paused=self.paused,
            earnings_per_day=earnings_per_day,
            days_of_rewards_remaining=days_remaining,
            estimated_apy=estimated_apy,
            owner_address=self.owner_address.hex(),
            token_uid=self.token_uid.hex(),
        )

    @view
    def get_user_staking_info(
        self, address: Address, timestamp: Timestamp
    ) -> UserStakingInfo:
        """Get comprehensive user staking information.

        Args:
            address: User address
            timestamp: Current timestamp for calculations

        Returns:
            UserStakingInfo with current stake, rewards, and timelock status
        """
        if address not in self.user_deposits:
            return UserStakingInfo(
                deposits=0,
                pending_rewards=0,
                max_withdrawal=0,
                stake_timestamp=0,
                days_until_unlock=0,
                can_unstake=False,
            )

        stake_time = self.user_stake_timestamp.get(address, 0)
        time_since_stake = int(timestamp) - stake_time
        days_since_stake = time_since_stake // DAY_IN_SECONDS
        days_until_unlock = max(0, MIN_PERIOD_DAYS - days_since_stake)
        can_unstake = days_until_unlock == 0

        # Calculate pending rewards
        pending = self._pending_rewards(address)

        # Get max withdrawal
        max_withdrawal = self.get_max_withdrawal(address, timestamp)

        return UserStakingInfo(
            deposits=self.user_deposits[address],
            pending_rewards=pending,
            max_withdrawal=max_withdrawal,
            stake_timestamp=stake_time,
            days_until_unlock=days_until_unlock,
            can_unstake=can_unstake,
        )

    @view
    def get_stake_validation_info(self) -> StakeValidationInfo:
        """Get validation parameters for stake/unstake operations.

        Returns contract constants and current state for UI validation.
        """
        return StakeValidationInfo(
            min_stake_amount=MIN_STAKE_AMOUNT,
            max_stake_amount=MAX_STAKE_AMOUNT,
            min_period_days=MIN_PERIOD_DAYS,
            is_paused=self.paused,
        )

    # Routing methods for DozerTools integration
    @public(allow_deposit=True)
    def routed_stake(self, ctx: Context, user_address: Address) -> None:
        """Stake tokens via DozerTools routing."""
        self._only_creator_contract(ctx)

        if self.paused:
            raise InvalidState("Contract is paused")
        if user_address == self.owner_address:
            raise Unauthorized("admin, please use other address to stake")

        action = self._get_single_deposit_action(ctx)
        amount = Amount(action.amount)
        self._validate_stake_amount(amount)
        self._validate_address(user_address)

        # update pool parameters
        self._update_pool(ctx)
        # update rewards if user already have balance in pool
        pending = self._pending_rewards(user_address)
        # create entries for newcomers
        if pending == 0:
            if user_address not in self.user_deposits:
                self.user_deposits[user_address] = Amount(0)
                self.user_actual_stake[user_address] = Amount(0)
            self.user_stake_timestamp[user_address] = int(ctx.block.timestamp)

        self._safe_pay(pending, user_address)
        self.user_deposits[user_address] = Amount(
            self.user_deposits[user_address] + amount
        )
        # Track actual stake (deposits only, not auto-compounded rewards)
        self.user_actual_stake[user_address] = Amount(
            self.user_actual_stake[user_address] + amount
        )
        # Only add the new deposit amount to total_staked, NOT pending rewards
        self.total_staked = Amount(self.total_staked + amount)
        self.user_debit[user_address] = (
            self.user_deposits[user_address] * self.rewards_per_share
        ) // PRECISION
        self._validate_state()

    @public(allow_withdrawal=True)
    def routed_unstake(self, ctx: Context, user_address: Address) -> None:
        """Unstake tokens via DozerTools routing."""
        self._only_creator_contract(ctx)

        if self.paused:
            raise InvalidState("Contract is paused")

        action = self._get_single_withdrawal_action(ctx)
        if user_address not in self.user_deposits:
            raise Unauthorized("user not staked")

        self._validate_unstake_time(ctx, user_address)
        amount = action.amount

        self._update_pool(ctx)
        pending = self._pending_rewards(user_address)

        # User can withdraw up to deposits + pending rewards
        max_withdrawal = self.user_deposits[user_address] + pending
        if amount > max_withdrawal:
            raise InsufficientBalance("insufficient funds")

        # Calculate how much comes from actual stake vs rewards
        # user_actual_stake tracks only deposits (not auto-compounded rewards)
        # For legacy users without user_actual_stake, use a conservative estimate
        if user_address not in self.user_actual_stake:
            # Initialize with current deposits (migration for existing users)
            self.user_actual_stake[user_address] = self.user_deposits[user_address]

        user_actual = self.user_actual_stake.get(user_address, 0)

        # Split withdrawal into: actual stake, compounded rewards (in deposits), pending rewards
        amount_from_deposits = min(amount, self.user_deposits[user_address])
        amount_from_pending = amount - amount_from_deposits

        # Of the amount from deposits, how much is actual stake vs compounded rewards?
        amount_from_stake = min(amount_from_deposits, user_actual)
        amount_from_compounded = amount_from_deposits - amount_from_stake

        total_rewards = amount_from_compounded + amount_from_pending

        # Deduct from deposits and actual stake
        self.user_deposits[user_address] = Amount(
            self.user_deposits[user_address] - amount_from_deposits
        )
        self.user_actual_stake[user_address] = Amount(
            self.user_actual_stake[user_address] - amount_from_stake
        )

        # Deduct from owner balance (for all rewards: compounded + pending)
        if total_rewards > 0:
            if total_rewards > self.owner_balance:
                raise InsufficientBalance("insufficient owner balance for rewards")
            self.owner_balance = Amount(self.owner_balance - total_rewards)

        # Update total_staked (only the actual stake portion affects total_staked)
        self.total_staked = Amount(self.total_staked - amount_from_stake)

        # Update user debit to reflect that pending rewards were paid out
        self.user_debit[user_address] = (
            self.user_deposits[user_address] * self.rewards_per_share
        ) // PRECISION

        self._validate_state()

    @public
    def migrate_v1_to_v2(self, ctx: Context) -> None:
        """Migration method to initialize V2 fields after upgrade from V1.

        Args:
            ctx: Transaction context

        Raises:
            Unauthorized: If caller is not the owner or creator contract
        """
        # Only owner or creator can migrate
        if ctx.caller_id != self.owner_address and ContractId(ctx.caller_id) != self.creator_contract_id:
            raise Unauthorized("Only owner or creator can migrate")

        # Initialize new V2 field (1x multiplier by default, in basis points)
        self.reward_multiplier = 10000

    @view
    def get_reward_multiplier(self) -> int:
        """Get reward multiplier (V2 feature).

        Returns:
            Reward multiplier in basis points (10000 = 1x)
        """
        # Handle case where field doesn't exist yet (pre-migration)
        try:
            return self.reward_multiplier
        except (KeyError, AttributeError):
            return 0

    @public
    def upgrade_contract(self, ctx: Context, new_blueprint_id: BlueprintId, new_version: str) -> None:
        """Upgrade this contract to a new blueprint version.

        Args:
            ctx: Transaction context
            new_blueprint_id: The blueprint ID to upgrade to
            new_version: Version string for the new blueprint (e.g., "1.1.0")

        Raises:
            Unauthorized: If caller is not the owner or creator contract
            InvalidVersion: If new version is not higher than current version
        """
        # Only owner or creator contract can upgrade
        if ctx.caller_id != self.owner_address and ContractId(ctx.caller_id) != self.creator_contract_id:
            raise Unauthorized("Only owner or creator contract can upgrade this contract")

        # Validate version is newer
        if not self._is_version_higher(new_version, self.contract_version):
            raise InvalidVersion(f"New version {new_version} must be higher than current {self.contract_version}")
        self.contract_version = new_version

        # Perform the upgrade
        self.syscall.change_blueprint(new_blueprint_id)

    def _is_version_higher(self, new_version: str, current_version: str) -> bool:
        """Compare semantic versions (e.g., "1.2.3").

        Returns True if new_version > current_version.
        Returns False if versions are malformed or equal.
        """
        # Split versions by '.'
        new_parts_str = new_version.split('.')
        current_parts_str = current_version.split('.')

        # Check if all parts are valid integers
        new_parts: list[int] = []
        for part in new_parts_str:
            # Simple check: all characters must be digits
            if not part or not all(c in '0123456789' for c in part):
                return False  # Invalid format
            new_parts.append(int(part))

        current_parts: list[int] = []
        for part in current_parts_str:
            if not part or not all(c in '0123456789' for c in part):
                return False  # Invalid format
            current_parts.append(int(part))

        # Pad shorter version with zeros
        max_len = len(new_parts) if len(new_parts) > len(current_parts) else len(current_parts)
        while len(new_parts) < max_len:
            new_parts.append(0)
        while len(current_parts) < max_len:
            current_parts.append(0)

        # Compare versions
        return new_parts > current_parts

    @view
    def get_contract_version(self) -> str:
        """Get the current contract version.

        Returns:
            Version string (e.g., "1.0.0")
        """
        return self.contract_version


class Unauthorized(NCFail):
    pass


class InvalidTokens(NCFail):
    pass


class InsufficientBalance(NCFail):
    pass


class InvalidActions(NCFail):
    pass


class InvalidAmount(NCFail):
    pass


class InvalidTime(NCFail):
    pass


class InvalidState(NCFail):
    pass


class InvalidInput(NCFail):
    pass
