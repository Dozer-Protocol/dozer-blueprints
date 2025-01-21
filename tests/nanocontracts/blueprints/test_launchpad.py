from hathor.conf.get_settings import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
import os
import time

from hathor.nanocontracts.blueprints.launchpad import (
    Launchpad,
    SaleState,
    LaunchpadErrors,
)

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class LaunchpadTestCase(BlueprintTestCase):
    """Test suite for the Launchpad blueprint contract."""

    def setUp(self):
        super().setUp()

        # Set up contract
        self.contract_id = self.gen_random_nanocontract_id()
        self.runner.register_contract(Launchpad, self.contract_id)
        self.storage = self.runner.get_storage(self.contract_id)

        # Generate test tokens and addresses
        self.token_uid = self.gen_random_token_uid()
        self.owner_address, self.owner_key = self._get_any_address()
        self.platform_address, _ = self._get_any_address()

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

    def _initialize_sale(self, params: dict = None, activate: bool = True) -> None:
        """Initialize sale with default or custom parameters."""
        if params is None:
            params = {}

        ctx = Context([], self.tx, self.owner_address, timestamp=self.start_time - 100)

        self.runner.call_public_method(
            self.contract_id,
            "initialize",
            ctx,
            params.get("token_uid", self.token_uid),
            params.get("rate", self.rate),
            params.get("soft_cap", self.soft_cap),
            params.get("hard_cap", self.hard_cap),
            params.get("min_deposit", self.min_deposit),
            params.get("start_time", self.start_time),
            params.get("end_time", self.end_time),
            params.get("platform_fee", self.platform_fee),
        )

        if activate:
            activate_ctx = Context(
                [],
                self.tx,
                self.owner_address,
                timestamp=self.start_time - 1,  # Just after start time
            )
            self.runner.call_public_method(
                self.contract_id, "early_activate", activate_ctx
            )

    def _create_deposit_context(
        self, amount: int, address: bytes = None, timestamp: int = None
    ) -> Context:
        """Create a context for HTR deposits."""
        if address is None:
            address = self._get_any_address()[0]
        if timestamp is None:
            timestamp = self.start_time + 100

        return Context(
            [NCAction(NCActionType.DEPOSIT, HTR_UID, amount)],
            self.tx,
            address,
            timestamp=timestamp,
        )

    def test_initialize(self):
        """Test contract initialization with valid parameters."""
        self._initialize_sale(activate=False)  # Don't activate for initialization test

        # Verify initial state
        self.assertEqual(self.storage.get("token_uid"), self.token_uid)
        self.assertEqual(self.storage.get("rate"), self.rate)
        self.assertEqual(self.storage.get("soft_cap"), self.soft_cap)
        self.assertEqual(self.storage.get("hard_cap"), self.hard_cap)
        self.assertEqual(self.storage.get("state"), SaleState.PENDING)
        self.assertEqual(self.storage.get("total_raised"), 0)
        self.assertEqual(self.storage.get("owner"), self.owner_address)

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

    def test_state_transitions(self):
        """Test state transitions in the sale lifecycle."""
        # Initialize sale
        self._initialize_sale(activate=False)

        # Verify initial pending state
        self.assertEqual(self.storage.get("state"), SaleState.PENDING)

        # Try participation during PENDING state - should fail
        with self.assertRaises(NCFail) as cm:
            deposit_amount = 100_00
            ctx = self._create_deposit_context(
                deposit_amount, timestamp=self.start_time - 1
            )
            self.runner.call_public_method(self.contract_id, "participate", ctx)
        self.assertEqual(str(cm.exception), LaunchpadErrors.INVALID_STATE)

        # Activate the sale
        activate_ctx = Context(
            [], self.tx, self.owner_address, timestamp=self.start_time - 1
        )
        self.runner.call_public_method(self.contract_id, "early_activate", activate_ctx)
        self.assertEqual(self.storage.get("state"), SaleState.ACTIVE)

        # Test participation works in ACTIVE state
        deposit_ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", deposit_ctx)
        self.assertEqual(self.storage.get("total_raised"), deposit_amount)

        # Test pause -> PAUSED
        pause_ctx = Context(
            [], self.tx, self.owner_address, timestamp=self.start_time + 2
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)
        self.assertEqual(self.storage.get("state"), SaleState.PAUSED)

        # Test unpause -> ACTIVE
        self.runner.call_public_method(self.contract_id, "unpause", pause_ctx)
        self.assertEqual(self.storage.get("state"), SaleState.ACTIVE)

        # Test reaching soft cap -> SUCCESS
        remaining = self.soft_cap - self.storage.get("total_raised")
        if remaining > 0:
            ctx = self._create_deposit_context(remaining)
            self.runner.call_public_method(self.contract_id, "participate", ctx)
            self.assertEqual(self.storage.get("state"), SaleState.SUCCESS)

    def test_participate(self):
        """Test basic participation functionality."""
        # Initialize and activate sale
        self._initialize_sale(activate=True)
        deposit_amount = 100_00

        # Create participation context
        ctx = self._create_deposit_context(deposit_amount)

        # Verify sale is in active state
        self.assertEqual(self.storage.get("state"), SaleState.ACTIVE)

        # Participate in sale
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify state changes
        self.assertEqual(self.storage.get("total_raised"), deposit_amount)
        self.assertEqual(self.storage.get("total_sold"), deposit_amount * self.rate)
        self.assertEqual(self.storage.get("participants_count"), 1)

    def test_participate_multiple_users(self):
        """Test participation from multiple users."""
        self._initialize_sale()
        num_users = 5
        deposit_amount = 100_00

        for _ in range(num_users):
            ctx = self._create_deposit_context(deposit_amount)
            self.runner.call_public_method(self.contract_id, "participate", ctx)

        self.assertEqual(self.storage.get("total_raised"), deposit_amount * num_users)
        self.assertEqual(self.storage.get("participants_count"), num_users)

    def test_soft_cap_reached(self):
        """Test sale state transition when soft cap is reached."""
        self._initialize_sale()

        # Deposit enough to reach soft cap
        ctx = self._create_deposit_context(self.soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        self.assertEqual(self.storage.get("state"), SaleState.SUCCESS)

    def test_claim_tokens(self):
        """Test token claiming after successful sale."""
        self._initialize_sale()

        # Reach soft cap
        ctx = self._create_deposit_context(self.soft_cap)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Attempt to claim tokens
        claim_ctx = Context([], self.tx, ctx.address, timestamp=self.end_time + 100)
        self.runner.call_public_method(self.contract_id, "claim_tokens", claim_ctx)

        # Verify claim status
        # self.assertTrue(self.storage.get_path(["claimed", ctx.address]))

    def test_claim_refund(self):
        """Test refund claiming after failed sale."""
        self._initialize_sale()
        deposit_amount = 100_00

        # Make deposit below soft cap
        ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Force sale to failed state
        finalize_ctx = Context(
            [], self.tx, self.owner_address, timestamp=self.end_time + 100
        )
        self.runner.call_public_method(self.contract_id, "finalize", finalize_ctx)

        # Claim refund
        refund_ctx = Context([], self.tx, ctx.address, timestamp=self.end_time + 200)
        self.runner.call_public_method(self.contract_id, "claim_refund", refund_ctx)

        # Verify refund status
        # self.assertTrue(self.storage.get_path(["claimed", ctx.address]))

    def test_owner_functions(self):
        """Test owner-only functions."""
        self._initialize_sale()

        # Test pause/unpause
        pause_ctx = Context(
            [], self.tx, self.owner_address, timestamp=self.start_time + 100
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)
        self.assertEqual(self.storage.get("state"), SaleState.PAUSED)

        self.runner.call_public_method(self.contract_id, "unpause", pause_ctx)
        self.assertEqual(self.storage.get("state"), SaleState.ACTIVE)

        # Test unauthorized access
        unauthorized_ctx = Context(
            [], self.tx, self._get_any_address()[0], timestamp=self.start_time + 100
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.contract_id, "pause", unauthorized_ctx)

    def test_sale_state_transitions(self):
        """Test sale state transitions and validations."""
        # Initialize without activating
        self._initialize_sale(activate=False)

        # Verify initial pending state
        self.assertEqual(self.storage.get("state"), SaleState.PENDING)

        # Try to participate while pending - should fail
        deposit_amount = 100_00
        ctx = self._create_deposit_context(
            deposit_amount, timestamp=self.start_time - 1
        )
        with self.assertRaises(NCFail) as cm:
            self.runner.call_public_method(self.contract_id, "participate", ctx)
        self.assertEqual(str(cm.exception), LaunchpadErrors.INVALID_STATE)

        # Activate sale
        activate_ctx = Context(
            [],
            self.tx,
            self.owner_address,
            timestamp=self.start_time - 1,  # Just after start time
        )
        self.runner.call_public_method(self.contract_id, "early_activate", activate_ctx)
        # Now participation should work
        ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Verify participation succeeded
        self.assertEqual(self.storage.get("total_raised"), deposit_amount)

    def test_view_functions(self):
        """Test view functions return correct information."""
        self._initialize_sale()
        deposit_amount = 100_00

        # Make a deposit
        ctx = self._create_deposit_context(deposit_amount)
        self.runner.call_public_method(self.contract_id, "participate", ctx)

        # Test get_sale_info
        sale_info = self.runner.call_view_method(self.contract_id, "get_sale_info")
        self.assertEqual(sale_info["total_raised"], deposit_amount)
        self.assertEqual(sale_info["participants"], 1)

        # Test get_participant_info
        participant_info = self.runner.call_view_method(
            self.contract_id, "get_participant_info", ctx.address
        )
        self.assertEqual(participant_info["deposited"], deposit_amount)
        self.assertEqual(participant_info["tokens_due"], deposit_amount * self.rate)

        # Test get_sale_progress
        progress = self.runner.call_view_method(self.contract_id, "get_sale_progress")
        expected_percent = (deposit_amount * 100) // self.hard_cap
        self.assertEqual(progress["percent_filled"], expected_percent)
