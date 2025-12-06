from hathor.conf.get_settings import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    NCDepositAction,
    NCWithdrawalAction,
    Address,
    Amount,
    TokenUid,
    Timestamp,
)
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
import os
import time

from hathor.nanocontracts.blueprints.crowdsale import (
    Crowdsale,
    SaleState,
    CrowdsaleErrors,
)

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class CrowdsaleTestCase(BlueprintTestCase):
    """Test suite for the Crowdsale blueprint contract."""

    def setUp(self):
        super().setUp()

        # Set up contract
        self.contract_id = self.gen_random_contract_id()
        self.blueprint_id = self._register_blueprint_class(Crowdsale)

        # Generate test tokens and addresses
        self.token_uid = self.gen_random_token_uid()
        self.owner_address, self.owner_key = self._get_any_address()
        self.platform_address = (
            self.owner_address
        )  # TODO: Think how to define platform address

        # Set up base transaction for contexts
        self.tx = self.get_genesis_tx()

        # Default test parameters
        self.rate = 100  # 100 tokens per HTR
        self.soft_cap = 1000_00  # 1000 HTR
        self.hard_cap = 5000_00  # 5000 HTR
        self.min_deposit = 10_00  # 10 HTR
        self.platform_fee = 500  # 5%
        self.participation_fee = 200  # 2%
        self.start_time = int(time.time())
        self.end_time = self.start_time + 86400  # 24 hours

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair."""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _initialize_sale(
        self, params: dict | None = None, activate: bool = True
    ) -> None:
        """Initialize sale with default or custom parameters."""
        if params is None:
            params = {}

        # Set parameters
        token_uid = params.get("token_uid", self.token_uid)
        rate = params.get("rate", self.rate)
        soft_cap = params.get("soft_cap", self.soft_cap)
        hard_cap = params.get("hard_cap", self.hard_cap)
        min_deposit = params.get("min_deposit", self.min_deposit)
        start_time = params.get("start_time", self.start_time)
        end_time = params.get("end_time", self.end_time)
        platform_fee = params.get("platform_fee", self.platform_fee)
        participation_fee = params.get("participation_fee", self.participation_fee)

        # Create context with token deposit action
        init_ctx = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=hard_cap * rate)],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=start_time - 100,
        )

        # Generate creator contract ID for DozerTools routing
        creator_contract_id = self.gen_random_contract_id()

        # Use create_contract instead of call_public_method for initialization
        self.runner.create_contract(
            self.contract_id,
            self.blueprint_id,
            init_ctx,
            token_uid,
            rate,
            soft_cap,
            hard_cap,
            min_deposit,
            start_time,
            end_time,
            platform_fee,
            participation_fee,
            creator_contract_id,
        )

        if activate:
            activate_ctx = self.create_context(
                actions=[],
                vertex=self.tx,
                caller_id=Address(self.owner_address),
                timestamp=start_time - 1,
            )
            self.runner.call_public_method(
                self.contract_id, "early_activate", activate_ctx
            )

    def _create_deposit_context(
        self, amount: int, address: bytes | None = None, timestamp: int | None = None
    ) -> Context:
        """Create a context for HTR deposits."""
        if address is None:
            address = self._get_any_address()[0]
        if timestamp is None:
            timestamp = self.start_time + 100

        return self.create_context(
            actions=[NCDepositAction(token_uid=HTR_UID, amount=amount)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(address),
            timestamp=timestamp,
        )

    def _calculate_platform_fee(self, amount: int) -> int:
        """Helper to calculate platform fee."""
        return amount * self.platform_fee // 10000

    def _calculate_participation_fee(self, amount: int) -> int:
        """Helper to calculate participation fee."""
        return amount * self.participation_fee // 10000

    def _calculate_net_amount(self, gross_amount: int) -> int:
        """Helper to calculate net amount after participation fee."""
        participation_fee = self._calculate_participation_fee(gross_amount)
        return gross_amount - participation_fee

    def _check_contract_balances(self) -> None:
        """Verify contract balances match registered token states."""
        # Get actual contract balances
        storage = self.runner.get_storage(self.contract_id)
        actual_htr_balance = storage.get_balance(HTR_UID).value
        actual_token_balance = storage.get_balance(self.token_uid).value

        # Get registered balances from contract state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        expected_htr_balance = contract.htr_balance
        expected_token_balance = contract.sale_token_balance

        # Verify HTR balance
        self.assertEqual(
            actual_htr_balance,
            expected_htr_balance,
            f"HTR balance mismatch. Expected: {expected_htr_balance}, Got: {actual_htr_balance}",
        )

        # Verify token balance
        self.assertEqual(
            actual_token_balance,
            expected_token_balance,
            f"Token balance mismatch. Expected: {expected_token_balance}, Got: {actual_token_balance}",
        )

    def test_initialize(self):
        """Test contract initialization with valid parameters."""
        self._initialize_sale(activate=False)  # Don't activate for initialization test

        # Verify initial state using contract instance
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.token_uid, self.token_uid)
        self.assertEqual(contract.rate, self.rate)
        self.assertEqual(contract.soft_cap, self.soft_cap)
        self.assertEqual(contract.hard_cap, self.hard_cap)
        self.assertEqual(contract.state, SaleState.PENDING)
        self.assertEqual(contract.total_raised, 0)
        self.assertEqual(contract.owner, Address(self.owner_address))

        # Verify contract balances
        self._check_contract_balances()

    def test_initialize_invalid_params(self):
        """Test initialization with invalid parameters."""
        # Test soft cap >= hard cap
        with self.assertRaises(NCFail):
            self._initialize_sale({"soft_cap": 1000_00, "hard_cap": 1000_00})

        # Test invalid time range
        with self.assertRaises(NCFail):
            self._initialize_sale(
                {"start_time": self.end_time, "end_time": self.start_time}
            )

        # Test invalid platform fee (above maximum)
        with self.assertRaises(NCFail):
            self._initialize_sale({"platform_fee": 1500})  # Above maximum (10%)

        # Test invalid participation fee (above maximum)
        with self.assertRaises(NCFail):
            self._initialize_sale({"participation_fee": 500})  # Above maximum (3%)

    def test_initialize_invalid_token_deposit(self):
        """Test initialization with insufficient or invalid token deposit."""
        # Test insufficient tokens
        insufficient_tokens = self.hard_cap * self.rate - 1
        ctx = self.create_context(
            actions=[
                NCDepositAction(token_uid=self.token_uid, amount=insufficient_tokens)
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time - 100,
        )

        creator_contract_id = self.gen_random_contract_id()
        with self.assertRaises(NCFail):
            contract_id = self.gen_random_contract_id()
            self.runner.create_contract(
                contract_id,
                self.blueprint_id,
                ctx,
                self.token_uid,
                self.rate,
                self.soft_cap,
                self.hard_cap,
                self.min_deposit,
                self.start_time,
                self.end_time,
                self.platform_fee,
                self.participation_fee,
                creator_contract_id,
            )

        # Test wrong token
        wrong_token = self.gen_random_token_uid()
        ctx = self.create_context(
            actions=[
                NCDepositAction(token_uid=wrong_token, amount=self.hard_cap * self.rate)
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time - 100,
        )

        with self.assertRaises(NCFail):
            contract_id = self.gen_random_contract_id()
            self.runner.create_contract(
                contract_id,
                self.blueprint_id,
                ctx,
                self.token_uid,  # Notice different from deposited token
                self.rate,
                self.soft_cap,
                self.hard_cap,
                self.min_deposit,
                self.start_time,
                self.end_time,
                self.platform_fee,
                self.participation_fee,
                creator_contract_id,
            )

    def test_state_transitions(self):
        """Test state transitions in the sale lifecycle."""
        # Initialize sale
        self._initialize_sale(activate=False)

        # Verify initial pending state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.PENDING)

        # Try participation during PENDING state - should fail
        with self.assertRaises(NCFail) as cm:
            deposit_amount = 100_00
            ctx = self._create_deposit_context(
                deposit_amount, timestamp=self.start_time - 1
            )
            self.runner.call_public_method(self.contract_id, "participate", ctx)
        self.assertEqual(str(cm.exception), CrowdsaleErrors.INVALID_STATE)

        # Activate the sale
        activate_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time - 1,
        )
        self.runner.call_public_method(self.contract_id, "early_activate", activate_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)

        # Test participation works in ACTIVE state
        deposit_ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        # total_raised stores NET amount (after participation fee)
        net_amount = self._calculate_net_amount(deposit_amount)
        self.assertEqual(contract.total_raised, net_amount)

        # Test pause -> PAUSED
        pause_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time + 2,
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.PAUSED)

        # Test unpause -> ACTIVE
        self.runner.call_public_method(self.contract_id, "unpause", pause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)

        # Test reaching soft cap -> SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        # total_raised is NET amount, need to calculate GROSS amount to reach soft_cap
        net_remaining = self.soft_cap - contract.total_raised
        if net_remaining > 0:
            # Calculate gross amount needed so that after fee deduction, we reach soft_cap
            # gross * (1 - fee_rate) = net_remaining
            # gross = net_remaining / (1 - fee_rate) = net_remaining * 10000 / (10000 - participation_fee)
            gross_remaining = (net_remaining * 10000) // (
                10000 - self.participation_fee
            )
            ctx = self._create_deposit_context(gross_remaining)
            self.runner.call_public_method(self.contract_id, "participate", ctx)
            contract = self.get_readonly_contract(self.contract_id)
            assert isinstance(contract, Crowdsale)
            self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Verify contract balances
        self._check_contract_balances()

    def test_participate(self):
        """Test basic participation functionality."""
        # Initialize and activate sale
        self._initialize_sale(activate=True)
        gross_amount = 100_00

        # Create participation context
        ctx = self._create_deposit_context(gross_amount)

        # Verify sale is in active state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)

        # Participate in sale
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Calculate expected values
        participation_fee = self._calculate_participation_fee(gross_amount)
        net_amount = self._calculate_net_amount(gross_amount)

        # Verify state changes
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_raised, net_amount)  # NET amount
        self.assertEqual(contract.total_sold, net_amount * self.rate)  # Based on NET
        self.assertEqual(contract.participants_count, 1)
        self.assertEqual(contract.total_participation_fees_collected, participation_fee)
        self.assertEqual(contract.htr_balance, gross_amount)  # GROSS in balance

        # Verify contract balances
        self._check_contract_balances()

    def test_participate_multiple_users(self):
        """Test participation from multiple users."""
        self._initialize_sale()
        num_users = 5
        gross_amount = 100_00

        for _ in range(num_users):
            ctx = self._create_deposit_context(gross_amount)
            self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Calculate expected values
        net_amount_per_user = self._calculate_net_amount(gross_amount)
        total_net = net_amount_per_user * num_users
        total_fees = self._calculate_participation_fee(gross_amount) * num_users

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_raised, total_net)  # Total NET amount
        self.assertEqual(contract.participants_count, num_users)
        self.assertEqual(contract.total_participation_fees_collected, total_fees)

        # Verify contract balances
        self._check_contract_balances()

    def test_soft_cap_reached(self):
        """Test sale state transition when soft cap is reached."""
        self._initialize_sale()

        # Need to deposit GROSS amount such that NET amount >= soft_cap
        # net = gross * (1 - participation_fee_rate)
        # gross = soft_cap / (1 - participation_fee_rate)
        # With 2% fee: gross = soft_cap / 0.98
        gross_needed = (self.soft_cap * 10000) // (10000 - self.participation_fee) + 1

        ctx = self._create_deposit_context(gross_needed)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)
        self.assertGreaterEqual(contract.total_raised, self.soft_cap)
        # Verify contract balances
        self._check_contract_balances()

    def test_claim_tokens(self):
        """Test token claiming after successful sale."""
        self._initialize_sale()

        # Reach soft cap (need gross amount that gives net >= soft_cap)
        gross_amount = (self.soft_cap * 10000) // (10000 - self.participation_fee) + 1
        deposit_ctx = self._create_deposit_context(gross_amount)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Finalize the sale to transition to COMPLETED_SUCCESS
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 50,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify state is now COMPLETED_SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)

        # Calculate tokens due based on NET amount
        net_amount = self._calculate_net_amount(gross_amount)
        tokens_due = net_amount * self.rate

        # Attempt to claim tokens
        claim_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=tokens_due)],
            vertex=self.tx,
            caller_id=Address(deposit_ctx.caller_id),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "claim_tokens", claim_ctx)

        # Verify claim status
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", Address(deposit_ctx.caller_id)
        )
        self.assertTrue(participant_info.has_claimed)
        self.assertEqual(participant_info.tokens_due, 0)
        # Verify contract balances
        self._check_contract_balances()

    def test_claim_refund(self):
        """Test refund claiming after failed sale."""
        self._initialize_sale()
        gross_amount = self.soft_cap // 2  # Below soft cap

        # Make deposit
        deposit_ctx = self._create_deposit_context(gross_amount)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)

        # Calculate net amount (what user actually contributed after fee)
        net_amount = self._calculate_net_amount(gross_amount)

        # Verify state is ACTIVE (soft cap not reached)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)

        # Force sale to failed state
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify state is now COMPLETED_FAILED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_FAILED)

        # Claim refund (user gets back NET amount, participation fee is not refunded)
        refund_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=net_amount)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(deposit_ctx.caller_id),
            timestamp=self.end_time + 200,
        )
        self.runner.call_public_method(self.contract_id, "claim_refund", refund_ctx)

        # Verify refund status
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", Address(deposit_ctx.caller_id)
        )
        self.assertTrue(participant_info.has_claimed)
        self.assertEqual(participant_info.deposited, 0)
        # Verify contract balances
        self._check_contract_balances()

    def test_owner_functions(self):
        """Test owner-only functions."""
        self._initialize_sale()

        # Test pause/unpause
        pause_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.PAUSED)

        self.runner.call_public_method(self.contract_id, "unpause", pause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)

        # Test unauthorized access
        unauthorized_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self._get_any_address()[0]),
            timestamp=self.start_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.contract_id, "pause", unauthorized_ctx)

    def test_sale_state_transitions(self):
        """Test sale state transitions and validations."""
        # Initialize without activating
        self._initialize_sale(activate=False)

        # Verify initial pending state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.PENDING)

        # Try to participate while pending - should fail
        deposit_amount = 100_00
        ctx = self._create_deposit_context(
            deposit_amount, timestamp=self.start_time - 1
        )
        with self.assertRaises(NCFail) as cm:
            self.runner.call_public_method(self.contract_id, "participate", ctx)
        self.assertEqual(str(cm.exception), CrowdsaleErrors.INVALID_STATE)

        # Activate sale
        activate_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time - 1,  # Just after start time
        )
        self.runner.call_public_method(self.contract_id, "early_activate", activate_ctx)
        # Now participation should work
        ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify participation succeeded
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        # total_raised stores NET amount (after participation fee)
        net_amount = self._calculate_net_amount(deposit_amount)
        self.assertEqual(contract.total_raised, net_amount)

        # Verify contract balances
        self._check_contract_balances()

    def test_view_functions(self):
        """Test view functions return correct information."""
        self._initialize_sale()
        deposit_amount = 100_00

        # Make a deposit
        ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Test get_sale_info
        sale_info = self.runner.call_view_method(self.contract_id, "get_sale_info")
        # total_raised stores NET amount (after participation fee)
        net_amount = self._calculate_net_amount(deposit_amount)
        self.assertEqual(sale_info.total_raised, net_amount)
        self.assertEqual(sale_info.participants, 1)

        # Test get_participant_info
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", Address(ctx.caller_id)
        )
        # deposited stores NET amount (used for token calculations)
        self.assertEqual(participant_info.deposited, net_amount)
        self.assertEqual(participant_info.tokens_due, net_amount * self.rate)

        # Test get_sale_progress
        progress = self.runner.call_view_method(self.contract_id, "get_sale_progress")
        # Progress is based on NET amount
        expected_percent = (net_amount * 100) // self.hard_cap
        self.assertEqual(progress.percent_filled, expected_percent)

    def test_withdraw_remaining_tokens(self):
        """Test withdrawal of unsold tokens after successful sale."""
        self._initialize_sale()

        # Reach soft cap - need to deposit enough GROSS amount so NET reaches soft_cap
        # With 2% fee: gross * 0.98 = soft_cap, so gross = soft_cap / 0.98
        # Using integer math: gross = (soft_cap * 10000) // (10000 - participation_fee)
        gross_amount_needed = (self.soft_cap * 10000) // (
            10000 - self.participation_fee
        )
        ctx_deposit = self._create_deposit_context(gross_amount_needed)
        self.runner.call_public_method(self.contract_id, "participate", ctx_deposit)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Try to withdraw before COMPLETED_SUCCESS state (should fail in SOFT_CAP_REACHED)
        ctx_withdraw = self.create_context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=1000)],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", ctx_withdraw
            )

        # Finalize the sale
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 50,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify state is now COMPLETED_SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)

        # Calculate unsold tokens (initial_deposit - total_sold)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        initial_deposit = contract.initial_token_deposit
        total_sold = contract.total_sold
        unsold_tokens = initial_deposit - total_sold
        initial_balance = contract.sale_token_balance

        # Verify unsold tokens > 0 (we only reached soft cap, not hard cap)
        self.assertGreater(unsold_tokens, 0)

        # Try unauthorized withdrawal
        unauthorized_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(token_uid=self.token_uid, amount=unsold_tokens)
            ],
            vertex=self.tx,
            caller_id=Address(self._get_any_address()[0]),
            timestamp=self.end_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", unauthorized_ctx
            )

        # Attempt invalid withdrawal amount (trying to withdraw all balance instead of just unsold)
        wrong_amount_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=initial_balance
                )
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", wrong_amount_ctx
            )

        # Successful withdrawal of ONLY unsold tokens
        correct_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(token_uid=self.token_uid, amount=unsold_tokens)
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_remaining_tokens", correct_ctx
        )

        # Verify balance decreased by unsold tokens (not zero - users can still claim)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.sale_token_balance, initial_balance - unsold_tokens)
        self.assertEqual(contract.unsold_tokens_withdrawn, True)

        # Try to withdraw again (should fail - already withdrawn)
        duplicate_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(token_uid=self.token_uid, amount=unsold_tokens)
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 200,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", duplicate_ctx
            )

        # Verify contract balances
        self._check_contract_balances()

    def test_withdraw_unsold_before_users_claim(self):
        """Test that admin can withdraw unsold tokens before users claim their allocations.

        This validates the key fix: unsold tokens (never allocated) can be withdrawn
        immediately after finalization, while users can still claim their allocated
        tokens afterward.
        """
        self._initialize_sale()

        # Create multiple participants
        num_participants = 3
        participant_addresses = []
        participant_deposits = []

        # Each participant deposits different amounts
        for i in range(num_participants):
            participant_addr, _ = self._get_any_address()
            participant_addresses.append(participant_addr)

            # Gross amounts: 500, 600, 700 HTR
            gross_amount = (500 + i * 100) * 100
            participant_deposits.append(gross_amount)

            ctx = self._create_deposit_context(gross_amount, participant_addr)
            self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify we reached soft cap
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Calculate expected amounts
        total_gross = sum(participant_deposits)
        total_participation_fees = sum(
            self._calculate_participation_fee(amt) for amt in participant_deposits
        )
        total_net = total_gross - total_participation_fees
        expected_total_sold = total_net * self.rate

        # Verify total_sold matches
        self.assertEqual(contract.total_sold, expected_total_sold)

        # Finalize the sale
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 50,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Get contract state after finalization
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)

        initial_deposit = contract.initial_token_deposit
        total_sold = contract.total_sold
        unsold_tokens = initial_deposit - total_sold
        balance_before_admin_withdraw = contract.sale_token_balance

        # Verify unsold tokens exist (hard cap was 5000 HTR, we only sold 1800 HTR worth)
        self.assertGreater(unsold_tokens, 0)
        self.assertEqual(balance_before_admin_withdraw, initial_deposit)  # Nothing claimed yet

        # Admin withdraws UNSOLD tokens BEFORE any user claims
        admin_withdraw_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(token_uid=self.token_uid, amount=unsold_tokens)
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_remaining_tokens", admin_withdraw_ctx
        )

        # Verify admin withdrawal
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.unsold_tokens_withdrawn, True)
        self.assertEqual(
            contract.sale_token_balance,
            balance_before_admin_withdraw - unsold_tokens
        )

        # Now ALL users should STILL be able to claim their tokens
        for i, participant_addr in enumerate(participant_addresses):
            # Calculate expected tokens for this participant
            gross_amount = participant_deposits[i]
            net_amount = gross_amount - self._calculate_participation_fee(gross_amount)
            expected_tokens = net_amount * self.rate

            # Get balance before claim
            contract = self.get_readonly_contract(self.contract_id)
            assert isinstance(contract, Crowdsale)
            balance_before_claim = contract.sale_token_balance

            # User claims their tokens
            claim_ctx = self.create_context(
                actions=[
                    NCWithdrawalAction(
                        token_uid=self.token_uid,
                        amount=expected_tokens
                    )
                ],
                vertex=self.tx,
                caller_id=Address(participant_addr),
                timestamp=self.end_time + 200 + i,
            )
            self.runner.call_public_method(
                self.contract_id, "claim_tokens", claim_ctx
            )

            # Verify claim succeeded
            contract = self.get_readonly_contract(self.contract_id)
            assert isinstance(contract, Crowdsale)
            self.assertEqual(contract.claimed.get(Address(participant_addr), False), True)
            self.assertEqual(
                contract.sale_token_balance,
                balance_before_claim - expected_tokens
            )

        # After all claims, balance should be zero (all sold tokens claimed, unsold already withdrawn)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.sale_token_balance, 0)

        # Verify final accounting
        self.assertEqual(contract.unsold_tokens_withdrawn, True)
        # Verify all participants have claimed
        for participant_addr in participant_addresses:
            self.assertEqual(contract.claimed.get(Address(participant_addr), False), True)

        # Verify contract balances
        self._check_contract_balances()

    def test_comprehensive_sale_lifecycle(self):
        """Test complete sale lifecycle with all new validations."""
        self._initialize_sale()

        # Multiple participants
        participants = []
        total_gross = 0
        total_net = 0
        gross_deposit = (self.soft_cap // 2) + 100_00

        # Two participants to reach success
        for _ in range(2):
            participant_addr = self._get_any_address()[0]
            participants.append((participant_addr, gross_deposit))
            ctx = self._create_deposit_context(
                gross_deposit, address=Address(participant_addr)
            )
            self.runner.call_public_method(self.contract_id, "participate", ctx)
            total_gross += gross_deposit
            total_net += self._calculate_net_amount(gross_deposit)

        # Calculate total participation fees
        total_participation_fees = total_gross - total_net

        # Verify sale reached SOFT_CAP_REACHED state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)
        self.assertEqual(contract.total_raised, total_net)  # Total NET
        self.assertEqual(
            contract.total_participation_fees_collected, total_participation_fees
        )

        # Finalize the sale to COMPLETED_SUCCESS
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 50,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify state is now COMPLETED_SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)

        # Participants claim tokens first
        for participant, _ in participants:
            participant_info = self.runner.call_view_method(
                self.contract_id, "get_participant_info", Address(participant)
            )
            tokens_due = participant_info.tokens_due
            claim_ctx = self.create_context(
                actions=[
                    NCWithdrawalAction(token_uid=self.token_uid, amount=tokens_due)
                ],
                vertex=self.tx,
                caller_id=Address(participant),
                timestamp=self.end_time + 100,
            )
            self.runner.call_public_method(self.contract_id, "claim_tokens", claim_ctx)

        # Calculate platform fee and withdrawable HTR (based on NET amount)
        platform_fee = total_net * self.platform_fee // 10000
        withdrawable_htr = total_net - platform_fee

        # Owner withdraws HTR
        owner_withdrawal_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=withdrawable_htr)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_raised_htr", owner_withdrawal_ctx
        )

        # Platform withdraws platform fees (from successful sale)
        platform_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=platform_fee)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(
                self.platform_address
            ),  # Using platform address from setUp
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_platform_fees", platform_ctx
        )

        # Platform withdraws participation fees
        participation_fee_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=total_participation_fees)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(self.platform_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_participation_fees", participation_fee_ctx
        )

        # Get remaining tokens and withdraw
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        remaining_tokens = contract.sale_token_balance
        if remaining_tokens > 0:
            token_withdrawal_ctx = self.create_context(
                actions=[
                    NCWithdrawalAction(
                        token_uid=self.token_uid, amount=remaining_tokens
                    )
                ],
                vertex=self.tx,
                caller_id=Address(self.owner_address),
                timestamp=self.end_time + 100,
            )
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", token_withdrawal_ctx
            )

        # Verify final state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.sale_token_balance, 0)
        self.assertTrue(contract.owner_withdrawn)
        self.assertTrue(contract.platform_fees_withdrawn)
        self.assertTrue(contract.participation_fees_withdrawn)

        # Verify contract balances
        self._check_contract_balances()

    def test_zero_fees(self):
        """Test crowdsale with zero participation and platform fees."""
        # Initialize with zero fees
        self._initialize_sale({"participation_fee": 0, "platform_fee": 0})

        # Participate with 100 HTR
        gross_amount = 100_00
        deposit_ctx = self._create_deposit_context(gross_amount)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)

        # With zero participation fee, net = gross
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_raised, gross_amount)  # No fee deducted
        self.assertEqual(contract.total_participation_fees_collected, 0)
        self.assertEqual(contract.htr_balance, gross_amount)

        # Test fee info view
        fee_info = self.runner.call_view_method(self.contract_id, "get_fee_info")
        self.assertEqual(fee_info["participation_fee_bp"], "0")
        self.assertEqual(fee_info["platform_fee_bp"], "0")
        self.assertEqual(fee_info["total_participation_fees_collected"], "0")

        # Verify contract balances
        self._check_contract_balances()

    def test_participation_fee_withdrawal(self):
        """Test participation fee withdrawal functionality."""
        self._initialize_sale()

        # Multiple users participate
        num_participants = 3
        gross_amount = 100_00
        for _ in range(num_participants):
            ctx = self._create_deposit_context(gross_amount)
            self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Calculate total participation fees
        total_fees = self._calculate_participation_fee(gross_amount) * num_participants

        # Verify fees collected
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_participation_fees_collected, total_fees)
        self.assertFalse(contract.participation_fees_withdrawn)

        # Platform withdraws participation fees
        withdraw_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=total_fees)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(self.platform_address),
            timestamp=self.start_time + 200,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_participation_fees", withdraw_ctx
        )

        # Verify withdrawal status
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertTrue(contract.participation_fees_withdrawn)

        # Try to withdraw again (should fail)
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_participation_fees", withdraw_ctx
            )

        # Verify contract balances
        self._check_contract_balances()

    def test_continued_participation_after_soft_cap(self):
        """Test that participation continues after soft cap is reached."""
        self._initialize_sale()

        # First participant reaches soft cap
        gross_to_soft_cap = (self.soft_cap * 10000) // (
            10000 - self.participation_fee
        ) + 1
        ctx1 = self._create_deposit_context(gross_to_soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx1)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)
        initial_raised = contract.total_raised

        # Second participant joins AFTER soft cap reached
        additional_gross = 500_00
        ctx2 = self._create_deposit_context(additional_gross)
        self.runner.call_public_method(self.contract_id, "participate", ctx2)

        # Verify participation was successful and state still SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)
        additional_net = self._calculate_net_amount(additional_gross)
        self.assertEqual(contract.total_raised, initial_raised + additional_net)
        self.assertEqual(contract.participants_count, 2)

        # Verify contract balances
        self._check_contract_balances()

    def test_hard_cap_with_margin_auto_finalize(self):
        """Test that reaching hard cap + 0.5% margin auto-finalizes to COMPLETED_SUCCESS."""
        self._initialize_sale()

        # Calculate amount to reach hard cap with margin
        # Hard cap margin is 0.5% = 50 basis points
        # hard_cap_with_margin = hard_cap + (hard_cap * 50 / 10000)
        hard_cap_with_margin = self.hard_cap + (self.hard_cap * 50 // 10000)

        # Participate with amount that reaches hard cap margin (considering participation fee)
        gross_needed = (hard_cap_with_margin * 10000) // (
            10000 - self.participation_fee
        )
        ctx = self._create_deposit_context(gross_needed)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state auto-finalized to COMPLETED_SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)
        self.assertGreaterEqual(contract.total_raised, hard_cap_with_margin)

        # Verify contract balances
        self._check_contract_balances()

    def test_participation_rejected_after_end_time(self):
        """Test that participation is rejected after end_time."""
        self._initialize_sale()

        # Participate to reach soft cap
        gross_to_soft_cap = (self.soft_cap * 10000) // (
            10000 - self.participation_fee
        ) + 1
        ctx = self._create_deposit_context(
            gross_to_soft_cap, timestamp=self.start_time + 100
        )
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Try to participate after end_time - should be rejected
        late_ctx = self._create_deposit_context(100_00, timestamp=self.end_time + 100)
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.contract_id, "participate", late_ctx)

        # Manual finalization required to transition to COMPLETED_SUCCESS
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify state is now COMPLETED_SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)

        # Verify contract balances
        self._check_contract_balances()

    def test_participation_rejected_after_end_time_without_soft_cap(self):
        """Test that participation is rejected after end_time when soft cap not reached."""
        self._initialize_sale()

        # Participate below soft cap
        gross_amount = self.soft_cap // 3
        ctx = self._create_deposit_context(
            gross_amount, timestamp=self.start_time + 100
        )
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state is ACTIVE
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)
        self.assertLess(contract.total_raised, self.soft_cap)

        # Try to participate after end_time - should be rejected
        late_ctx = self._create_deposit_context(100_00, timestamp=self.end_time + 100)
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.contract_id, "participate", late_ctx)

        # Manual finalization required to transition to COMPLETED_FAILED
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify state is now COMPLETED_FAILED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_FAILED)

        # Verify contract balances
        self._check_contract_balances()

    def test_withdrawals_blocked_in_soft_cap_reached(self):
        """Test that withdrawals are blocked in SOFT_CAP_REACHED state."""
        self._initialize_sale()

        # Reach soft cap
        gross_to_soft_cap = (self.soft_cap * 10000) // (
            10000 - self.participation_fee
        ) + 1
        participant_ctx = self._create_deposit_context(gross_to_soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", participant_ctx)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Try to claim tokens - should fail (not COMPLETED_SUCCESS yet)
        net_amount = self._calculate_net_amount(gross_to_soft_cap)
        tokens_due = net_amount * self.rate
        claim_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=tokens_due)],
            vertex=self.tx,
            caller_id=Address(participant_ctx.caller_id),
            timestamp=self.start_time + 200,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.contract_id, "claim_tokens", claim_ctx)

        # Try to withdraw raised HTR as owner - should fail
        platform_fee = self._calculate_platform_fee(net_amount)
        withdrawable = net_amount - platform_fee
        withdraw_htr_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=withdrawable)],  # type: ignore
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time + 200,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_raised_htr", withdraw_htr_ctx
            )

        # Verify contract balances
        self._check_contract_balances()

    def test_pause_unpause_in_soft_cap_reached(self):
        """Test pause/unpause functionality in SOFT_CAP_REACHED state."""
        self._initialize_sale()

        # Reach soft cap
        gross_to_soft_cap = (self.soft_cap * 10000) // (
            10000 - self.participation_fee
        ) + 1
        ctx = self._create_deposit_context(gross_to_soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Pause the sale
        pause_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time + 200,
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)

        # Verify paused
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.PAUSED)

        # Unpause - should return to SOFT_CAP_REACHED (not ACTIVE)
        self.runner.call_public_method(self.contract_id, "unpause", pause_ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Verify contract balances
        self._check_contract_balances()

    def test_finalize_from_soft_cap_reached(self):
        """Test manual finalization from SOFT_CAP_REACHED state."""
        self._initialize_sale()

        # Reach soft cap
        gross_to_soft_cap = (self.soft_cap * 10000) // (
            10000 - self.participation_fee
        ) + 1
        ctx = self._create_deposit_context(gross_to_soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state is SOFT_CAP_REACHED
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Manually finalize
        finalize_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.start_time + 500,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Verify transitioned to COMPLETED_SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.COMPLETED_SUCCESS)

        # Verify contract balances
        self._check_contract_balances()

    def test_hard_cap_boundary(self):
        """Test that hard cap (without margin) is still enforced."""
        self._initialize_sale()

        # Try to participate with amount that exceeds hard cap (without margin)
        gross_over_hard_cap = (self.hard_cap * 10000) // (
            10000 - self.participation_fee
        ) + 500_00
        ctx = self._create_deposit_context(gross_over_hard_cap)

        # Should fail because it exceeds hard cap
        with self.assertRaises(NCFail) as cm:
            self.runner.call_public_method(self.contract_id, "participate", ctx)
        self.assertEqual(str(cm.exception), CrowdsaleErrors.ABOVE_MAX)

        # Verify contract balances
        self._check_contract_balances()

    def test_multiple_participations_reaching_states(self):
        """Test multiple small participations gradually reaching soft cap and beyond."""
        self._initialize_sale()

        # Multiple small deposits
        num_deposits = 15
        gross_each = 100_00

        for i in range(num_deposits):
            ctx = self._create_deposit_context(gross_each)
            self.runner.call_public_method(self.contract_id, "participate", ctx)

            contract = self.get_readonly_contract(self.contract_id)
            assert isinstance(contract, Crowdsale)

            # Check state transitions appropriately
            if contract.total_raised < self.soft_cap:
                self.assertEqual(contract.state, SaleState.ACTIVE)
            else:
                self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)

        # Verify final state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SOFT_CAP_REACHED)
        self.assertEqual(contract.participants_count, num_deposits)

        # Verify contract balances
        self._check_contract_balances()

    def test_hard_cap_exact_value_auto_finalize(self):
        """Test that reaching exactly the hard cap (not margin) auto-finalizes to COMPLETED_SUCCESS."""
        self._initialize_sale()

        # Calculate amount to reach EXACTLY the hard cap (not the margin)
        # We need to deposit enough so that the NET amount equals hard_cap
        gross_needed = (self.hard_cap * 10000) // (
            10000 - self.participation_fee
        )
        ctx = self._create_deposit_context(gross_needed)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify the net amount reached is at or very close to hard cap
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        net_amount = self._calculate_net_amount(gross_needed)

        # Check that we're at hard cap (or very close due to integer division)
        self.assertGreaterEqual(contract.total_raised, self.hard_cap)

        # BUG: This should be COMPLETED_SUCCESS when reaching hard cap
        # but currently it only auto-finalizes when reaching hard_cap + margin
        print(f"Hard cap: {self.hard_cap}")
        print(f"Total raised: {contract.total_raised}")
        print(f"State: {contract.state}")
        print(f"Expected state: {SaleState.COMPLETED_SUCCESS}")

        # This test will FAIL with current implementation
        # because it only auto-finalizes at hard_cap + 0.5% margin
        self.assertEqual(
            contract.state,
            SaleState.COMPLETED_SUCCESS,
            f"Sale should auto-finalize when reaching hard cap. State is {contract.state}"
        )

        # Verify contract balances
        self._check_contract_balances()
