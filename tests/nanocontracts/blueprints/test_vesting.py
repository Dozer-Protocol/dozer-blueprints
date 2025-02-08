import os
from typing import Any
from hathor.conf.get_settings import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

from hathor.nanocontracts.blueprints.vesting import (
    Vesting,
    AllocationNotConfigured,
    InsufficientAvailableBalance,
    InvalidIndex,
    CustomNameRequired,
    InvalidTokenDeposit,
    NoAllocation,
    InvalidTimelock,
    InsufficientVestedAmount,
    InvalidBeneficiary,
    MAX_ALLOCATIONS,
    MONTH_IN_SECONDS,
    PRECISION,
)

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class VestingTestCase(BlueprintTestCase):
    """Test suite for the Vesting blueprint contract."""

    def setUp(self):
        super().setUp()

        # Set up contract
        self.contract_id = self.gen_random_nanocontract_id()
        self.runner.register_contract(Vesting, self.contract_id)
        self.storage = self.runner.get_storage(self.contract_id)

        # Generate test tokens and addresses
        self.token_uid = self.gen_random_token_uid()
        self.admin_address, self.admin_key = self._get_any_address()

        # Set up base transaction for contexts
        self.tx = self.get_genesis_tx()

        # Constants
        self.initial_deposit = 1_000_000_00
        self.month_in_seconds = MONTH_IN_SECONDS

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair."""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _initialize_contract(self, amount: int | None = None) -> None:
        """Initialize contract with token deposit."""
        if amount is None:
            amount = self.initial_deposit

        ctx = Context(
            [NCAction(NCActionType.DEPOSIT, self.token_uid, amount)],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(
            self.contract_id, "initialize", ctx, self.token_uid
        )

    def _configure_vesting(
        self,
        index: int,
        amount: int,
        cliff_months: int = 6,
        vesting_months: int = 24,
        beneficiary: bytes | None = None,
        custom_name: str | None = None,
    ) -> bytes:
        """Configure a vesting allocation."""
        if beneficiary is None:
            beneficiary = self._get_any_address()[0]

        ctx = Context([], self.tx, self.admin_address, timestamp=self.clock.seconds())

        self.runner.call_public_method(
            self.contract_id,
            "configure_vesting",
            ctx,
            index,
            amount,
            beneficiary,
            cliff_months,
            vesting_months,
            custom_name,
        )

        return beneficiary

    def test_initialize(self):
        """Test contract initialization."""
        # Test initialization with wrong token first
        wrong_token = self.gen_random_token_uid()
        ctx = Context(
            [NCAction(NCActionType.DEPOSIT, wrong_token, self.initial_deposit)],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

        with self.assertRaises(InvalidTokenDeposit):
            self.runner.call_public_method(
                self.contract_id, "initialize", ctx, self.token_uid
            )

        # Then test valid initialization
        self._initialize_contract()

        # Verify initial state
        self.assertEqual(self.storage.get("admin"), self.admin_address)
        self.assertEqual(self.storage.get("token_uid"), self.token_uid)
        self.assertEqual(self.storage.get("available_balance"), self.initial_deposit)
        self.assertEqual(self.storage.get("total_allocated"), 0)
        self.assertEqual(self.storage.get("is_started"), False)

    def test_configure_vesting(self):
        """Test vesting configuration."""
        self._initialize_contract()

        # Test basic configuration
        amount = 100_000_00
        beneficiary = self._configure_vesting(0, amount)

        # Verify configuration
        info = self.runner.call_view_method(
            self.contract_id, "get_vesting_info", 0, self.clock.seconds()
        )

        self.assertEqual(info["beneficiary"], beneficiary)
        self.assertEqual(info["amount"], amount)
        self.assertEqual(info["withdrawn"], 0)
        self.assertEqual(info["vested"], 0)

        # Test custom name requirement
        with self.assertRaises(CustomNameRequired):
            self._configure_vesting(7, amount)  # Index 7+ requires custom name

        # Test insufficient balance
        with self.assertRaises(InsufficientAvailableBalance):
            self._configure_vesting(1, self.initial_deposit + 1)

    def test_start_vesting(self):
        """Test vesting schedule start."""
        self._initialize_contract()
        amount = 100_000_00
        beneficiary = self._configure_vesting(0, amount)

        start_time = self.clock.seconds()
        ctx = Context([], self.tx, self.admin_address, timestamp=start_time)
        self.runner.call_public_method(self.contract_id, "start_vesting", ctx)

        # Verify started state
        self.assertTrue(self.storage.get("is_started"))
        self.assertEqual(self.storage.get("vesting_start"), start_time)

        # Test cannot configure after start
        with self.assertRaises(NCFail):
            self._configure_vesting(1, amount)

    def test_claim_allocation(self):
        """Test token claiming process."""
        self._initialize_contract()
        amount = 100_000_00
        cliff_months = 6
        vesting_months = 12
        beneficiary = self._configure_vesting(0, amount, cliff_months, vesting_months)

        # Start vesting
        start_time = self.clock.seconds()
        ctx = Context([], self.tx, self.admin_address, timestamp=start_time)
        self.runner.call_public_method(self.contract_id, "start_vesting", ctx)

        # Try claiming before cliff (should fail)
        early_claim_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, self.token_uid, 1)],
            self.tx,
            beneficiary,
            timestamp=start_time + (cliff_months * self.month_in_seconds) - 1,
        )
        with self.assertRaises(InsufficientVestedAmount):
            self.runner.call_public_method(
                self.contract_id, "claim_allocation", early_claim_ctx, 0
            )

        # Claim one month after cliff
        after_cliff = start_time + ((cliff_months + 1) * self.month_in_seconds)
        monthly_vesting = amount // vesting_months
        claim_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, self.token_uid, monthly_vesting)],
            self.tx,
            beneficiary,
            timestamp=after_cliff,
        )
        self.runner.call_public_method(
            self.contract_id, "claim_allocation", claim_ctx, 0
        )

        # Verify withdrawal
        info = self.runner.call_view_method(
            self.contract_id, "get_vesting_info", 0, after_cliff
        )
        self.assertEqual(info["withdrawn"], monthly_vesting)

    def test_change_beneficiary(self):
        """Test beneficiary change functionality."""
        self._initialize_contract()
        amount = 100_000_00
        old_beneficiary = self._configure_vesting(0, amount)
        new_beneficiary = self._get_any_address()[0]

        # Change beneficiary
        ctx = Context([], self.tx, old_beneficiary, timestamp=self.clock.seconds())
        self.runner.call_public_method(
            self.contract_id, "change_beneficiary", ctx, 0, new_beneficiary
        )

        # Verify change
        info = self.runner.call_view_method(
            self.contract_id, "get_vesting_info", 0, self.clock.seconds()
        )
        self.assertEqual(info["beneficiary"], new_beneficiary)

        # Test unauthorized change
        unauthorized_ctx = Context(
            [], self.tx, self._get_any_address()[0], timestamp=self.clock.seconds()
        )
        with self.assertRaises(InvalidBeneficiary):
            self.runner.call_public_method(
                self.contract_id,
                "change_beneficiary",
                unauthorized_ctx,
                0,
                new_beneficiary,
            )

    def test_deposit_withdraw_available(self):
        """Test deposit and withdrawal of available tokens."""
        self._initialize_contract()

        # Test deposit
        deposit_amount = 50_000_00
        deposit_ctx = Context(
            [NCAction(NCActionType.DEPOSIT, self.token_uid, deposit_amount)],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "deposit_tokens", deposit_ctx)

        self.assertEqual(
            self.storage.get("available_balance"), self.initial_deposit + deposit_amount
        )

        # Test withdrawal
        withdraw_amount = 20_000_00
        withdraw_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, self.token_uid, withdraw_amount)],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(
            self.contract_id, "withdraw_available", withdraw_ctx
        )

        self.assertEqual(
            self.storage.get("available_balance"),
            self.initial_deposit + deposit_amount - withdraw_amount,
        )

    def test_vesting_schedule(self):
        """Test vesting schedule calculations."""
        self._initialize_contract()
        amount = 120_000_00
        cliff_months = 6
        vesting_months = 12
        beneficiary = self._configure_vesting(0, amount, cliff_months, vesting_months)

        # Start vesting
        start_time = self.clock.seconds()
        ctx = Context([], self.tx, self.admin_address, timestamp=start_time)
        self.runner.call_public_method(self.contract_id, "start_vesting", ctx)

        # Check vesting at different times
        check_points = [
            (3, 0),  # During cliff
            (6, 0),  # At cliff start
            (7, amount // 12),  # One month after cliff
            (12, amount * 6 // 12),  # Mid vesting
            (18, amount),  # Full vesting
            (24, amount),  # After vesting
        ]

        for months, expected_vested in check_points:
            timestamp = start_time + (months * self.month_in_seconds)
            info = self.runner.call_view_method(
                self.contract_id, "get_vesting_info", 0, timestamp
            )
            self.assertEqual(
                info["vested"], expected_vested, f"Incorrect vesting at {months} months"
            )

    def test_multiple_allocations(self):
        """Test multiple vesting allocations."""
        self._initialize_contract()
        allocations = [
            (0, 100_000_00, 6, 24, "Team"),
            (1, 200_000_00, 12, 36, "Treasury"),
            (2, 150_000_00, 3, 12, "Advisors"),
        ]

        # Configure allocations
        beneficiaries = []
        for index, amount, cliff, duration, _ in allocations:
            beneficiary = self._configure_vesting(index, amount, cliff, duration)
            beneficiaries.append(beneficiary)

        # Start vesting
        start_time = self.clock.seconds()
        ctx = Context([], self.tx, self.admin_address, timestamp=start_time)
        self.runner.call_public_method(self.contract_id, "start_vesting", ctx)

        # Test each allocation one month after its cliff
        for i, (index, amount, cliff, duration, _) in enumerate(allocations):
            claim_time = start_time + ((cliff + 1) * self.month_in_seconds)
            expected_vested = amount // duration

            info = self.runner.call_view_method(
                self.contract_id, "get_vesting_info", index, claim_time
            )
            self.assertEqual(info["vested"], expected_vested)

            # Test claiming
            claim_ctx = Context(
                [NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_vested)],
                self.tx,
                beneficiaries[i],
                timestamp=claim_time,
            )
            self.runner.call_public_method(
                self.contract_id, "claim_allocation", claim_ctx, index
            )

    def test_admin_claim_allocation(self):
        """Test admin claiming on behalf of beneficiary."""
        self._initialize_contract()
        amount = 120_000_00
        cliff_months = 6
        vesting_months = 12
        beneficiary = self._configure_vesting(0, amount, cliff_months, vesting_months)

        # Start vesting
        start_time = self.clock.seconds()
        ctx = Context([], self.tx, self.admin_address, timestamp=start_time)
        self.runner.call_public_method(self.contract_id, "start_vesting", ctx)

        # Move time to after cliff
        claim_time = start_time + ((cliff_months + 1) * self.month_in_seconds)
        expected_vested = amount // vesting_months

        # Admin claims on behalf of beneficiary
        admin_claim_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_vested)],
            self.tx,
            self.admin_address,
            timestamp=claim_time,
        )

        self.runner.call_public_method(
            self.contract_id, "admin_claim_allocation", admin_claim_ctx, 0, beneficiary
        )

        # Verify withdrawal was successful
        info = self.runner.call_view_method(
            self.contract_id, "get_vesting_info", 0, claim_time
        )
        self.assertEqual(info["withdrawn"], expected_vested)

        # Test claiming with wrong beneficiary address
        wrong_beneficiary = self._get_any_address()[0]
        wrong_claim_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_vested)],
            self.tx,
            self.admin_address,
            timestamp=claim_time,
        )

        with self.assertRaises(InvalidBeneficiary):
            self.runner.call_public_method(
                self.contract_id,
                "admin_claim_allocation",
                wrong_claim_ctx,
                0,
                wrong_beneficiary,
            )
