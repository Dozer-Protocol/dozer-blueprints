from typing import NamedTuple

from hathor import (
    Address,
    Amount,
    Blueprint,
    BlueprintId,
    CallerId,
    Context,
    ContractId,
    NCDepositAction,
    NCFail,
    NCWithdrawalAction,
    TokenUid,
    Timestamp,
    export,
    public,
    view,
)

# Constants
HTR_UID = b"\x00"
MIN_PLATFORM_FEE = 0  # 0% (allow no platform fee)
MAX_PLATFORM_FEE = 1000  # 10%
MIN_PARTICIPATION_FEE = 0  # 0% (allow no participation fee)
MAX_PARTICIPATION_FEE = 300  # 3%


class CrowdsaleSaleInfo(NamedTuple):
    """General sale information."""

    token_uid: str
    rate: int
    soft_cap: int
    hard_cap: int
    min_deposit: int
    total_raised: int
    total_sold: int
    state: int
    start_time: int
    end_time: int
    participants: int


class CrowdsaleParticipantInfo(NamedTuple):
    """Participant-specific information."""

    deposited: int
    tokens_due: int
    has_claimed: bool


class CrowdsaleSaleProgress(NamedTuple):
    """Current sale progress metrics."""

    percent_filled: int
    percent_soft_cap: int
    is_successful: bool


class CrowdsaleWithdrawalInfo(NamedTuple):
    """Withdrawal-related information."""

    total_raised: int
    platform_fees: int
    withdrawable: int
    is_withdrawn: bool
    can_withdraw: bool


BASIS_POINTS = 10000  # For fee calculations
HARD_CAP_MARGIN_BP = 50  # 0.5% margin for hard cap (in basis points)


class SaleState:
    """Sale states for the Crowdsale"""

    PENDING = 0  # Configured but not started
    ACTIVE = 1  # Accepting deposits
    PAUSED = 2  # Temporarily halted
    SOFT_CAP_REACHED = 3  # Soft cap reached, still accepting participants
    COMPLETED_FAILED = 4  # Sale ended below soft cap
    COMPLETED_SUCCESS = 5  # Sale ended above soft cap


class CrowdsaleErrors:
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


