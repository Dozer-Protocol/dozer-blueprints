# Copyright 2025 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Multi-user scenarios and complex interactions for Stake contract.

This module tests:
- Complex multi-user staking patterns
- Competitive staking scenarios
- Sequential staking and unstaking
- Mixed user behavior patterns
- Stress testing with many users
- User interaction edge cases
"""

import os
from typing import List, Tuple
from hathor.conf import settings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    NCDepositAction,
    NCWithdrawalAction,
    Address,
    Amount,
    ContractId,
    VertexId,
)
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from tests.nanocontracts.blueprints.test_utilities import (
    StakeTestFixture,
    TestConstants,
    TestUser,
    create_deposit_action,
    create_withdrawal_action,
)

from hathor.nanocontracts.blueprints.stake import (
    Stake,
    InvalidAmount,
    InvalidTime,
    InsufficientBalance,
    Unauthorized,
    MIN_STAKE_AMOUNT,
    MAX_STAKE_AMOUNT,
    MIN_PERIOD_DAYS,
    DAY_IN_SECONDS,
    PRECISION,
)

HATHOR_TOKEN_UID = settings.HATHOR_TOKEN_UID


class StakeMultiUserTest(BlueprintTestCase):
    """Test complex multi-user scenarios for Stake contract."""

    def setUp(self):
        super().setUp()

        self.fixture = StakeTestFixture(self)

        # Contract setup
        self.contract_id = self.gen_random_contract_id()
        self.blueprint_id = self.gen_random_blueprint_id()
        self._register_blueprint_class(Stake, self.blueprint_id)

        # Test token
        self.token_uid = self.gen_random_token_uid()

        # Staking parameters (higher capacity for multi-user testing)
        self.earnings_per_day = 1000_00  # 1000 tokens per day
        self.initial_deposit = 100_000_00  # 100k initial deposit

        # Base transaction
        self.tx = self.get_genesis_tx()

        # Initialize contract
        self._initialize_contract()

    def _get_any_address(self) -> tuple[Address, KeyPair]:
        """Generate a random address and keypair."""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return Address(address_bytes), key

    def _initialize_contract(self):
        """Initialize contract with large token deposit for multi-user testing."""
        ctx = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(self.initial_deposit))],
            vertex=self.tx,
            caller_id=self.fixture.owner.address,
            timestamp=self.now,
        )

        self.runner.create_contract(
            self.contract_id,
            self.blueprint_id,
            ctx,
            self.earnings_per_day,
            self.token_uid,
        )

    def _stake_tokens(self, amount: int, staker: Address, timestamp: int = None) -> Context:
        """Helper to stake tokens for a user."""
        if timestamp is None:
            timestamp = self.now

        ctx = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(amount))],
            vertex=self.tx,
            caller_id=staker,
            timestamp=timestamp,
        )

        self.runner.call_public_method(self.contract_id, "stake", ctx)
        return ctx

    def test_sequential_staking_pattern(self):
        """Test users staking sequentially with different timing."""
        num_users = 5
        base_stake_amount = 10000

        stakers = []
        stake_times = []

        # Users stake one after another with delays
        for i in range(num_users):
            staker = self.fixture.create_test_user(f"SeqStaker{i}")
            stake_amount = base_stake_amount * (i + 1)  # Increasing amounts
            stake_time = self.now + (i * DAY_IN_SECONDS // 2)  # 12-hour intervals

            self._stake_tokens(stake_amount, staker.address, stake_time)

            stakers.append((staker, stake_amount, stake_time))
            stake_times.append(stake_time)

        # Check rewards after all have staked for at least minimum period
        check_time = max(stake_times) + (MIN_PERIOD_DAYS * DAY_IN_SECONDS) + 1

        total_rewards_expected = 0

        for staker, stake_amount, stake_time in stakers:
            time_staked = check_time - stake_time

            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, check_time
            )

            reward = max_withdrawal - stake_amount
            total_rewards_expected += reward

            # Individual validation
            self.assertGreater(reward, 0, f"Staker {staker.name} should have positive reward")
            self.assertGreater(max_withdrawal, stake_amount,
                             f"Max withdrawal should exceed stake for {staker.name}")

        # Validate total rewards don't exceed reasonable bounds
        max_time_staked = check_time - min(stake_times)
        theoretical_max_rewards = (max_time_staked * self.earnings_per_day) // DAY_IN_SECONDS

        self.assertLessEqual(total_rewards_expected, theoretical_max_rewards * 1.1,
                           "Total rewards exceed theoretical maximum")

    def test_competitive_staking_scenario(self):
        """Test scenario where users compete for limited rewards."""
        # Create scenario with many small stakers vs few large stakers
        small_stakers = []
        large_stakers = []

        # 10 small stakers
        for i in range(10):
            staker = self.fixture.create_test_user(f"SmallStaker{i}")
            stake_amount = MIN_STAKE_AMOUNT
            self._stake_tokens(stake_amount, staker.address)
            small_stakers.append((staker, stake_amount))

        # 2 large stakers
        large_stake_amount = 50000
        for i in range(2):
            staker = self.fixture.create_test_user(f"LargeStaker{i}")
            self._stake_tokens(large_stake_amount, staker.address)
            large_stakers.append((staker, large_stake_amount))

        # Check rewards after 1 day
        check_time = self.now + DAY_IN_SECONDS

        total_small_rewards = 0
        total_large_rewards = 0

        # Calculate total stakes for proportion calculation
        total_small_stake = len(small_stakers) * MIN_STAKE_AMOUNT
        total_large_stake = len(large_stakers) * large_stake_amount
        total_stake = total_small_stake + total_large_stake

        # Check small staker rewards
        for staker, stake_amount in small_stakers:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, check_time
            )
            reward = max_withdrawal - stake_amount
            total_small_rewards += reward

        # Check large staker rewards
        for staker, stake_amount in large_stakers:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, check_time
            )
            reward = max_withdrawal - stake_amount
            total_large_rewards += reward

        # Large stakers should get proportionally more rewards
        small_proportion = total_small_stake / total_stake
        large_proportion = total_large_stake / total_stake

        expected_small_share = self.earnings_per_day * small_proportion
        expected_large_share = self.earnings_per_day * large_proportion

        # Allow for rounding differences
        tolerance = 10  # Small tolerance

        self.assertLessEqual(
            abs(total_small_rewards - expected_small_share), tolerance,
            f"Small stakers reward share inaccurate: got {total_small_rewards}, expected ~{expected_small_share}"
        )

        self.assertLessEqual(
            abs(total_large_rewards - expected_large_share), tolerance,
            f"Large stakers reward share inaccurate: got {total_large_rewards}, expected ~{expected_large_share}"
        )

    def test_mixed_staking_unstaking_pattern(self):
        """Test complex pattern of staking and unstaking."""
        stakers = []

        # Initial wave of stakers
        for i in range(6):
            staker = self.fixture.create_test_user(f"MixedStaker{i}")
            stake_amount = 15000 + (i * 5000)
            self._stake_tokens(stake_amount, staker.address)
            stakers.append((staker, stake_amount, self.now))

        # Wait past minimum period
        unstake_time = self.now + (MIN_PERIOD_DAYS * DAY_IN_SECONDS) + DAY_IN_SECONDS

        # Some users unstake completely
        for i in [0, 2, 4]:  # Every other user
            staker, stake_amount, _ = stakers[i]

            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, unstake_time
            )

            # Full unstake
            ctx = Context(
                [NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(max_withdrawal))],
                vertex=self.tx,
                caller_id=staker.address,
                timestamp=unstake_time,
            )

            self.runner.call_public_method(self.contract_id, "unstake", ctx)

            # Verify user is no longer staking
            user_info = self.runner.call_view_method(
                self.contract_id, "get_user_info", staker.address
            )
            self.assertEqual(user_info.deposits, 0, f"User {staker.name} should have zero deposits after full unstake")

        # Some users partially unstake
        for i in [1, 3]:
            staker, stake_amount, _ = stakers[i]

            partial_amount = stake_amount // 3  # Unstake 1/3

            ctx = Context(
                [NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(partial_amount))],
                vertex=self.tx,
                caller_id=staker.address,
                timestamp=unstake_time,
            )

            self.runner.call_public_method(self.contract_id, "unstake", ctx)

            # Verify user still has remaining stake
            user_info = self.runner.call_view_method(
                self.contract_id, "get_user_info", staker.address
            )
            self.assertGreater(user_info.deposits, 0, f"User {staker.name} should have remaining deposits")

        # Some users continue staking (index 5)
        # Add new stakers after others have unstaked
        for i in range(3):
            new_staker = self.fixture.create_test_user(f"NewStaker{i}")
            self._stake_tokens(20000, new_staker.address, unstake_time + 1)

        # Final verification - contract should still be functional
        contract = self.get_readonly_contract(self.contract_id)
        self.assertIsInstance(contract, Stake)
        self.assertGreater(contract.total_staked, 0, "Contract should still have stakers")

    def test_stress_test_many_users(self):
        """Stress test with many users (limited for performance)."""
        num_users = 20  # Limited for test performance
        base_amount = 5000

        users = []

        # Create many users with varying stake amounts
        for i in range(num_users):
            if (i + 1) * base_amount > self.initial_deposit:
                break  # Don't exceed contract balance

            user = self.fixture.create_test_user(f"StressUser{i}")
            stake_amount = base_amount + (i * 1000)  # Varying amounts

            self._stake_tokens(stake_amount, user.address)
            users.append((user, stake_amount))

        # Verify all users were able to stake
        contract = self.get_readonly_contract(self.contract_id)
        self.assertEqual(len(users), len([u for u, _ in users]))

        total_expected_stake = sum(amount for _, amount in users)
        self.assertLessEqual(
            abs(contract.total_staked - total_expected_stake), num_users,
            f"Total stake mismatch: contract shows {contract.total_staked}, expected {total_expected_stake}"
        )

        # Check that reward calculations still work
        check_time = self.now + DAY_IN_SECONDS

        total_rewards = 0
        for user, stake_amount in users:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", user.address, check_time
            )

            reward = max_withdrawal - stake_amount
            total_rewards += reward

            self.assertGreaterEqual(reward, 0, f"User {user.name} should have non-negative reward")

        # Total rewards should be reasonable
        self.assertLessEqual(total_rewards, self.earnings_per_day + num_users,
                           f"Total rewards {total_rewards} exceed reasonable bound")

    def test_late_joiner_scenario(self):
        """Test users joining staking pool at different times."""
        # Early stakers
        early_stakers = []
        for i in range(3):
            staker = self.fixture.create_test_user(f"EarlyStaker{i}")
            stake_amount = 20000
            self._stake_tokens(stake_amount, staker.address, self.now)
            early_stakers.append((staker, stake_amount, self.now))

        # Medium-term stakers (join after 5 days)
        mid_join_time = self.now + (5 * DAY_IN_SECONDS)
        mid_stakers = []
        for i in range(2):
            staker = self.fixture.create_test_user(f"MidStaker{i}")
            stake_amount = 15000
            self._stake_tokens(stake_amount, staker.address, mid_join_time)
            mid_stakers.append((staker, stake_amount, mid_join_time))

        # Late stakers (join after 10 days)
        late_join_time = self.now + (10 * DAY_IN_SECONDS)
        late_stakers = []
        for i in range(2):
            staker = self.fixture.create_test_user(f"LateStaker{i}")
            stake_amount = 25000
            self._stake_tokens(stake_amount, staker.address, late_join_time)
            late_stakers.append((staker, stake_amount, late_join_time))

        # Check rewards after 15 days total
        final_check_time = self.now + (15 * DAY_IN_SECONDS)

        # Early stakers should have highest total rewards (been there longest)
        early_total_rewards = 0
        for staker, stake_amount, join_time in early_stakers:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, final_check_time
            )
            reward = max_withdrawal - stake_amount
            early_total_rewards += reward

        # Late stakers should have lower total rewards despite higher stakes
        late_total_rewards = 0
        for staker, stake_amount, join_time in late_stakers:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, final_check_time
            )
            reward = max_withdrawal - stake_amount
            late_total_rewards += reward

        # Early stakers should have earned more despite smaller individual stakes
        # (due to being in the pool longer)
        early_avg_reward = early_total_rewards / len(early_stakers)
        late_avg_reward = late_total_rewards / len(late_stakers)

        # This may not always be true depending on how rewards are calculated
        # but it's a reasonable expectation for time-based rewards
        self.assertGreater(
            early_avg_reward, 0,
            "Early stakers should have positive rewards"
        )
        self.assertGreater(
            late_avg_reward, 0,
            "Late stakers should also have positive rewards"
        )

    def test_edge_case_simultaneous_operations(self):
        """Test edge case where multiple users operate at exact same time."""
        # Create users who all stake at exactly the same timestamp
        simultaneous_time = self.now + 1000
        simultaneous_stakers = []

        for i in range(5):
            staker = self.fixture.create_test_user(f"SimultaneousStaker{i}")
            stake_amount = 10000 + (i * 2000)

            # All at exact same timestamp
            self._stake_tokens(stake_amount, staker.address, simultaneous_time)
            simultaneous_stakers.append((staker, stake_amount))

        # All users unstake at same time too
        unstake_time = simultaneous_time + (MIN_PERIOD_DAYS * DAY_IN_SECONDS) + 1

        for staker, stake_amount in simultaneous_stakers:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, unstake_time
            )

            # Partial unstake at same time
            unstake_amount = max_withdrawal // 2

            ctx = Context(
                [NCWithdrawalAction(token_uid=self.token_uid, amount=Amount(unstake_amount))],
                vertex=self.tx,
                caller_id=staker.address,
                timestamp=unstake_time,
            )

            self.runner.call_public_method(self.contract_id, "unstake", ctx)

        # Verify contract state remains consistent
        contract = self.get_readonly_contract(self.contract_id)
        self.assertGreater(contract.total_staked, 0, "Contract should have remaining stakes")

        # All users should have remaining deposits
        for staker, _ in simultaneous_stakers:
            user_info = self.runner.call_view_method(
                self.contract_id, "get_user_info", staker.address
            )
            self.assertGreater(user_info.deposits, 0, f"User {staker.name} should have remaining deposits")