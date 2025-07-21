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
        self.blueprint_id = self.gen_random_blueprint_id()
        self.register_blueprint_class(self.blueprint_id, Crowdsale)

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

        # Create context with token deposit action
        init_ctx = Context(
            actions=[NCDepositAction(token_uid=token_uid, amount=hard_cap * rate)],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=start_time - 100,
        )

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
        )

        if activate:
            activate_ctx = Context(
                actions=[],
                vertex=self.tx,
                address=Address(self.owner_address),
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
        

        return Context(
            actions=[NCDepositAction(token_uid=HTR_UID, amount=amount)], # type: ignore
            vertex=self.tx,
            address=Address(address),
            timestamp=timestamp,
        )

    def _calculate_platform_fee(self, amount: int) -> int:
        """Helper to calculate platform fee."""
        return amount * self.platform_fee // 10000

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

        # Test invalid platform fee
        with self.assertRaises(NCFail):
            self._initialize_sale({"platform_fee": 50})  # Below minimum

    def test_initialize_invalid_token_deposit(self):
        """Test initialization with insufficient or invalid token deposit."""
        # Test insufficient tokens
        insufficient_tokens = self.hard_cap * self.rate - 1
        ctx = Context(
            actions=[
                NCDepositAction(token_uid=self.token_uid, amount=insufficient_tokens)
            ],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.start_time - 100,
        )

        with self.assertRaises(NCFail):
            self.runner.create_contract(
                self.contract_id,
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
            )

        # Test wrong token
        wrong_token = self.gen_random_token_uid()
        ctx = Context(
            actions=[
                NCDepositAction(token_uid=wrong_token, amount=self.hard_cap * self.rate)
            ],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.start_time - 100,
        )

        with self.assertRaises(NCFail):
            self.runner.create_contract(
                self.contract_id,
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
        activate_ctx = Context(
            actions=[],
            vertex=self.tx,
            address=Address(self.owner_address),
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
        self.assertEqual(contract.total_raised, deposit_amount)

        # Test pause -> PAUSED
        pause_ctx = Context(
            actions=[],
            vertex=self.tx,
            address=Address(self.owner_address),
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

        # Test reaching soft cap -> SUCCESS
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        remaining = self.soft_cap - contract.total_raised
        if remaining > 0:
            ctx = self._create_deposit_context(remaining)
            self.runner.call_public_method(self.contract_id, "participate", ctx)
            contract = self.get_readonly_contract(self.contract_id)
            assert isinstance(contract, Crowdsale)
            self.assertEqual(contract.state, SaleState.SUCCESS)

        # Verify contract balances
        self._check_contract_balances()

    def test_participate(self):
        """Test basic participation functionality."""
        # Initialize and activate sale
        self._initialize_sale(activate=True)
        deposit_amount = 100_00

        # Create participation context
        ctx = self._create_deposit_context(deposit_amount)

        # Verify sale is in active state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.ACTIVE)

        # Participate in sale
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state changes
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_raised, deposit_amount)
        self.assertEqual(contract.total_sold, deposit_amount * self.rate)
        self.assertEqual(contract.participants_count, 1)

        # Verify contract balances
        self._check_contract_balances()

    def test_participate_multiple_users(self):
        """Test participation from multiple users."""
        self._initialize_sale()
        num_users = 5
        deposit_amount = 100_00

        for _ in range(num_users):
            ctx = self._create_deposit_context(deposit_amount)
            self.runner.call_public_method(self.contract_id, "participate", ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_raised, deposit_amount * num_users)
        self.assertEqual(contract.participants_count, num_users)

        # Verify contract balances
        self._check_contract_balances()

    def test_soft_cap_reached(self):
        """Test sale state transition when soft cap is reached."""
        self._initialize_sale()

        # Deposit enough to reach soft cap
        ctx = self._create_deposit_context(self.soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SUCCESS)
        # Verify contract balances
        self._check_contract_balances()

    def test_claim_tokens(self):
        """Test token claiming after successful sale."""
        self._initialize_sale()

        # Reach soft cap
        deposit_ctx = self._create_deposit_context(self.soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)

        # Calculate tokens due
        tokens_due = self.soft_cap * self.rate

        # Attempt to claim tokens
        claim_ctx = Context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=tokens_due)],
            vertex=self.tx,
            address=deposit_ctx.address,
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "claim_tokens", claim_ctx)

        # Verify claim status
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", deposit_ctx.address
        )
        self.assertTrue(participant_info.has_claimed)
        self.assertEqual(participant_info.tokens_due, 0)
        # Verify contract balances
        self._check_contract_balances()

    def test_claim_refund(self):
        """Test refund claiming after failed sale."""
        self._initialize_sale()
        deposit_amount = self.soft_cap // 2  # Below soft cap

        # Make deposit
        deposit_ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)

        # Force sale to failed state
        finalize_ctx = Context(
            actions=[],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Claim refund
        refund_ctx = Context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=deposit_amount)], # type: ignore
            vertex=self.tx,
            address=deposit_ctx.address,
            timestamp=self.end_time + 200,
        )
        self.runner.call_public_method(self.contract_id, "claim_refund", refund_ctx)

        # Verify refund status
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", deposit_ctx.address
        )
        self.assertTrue(participant_info.has_claimed)
        self.assertEqual(participant_info.deposited, 0)
        # Verify contract balances
        self._check_contract_balances()

    def test_owner_functions(self):
        """Test owner-only functions."""
        self._initialize_sale()

        # Test pause/unpause
        pause_ctx = Context(
            actions=[],
            vertex=self.tx,
            address=Address(self.owner_address),
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
        unauthorized_ctx = Context(
            actions=[],
            vertex=self.tx,
            address=Address(self._get_any_address()[0]),
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
        activate_ctx = Context(
            actions=[],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.start_time - 1,  # Just after start time
        )
        self.runner.call_public_method(self.contract_id, "early_activate", activate_ctx)
        # Now participation should work
        ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify participation succeeded
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.total_raised, deposit_amount)

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
        self.assertEqual(sale_info.total_raised, deposit_amount)
        self.assertEqual(sale_info.participants, 1)

        # Test get_participant_info
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", ctx.address
        )
        self.assertEqual(participant_info.deposited, deposit_amount)
        self.assertEqual(participant_info.tokens_due, deposit_amount * self.rate)

        # Test get_sale_progress
        progress = self.runner.call_view_method(self.contract_id, "get_sale_progress")
        expected_percent = (deposit_amount * 100) // self.hard_cap
        self.assertEqual(progress.percent_filled, expected_percent)

    def test_withdraw_remaining_tokens(self):
        """Test withdrawal of remaining tokens after successful sale."""
        self._initialize_sale()

        # Reach soft cap
        ctx_deposit = self._create_deposit_context(self.soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx_deposit)

        # Try to withdraw before SUCCESS state
        ctx_withdraw = Context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=1000)],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.start_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", ctx_withdraw
            )

        # Get initial token balance
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        initial_balance = contract.sale_token_balance

        # Try unauthorized withdrawal
        unauthorized_ctx = Context(
            actions=[
                NCWithdrawalAction(token_uid=self.token_uid, amount=initial_balance)
            ],
            vertex=self.tx,
            address=Address(self._get_any_address()[0]),
            timestamp=self.end_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", unauthorized_ctx
            )

        # Attempt invalid withdrawal amount
        wrong_amount_ctx = Context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=initial_balance - 100
                )
            ],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.contract_id, "withdraw_remaining_tokens", wrong_amount_ctx
            )

        # Successful withdrawal
        correct_ctx = Context(
            actions=[
                NCWithdrawalAction(token_uid=self.token_uid, amount=initial_balance)
            ],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_remaining_tokens", correct_ctx
        )

        # Verify balance is zero
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.sale_token_balance, 0)

        # Verify contract balances
        self._check_contract_balances()

    def test_comprehensive_sale_lifecycle(self):
        """Test complete sale lifecycle with all new validations."""
        self._initialize_sale()

        # Multiple participants
        participants = []
        total_raised = 0
        individual_deposit = (self.soft_cap // 2) + 100_00

        # Two participants to reach success
        for _ in range(2):
            participant_addr = self._get_any_address()[0]
            participants.append(participant_addr)
            ctx = self._create_deposit_context(
                individual_deposit, address=participant_addr
            )
            self.runner.call_public_method(self.contract_id, "participate", ctx)
            total_raised += individual_deposit

        # Verify sale reached SUCCESS state
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        self.assertEqual(contract.state, SaleState.SUCCESS)
        self.assertEqual(contract.total_raised, total_raised)

        # Participants claim tokens first
        for participant in participants:
            participant_info = self.runner.call_view_method(
                self.contract_id, "get_participant_info", participant
            )
            tokens_due = participant_info.tokens_due
            claim_ctx = Context(
                actions=[
                    NCWithdrawalAction(token_uid=self.token_uid, amount=tokens_due)
                ],
                vertex=self.tx,
                address=participant,
                timestamp=self.end_time + 100,
            )
            self.runner.call_public_method(self.contract_id, "claim_tokens", claim_ctx)

        # Calculate platform fee and withdrawable HTR
        platform_fee = total_raised * self.platform_fee // 10000
        withdrawable_htr = total_raised - platform_fee

        # Owner withdraws HTR
        owner_withdrawal_ctx = Context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=withdrawable_htr)], # type: ignore
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_raised_htr", owner_withdrawal_ctx
        )

        # Platform withdraws fees
        platform_ctx = Context(
            actions=[NCWithdrawalAction(token_uid=HTR_UID, amount=platform_fee)], # type: ignore
            vertex=self.tx,
            address=Address(self.platform_address),  # Using platform address from setUp
            timestamp=self.end_time + 100,
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_platform_fees", platform_ctx
        )

        # Get remaining tokens and withdraw
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Crowdsale)
        remaining_tokens = contract.sale_token_balance
        if remaining_tokens > 0:
            token_withdrawal_ctx = Context(
                actions=[
                    NCWithdrawalAction(
                        token_uid=self.token_uid, amount=remaining_tokens
                    )
                ],
                vertex=self.tx,
                address=Address(self.owner_address),
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

        # Verify contract balances
        self._check_contract_balances()
