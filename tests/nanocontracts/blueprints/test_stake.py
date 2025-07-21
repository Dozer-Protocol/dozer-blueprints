import os
from hathor.conf.get_settings import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import NCDepositAction, NCWithdrawalAction, Address, Amount, TokenUid
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

from hathor.nanocontracts.blueprints.stake import (
    Stake,
    InvalidAmount,
    InvalidTime,
    InvalidState,
    InvalidInput,
    InvalidActions,
    InvalidTokens,
    InsufficientBalance,
    Unauthorized,
    MIN_STAKE_AMOUNT,
    MAX_STAKE_AMOUNT,
    MIN_PERIOD_DAYS,
    DAY_IN_SECONDS,
    PRECISION,
)

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class StakeTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        # Set up contract
        self.contract_id = self.gen_random_contract_id()
        self.blueprint_id = self.gen_random_blueprint_id()
        self.register_blueprint_class(self.blueprint_id, Stake)

        # Generate test tokens and addresses
        self.token_uid = self.gen_random_token_uid()
        self.owner_address, self.owner_key = self._get_any_address()

        # Default test parameters
        self.earnings_per_day = 100_00  # 100 tokens per day
        self.initial_deposit = 10_000_00  # Initial owner deposit
        self.base_stake = 1_000_00  # Base stake amount for tests

        # Set up base transaction for contexts
        self.tx = self.get_genesis_tx()

        # Initialize contract in setUp
        self._initialize_contract()

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
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(amount))],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )

        self.runner.create_contract(
            self.contract_id,
            self.blueprint_id,
            ctx,
            self.earnings_per_day,
            self.token_uid,
        )

    def _stake_tokens(self, amount: int, address: bytes | None = None) -> Context:
        """Helper to stake tokens."""
        if address is None:
            address = self._get_any_address()[0]

        ctx = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(amount))],
            vertex=self.tx,
            address=Address(address),
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(self.contract_id, "stake", ctx)
        return ctx

    def test_initialization_validation(self):
        """Test contract initialization validation."""
        # Test initialization with insufficient deposit
        insufficient_amount = MIN_PERIOD_DAYS * self.earnings_per_day - 1
        ctx = Context(
            [
                NCDepositAction(
                    token_uid=self.token_uid, amount=Amount(insufficient_amount)
                )
            ],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )

        with self.assertRaises(InsufficientBalance):
            contract_id = self.gen_random_contract_id()
            blueprint_id = self.gen_random_blueprint_id()
            self.register_blueprint_class(blueprint_id, Stake)
            self.runner.create_contract(
                contract_id, blueprint_id, ctx, self.earnings_per_day, self.token_uid
            )

    def test_stake(self):
        """Test basic staking functionality."""
        stake_amount = self.base_stake
        ctx = self._stake_tokens(stake_amount)
        user_info = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.address
        )

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, stake_amount)
        self.assertEqual(
            user_info["deposits"],
            stake_amount,
        )

        # Test stake limits
        with self.assertRaises(InvalidAmount):
            self._stake_tokens(MIN_STAKE_AMOUNT - 1)

        with self.assertRaises(InvalidAmount):
            self._stake_tokens(MAX_STAKE_AMOUNT + 1)

        # Test owner can't stake
        with self.assertRaises(Unauthorized):
            self._stake_tokens(stake_amount, self.owner_address)

    def test_unstake(self):
        """Test unstaking functionality."""
        stake_amount = self.base_stake

        # Stake tokens
        ctx = self._stake_tokens(stake_amount)
        initial_time = self.clock.seconds()

        # Try unstaking before timelock
        unstake_ctx = Context(
            [NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(stake_amount))],
            vertex=self.tx,
            address=ctx.address,
            timestamp=initial_time + DAY_IN_SECONDS,  # Only 1 day passed
        )
        with self.assertRaises(InvalidTime):
            self.runner.call_public_method(self.contract_id, "unstake", unstake_ctx)

        # Calculate pending rewards and total withdrawal amount
        time_passed = MIN_PERIOD_DAYS * DAY_IN_SECONDS + 1
        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS
        total_reward = (rewards_per_second * time_passed * stake_amount) // (
            PRECISION * stake_amount
        )

        # Get max withdrawal first
        max_withdrawal = self.runner.call_view_method(
            self.contract_id,
            "get_max_withdrawal",
            ctx.address,
            initial_time + time_passed,
        )

        # Successful unstake after timelock
        unstake_ctx = Context(
            [
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(max_withdrawal)
                )
            ],
            vertex=self.tx,
            address=ctx.address,
            timestamp=initial_time + time_passed,
        )
        self.runner.call_public_method(self.contract_id, "unstake", unstake_ctx)

        # Verify user deposits and staked amount are zero after withdrawal
        user_info = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.address
        )
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, 0)
        self.assertEqual(user_info["deposits"], 0)

    def test_rewards_calculation(self):
        """Test reward calculation and distribution."""
        stake_amount = self.base_stake
        ctx = self._stake_tokens(stake_amount)
        initial_time = self.clock.seconds()

        # Calculate expected rewards after 1 day
        one_day_later = initial_time + DAY_IN_SECONDS
        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS
        # Calculate with full precision for both time periods
        reward_amount = (rewards_per_second * DAY_IN_SECONDS * stake_amount) // (
            PRECISION * stake_amount
        )

        max_withdrawal = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx.address, one_day_later
        )

        self.assertEqual(stake_amount + reward_amount, max_withdrawal)

        # Two days rewards calculation
        two_days_later = initial_time + (2 * DAY_IN_SECONDS)
        two_days_reward = (rewards_per_second * 2 * DAY_IN_SECONDS * stake_amount) // (
            PRECISION * stake_amount
        )

        max_withdrawal = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx.address, two_days_later
        )

        self.assertEqual(max_withdrawal, stake_amount + two_days_reward)

    def test_emergency_functions(self):
        """Test emergency pause and withdrawal functionality."""
        stake_amount = self.base_stake
        ctx = self._stake_tokens(stake_amount)

        # Test pause
        pause_ctx = Context(
            [],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertTrue(contract.paused)

        # Test emergency withdrawal
        emergency_ctx = Context(
            [NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(stake_amount))],
            vertex=self.tx,
            address=ctx.address,
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(
            self.contract_id, "emergency_withdraw", emergency_ctx
        )

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, 0)

        # Test unpause
        unpause_ctx = Context(
            [],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "unpause", unpause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertFalse(contract.paused)

    def test_owner_operations(self):
        """Test owner-specific operations."""
        # Test owner deposit
        deposit_amount = 1_000_00
        deposit_ctx = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(deposit_amount))],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "owner_deposit", deposit_ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.owner_balance, self.initial_deposit + deposit_amount)

        # Test owner withdrawal
        withdraw_ctx = Context(
            [
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(deposit_amount)
                )
            ],
            vertex=self.tx,
            address=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "owner_withdraw", withdraw_ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.owner_balance, self.initial_deposit)

        # Test unauthorized withdrawal
        unauthorized_ctx = Context(
            [
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(deposit_amount)
                )
            ],
            vertex=self.tx,
            address=Address(self._get_any_address()[0]),
            timestamp=self.clock.seconds(),
        )
        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.contract_id, "owner_withdraw", unauthorized_ctx
            )

    def test_multiple_stakers(self):
        """Test staking with multiple users."""
        num_stakers = 5
        stake_amount = self.base_stake
        total_stake = stake_amount * num_stakers

        stakers = []
        for _ in range(num_stakers):
            ctx = self._stake_tokens(stake_amount)
            stakers.append(ctx.address)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, total_stake)

        # Check rewards distribution
        initial_time = self.clock.seconds()
        one_day_later = initial_time + DAY_IN_SECONDS
        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS
        total_reward = (rewards_per_second * DAY_IN_SECONDS * stake_amount) // (
            PRECISION
        )

        for staker in stakers:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker, one_day_later
            )
            self.assertEqual(
                max_withdrawal // PRECISION, (stake_amount + total_reward) // PRECISION
            )

    def test_front_end_api(self):
        """Test front end API functionality."""
        stake_amount = self.base_stake
        self._stake_tokens(stake_amount)

        api_data = self.runner.call_view_method(self.contract_id, "front_end_api")

        self.assertEqual(api_data["owner_balance"], self.initial_deposit)
        self.assertEqual(api_data["total_staked"], stake_amount)
        self.assertFalse(api_data["paused"])
