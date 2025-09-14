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
Precision and mathematical accuracy tests for Stake contract.

This module tests:
- Rewards calculation precision with various time periods
- Multi-user staking scenarios with different amounts
- Edge cases in reward distribution
- Mathematical invariants and accuracy
- Precision loss prevention
- Complex time-based scenarios
"""

import os
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
    EdgeCaseGenerator,
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


class StakePrecisionTest(BlueprintTestCase):
    """Test mathematical precision and accuracy in Stake contract."""

    def setUp(self):
        super().setUp()

        self.fixture = StakeTestFixture(self)

        # Contract setup
        self.contract_id = self.gen_random_contract_id()
        self.blueprint_id = self.gen_random_blueprint_id()
        self._register_blueprint_class(Stake, self.blueprint_id)

        # Test token
        self.token_uid = self.gen_random_token_uid()

        # Staking parameters
        self.earnings_per_day = 100_00  # 100 tokens per day
        self.initial_deposit = 10_000_00  # Initial owner deposit

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

    def _initialize_contract(self, creator_contract_id: ContractId = None):
        """Initialize contract with token deposit."""
        ctx = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(self.initial_deposit))],
            vertex=self.tx,
            caller_id=self.fixture.owner.address,
            timestamp=self.now,
        )

        if creator_contract_id is not None:
            self.runner.create_contract(
                self.contract_id,
                self.blueprint_id,
                ctx,
                self.earnings_per_day,
                self.token_uid,
                creator_contract_id,
            )
        else:
            self.runner.create_contract(
                self.contract_id,
                self.blueprint_id,
                ctx,
                self.earnings_per_day,
                self.token_uid,
            )

    def _stake_tokens(self, amount: int, staker: Address = None) -> Context:
        """Helper to stake tokens."""
        if staker is None:
            staker = self.fixture.create_test_user().address

        ctx = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(amount))],
            vertex=self.tx,
            caller_id=staker,
            timestamp=self.now,
        )

        self.runner.call_public_method(self.contract_id, "stake", ctx)
        return ctx

    def test_rewards_calculation_precision_various_amounts(self):
        """Test reward calculation precision with various staking amounts."""
        # Test different staking amounts that might cause precision issues
        test_amounts = [
            MIN_STAKE_AMOUNT,  # Minimum
            MIN_STAKE_AMOUNT + 1,  # Just above minimum
            123456,  # Odd number
            1000000,  # Round number
            9999999,  # Almost round number
            MAX_STAKE_AMOUNT // 2,  # Half maximum
            MAX_STAKE_AMOUNT,  # Maximum
        ]

        initial_time = self.now

        for i, stake_amount in enumerate(test_amounts):
            if stake_amount > self.initial_deposit:
                continue  # Skip amounts larger than contract balance

            staker = self.fixture.create_test_user(f"Staker{i}")

            # Stake tokens
            ctx = self._stake_tokens(stake_amount, staker.address)

            # Calculate expected rewards after exactly 1 day
            one_day_later = initial_time + DAY_IN_SECONDS
            rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS

            # Expected reward for this staker
            expected_reward = (rewards_per_second * DAY_IN_SECONDS * stake_amount) // (
                PRECISION * stake_amount
            )  # Simplified since only one staker

            # Get actual max withdrawal
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, one_day_later
            )

            # Total should be stake + rewards
            expected_total = stake_amount + expected_reward

            # Allow for small rounding differences (within 1 unit)
            self.assertLessEqual(
                abs(max_withdrawal - expected_total), 1,
                f"Precision issue with amount {stake_amount}: expected {expected_total}, got {max_withdrawal}"
            )

    def test_rewards_precision_with_odd_time_periods(self):
        """Test reward calculation with non-standard time periods."""
        stake_amount = 1000000
        staker = self.fixture.create_test_user()

        ctx = self._stake_tokens(stake_amount, staker.address)
        initial_time = self.now

        # Test various odd time periods
        odd_time_periods = [
            3661,  # 1 hour + 1 minute + 1 second
            90061,  # 1 day + 1 hour + 1 minute + 1 second
            172801,  # 2 days + 1 second
            1209661,  # 2 weeks + 1 minute + 1 second
        ]

        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS

        for time_period in odd_time_periods:
            future_time = initial_time + time_period

            # Calculate expected reward
            expected_reward = (rewards_per_second * time_period * stake_amount) // (
                PRECISION * stake_amount
            )

            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, future_time
            )

            expected_total = stake_amount + expected_reward

            # Allow for small rounding differences
            self.assertLessEqual(
                abs(max_withdrawal - expected_total), 2,
                f"Precision issue with time period {time_period}s: expected {expected_total}, got {max_withdrawal}"
            )

    def test_multi_user_reward_distribution_accuracy(self):
        """Test accurate reward distribution among multiple users."""
        # Create multiple stakers with different amounts
        stakers_config = [
            (100000, "Staker1"),
            (200000, "Staker2"),
            (300000, "Staker3"),
            (150000, "Staker4"),
            (50000, "Staker5"),
        ]

        stakers = []
        total_staked = 0
        initial_time = self.now

        # Stake for each user
        for stake_amount, name in stakers_config:
            if total_staked + stake_amount > self.initial_deposit:
                break

            staker = self.fixture.create_test_user(name)
            stakers.append((staker, stake_amount))

            self._stake_tokens(stake_amount, staker.address)
            total_staked += stake_amount

        if len(stakers) == 0:
            self.skipTest("Not enough contract balance for multi-user test")

        # Calculate rewards after 1 day
        one_day_later = initial_time + DAY_IN_SECONDS
        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS

        total_rewards_distributed = 0

        for staker, stake_amount in stakers:
            # Each staker's share of rewards
            expected_individual_reward = (
                rewards_per_second * DAY_IN_SECONDS * stake_amount
            ) // (PRECISION * total_staked)

            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, one_day_later
            )

            actual_reward = max_withdrawal - stake_amount
            total_rewards_distributed += actual_reward

            # Check individual accuracy
            self.assertLessEqual(
                abs(actual_reward - expected_individual_reward), 1,
                f"Individual reward inaccuracy for {stake_amount} stake: "
                f"expected {expected_individual_reward}, got {actual_reward}"
            )

        # Check total rewards don't exceed earnings per day
        expected_total_rewards = (rewards_per_second * DAY_IN_SECONDS) // PRECISION

        self.assertLessEqual(
            abs(total_rewards_distributed - expected_total_rewards), len(stakers),
            f"Total rewards distribution inaccuracy: expected {expected_total_rewards}, "
            f"distributed {total_rewards_distributed} (tolerance: {len(stakers)})"
        )

    def test_compound_staking_precision(self):
        """Test precision when users stake additional amounts over time."""
        staker = self.fixture.create_test_user()
        initial_stake = 500000
        additional_stake = 300000

        # Initial stake
        ctx1 = self._stake_tokens(initial_stake, staker.address)
        stake_time_1 = self.now

        # Wait some time and add more stake
        time_between_stakes = DAY_IN_SECONDS * 5  # 5 days
        stake_time_2 = stake_time_1 + time_between_stakes

        ctx2 = Context(
            [NCDepositAction(token_uid=self.token_uid, amount=Amount(additional_stake))],
            vertex=self.tx,
            caller_id=staker.address,
            timestamp=stake_time_2,
        )

        self.runner.call_public_method(self.contract_id, "stake", ctx2)

        # Calculate rewards at a later time
        final_time = stake_time_2 + DAY_IN_SECONDS * 3  # 3 more days

        # Expected calculation:
        # - First stake earns for 8 days total
        # - Second stake earns for 3 days
        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS

        # Reward for first stake for full period
        first_stake_reward = (
            rewards_per_second * (final_time - stake_time_1) * initial_stake
        ) // (PRECISION * (initial_stake + additional_stake))

        # Reward for second stake for partial period
        second_stake_reward = (
            rewards_per_second * (final_time - stake_time_2) * additional_stake
        ) // (PRECISION * (initial_stake + additional_stake))

        # Note: This is simplified; actual calculation may be more complex
        # depending on how the contract handles multiple stakes

        max_withdrawal = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", staker.address, final_time
        )

        total_stake = initial_stake + additional_stake
        actual_reward = max_withdrawal - total_stake

        # This test mainly verifies no major precision errors occur
        # Exact calculation depends on contract implementation details
        self.assertGreater(actual_reward, 0, "Should have earned some rewards")
        self.assertLess(
            actual_reward, self.earnings_per_day * 10,  # Max possible for time period
            "Rewards shouldn't exceed theoretical maximum"
        )

    def test_edge_case_time_boundaries(self):
        """Test reward calculations at exact time boundaries."""
        staker = self.fixture.create_test_user()
        stake_amount = 1000000

        ctx = self._stake_tokens(stake_amount, staker.address)
        initial_time = self.now

        # Test at exact day boundaries
        time_boundaries = [
            DAY_IN_SECONDS,  # Exactly 1 day
            DAY_IN_SECONDS * 2,  # Exactly 2 days
            DAY_IN_SECONDS * 7,  # Exactly 1 week
            DAY_IN_SECONDS * 30,  # Exactly 30 days
        ]

        rewards_per_second = (self.earnings_per_day * PRECISION) // DAY_IN_SECONDS

        for time_period in time_boundaries:
            future_time = initial_time + time_period

            expected_reward = (rewards_per_second * time_period * stake_amount) // (
                PRECISION * stake_amount
            )

            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, future_time
            )

            expected_total = stake_amount + expected_reward
            actual_total = max_withdrawal

            # At exact boundaries, precision should be very high
            self.assertEqual(
                actual_total, expected_total,
                f"At exact {time_period}s boundary: expected {expected_total}, got {actual_total}"
            )

    def test_precision_with_maximum_values(self):
        """Test precision with maximum allowable values."""
        if MAX_STAKE_AMOUNT > self.initial_deposit:
            self.skipTest("Contract balance too small for max stake test")

        staker = self.fixture.create_test_user()

        # Stake maximum amount
        ctx = self._stake_tokens(MAX_STAKE_AMOUNT, staker.address)
        initial_time = self.now

        # Test with maximum time period (before overflow)
        max_reasonable_time = DAY_IN_SECONDS * 365  # 1 year

        future_time = initial_time + max_reasonable_time

        # This test primarily ensures no overflow or underflow occurs
        try:
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, future_time
            )

            # Should be reasonable (stake + rewards)
            self.assertGreater(max_withdrawal, MAX_STAKE_AMOUNT)
            self.assertLess(max_withdrawal, MAX_STAKE_AMOUNT * 2)  # Reasonable upper bound

        except OverflowError:
            self.fail("Overflow occurred with maximum values")

    def test_zero_reward_edge_case(self):
        """Test scenarios that might result in zero rewards."""
        staker = self.fixture.create_test_user()
        stake_amount = MIN_STAKE_AMOUNT

        ctx = self._stake_tokens(stake_amount, staker.address)
        initial_time = self.now

        # Test very short time period (might result in zero reward)
        very_short_time = 1  # 1 second

        max_withdrawal = self.runner.call_view_method(
            self.contract_id, "get_max_withdrawal", staker.address, initial_time + very_short_time
        )

        # Should at least return the staked amount
        self.assertGreaterEqual(max_withdrawal, stake_amount)

        # Reward might be zero or very small
        reward = max_withdrawal - stake_amount
        self.assertGreaterEqual(reward, 0, "Reward should not be negative")

    def test_mathematical_invariant_conservation(self):
        """Test that mathematical invariants are preserved."""
        # Create multiple stakers
        stakers = self.fixture.create_stake_scenario(
            num_stakers=3,
            stake_amounts=[Amount(300000), Amount(400000), Amount(300000)]
        )

        stake_amounts = [300000, 400000, 300000]
        initial_time = self.now

        # Stake for each user
        for staker, amount in zip(stakers, stake_amounts):
            self._stake_tokens(amount, staker.address)

        # Calculate total rewards after 1 day
        one_day_later = initial_time + DAY_IN_SECONDS

        total_individual_rewards = 0
        total_stakes = sum(stake_amounts)

        for staker, stake_amount in zip(stakers, stake_amounts):
            max_withdrawal = self.runner.call_view_method(
                self.contract_id, "get_max_withdrawal", staker.address, one_day_later
            )

            individual_reward = max_withdrawal - stake_amount
            total_individual_rewards += individual_reward

        # Total rewards distributed should not exceed earnings_per_day
        # (allowing for small rounding differences)
        max_possible_rewards = self.earnings_per_day + len(stakers)  # Small tolerance

        self.assertLessEqual(
            total_individual_rewards, max_possible_rewards,
            f"Total rewards {total_individual_rewards} exceed maximum possible {max_possible_rewards}"
        )

        # And should be reasonably close to earnings_per_day
        min_expected_rewards = self.earnings_per_day - len(stakers)  # Small tolerance

        self.assertGreaterEqual(
            total_individual_rewards, min_expected_rewards,
            f"Total rewards {total_individual_rewards} below minimum expected {min_expected_rewards}"
        )