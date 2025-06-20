from typing import List
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import NCDepositAction, NCWithdrawalAction, public, view
from hathor.types import Address, Amount, TokenUid, Timestamp, AddressB58
from math import floor, ceil

# Constants
DAY_IN_SECONDS: int = 60 * 60 * 24
MIN_PERIOD_DAYS: int = 30
MIN_STAKE_AMOUNT: Amount = 100  # Minimum stake amount
MAX_STAKE_AMOUNT: Amount = 1000000  # Maximum stake amount per address
PRECISION: int = 10**20  # For fixed-point arithmetic


class Stake(Blueprint):
    """Stake blueprint with enhanced features.

    The life cycle of contracts using this blueprint is the following:

    1. [Owner] Create a contract define earning per day.
    2. [Owner] `deposit()` or `withdraw()`.
    3. [User] `stake(...)`.
    4. [User] `unstake(...)`.
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
    owner_address: Address

    # User
    user_deposits: dict[Address, Amount]
    user_debit: dict[Address, int]  # Changed to int for fixed-point arithmetic
    user_stake_timestamp: dict[Address, int]  # For timelock

    def _validate_state(self) -> None:
        """Validate contract state invariants"""
        assert self.total_staked >= 0, "Invalid total staked"
        assert self.owner_balance >= 0, "Invalid owner balance"

    def _validate_address(self, address: Address) -> None:
        """Validate address format"""
        if not isinstance(address, Address):
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
            int(ctx.timestamp)
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
        if ctx.address != self.owner_address:
            raise Unauthorized("Unauthorized")

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
        now = int(ctx.timestamp)
        if self.last_reward != 0 and now <= self.last_reward:
            return
        if self.total_staked == 0:
            self.last_reward = now
            return
        multiplier = (now - self.last_reward) * PRECISION
        self.rewards_per_share += (
            multiplier * self.earnings_per_second
        ) // self.total_staked
        self.last_reward = now

    def _pending_rewards(self, address: Address) -> Amount:
        """Calculate pending rewards with fixed-point arithmetic"""
        if address not in self.user_deposits:
            return Amount(0)
        return Amount(
            floor(
                (self.user_deposits[address] * self.rewards_per_share) // PRECISION
                - self.user_debit.get(address, 0)
            )
        )

    @public(allow_deposit=True)
    def initialize(
        self, ctx: Context, earnings_per_day: int, token_uid: TokenUid
    ) -> None:
        self.token_uid = token_uid
        action = self._get_single_deposit_action(ctx)
        self.earnings_per_second = (earnings_per_day * PRECISION) // DAY_IN_SECONDS
        self.owner_address = Address(ctx.address)
        amount = action.amount
        self._amount_check(amount, earnings_per_day)
        self.owner_balance = Amount(amount)
        self.total_staked = Amount(0)
        self.last_reward = 0
        self.rewards_per_share = 0
        self.paused = False
        self._validate_state()

    @public
    def pause(self, ctx: Context) -> None:
        """Emergency pause functionality"""
        if ctx.address != self.owner_address:
            raise Unauthorized("Only owner can pause")
        self.paused = True

    @public
    def unpause(self, ctx: Context) -> None:
        """Unpause functionality"""
        if ctx.address != self.owner_address:
            raise Unauthorized("Only owner can unpause")
        self.paused = False

    @public(allow_withdrawal=True)
    def emergency_withdraw(self, ctx: Context) -> None:
        """Emergency withdrawal without timelock"""
        if not self.paused:
            raise InvalidState("Contract must be paused")
        action = self._get_single_withdrawal_action(ctx)
        address = Address(ctx.address)
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
        if ctx.address == self.owner_address:
            raise Unauthorized("admin, please use other address to stake")
        action = self._get_single_deposit_action(ctx)
        address = Address(ctx.address)
        amount = action.amount
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
            self.user_stake_timestamp[address] = int(ctx.timestamp)

        self._safe_pay(pending, address)
        self.user_deposits[address] = Amount(self.user_deposits[address] + amount)
        self.total_staked = Amount(self.total_staked + amount + pending)
        self.user_debit[address] = (
            self.user_deposits[address] * self.rewards_per_share
        ) // PRECISION
        self._validate_state()

    @public(allow_withdrawal=True)
    def unstake(self, ctx: Context) -> None:
        if self.paused:
            raise InvalidState("Contract is paused")
        action = self._get_single_withdrawal_action(ctx)
        address = Address(ctx.address)
        if address not in self.user_deposits:
            raise Unauthorized("user not staked")

        self._validate_unstake_time(ctx, address)
        amount = action.amount

        self._update_pool(ctx)
        pending = self._pending_rewards(address)
        if amount > (self.user_deposits[address] + pending):
            raise InsufficientBalance("insufficient funds")

        # First update total staked
        self.total_staked = Amount(
            max(0, self.total_staked - self.user_deposits[address])
        )
        # Then set user's deposit to zero before updating with pending rewards
        self.user_deposits[address] = Amount(0)
        # Add pending rewards
        if pending > 0:
            self._safe_pay(pending, address)
        # Finally process withdrawal
        self.user_deposits[address] = Amount(
            max(0, self.user_deposits[address] - amount)
        )
        self.user_debit[address] = 0

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
            multiplier = int(timestamp) - self.last_reward
            rewards_per_share += (
                multiplier * self.earnings_per_second
            ) // self.total_staked
            pending = Amount(
                floor(
                    (self.user_deposits[address] * rewards_per_share) // PRECISION
                    - self.user_debit.get(address, 0)
                )
            )
            return Amount(self.user_deposits[address] + pending)
        else:
            return Amount(0)

    @view
    def get_user_info(self, address: Address) -> dict[str, int]:
        return {
            "deposits": self.user_deposits.get(address, 0),
        }

    @view
    def front_end_api(self) -> dict[str, float]:
        return {
            "owner_balance": self.owner_balance,
            "total_staked": self.total_staked,
            "rewards_per_share": self.rewards_per_share,
            "paused": self.paused,
        }


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
