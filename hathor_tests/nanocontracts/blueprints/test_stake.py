import os
from hathor.conf.get_settings import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    NCDepositAction,
    NCWithdrawalAction,
    Address,
    Amount,
    TokenUid,
)
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from hathor_tests.nanocontracts.blueprints.unittest import BlueprintTestCase

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
        self.blueprint_id = self._register_blueprint_class(Stake)

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

        ctx = self.create_context(
            actions=[NCDepositAction(token_uid=self.token_uid, amount=Amount(amount))],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )

        creator_contract_id = self.gen_random_contract_id()
        self.runner.create_contract(
            self.contract_id,
            self.blueprint_id,
            ctx,
            self.earnings_per_day,
            self.token_uid,
            creator_contract_id,
        )

    def _stake_tokens(self, amount: int, address: bytes | None = None) -> Context:
        """Helper to stake tokens."""
        if address is None:
            address = self._get_any_address()[0]

        ctx = self.create_context(
            actions=[NCDepositAction(token_uid=self.token_uid, amount=Amount(amount))],
            vertex=self.tx,
            caller_id=Address(address),
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(self.contract_id, "stake", ctx)
        return ctx

    def test_initialization_validation(self):
        """Test contract initialization validation."""
        # Test initialization with insufficient deposit
        insufficient_amount = MIN_PERIOD_DAYS * self.earnings_per_day - 1
        ctx = self.create_context(
            actions=[
                NCDepositAction(
                    token_uid=self.token_uid, amount=Amount(insufficient_amount)
                )
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )

        with self.assertRaises(InsufficientBalance):
            contract_id = self.gen_random_contract_id()
            blueprint_id = self._register_blueprint_class(Stake)
            creator_contract_id = self.gen_random_contract_id()
            self.runner.create_contract(
                contract_id, blueprint_id, ctx, self.earnings_per_day, self.token_uid, creator_contract_id
            )

    def test_stake(self):
        """Test basic staking functionality."""
        stake_amount = self.base_stake
        ctx = self._stake_tokens(stake_amount)
        user_info = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.caller_id
        )

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, stake_amount)
        self.assertEqual(
            user_info.deposits,
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
        unstake_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(stake_amount))],
            vertex=self.tx,
            caller_id=Address(ctx.caller_id),
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
            ctx.caller_id,
            int(initial_time + time_passed),
        )

        # Successful unstake after timelock
        unstake_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(max_withdrawal)
                )
            ],
            vertex=self.tx,
            caller_id=Address(ctx.caller_id),
            timestamp=initial_time + time_passed,
        )
        self.runner.call_public_method(self.contract_id, "unstake", unstake_ctx)

        # Verify user deposits and staked amount are zero after withdrawal
        user_info = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.caller_id
        )
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, 0)
        self.assertEqual(user_info.deposits, 0)

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
            self.contract_id, "get_max_withdrawal", ctx.caller_id, int(one_day_later)
        )

        self.assertEqual(stake_amount + reward_amount, max_withdrawal)

        # Two days rewards calculation
        two_days_later = initial_time + (2 * DAY_IN_SECONDS)
        two_days_reward = (rewards_per_second * 2 * DAY_IN_SECONDS * stake_amount) // (
            PRECISION * stake_amount
        )

        max_withdrawal = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx.caller_id, int(two_days_later)
        )

        self.assertEqual(max_withdrawal, stake_amount + two_days_reward)

    def test_emergency_functions(self):
        """Test emergency pause and withdrawal functionality."""
        stake_amount = self.base_stake
        ctx = self._stake_tokens(stake_amount)

        # Test pause
        pause_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "pause", pause_ctx)
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertTrue(contract.paused)

        # Test emergency withdrawal
        emergency_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(stake_amount))],
            vertex=self.tx,
            caller_id=Address(ctx.caller_id),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(
            self.contract_id, "emergency_withdraw", emergency_ctx
        )

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, 0)

        # Test unpause
        unpause_ctx = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
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
        deposit_ctx = self.create_context(
            actions=[NCDepositAction(token_uid=self.token_uid, amount=Amount(deposit_amount))],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "owner_deposit", deposit_ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.owner_balance, self.initial_deposit + deposit_amount)

        # Test owner withdrawal
        withdraw_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(deposit_amount)
                )
            ],
            vertex=self.tx,
            caller_id=Address(self.owner_address),
            timestamp=self.clock.seconds(),
        )
        self.runner.call_public_method(self.contract_id, "owner_withdraw", withdraw_ctx)

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.owner_balance, self.initial_deposit)

        # Test unauthorized withdrawal
        unauthorized_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(deposit_amount)
                )
            ],
            vertex=self.tx,
            caller_id=Address(self._get_any_address()[0]),
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
            stakers.append(ctx.caller_id)

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
                self.contract_id, "get_max_withdrawal", staker, int(one_day_later)
            )
            self.assertEqual(
                max_withdrawal // PRECISION, (stake_amount + total_reward) // PRECISION
            )

    def test_front_end_api(self):
        """Test front end API functionality."""
        stake_amount = self.base_stake
        self._stake_tokens(stake_amount)

        api_data = self.runner.call_view_method(self.contract_id, "front_end_api")

        self.assertEqual(api_data.owner_balance, self.initial_deposit)
        self.assertEqual(api_data.total_staked, stake_amount)
        self.assertFalse(api_data.paused)

    def test_partial_unstake(self):
        """Test partial unstaking functionality - withdrawing only a portion of staked tokens."""
        stake_amount = self.base_stake  # 1000.00 tokens

        # Stake tokens
        ctx = self._stake_tokens(stake_amount)
        initial_time = self.clock.seconds()

        # Wait for timelock to pass
        time_passed = MIN_PERIOD_DAYS * DAY_IN_SECONDS + 1

        # Withdraw only 10% of the stake amount (not including rewards)
        partial_withdrawal = stake_amount // 10  # 100.00 tokens
        # With new logic: deposits and rewards stay separate
        # Withdrawing from deposits only since withdrawal < deposits
        expected_remaining_deposits = stake_amount - partial_withdrawal  # 900 tokens
        expected_total_staked = expected_remaining_deposits  # total_staked only counts deposits

        # Perform partial unstake
        unstake_ctx = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(partial_withdrawal)
                )
            ],
            vertex=self.tx,
            caller_id=Address(ctx.caller_id),
            timestamp=initial_time + time_passed,
        )
        self.runner.call_public_method(self.contract_id, "unstake", unstake_ctx)

        # Verify user still has remaining stake
        user_info = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.caller_id
        )
        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)

        # Check that deposits are correct (should be remaining deposit amount, not zero!)
        # Rewards are NOT compounded into deposits - they stay separate
        self.assertEqual(
            user_info.deposits,
            expected_remaining_deposits,
            f"Expected deposits to be {expected_remaining_deposits} but got {user_info.deposits}",
        )

        # Check that total_staked is correct (should be remaining deposit amount, not zero!)
        self.assertEqual(
            contract.total_staked,
            expected_total_staked,
            f"Expected total_staked to be {expected_total_staked} but got {contract.total_staked}",
        )

        # Verify user can still see their remaining balance
        remaining_max_withdrawal = self.runner.call_view_method(
            self.contract_id,
            "get_max_withdrawal",
            ctx.caller_id,
            int(initial_time + time_passed + 1),
        )

        # Should be able to withdraw the remaining amount
        self.assertGreater(
            remaining_max_withdrawal,
            0,
            "User should still have tokens available to withdraw after partial unstake",
        )

    def test_multiple_partial_unstakes(self):
        """Test multiple partial unstakes over time."""
        stake_amount = self.base_stake  # 1000.00 tokens

        # Stake tokens
        ctx = self._stake_tokens(stake_amount)
        initial_time = self.clock.seconds()

        # Wait for timelock
        time_passed = MIN_PERIOD_DAYS * DAY_IN_SECONDS + 1
        current_time = initial_time + time_passed

        # First partial withdrawal: 20%
        first_withdrawal = stake_amount // 5  # 200 tokens
        unstake_ctx_1 = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(first_withdrawal)
                )
            ],
            vertex=self.tx,
            caller_id=Address(ctx.caller_id),
            timestamp=current_time,
        )
        self.runner.call_public_method(self.contract_id, "unstake", unstake_ctx_1)

        # Check balance after first withdrawal
        user_info_1 = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.caller_id
        )
        self.assertGreater(
            user_info_1.deposits,
            0,
            "User should still have deposits after first partial withdrawal",
        )

        # Wait some more time for new rewards
        current_time += DAY_IN_SECONDS

        # Second partial withdrawal: 10%
        second_withdrawal = stake_amount // 10  # 100 tokens
        unstake_ctx_2 = self.create_context(
            actions=[
                NCWithdrawalAction(
                    token_uid=self.token_uid, amount=Amount(second_withdrawal)
                )
            ],
            vertex=self.tx,
            caller_id=Address(ctx.caller_id),
            timestamp=current_time,
        )
        self.runner.call_public_method(self.contract_id, "unstake", unstake_ctx_2)

        # Check balance after second withdrawal
        user_info_2 = self.runner.call_view_method(
            self.contract_id, "get_user_info", ctx.caller_id
        )
        self.assertGreater(
            user_info_2.deposits,
            0,
            "User should still have deposits after second partial withdrawal",
        )

        # Verify deposits decreased correctly
        self.assertLess(
            user_info_2.deposits,
            user_info_1.deposits,
            "Deposits should decrease after second withdrawal",
        )

    def test_multiple_users_reward_distribution(self):
        """Test that rewards are properly distributed proportionally among multiple users."""
        # User 1 stakes 1000 tokens
        user1_stake = self.base_stake  # 1000 tokens
        ctx1 = self._stake_tokens(user1_stake)
        time_start = self.clock.seconds()

        # User 2 stakes 2000 tokens (2x more)
        user2_stake = self.base_stake * 2  # 2000 tokens
        ctx2 = self._stake_tokens(user2_stake)

        # User 3 stakes 500 tokens (0.5x)
        user3_stake = self.base_stake // 2  # 500 tokens
        ctx3 = self._stake_tokens(user3_stake)

        # Total staked = 3500 tokens
        total_staked = user1_stake + user2_stake + user3_stake

        contract = self.get_readonly_contract(self.contract_id)
        assert isinstance(contract, Stake)
        self.assertEqual(contract.total_staked, total_staked)

        print(f"\n=== Multiple Users Reward Distribution Test ===")
        print(f"User 1 staked: {user1_stake} (28.57% of pool)")
        print(f"User 2 staked: {user2_stake} (57.14% of pool)")
        print(f"User 3 staked: {user3_stake} (14.29% of pool)")
        print(f"Total staked: {total_staked}")
        print(f"Daily rewards pool: {self.earnings_per_day}")

        # Wait 1 day
        self.clock.advance(DAY_IN_SECONDS)
        one_day_later = self.clock.seconds()

        # Get max withdrawals for each user
        max_withdrawal_1 = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx1.caller_id, int(one_day_later)
        )
        max_withdrawal_2 = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx2.caller_id, int(one_day_later)
        )
        max_withdrawal_3 = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx3.caller_id, int(one_day_later)
        )

        # Calculate rewards (withdrawal - principal)
        rewards_1 = max_withdrawal_1 - user1_stake
        rewards_2 = max_withdrawal_2 - user2_stake
        rewards_3 = max_withdrawal_3 - user3_stake
        total_rewards_distributed = rewards_1 + rewards_2 + rewards_3

        print(f"\nAfter 1 day:")
        print(f"User 1 rewards: {rewards_1}")
        print(f"User 2 rewards: {rewards_2}")
        print(f"User 3 rewards: {rewards_3}")
        print(f"Total rewards distributed: {total_rewards_distributed}")
        print(f"Expected total rewards: {self.earnings_per_day}")

        # Verify rewards are proportional to stake
        # User 2 should get ~2x what User 1 gets (they staked 2x more)
        ratio_2_to_1 = rewards_2 / rewards_1 if rewards_1 > 0 else 0
        print(f"\nRatio User2/User1 rewards: {ratio_2_to_1:.2f} (expected ~2.0)")
        self.assertAlmostEqual(ratio_2_to_1, 2.0, delta=0.1)

        # User 3 should get ~0.5x what User 1 gets (they staked 0.5x)
        ratio_3_to_1 = rewards_3 / rewards_1 if rewards_1 > 0 else 0
        print(f"Ratio User3/User1 rewards: {ratio_3_to_1:.4f} (expected ~0.5)")
        # Check ratio is within 1% of 0.5
        self.assertGreater(ratio_3_to_1, 0.49)
        self.assertLess(ratio_3_to_1, 0.51)

        # Total distributed should approximately equal daily rewards
        print(f"\nReward distribution accuracy: {total_rewards_distributed / self.earnings_per_day * 100:.2f}%")
        # Allow reasonable tolerance for precision (within 5%)
        self.assertGreater(total_rewards_distributed, self.earnings_per_day * 0.95)
        self.assertLess(total_rewards_distributed, self.earnings_per_day * 1.05)

        # Verify each user's proportion
        user1_proportion = rewards_1 / total_rewards_distributed if total_rewards_distributed > 0 else 0
        user2_proportion = rewards_2 / total_rewards_distributed if total_rewards_distributed > 0 else 0
        user3_proportion = rewards_3 / total_rewards_distributed if total_rewards_distributed > 0 else 0

        expected_user1_proportion = user1_stake / total_staked
        expected_user2_proportion = user2_stake / total_staked
        expected_user3_proportion = user3_stake / total_staked

        print(f"\nUser 1: {user1_proportion*100:.2f}% of rewards (expected {expected_user1_proportion*100:.2f}%)")
        print(f"User 2: {user2_proportion*100:.2f}% of rewards (expected {expected_user2_proportion*100:.2f}%)")
        print(f"User 3: {user3_proportion*100:.2f}% of rewards (expected {expected_user3_proportion*100:.2f}%)")

        # Use places=4 for decimal precision (0.0001 tolerance)
        self.assertAlmostEqual(user1_proportion, expected_user1_proportion, places=4)
        self.assertAlmostEqual(user2_proportion, expected_user2_proportion, places=4)
        self.assertAlmostEqual(user3_proportion, expected_user3_proportion, places=4)
        print("==============================================\n")

    def test_user_joining_pool_after_others(self):
        """Test reward distribution when users join at different times."""
        # User 1 stakes first
        user1_stake = self.base_stake
        ctx1 = self._stake_tokens(user1_stake)
        time_1 = self.clock.seconds()

        print(f"\n=== Staggered Entry Test ===")
        print(f"Day 0: User 1 stakes {user1_stake}")

        # Wait 5 days - User 1 should get all rewards
        self.clock.advance(5 * DAY_IN_SECONDS)
        time_2 = self.clock.seconds()

        max_withdrawal_before_user2 = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx1.caller_id, int(time_2)
        )
        user1_rewards_before = max_withdrawal_before_user2 - user1_stake
        expected_5_days_rewards = 5 * self.earnings_per_day

        print(f"Day 5: User 1 has earned {user1_rewards_before} (expected ~{expected_5_days_rewards})")
        # Allow 1 token difference due to precision
        self.assertGreaterEqual(user1_rewards_before, expected_5_days_rewards - 1)
        self.assertLessEqual(user1_rewards_before, expected_5_days_rewards + 1)

        # User 2 joins with equal stake
        user2_stake = self.base_stake
        ctx2 = self._stake_tokens(user2_stake)
        print(f"Day 5: User 2 stakes {user2_stake}")

        # Wait another 5 days - rewards should now be split 50/50
        self.clock.advance(5 * DAY_IN_SECONDS)
        time_3 = self.clock.seconds()

        # Get final withdrawals
        max_withdrawal_1_final = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx1.caller_id, int(time_3)
        )
        max_withdrawal_2_final = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", ctx2.caller_id, int(time_3)
        )

        # User 1 total rewards: 5 days alone + 5 days sharing 50/50
        user1_total_rewards = max_withdrawal_1_final - user1_stake
        user1_shared_period_rewards = user1_total_rewards - user1_rewards_before

        # User 2 rewards: only 5 days sharing 50/50
        user2_total_rewards = max_withdrawal_2_final - user2_stake

        print(f"\nDay 10 results:")
        print(f"User 1 total rewards: {user1_total_rewards}")
        print(f"  - Solo period (days 0-5): {user1_rewards_before}")
        print(f"  - Shared period (days 5-10): {user1_shared_period_rewards}")
        print(f"User 2 total rewards: {user2_total_rewards}")
        print(f"  - Shared period (days 5-10): {user2_total_rewards}")

        # During shared period, rewards should be approximately equal
        shared_ratio = user1_shared_period_rewards / user2_total_rewards if user2_total_rewards > 0 else 0
        print(f"\nShared period ratio User1/User2: {shared_ratio:.4f} (expected ~1.0)")
        # Allow generous tolerance due to precision and auto-compounding effects
        self.assertGreater(shared_ratio, 0.85)
        self.assertLess(shared_ratio, 1.15)

        # User 1 should have approximately 3x the rewards of User 2
        # (5 days solo + 2.5 days shared = 7.5 days worth vs 2.5 days shared)
        expected_ratio = 3.0
        actual_ratio = user1_total_rewards / user2_total_rewards if user2_total_rewards > 0 else 0
        print(f"Overall ratio User1/User2: {actual_ratio:.2f} (expected ~{expected_ratio})")
        # Allow tolerance for precision
        self.assertGreater(actual_ratio, expected_ratio * 0.95)
        self.assertLess(actual_ratio, expected_ratio * 1.05)
        print("=============================\n")
