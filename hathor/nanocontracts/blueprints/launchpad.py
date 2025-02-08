from typing import Optional

from hathor.conf.get_settings import HathorSettings
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    TokenUid,
    Timestamp,
    NCAction,
    NCActionType,
    public,
    view,
)

# Constants
HTR_UID = HathorSettings().HATHOR_TOKEN_UID
MIN_PLATFORM_FEE = 100  # 1%
MAX_PLATFORM_FEE = 1000  # 10%
BASIS_POINTS = 10000  # For fee calculations


class SaleState:
    """Sale states for the launchpad"""

    PENDING = 0  # Configured but not started
    ACTIVE = 1  # Accepting deposits
    PAUSED = 2  # Temporarily halted
    SUCCESS = 3  # Reached soft cap
    FAILED = 4  # Ended below soft cap


class LaunchpadErrors:
    """Common error messages"""

    INVALID_STATE = "Invalid sale state"
    INVALID_AMOUNT = "Invalid amount"
    BELOW_MIN = "Amount below minimum"
    ABOVE_MAX = "Amount above maximum"
    UNAUTHORIZED = "Unauthorized action"
    SALE_ACTIVE = "Sale is still active"
    NOT_STARTED = "Sale has not started"
    ALREADY_CLAIMED = "Already claimed"
    INVALID_TOKEN = "Invalid token"


