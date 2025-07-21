from typing import NamedTuple

from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    NCDepositAction,
    NCWithdrawalAction,
    TokenUid,
    Timestamp,
    public,
    view,
)


MAX_ALLOCATIONS = 10
MONTH_IN_SECONDS = 30 * 24 * 3600  # 30 days in seconds
PRECISION = 10**8


class VestingInfo(NamedTuple):
    """Vesting information for a specific allocation."""
    
    name: str
    beneficiary: Address
    amount: int
    cliff_months: int
    vesting_months: int
    withdrawn: int
    vested: int
    claimable: int


class VestingContractInfo(NamedTuple):
    """Overall vesting contract information."""
    
    token_uid: str
    available: int
    total_allocated: int
    is_started: bool
    start_time: int | None
    allocations: list[int]


class AllocationNotConfigured(NCFail):
    pass


class InsufficientAvailableBalance(NCFail):
    pass


class InvalidIndex(NCFail):
    pass


class InvalidTokenDeposit(NCFail):
    pass


class NoAllocation(NCFail):
    pass


class InvalidTimelock(NCFail):
    pass


class InsufficientVestedAmount(NCFail):
    pass


class InvalidBeneficiary(NCFail):
    pass


class Vesting(Blueprint):
    """Vesting blueprint for token distribution with fixed allocation slots.

    State Variables:
        admin: Contract administrator address
        token_uid: Token being vested
        start_timestamp: Contract creation time
        available_balance: Unallocated tokens in contract
        total_allocated: Sum of all allocated tokens
        is_configured: Tracks which allocation slots are configured

        Allocation Data (per index 0-9):
        allocation_names: Allocation names (predefined or custom)
        allocation_amounts: Total tokens per allocation
        allocation_addresses: Beneficiary addresses
        allocation_cliffs: Cliff periods in months
        allocation_durations: Vesting durations in months
        allocation_withdrawn: Claimed tokens per allocation
    """

    # Contract state
    admin: bytes
    token_uid: TokenUid
    vesting_start: Timestamp
    is_started: bool

    # Balance tracking
    available_balance: Amount
    total_allocated: Amount

    # Configuration status
    is_configured: dict[int, bool]

    # Allocation data
    allocation_names: dict[int, str]
    allocation_amounts: dict[int, Amount]
    allocation_addresses: dict[int, Address]
    allocation_cliffs: dict[int, int]
    allocation_durations: dict[int, int]
    allocation_withdrawn: dict[int, Amount]

    def _validate_index(self, index: int) -> None:
        if not 0 <= index < MAX_ALLOCATIONS:
            raise InvalidIndex("Index out of range")

    def _only_admin(self, ctx: Context) -> None:
        if ctx.address != self.admin:
            raise NCFail("Only admin can call this method")

    def _get_single_deposit_action(
        self, ctx: Context, token_uid: TokenUid
    ) -> NCDepositAction:
        """Get a single deposit action for the specified token."""
        action = ctx.get_single_action(token_uid)
        if not isinstance(action, NCDepositAction):
            raise NCFail("Expected deposit action")
        return action

    def _get_single_withdrawal_action(
        self, ctx: Context, token_uid: TokenUid
    ) -> NCWithdrawalAction:
        """Get a single withdrawal action for the specified token."""
        action = ctx.get_single_action(token_uid)
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        return action

    def _calculate_vested_amount(self, index: int, timestamp: Timestamp) -> Amount:
        if not self.is_started:
            return Amount(0)

        months_passed = (timestamp - self.vesting_start) // MONTH_IN_SECONDS
        cliff_months = self.allocation_cliffs[index]

        if months_passed < cliff_months:
            return Amount(0)

        vesting_months = self.allocation_durations[index]
        total_amount = self.allocation_amounts[index]

        # Special case: vesting_months = 0 means immediately available after cliff
        if vesting_months == 0:
            return total_amount

        months_after_cliff = months_passed - cliff_months
        vesting_months_completed = min(months_after_cliff, vesting_months)

        vested_amount = (total_amount * vesting_months_completed) // vesting_months

        return Amount(min(vested_amount, total_amount))

    @public(allow_deposit=True)
    def initialize(self, ctx: Context, token_uid: TokenUid) -> None:
        """Initialize contract with token deposit."""
        self.token_uid = token_uid
        action = self._get_single_deposit_action(ctx, token_uid)

        self.admin = ctx.address
        self.available_balance = Amount(action.amount)
        self.total_allocated = Amount(0)
        self.is_started = False

    @public
    def configure_vesting(
        self,
        ctx: Context,
        index: int,
        amount: Amount,
        beneficiary: Address,
        cliff_months: int,
        vesting_months: int,
        name: str,
    ) -> None:
        """Configure vesting allocation slot."""
        self._only_admin(ctx)
        self._validate_index(index)

        if self.is_started:
            raise NCFail("Cannot configure after vesting started")

        if amount > self.available_balance:
            raise InsufficientAvailableBalance

        if amount <= 0 or cliff_months < 0 or vesting_months < 0:
            raise InvalidTimelock("Invalid parameters")

        self.allocation_names[index] = name
        self.allocation_amounts[index] = amount
        self.allocation_addresses[index] = beneficiary
        self.allocation_cliffs[index] = cliff_months
        self.allocation_durations[index] = vesting_months
        self.allocation_withdrawn[index] = Amount(0)
        self.is_configured[index] = True

        self.available_balance = Amount(self.available_balance - amount)
        self.total_allocated = Amount(self.total_allocated + amount)

    @public
    def start_vesting(self, ctx: Context) -> None:
        """Start vesting schedule for all configured allocations."""
        self._only_admin(ctx)

        if self.is_started:
            raise NCFail("Vesting already started")

        self.is_started = True
        self.vesting_start = Timestamp(ctx.timestamp)

    @public(allow_withdrawal=True)
    def claim_allocation(self, ctx: Context, index: int) -> None:
        """Claim vested tokens for an allocation."""
        self._validate_index(index)

        if not self.is_configured.get(index, False):
            raise AllocationNotConfigured

        if not self.is_started:
            raise NCFail("Vesting not started")

        beneficiary = self.allocation_addresses[index]
        if ctx.address != self.admin and ctx.address != beneficiary:
            raise InvalidBeneficiary("Only admin or beneficiary can claim")

        action = self._get_single_withdrawal_action(ctx, self.token_uid)

        vested = self._calculate_vested_amount(index, Timestamp(ctx.timestamp))
        withdrawn = self.allocation_withdrawn[index]
        claimable = vested - withdrawn

        if action.amount > claimable:
            raise InsufficientVestedAmount

        self.allocation_withdrawn[index] = Amount(
            self.allocation_withdrawn[index] + action.amount
        )

    @public
    def change_beneficiary(
        self, ctx: Context, index: int, new_beneficiary: Address
    ) -> None:
        """Change beneficiary address for allocation."""
        self._validate_index(index)

        if not self.is_configured.get(index, False):
            raise AllocationNotConfigured

        current_beneficiary = self.allocation_addresses[index]
        if ctx.address != current_beneficiary:
            raise InvalidBeneficiary("Only current beneficiary can change")

        self.allocation_addresses[index] = new_beneficiary

    @public(allow_deposit=True)
    def deposit_tokens(self, ctx: Context) -> None:
        """Deposit additional tokens to available balance."""
        self._only_admin(ctx)

        action = self._get_single_deposit_action(ctx, self.token_uid)
        self.available_balance = Amount(self.available_balance + action.amount)

    @public(allow_withdrawal=True)
    def withdraw_available(self, ctx: Context) -> None:
        """Withdraw tokens from available balance."""
        self._only_admin(ctx)

        action = self._get_single_withdrawal_action(ctx, self.token_uid)

        if action.amount > self.available_balance:
            raise InsufficientAvailableBalance

        self.available_balance = Amount(self.available_balance - action.amount)

    # @public(allow_withdrawal=True)
    # def admin_claim_allocation(self, ctx: Context, index: int) -> None:
    #     """Claim vested tokens for an allocation as admin on behalf of the beneficiary.

    #     This function allows the admin to withdraw tokens and send them to the
    #     beneficiary address registered in the allocation. The withdrawal action
    #     will be processed to transfer funds directly to the beneficiary.
    #     """
    #     self._only_admin(ctx)
    #     self._validate_index(index)

    #     if not self.is_configured.get(index, False):
    #         raise AllocationNotConfigured

    #     if not self.is_started:
    #         raise NCFail("Vesting not started")

    #     action = self._get_single_withdrawal_action(ctx, self.token_uid)

    #     vested = self._calculate_vested_amount(index, Timestamp(ctx.timestamp))
    #     withdrawn = self.allocation_withdrawn[index]
    #     claimable = vested - withdrawn

    #     if action.amount > claimable:
    #         raise InsufficientVestedAmount

    #     self.allocation_withdrawn[index] = Amount(
    #         self.allocation_withdrawn[index] + action.amount
    #     )

    @view
    def get_vesting_info(self, index: int, timestamp: Timestamp) -> VestingInfo:
        """Get vesting information for allocation."""
        if not self.is_configured.get(index, False):
            raise AllocationNotConfigured

        vested = Amount(0)
        if self.is_started:
            vested = self._calculate_vested_amount(index, timestamp)

        withdrawn = self.allocation_withdrawn[index]

        return VestingInfo(
            name=self.allocation_names[index],
            beneficiary=self.allocation_addresses[index],
            amount=self.allocation_amounts[index],
            cliff_months=self.allocation_cliffs[index],
            vesting_months=self.allocation_durations[index],
            withdrawn=withdrawn,
            vested=vested,
            claimable=vested - withdrawn,
        )

    @view
    def get_contract_info(self) -> VestingContractInfo:
        """Get overall contract information."""
        return VestingContractInfo(
            token_uid=self.token_uid.hex(),
            available=self.available_balance,
            total_allocated=self.total_allocated,
            is_started=self.is_started,
            start_time=self.vesting_start if self.is_started else None,
            allocations=[
                i for i in range(MAX_ALLOCATIONS) if self.is_configured.get(i, False)
            ],
        )

__blueprint__ = Vesting