@export
class Crowdsale(Blueprint):
    """Blueprint for token sales with platform fees and protection mechanisms."""

    # Sale configuration
    token_uid: TokenUid  # Token being sold
    rate: Amount  # Tokens per HTR
    soft_cap: Amount  # Minimum goal in HTR
    hard_cap: Amount  # Maximum cap in HTR
    min_deposit: Amount  # Minimum purchase in HTR
    start_time: Timestamp  # Sale start time
    end_time: Timestamp  # Sale end time
    platform_fee: Amount  # Fee in basis points (0-1000, 0 = no fee)
    participation_fee: (
        Amount  # Fee in basis points for each participation (0-300, 0 = no fee)
    )

    # Sale state
    state: int
    total_raised: Amount  # Total HTR received
    total_sold: Amount  # Total tokens sold
    participants_count: int  # Number of unique participants

    # Token balances
    initial_token_deposit: Amount  # Initial tokens deposited by owner
    sale_token_balance: Amount  # Balance of tokens being sold
    htr_balance: Amount  # Balance of HTR

    # Access control
    owner: CallerId  # Project owner (could be Address or ContractId for dozer_tools)
    platform: Address  # Platform fee recipient
    creator_contract_id: ContractId  # DozerTools contract that created this

    # Participant tracking
    deposits: dict[Address, Amount]  # HTR deposits per address
    claimed: dict[Address, bool]  # Claim status per address

    # Withdrawal tracking
    owner_withdrawn: bool  # Whether owner has withdrawn HTR
    unsold_tokens_withdrawn: bool  # Whether unsold tokens have been withdrawn
    platform_fees_withdrawn: bool  # Whether platform fees have been withdrawn
    platform_fees_collected: Amount  # Total fees collected

    # Participation fee tracking
    total_participation_fees_collected: Amount  # Total participation fees collected
    participation_fees_withdrawn: bool  # Whether participation fees have been withdrawn

    @public(allow_deposit=True)
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
        participation_fee: Amount,
        creator_contract_id: ContractId,
    ) -> None:
        """Initialize the sale contract with configuration parameters."""
        # Validate parameters
        if soft_cap >= hard_cap:
            raise NCFail("Soft cap must be less than hard cap")
        if start_time >= end_time:
            raise NCFail("Invalid time range")
        if platform_fee < MIN_PLATFORM_FEE or platform_fee > MAX_PLATFORM_FEE:
            raise NCFail("Invalid platform fee")
        if (
            participation_fee < MIN_PARTICIPATION_FEE
            or participation_fee > MAX_PARTICIPATION_FEE
        ):
            raise NCFail("Invalid participation fee")
        if rate <= 0 or min_deposit <= 0:
            raise NCFail("Invalid rate or minimum deposit")

        # Validate token deposit
        action = ctx.get_single_action(token_uid)
        if not isinstance(action, NCDepositAction):
            raise NCFail("Expected deposit action")

        # Validate sufficient tokens for hard cap
        tokens_needed = hard_cap * rate
        if action.amount < tokens_needed:
            raise NCFail(
                f"Insufficient tokens deposited. Need {tokens_needed}. Deposited {action.amount}."
            )

        # Initialize configuration
        self.token_uid = token_uid
        self.rate = rate
        self.soft_cap = soft_cap
        self.hard_cap = hard_cap
        self.min_deposit = min_deposit
        self.start_time = start_time
        self.end_time = end_time
        self.platform_fee = platform_fee
        self.participation_fee = participation_fee

        # Initialize state
        self.state = SaleState.PENDING
        self.total_raised = Amount(0)
        self.total_sold = Amount(0)
        self.participants_count = 0
        self.initial_token_deposit = Amount(action.amount)
        self.sale_token_balance = Amount(action.amount)
        self.htr_balance = Amount(0)

        # Set control addresses (following stake.py pattern)
        self.owner = ctx.caller_id  # Will be ContractId when created via dozer_tools

        # Platform fee recipient (for direct creation or dozer_tools platform)
        if isinstance(ctx.caller_id, Address):
            # Direct creation by user (not through dozer_tools)
            self.platform = Address(ctx.caller_id)
        else:
            # Created through dozer_tools contract - get platform owner
            tools_contract_id = ctx.caller_id
            tools_contract = self.syscall.get_contract(
                tools_contract_id, blueprint_id=None
            )
            platform_hex = tools_contract.view().get_contract_info()["owner"]
            self.platform = Address(bytes.fromhex(platform_hex))

        # Set creator_contract_id (for DozerTools routing)
        self.creator_contract_id = creator_contract_id

        # Initialize tracking dictionaries
        self.deposits = {}
        self.claimed = {}

        # Initialize withdrawal tracking
        self.owner_withdrawn = False
        self.unsold_tokens_withdrawn = False
        self.platform_fees_withdrawn = False
        self.platform_fees_collected = Amount(0)
        self.total_participation_fees_collected = Amount(0)
        self.participation_fees_withdrawn = False

    @public(allow_deposit=True)
    def participate(self, ctx: Context) -> None:
        """Participate in the sale by depositing HTR."""
        self._validate_sale_active(ctx)

        # Validate HTR deposit
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCDepositAction):
            raise NCFail("Expected deposit action")

        gross_amount = Amount(action.amount)

        # Calculate and deduct participation fee
        participation_fee = self._calculate_participation_fee(gross_amount)
        net_amount = Amount(gross_amount - participation_fee)

        if net_amount < self.min_deposit:
            raise NCFail(CrowdsaleErrors.BELOW_MIN)

        # Check hard cap with margin to allow deposits up to hard cap + margin
        hard_cap_with_margin = self.hard_cap + (
            self.hard_cap * HARD_CAP_MARGIN_BP // BASIS_POINTS
        )
        if self.total_raised + net_amount > hard_cap_with_margin:
            raise NCFail(CrowdsaleErrors.ABOVE_MAX)

        # Update participant tracking
        participant_address = Address(ctx.caller_id)
        if participant_address not in self.deposits:
            self.participants_count += 1

        # Update state with NET amount for token calculations
        self.deposits[participant_address] = Amount(
            self.deposits.get(participant_address, Amount(0)) + net_amount
        )
        self.total_raised = Amount(self.total_raised + net_amount)
        self.total_sold = Amount(self.total_sold + self._calculate_tokens(net_amount))

        # Track GROSS amount in HTR balance and participation fees separately
        self.htr_balance = Amount(self.htr_balance + gross_amount)
        self.total_participation_fees_collected = Amount(
            self.total_participation_fees_collected + participation_fee
        )

        # Check if soft cap reached (using net amount) - transition to SOFT_CAP_REACHED
        if self.total_raised >= self.soft_cap and self.state == SaleState.ACTIVE:
            self.state = SaleState.SOFT_CAP_REACHED

        # Check if hard cap reached - auto-finalize to success
        if self.total_raised >= self.hard_cap:
            self.state = SaleState.COMPLETED_SUCCESS

    @public(allow_withdrawal=True)
    def claim_tokens(self, ctx: Context) -> None:
        """Claim tokens after successful sale."""
        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        participant_address = Address(ctx.caller_id)
        if self.claimed.get(participant_address, False):
            raise NCFail(CrowdsaleErrors.ALREADY_CLAIMED)

        deposit = self.deposits.get(participant_address, Amount(0))
        if deposit == Amount(0):
            raise NCFail("No tokens to claim")

        tokens_due = self._calculate_tokens(deposit)

        # Validate token withdrawal
        action = ctx.get_single_action(self.token_uid)
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != tokens_due:
            raise NCFail("Invalid withdrawal amount")

        self.deposits[participant_address] = Amount(
            self.deposits[participant_address] - deposit
        )

        # Mark as claimed and update balance
        self.claimed[participant_address] = True
        self.sale_token_balance = Amount(self.sale_token_balance - tokens_due)

    @public(allow_withdrawal=True)
    def claim_refund(self, ctx: Context) -> None:
        """Claim refund if sale failed."""
        if self.state != SaleState.COMPLETED_FAILED:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        participant_address = Address(ctx.caller_id)
        if self.claimed.get(participant_address, False):
            raise NCFail(CrowdsaleErrors.ALREADY_CLAIMED)

        deposit = self.deposits.get(participant_address, Amount(0))
        if deposit == Amount(0):
            raise NCFail("No refund available")

        # Validate HTR withdrawal
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != deposit:
            raise NCFail("Invalid withdrawal amount")

        self.deposits[participant_address] = Amount(
            self.deposits[participant_address] - deposit
        )

        # Mark as claimed and update balance
        self.claimed[participant_address] = True
        self.htr_balance = Amount(self.htr_balance - deposit)

    def _is_owner(self, ctx: Context) -> bool:
        """Check if the caller is the owner."""
        return ctx.caller_id == self.owner

    @public(allow_withdrawal=True)
    def withdraw_raised_htr(self, ctx: Context) -> None:
        """Withdraw raised HTR after successful sale."""
        if not self._is_owner(ctx):
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if self.owner_withdrawn:
            raise NCFail("Already withdrawn")

        # Calculate amounts
        platform_fee = self._calculate_platform_fee(self.total_raised)
        withdrawable = self.total_raised - platform_fee

        # Validate owner HTR withdrawal
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != withdrawable:
            raise NCFail("Invalid withdrawal amount")

        # Mark as withdrawn and update balance
        self.owner_withdrawn = True
        self.platform_fees_collected = Amount(platform_fee)
        self.htr_balance = Amount(self.htr_balance - withdrawable)

    @public(allow_withdrawal=True)
    def withdraw_remaining_tokens(self, ctx: Context) -> None:
        """Withdraw unsold tokens after successful sale (owner only).

        This withdraws only tokens that were never sold to participants.
        Users can still claim their allocated tokens after this withdrawal.
        """
        if not self._is_owner(ctx):
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if self.unsold_tokens_withdrawn:
            raise NCFail("Unsold tokens already withdrawn")

        # Calculate unsold tokens (tokens never allocated to participants)
        unsold_tokens = Amount(self.initial_token_deposit - self.total_sold)
        if unsold_tokens == 0:
            raise NCFail("No unsold tokens to withdraw")

        # Validate token withdrawal action
        action = ctx.get_single_action(self.token_uid)
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != unsold_tokens:
            raise NCFail(f"Invalid withdrawal amount. Expected {unsold_tokens}")

        # Update balance and mark as withdrawn
        self.sale_token_balance = Amount(self.sale_token_balance - unsold_tokens)
        self.unsold_tokens_withdrawn = True

    @public(allow_withdrawal=True)
    def withdraw_platform_fees(self, ctx: Context) -> None:
        """Withdraw platform fees after successful sale."""
        if Address(ctx.caller_id) != self.platform:
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if self.platform_fees_withdrawn:
            raise NCFail("Platform fees already withdrawn")

        platform_fee = self._calculate_platform_fee(self.total_raised)

        # Validate platform fee withdrawal
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != platform_fee:
            raise NCFail("Invalid withdrawal amount")

        # Mark as withdrawn and update balance
        self.platform_fees_withdrawn = True
        self.htr_balance = Amount(self.htr_balance - platform_fee)

    @public(allow_withdrawal=True)
    def withdraw_participation_fees(self, ctx: Context) -> None:
        """Withdraw participation fees (platform/DozerTools owner only).

        Can be called at any time, regardless of sale state.
        Works even if participation_fee is 0 (will withdraw 0).
        """
        if Address(ctx.caller_id) != self.platform:
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.participation_fees_withdrawn:
            raise NCFail("Participation fees already withdrawn")

        total_fees = self.total_participation_fees_collected

        # Validate withdrawal
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != total_fees:
            raise NCFail(
                f"Invalid withdrawal amount. Expected: {total_fees}, Got: {action.amount}"
            )

        # Mark as withdrawn and update balance
        self.participation_fees_withdrawn = True
        self.htr_balance = Amount(self.htr_balance - total_fees)

    @public
    def early_activate(self, ctx: Context) -> None:
        """Activate the sale (owner only)."""
        if not self._is_owner(ctx):
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state != SaleState.PENDING:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if ctx.block.timestamp < self.start_time:
            self.start_time = Timestamp(ctx.block.timestamp)

        self.state = SaleState.ACTIVE

    def _only_creator_contract(self, ctx: Context) -> None:
        if ContractId(ctx.caller_id) != self.creator_contract_id:
            raise NCFail("Only creator contract can call this method")

    def _activate_if_started(self, ctx: Context) -> None:
        """Activate the sale (anyone)"""
        if self.state != SaleState.PENDING:
            return
        if ctx.block.timestamp >= self.start_time:
            self.state = SaleState.ACTIVE

    def _validate_sale_active(self, ctx: Context) -> None:
        """Validate sale is in active state and within time bounds."""
        self._activate_if_started(ctx)

        # Check if end time passed - reject participation
        if ctx.block.timestamp > self.end_time:
            raise NCFail(CrowdsaleErrors.SALE_ACTIVE)

        # Allow participation in ACTIVE or SOFT_CAP_REACHED states
        if self.state not in {SaleState.ACTIVE, SaleState.SOFT_CAP_REACHED}:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        if ctx.block.timestamp < self.start_time:
            raise NCFail(CrowdsaleErrors.NOT_STARTED)

    @public
    def pause(self, ctx: Context) -> None:
        """Pause the sale (only owner)."""
        if not self._is_owner(ctx):
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state not in {SaleState.ACTIVE, SaleState.SOFT_CAP_REACHED}:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        self.state = SaleState.PAUSED

    @public
    def unpause(self, ctx: Context) -> None:
        """Unpause the sale (only owner)."""
        if not self._is_owner(ctx):
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state != SaleState.PAUSED:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        # Return to SOFT_CAP_REACHED if soft cap was already reached, otherwise ACTIVE
        if self.total_raised >= self.soft_cap:
            self.state = SaleState.SOFT_CAP_REACHED
        else:
            self.state = SaleState.ACTIVE

    @public
    def finalize(self, ctx: Context) -> None:
        """Force end sale early (only owner)."""
        if not self._is_owner(ctx):
            raise NCFail(CrowdsaleErrors.UNAUTHORIZED)
        if self.state not in {
            SaleState.ACTIVE,
            SaleState.PAUSED,
            SaleState.SOFT_CAP_REACHED,
        }:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        if self.total_raised >= self.soft_cap:
            self.state = SaleState.COMPLETED_SUCCESS
        else:
            self.state = SaleState.COMPLETED_FAILED

    def _calculate_tokens(self, htr_amount: Amount) -> Amount:
        """Calculate tokens to be received for HTR amount."""
        return Amount(htr_amount * self.rate)

    def _calculate_participation_fee(self, amount: Amount) -> Amount:
        """Calculate participation fee for given amount.

        Returns 0 if participation_fee is 0 (no fee configured).
        """
        if self.participation_fee == 0:
            return Amount(0)
        return Amount(amount * self.participation_fee // BASIS_POINTS)

    def _calculate_platform_fee(self, amount: Amount) -> Amount:
        """Calculate platform fee for given amount.

        Returns 0 if platform_fee is 0 (no fee configured).
        """
        if self.platform_fee == 0:
            return Amount(0)
        return Amount(amount * self.platform_fee // BASIS_POINTS)

    @view
    def get_sale_info(self) -> CrowdsaleSaleInfo:
        """Get general sale information."""
        return CrowdsaleSaleInfo(
            token_uid=self.token_uid.hex(),
            rate=self.rate,
            soft_cap=self.soft_cap,
            hard_cap=self.hard_cap,
            min_deposit=self.min_deposit,
            total_raised=self.total_raised,
            total_sold=self.total_sold,
            state=self.state,
            start_time=self.start_time,
            end_time=self.end_time,
            participants=self.participants_count,
        )

    @view
    def get_participant_info(self, address: Address) -> CrowdsaleParticipantInfo:
        """Get participant-specific information."""
        deposit = self.deposits.get(address, Amount(0))
        return CrowdsaleParticipantInfo(
            deposited=deposit,
            tokens_due=self._calculate_tokens(deposit),
            has_claimed=self.claimed.get(address, False),
        )

    @view
    def get_sale_progress(self) -> CrowdsaleSaleProgress:
        """Get current sale progress metrics."""
        return CrowdsaleSaleProgress(
            percent_filled=(self.total_raised * 100) // self.hard_cap,
            percent_soft_cap=(self.total_raised * 100) // self.soft_cap,
            is_successful=self.state == SaleState.COMPLETED_SUCCESS,
        )

    @view
    def get_withdrawal_info(self) -> CrowdsaleWithdrawalInfo:
        """Get withdrawal-related information."""
        platform_fee = self._calculate_platform_fee(self.total_raised)
        withdrawable = self.total_raised - platform_fee
        can_withdraw = (
            self.state == SaleState.COMPLETED_SUCCESS and not self.owner_withdrawn
        )

        return CrowdsaleWithdrawalInfo(
            total_raised=self.total_raised,
            platform_fees=platform_fee,
            withdrawable=withdrawable,
            is_withdrawn=self.owner_withdrawn,
            can_withdraw=can_withdraw,
        )

    @view
    def get_unsold_token_info(self) -> dict[str, str]:
        """Get information about unsold tokens available for withdrawal."""
        unsold_tokens = Amount(self.initial_token_deposit - self.total_sold)
        can_withdraw_unsold = (
            self.state == SaleState.COMPLETED_SUCCESS
            and not self.unsold_tokens_withdrawn
            and unsold_tokens > 0
        )
        return {
            "initial_token_deposit": str(self.initial_token_deposit),
            "total_sold": str(self.total_sold),
            "unsold_tokens": str(unsold_tokens),
            "unsold_tokens_withdrawn": str(self.unsold_tokens_withdrawn).lower(),
            "can_withdraw_unsold": str(can_withdraw_unsold).lower(),
            "sale_token_balance": str(self.sale_token_balance),
        }

    @view
    def get_fee_info(self) -> dict[str, str]:
        """Get comprehensive fee information."""
        return {
            "participation_fee_bp": str(self.participation_fee),
            "platform_fee_bp": str(self.platform_fee),
            "participation_fee": str(self.participation_fee),
            "platform_fee": str(self.platform_fee),
            "total_participation_fees_collected": str(
                self.total_participation_fees_collected
            ),
            "participation_fees_withdrawn": str(
                self.participation_fees_withdrawn
            ).lower(),
            "platform_fees_collected": str(self.platform_fees_collected),
            "platform_fees_withdrawn": str(self.platform_fees_withdrawn).lower(),
        }

    # Routing methods for DozerTools integration
    @public(allow_deposit=True)
    def routed_participate(self, ctx: Context, user_address: Address) -> None:
        """Participate in the sale via DozerTools routing."""
        self._only_creator_contract(ctx)
        self._validate_sale_active(ctx)

        # Validate HTR deposit
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCDepositAction):
            raise NCFail("Expected deposit action")

        gross_amount = Amount(action.amount)

        # Calculate and deduct participation fee
        participation_fee = self._calculate_participation_fee(gross_amount)
        net_amount = Amount(gross_amount - participation_fee)

        if net_amount < self.min_deposit:
            raise NCFail(CrowdsaleErrors.BELOW_MIN)

        # Check hard cap with margin to allow deposits up to hard cap + margin
        hard_cap_with_margin = self.hard_cap + (
            self.hard_cap * HARD_CAP_MARGIN_BP // BASIS_POINTS
        )
        if self.total_raised + net_amount > hard_cap_with_margin:
            raise NCFail(CrowdsaleErrors.ABOVE_MAX)

        # Update participant tracking
        if user_address not in self.deposits:
            self.participants_count += 1

        # Update state with NET amount for token calculations
        self.deposits[user_address] = Amount(
            self.deposits.get(user_address, Amount(0)) + net_amount
        )
        self.total_raised = Amount(self.total_raised + net_amount)
        self.total_sold = Amount(self.total_sold + self._calculate_tokens(net_amount))

        # Track GROSS amount in HTR balance and participation fees separately
        self.htr_balance = Amount(self.htr_balance + gross_amount)
        self.total_participation_fees_collected = Amount(
            self.total_participation_fees_collected + participation_fee
        )

        # Check if soft cap reached (using net amount) - transition to SOFT_CAP_REACHED
        if self.total_raised >= self.soft_cap and self.state == SaleState.ACTIVE:
            self.state = SaleState.SOFT_CAP_REACHED

        # Check if hard cap reached - auto-finalize to success
        if self.total_raised >= self.hard_cap:
            self.state = SaleState.COMPLETED_SUCCESS

    @public(allow_withdrawal=True)
    def routed_claim_tokens(self, ctx: Context, user_address: Address) -> None:
        """Claim tokens after successful sale via DozerTools routing."""
        self._only_creator_contract(ctx)

        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        if self.claimed.get(user_address, False):
            raise NCFail(CrowdsaleErrors.ALREADY_CLAIMED)

        deposit = self.deposits.get(user_address, Amount(0))
        if deposit == Amount(0):
            raise NCFail("No tokens to claim")

        tokens_due = self._calculate_tokens(deposit)

        # Validate token withdrawal
        action = ctx.get_single_action(self.token_uid)
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != tokens_due:
            raise NCFail("Invalid withdrawal amount")

        self.deposits[user_address] = Amount(self.deposits[user_address] - deposit)

        # Mark as claimed and update balance
        self.claimed[user_address] = True
        self.sale_token_balance = Amount(self.sale_token_balance - tokens_due)

    @public(allow_withdrawal=True)
    def routed_claim_refund(self, ctx: Context, user_address: Address) -> None:
        """Claim refund if sale failed via DozerTools routing."""
        self._only_creator_contract(ctx)

        if self.state != SaleState.COMPLETED_FAILED:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        if self.claimed.get(user_address, False):
            raise NCFail(CrowdsaleErrors.ALREADY_CLAIMED)

        deposit = self.deposits.get(user_address, Amount(0))
        if deposit == Amount(0):
            raise NCFail("No refund available")

        # Validate HTR withdrawal
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != deposit:
            raise NCFail("Invalid withdrawal amount")

        self.deposits[user_address] = Amount(self.deposits[user_address] - deposit)

        # Mark as claimed and update balance
        self.claimed[user_address] = True
        self.htr_balance = Amount(self.htr_balance - deposit)

    @public
    def routed_pause(self, ctx: Context, user_address: Address) -> None:
        """Pause the sale via DozerTools routing (DozerTools handles authorization)."""
        self._only_creator_contract(ctx)

        # DozerTools is responsible for authorization - just execute the action
        if self.state not in {SaleState.ACTIVE, SaleState.SOFT_CAP_REACHED}:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        self.state = SaleState.PAUSED

    @public
    def routed_unpause(self, ctx: Context, user_address: Address) -> None:
        """Unpause the sale via DozerTools routing (DozerTools handles authorization)."""
        self._only_creator_contract(ctx)

        # DozerTools is responsible for authorization - just execute the action
        if self.state != SaleState.PAUSED:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        # Return to SOFT_CAP_REACHED if soft cap was already reached, otherwise ACTIVE
        if self.total_raised >= self.soft_cap:
            self.state = SaleState.SOFT_CAP_REACHED
        else:
            self.state = SaleState.ACTIVE

    @public
    def routed_early_activate(self, ctx: Context, user_address: Address) -> None:
        """Activate the sale early via DozerTools routing (DozerTools handles authorization)."""
        self._only_creator_contract(ctx)

        # DozerTools is responsible for authorization - just execute the action
        if self.state != SaleState.PENDING:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if ctx.block.timestamp < self.start_time:
            self.start_time = Timestamp(ctx.block.timestamp)

        self.state = SaleState.ACTIVE

    @public
    def routed_finalize(self, ctx: Context, user_address: Address) -> None:
        """Force end sale early via DozerTools routing (DozerTools handles authorization)."""
        self._only_creator_contract(ctx)

        # DozerTools is responsible for authorization - just execute the action
        if self.state not in {
            SaleState.ACTIVE,
            SaleState.PAUSED,
            SaleState.SOFT_CAP_REACHED,
        }:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)

        if self.total_raised >= self.soft_cap:
            self.state = SaleState.COMPLETED_SUCCESS
        else:
            self.state = SaleState.COMPLETED_FAILED

    @public(allow_withdrawal=True)
    def routed_withdraw_raised_htr(self, ctx: Context, user_address: Address) -> None:
        """Withdraw raised HTR after successful sale via DozerTools routing (DozerTools handles authorization)."""
        self._only_creator_contract(ctx)

        # DozerTools is responsible for authorization - just execute the action
        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if self.owner_withdrawn:
            raise NCFail("Already withdrawn")

        # Calculate amounts (platform fee stays in contract for platform to withdraw)
        platform_fee = self._calculate_platform_fee(self.total_raised)
        withdrawable = self.total_raised - platform_fee

        # Validate owner HTR withdrawal
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != withdrawable:
            raise NCFail("Invalid withdrawal amount")

        # Mark as withdrawn and update balance
        self.owner_withdrawn = True
        self.platform_fees_collected = Amount(platform_fee)
        self.htr_balance = Amount(self.htr_balance - withdrawable)

    @public(allow_withdrawal=True)
    def routed_withdraw_remaining_tokens(
        self, ctx: Context, user_address: Address
    ) -> None:
        """Withdraw unsold tokens after successful sale via DozerTools routing.

        This withdraws only tokens that were never sold to participants.
        Users can still claim their allocated tokens after this withdrawal.
        DozerTools handles authorization.
        """
        self._only_creator_contract(ctx)

        # DozerTools is responsible for authorization - just execute the action
        if self.state != SaleState.COMPLETED_SUCCESS:
            raise NCFail(CrowdsaleErrors.INVALID_STATE)
        if self.unsold_tokens_withdrawn:
            raise NCFail("Unsold tokens already withdrawn")

        # Calculate unsold tokens (tokens never allocated to participants)
        unsold_tokens = Amount(self.initial_token_deposit - self.total_sold)
        if unsold_tokens == 0:
            raise NCFail("No unsold tokens to withdraw")

        # Validate token withdrawal action
        action = ctx.get_single_action(self.token_uid)
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Expected withdrawal action")
        if action.amount != unsold_tokens:
            raise NCFail(f"Invalid withdrawal amount. Expected {unsold_tokens}")

        # Update balance and mark as withdrawn
        self.sale_token_balance = Amount(self.sale_token_balance - unsold_tokens)
        self.unsold_tokens_withdrawn = True

    @public
    def upgrade_contract(self, ctx: Context, new_blueprint_id: BlueprintId, new_version: str) -> None:
        """Upgrade this contract to a new blueprint version.

        Args:
            ctx: Transaction context
            new_blueprint_id: The blueprint ID to upgrade to
            new_version: Version string for the new blueprint (e.g., "1.1.0")

        Raises:
            NCFail: If caller is not the owner or creator contract
        """
        # Only owner or creator contract can upgrade
        if not self._is_owner(ctx) and ContractId(ctx.caller_id) != self.creator_contract_id:
            raise NCFail("Only owner or creator contract can upgrade this contract")

        # Perform the upgrade
        self.syscall.change_blueprint(new_blueprint_id)