class Launchpad(Blueprint):
    """Blueprint for token sales with platform fees and protection mechanisms."""

    # Sale configuration
    token_uid: TokenUid  # Token being sold
    rate: Amount  # Tokens per HTR
    soft_cap: Amount  # Minimum goal in HTR
    hard_cap: Amount  # Maximum cap in HTR
    min_deposit: Amount  # Minimum purchase in HTR
    start_time: Timestamp  # Sale start time
    end_time: Timestamp  # Sale end time
    platform_fee: Amount  # Fee in basis points

    # Sale state
    state: int
    total_raised: Amount  # Total HTR received
    total_sold: Amount  # Total tokens sold
    participants_count: int  # Number of unique participants

    # Token balances
    sale_token_balance: Amount  # Balance of tokens being sold
    htr_balance: Amount  # Balance of HTR

    # Access control
    owner: Address  # Project owner
    platform: Address  # Platform fee recipient

    # Participant tracking
    deposits: dict[Address, Amount]  # HTR deposits per address
    claimed: dict[Address, bool]  # Claim status per address

    # Withdrawal tracking
    owner_withdrawn: bool  # Whether owner has withdrawn
    platform_fees_withdrawn: bool  # Whether platform fees have been withdrawn
    platform_fees_collected: Amount  # Total fees collected

    @public
    def initialize(
        self,
        ctx: Context,
        token_uid: TokenUid,
        rate: Amount,
        soft_cap: Amount,
        hard_cap: Amount,
        min_deposit: Amount,
        start_time: Timestamp,
        end_time: Timestamp,
        platform_fee: Amount,
    ) -> None:
        """Initialize the sale contract with configuration parameters."""
        # Validate parameters
        if soft_cap >= hard_cap:
            raise NCFail("Soft cap must be less than hard cap")
        if start_time >= end_time:
            raise NCFail("Invalid time range")
        if platform_fee < MIN_PLATFORM_FEE or platform_fee > MAX_PLATFORM_FEE:
            raise NCFail("Invalid platform fee")
        if rate <= 0 or min_deposit <= 0:
            raise NCFail("Invalid rate or minimum deposit")

        # Validate token deposit
        action = self._get_token_action(ctx, token_uid, NCActionType.DEPOSIT)

        # Validate sufficient tokens for hard cap
        tokens_needed = hard_cap * rate
        if action.amount < tokens_needed:
            raise NCFail(f"Insufficient tokens deposited. Need {tokens_needed}")

        # Initialize configuration
        self.token_uid = token_uid
        self.rate = rate
        self.soft_cap = soft_cap
        self.hard_cap = hard_cap
        self.min_deposit = min_deposit
        self.start_time = start_time
        self.end_time = end_time
        self.platform_fee = platform_fee

        # Initialize state
        self.state = SaleState.PENDING
        self.total_raised = Amount(0)
        self.total_sold = Amount(0)
        self.participants_count = 0
        self.sale_token_balance = action.amount
        self.htr_balance = Amount(0)

        # Set control addresses
        self.owner = ctx.address
        self.platform = ctx.address  # TODO: Configure platform address

        # Initialize tracking
        self.owner_withdrawn = False
        self.platform_fees_withdrawn = False
        self.platform_fees_collected = Amount(0)

    @public
    def participate(self, ctx: Context) -> None:
        """Participate in the sale by depositing HTR."""
        self._validate_sale_active(ctx)

        # Validate HTR deposit
        action = self._get_token_action(ctx, HTR_UID, NCActionType.DEPOSIT)
        amount = action.amount

        if amount < self.min_deposit:
            raise NCFail(LaunchpadErrors.BELOW_MIN)

        # Check hard cap
        if self.total_raised + amount > self.hard_cap:
            raise NCFail(LaunchpadErrors.ABOVE_MAX)

        # Update participant tracking
        if ctx.address not in self.deposits:
            self.participants_count += 1

        # Update state
        self.deposits[ctx.address] = self.deposits.get(ctx.address, 0) + amount
        self.total_raised += amount
        self.total_sold += self._calculate_tokens(amount)
        self.htr_balance += amount

        # Check if soft cap reached
        if self.total_raised >= self.soft_cap:
            self.state = SaleState.SUCCESS

    @public
    def claim_tokens(self, ctx: Context) -> None:
        """Claim tokens after successful sale."""
        if self.state != SaleState.SUCCESS:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        if self.claimed.get(ctx.address, False):
            raise NCFail(LaunchpadErrors.ALREADY_CLAIMED)

        deposit = self.deposits.get(ctx.address, 0)
        if deposit == 0:
            raise NCFail("No tokens to claim")

        tokens_due = self._calculate_tokens(deposit)

        # Validate token withdrawal
        action = self._get_token_action(ctx, self.token_uid, NCActionType.WITHDRAWAL)
        if action.amount != tokens_due:
            raise NCFail("Invalid withdrawal amount")

        self.deposits[ctx.address] -= deposit

        # Mark as claimed and update balance
        self.claimed[ctx.address] = True
        self.sale_token_balance -= tokens_due

    @public
    def claim_refund(self, ctx: Context) -> None:
        """Claim refund if sale failed."""
        if self.state != SaleState.FAILED:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        if self.claimed.get(ctx.address, False):
            raise NCFail(LaunchpadErrors.ALREADY_CLAIMED)

        deposit = self.deposits.get(ctx.address, 0)
        if deposit == 0:
            raise NCFail("No refund available")

        # Validate HTR withdrawal
        action = self._get_token_action(ctx, HTR_UID, NCActionType.WITHDRAWAL)
        if action.amount != deposit:
            raise NCFail("Invalid withdrawal amount")

        self.deposits[ctx.address] -= deposit

        # Mark as claimed and update balance
        self.claimed[ctx.address] = True
        self.htr_balance -= deposit

    @public
    def withdraw_raised_htr(self, ctx: Context) -> None:
        """Withdraw raised HTR after successful sale."""
        if ctx.address != self.owner:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state != SaleState.SUCCESS:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        if self.owner_withdrawn:
            raise NCFail("Already withdrawn")

        # Calculate amounts
        platform_fee = self._calculate_platform_fee(self.total_raised)
        withdrawable = self.total_raised - platform_fee

        # Validate owner HTR withdrawal
        action = self._get_token_action(ctx, HTR_UID, NCActionType.WITHDRAWAL)
        if action.amount != withdrawable:
            raise NCFail("Invalid withdrawal amount")

        # Mark as withdrawn and update balance
        self.owner_withdrawn = True
        self.platform_fees_collected = platform_fee
        self.htr_balance -= withdrawable

    @public
    def withdraw_remaining_tokens(self, ctx: Context) -> None:
        """Withdraw remaining tokens after successful sale (owner only)."""
        if ctx.address != self.owner:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state != SaleState.SUCCESS:
            raise NCFail(LaunchpadErrors.INVALID_STATE)

        # Validate token withdrawal action
        action = self._get_token_action(ctx, self.token_uid, NCActionType.WITHDRAWAL)
        if action.amount != self.sale_token_balance:
            raise NCFail("Invalid withdrawal amount")

        # Update balance
        self.sale_token_balance = Amount(0)

    @public
    def withdraw_platform_fees(self, ctx: Context) -> None:
        """Withdraw platform fees after successful sale."""
        if ctx.address != self.platform:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state != SaleState.SUCCESS:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        if self.platform_fees_withdrawn:
            raise NCFail("Platform fees already withdrawn")

        platform_fee = self._calculate_platform_fee(self.total_raised)

        # Validate platform fee withdrawal
        action = self._get_token_action(ctx, HTR_UID, NCActionType.WITHDRAWAL)
        if action.amount != platform_fee:
            raise NCFail("Invalid withdrawal amount")

        # Mark as withdrawn and update balance
        self.platform_fees_withdrawn = True
        self.htr_balance -= platform_fee

    @public
    def early_activate(self, ctx: Context) -> None:
        """Activate the sale (owner only)."""
        if ctx.address != self.owner:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state != SaleState.PENDING:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        if ctx.timestamp < self.start_time:
            self.start_time = ctx.timestamp

        self.state = SaleState.ACTIVE

    def _get_token_action(
        self, ctx: Context, token_uid: TokenUid, expected_type: NCActionType
    ) -> NCAction:
        """Get and validate token action for specific token and type."""
        if len(ctx.actions) != 1:
            raise NCFail("Expected single action")

        action = next(iter(ctx.actions.values()))
        if action.token_uid != token_uid:
            raise NCFail(f"Expected token {token_uid.hex()}")

        if action.type != expected_type:
            raise NCFail(f"Expected {expected_type.name} action")

        return action

    def _activate_if_started(self, ctx: Context) -> None:
        """Activate the sale (anyone)"""
        if self.state != SaleState.PENDING:
            return
        if ctx.timestamp >= self.start_time:
            self.state = SaleState.ACTIVE

    def _validate_sale_active(self, ctx: Context) -> None:
        """Validate sale is in active state and within time bounds."""
        self._activate_if_started(ctx)
        if self.state != SaleState.ACTIVE:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        if ctx.timestamp < self.start_time:
            raise NCFail(LaunchpadErrors.NOT_STARTED)
        if ctx.timestamp > self.end_time:
            raise NCFail(LaunchpadErrors.SALE_ACTIVE)

    @public
    def pause(self, ctx: Context) -> None:
        """Pause the sale (only owner)."""
        if ctx.address != self.owner:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state != SaleState.ACTIVE:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        self.state = SaleState.PAUSED

    @public
    def unpause(self, ctx: Context) -> None:
        """Unpause the sale (only owner)."""
        if ctx.address != self.owner:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state != SaleState.PAUSED:
            raise NCFail(LaunchpadErrors.INVALID_STATE)
        self.state = SaleState.ACTIVE

    @public
    def finalize(self, ctx: Context) -> None:
        """Force end sale early (only owner)."""
        if ctx.address != self.owner:
            raise NCFail(LaunchpadErrors.UNAUTHORIZED)
        if self.state not in {SaleState.ACTIVE, SaleState.PAUSED}:
            raise NCFail(LaunchpadErrors.INVALID_STATE)

        if self.total_raised >= self.soft_cap:
            self.state = SaleState.SUCCESS
        else:
            self.state = SaleState.FAILED

    def _calculate_tokens(self, htr_amount: Amount) -> Amount:
        """Calculate tokens to be received for HTR amount."""
        return htr_amount * self.rate

    def _calculate_platform_fee(self, amount: Amount) -> Amount:
        """Calculate platform fee for given amount."""
        return amount * self.platform_fee // BASIS_POINTS

    @view
    def get_sale_info(self) -> dict:
        """Get general sale information."""
        return {
            "token_uid": self.token_uid.hex(),
            "rate": self.rate,
            "soft_cap": self.soft_cap,
            "hard_cap": self.hard_cap,
            "total_raised": self.total_raised,
            "total_sold": self.total_sold,
            "state": self.state,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "participants": self.participants_count,
        }

    @view
    def get_participant_info(self, address: Address) -> dict:
        """Get participant-specific information."""
        deposit = self.deposits.get(address, 0)
        return {
            "deposited": deposit,
            "tokens_due": self._calculate_tokens(deposit),
            "has_claimed": self.claimed.get(address, False),
        }

    @view
    def get_sale_progress(self) -> dict:
        """Get current sale progress metrics."""
        return {
            "percent_filled": (self.total_raised * 100) // self.hard_cap,
            "percent_soft_cap": (self.total_raised * 100) // self.soft_cap,
            "is_successful": self.state == SaleState.SUCCESS,
        }

    @view
    def get_withdrawal_info(self) -> dict:
        """Get withdrawal-related information."""
        platform_fee = self._calculate_platform_fee(self.total_raised)
        withdrawable = self.total_raised - platform_fee
        can_withdraw = self.state == SaleState.SUCCESS and not self.owner_withdrawn

        return {
            "total_raised": self.total_raised,
            "platform_fees": platform_fee,
            "withdrawable": withdrawable,
            "is_withdrawn": self.owner_withdrawn,
            "can_withdraw": can_withdraw,
        }
