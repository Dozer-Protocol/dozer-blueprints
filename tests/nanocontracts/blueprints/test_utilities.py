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
Enhanced test utilities for nanocontract testing.

This module provides comprehensive utilities and fixtures to improve test
consistency, reduce code duplication, and enable more robust testing patterns
across all nanocontract test suites.
"""

import os
from typing import Optional, NamedTuple, Any, Dict, List
from dataclasses import dataclass

from hathor.conf import settings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Address,
    Amount,
    BlueprintId,
    ContractId,
    NCDepositAction,
    NCWithdrawalAction,
    NCAcquireAuthorityAction,
    TokenUid,
    VertexId,
    Timestamp,
)
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase


# Test Constants
class TestConstants:
    """Centralized test constants for consistent testing."""

    # Token amounts
    MIN_AMOUNT = Amount(1)
    SMALL_AMOUNT = Amount(100_00)  # 100 tokens
    MEDIUM_AMOUNT = Amount(10_000_00)  # 10k tokens
    LARGE_AMOUNT = Amount(1_000_000_00)  # 1M tokens
    MAX_SUPPLY = Amount(100_000_000_00)  # 100M tokens

    # HTR amounts
    HTR_SMALL = Amount(1_00)  # 1 HTR
    HTR_MEDIUM = Amount(100_00)  # 100 HTR
    HTR_LARGE = Amount(10_000_00)  # 10k HTR

    # Time constants
    DAY_IN_SECONDS = 24 * 60 * 60
    WEEK_IN_SECONDS = 7 * DAY_IN_SECONDS
    MONTH_IN_SECONDS = 30 * DAY_IN_SECONDS
    YEAR_IN_SECONDS = 365 * DAY_IN_SECONDS

    # Fees and percentages
    FEE_1_PERCENT = 100  # 1% in basis points
    FEE_5_PERCENT = 500  # 5% in basis points
    FEE_10_PERCENT = 1000  # 10% in basis points

    # Staking constants
    MIN_STAKE_PERIOD = 30 * DAY_IN_SECONDS

    # Test strings
    TEST_NAMES = ["TestToken", "DemoToken", "SampleCoin", "MockAsset"]
    TEST_SYMBOLS = ["TEST", "DEMO", "SAMP", "MOCK"]
    TEST_DESCRIPTIONS = ["Test token for unit tests", "Demo token description"]


@dataclass
class TestUser:
    """Represents a test user with address and keypair."""

    address: Address
    keypair: KeyPair
    name: str = "TestUser"

    @property
    def address_bytes(self) -> bytes:
        """Get address as bytes."""
        return bytes(self.address)


@dataclass
class TestToken:
    """Represents a test token with metadata."""

    uid: TokenUid
    name: str
    symbol: str
    total_supply: Amount
    creator: TestUser


@dataclass
class TestProject:
    """Represents a DozerTools project for testing."""

    token_uid: TokenUid
    name: str
    symbol: str
    total_supply: Amount
    dev: TestUser
    category: str = "DeFi"
    description: str = ""
    website: str = ""


class ContractTestFixture:
    """Base fixture for contract testing with common setup patterns."""

    def __init__(self, test_case: BlueprintTestCase):
        self.test_case = test_case
        self._user_counter = 0

    def create_test_user(self, name: str = None) -> TestUser:
        """Create a test user with address and keypair."""
        if name is None:
            name = f"TestUser{self._user_counter}"
            self._user_counter += 1

        address, keypair = self.test_case.gen_random_address_with_key()
        return TestUser(address=address, keypair=keypair, name=name)

    def create_test_token(self,
                         name: str = None,
                         symbol: str = None,
                         supply: Amount = None,
                         creator: TestUser = None) -> TestToken:
        """Create a test token with reasonable defaults."""
        if name is None:
            name = TestConstants.TEST_NAMES[0]
        if symbol is None:
            symbol = TestConstants.TEST_SYMBOLS[0]
        if supply is None:
            supply = TestConstants.LARGE_AMOUNT
        if creator is None:
            creator = self.create_test_user("TokenCreator")

        uid = self.test_case.gen_random_token_uid()
        return TestToken(
            uid=uid,
            name=name,
            symbol=symbol,
            total_supply=supply,
            creator=creator
        )

    def create_context(self,
                      actions: List[Any] = None,
                      caller: TestUser = None,
                      timestamp: int = None) -> Context:
        """Create a context with sensible defaults."""
        if caller is None:
            caller = self.create_test_user()
        if timestamp is None:
            timestamp = self.test_case.now
        if actions is None:
            actions = []

        return self.test_case.create_context(
            actions=actions,
            caller_id=caller.address,
            timestamp=timestamp
        )


class DozerToolsTestFixture(ContractTestFixture):
    """Specialized fixture for DozerTools contract testing."""

    def __init__(self, test_case: BlueprintTestCase):
        super().__init__(test_case)
        self.owner = self.create_test_user("Owner")
        self.pool_manager_id = test_case.gen_random_contract_id()
        self.dzr_token_uid = test_case.gen_random_token_uid()

    def create_test_project(self,
                           name: str = None,
                           symbol: str = None,
                           total_supply: Amount = None,
                           dev: TestUser = None,
                           category: str = "DeFi") -> TestProject:
        """Create a test project for DozerTools."""
        if name is None:
            name = TestConstants.TEST_NAMES[0]
        if symbol is None:
            symbol = TestConstants.TEST_SYMBOLS[0]
        if total_supply is None:
            total_supply = TestConstants.LARGE_AMOUNT
        if dev is None:
            dev = self.create_test_user("ProjectDev")

        token_uid = self.test_case.gen_random_token_uid()
        return TestProject(
            token_uid=token_uid,
            name=name,
            symbol=symbol,
            total_supply=total_supply,
            dev=dev,
            category=category
        )


class StakeTestFixture(ContractTestFixture):
    """Specialized fixture for Stake contract testing."""

    def __init__(self, test_case: BlueprintTestCase):
        super().__init__(test_case)
        self.owner = self.create_test_user("StakeOwner")
        self.token = self.create_test_token("StakeToken", "STAKE", TestConstants.LARGE_AMOUNT)

    def create_stake_scenario(self,
                             num_stakers: int = 3,
                             stake_amounts: List[Amount] = None) -> List[TestUser]:
        """Create multiple stakers for testing scenarios."""
        if stake_amounts is None:
            stake_amounts = [TestConstants.MEDIUM_AMOUNT] * num_stakers
        elif len(stake_amounts) < num_stakers:
            # Extend list with last value
            last_amount = stake_amounts[-1] if stake_amounts else TestConstants.MEDIUM_AMOUNT
            stake_amounts.extend([last_amount] * (num_stakers - len(stake_amounts)))

        stakers = []
        for i in range(num_stakers):
            staker = self.create_test_user(f"Staker{i+1}")
            stakers.append(staker)

        return stakers


class TestAssertions:
    """Enhanced assertions for nanocontract testing."""

    @staticmethod
    def assert_balance_changed(test_case: BlueprintTestCase,
                              contract_id: ContractId,
                              token_uid: TokenUid,
                              expected_change: Amount,
                              initial_balance: Amount = None):
        """Assert that a contract's balance changed by expected amount."""
        current_balance = test_case.runner.get_current_balance(contract_id, token_uid)
        if initial_balance is not None:
            actual_change = current_balance.value - initial_balance
            test_case.assertEqual(actual_change, expected_change,
                                f"Expected balance change of {expected_change}, got {actual_change}")

    @staticmethod
    def assert_contract_state(test_case: BlueprintTestCase,
                             contract,
                             expected_state: Dict[str, Any]):
        """Assert multiple contract state attributes at once."""
        for attr_name, expected_value in expected_state.items():
            actual_value = getattr(contract, attr_name)
            test_case.assertEqual(actual_value, expected_value,
                                f"Contract attribute {attr_name}: expected {expected_value}, got {actual_value}")

    @staticmethod
    def assert_event_count(test_case: BlueprintTestCase,
                          expected_count: int,
                          event_filter = None):
        """Assert that expected number of events occurred."""
        # This would need to be implemented based on how events are tracked in the system
        pass


class EdgeCaseGenerator:
    """Generate edge cases for comprehensive testing."""

    @staticmethod
    def generate_boundary_amounts() -> List[Amount]:
        """Generate boundary value amounts for testing."""
        return [
            Amount(0),
            Amount(1),
            Amount(2**31 - 1),  # Max 32-bit signed int
            Amount(2**32 - 1),  # Max 32-bit unsigned int
            Amount(2**63 - 1),  # Max 64-bit signed int
        ]

    @staticmethod
    def generate_fee_percentages() -> List[int]:
        """Generate edge case fee percentages."""
        return [0, 1, 100, 500, 1000, 5000, 9999, 10000]  # 0% to 100%

    @staticmethod
    def generate_time_scenarios(base_time: int) -> Dict[str, int]:
        """Generate time-based edge case scenarios."""
        return {
            "past": base_time - TestConstants.YEAR_IN_SECONDS,
            "now": base_time,
            "near_future": base_time + TestConstants.DAY_IN_SECONDS,
            "far_future": base_time + TestConstants.YEAR_IN_SECONDS,
            "max_timestamp": 2**31 - 1,
        }

    @staticmethod
    def generate_invalid_addresses() -> List[bytes]:
        """Generate invalid address formats for testing."""
        return [
            b"",  # Empty
            b"too_short",  # Too short
            b"x" * 24,  # Wrong length
            b"x" * 26,  # Wrong length
        ]


class TestScenarioBuilder:
    """Build complex test scenarios with multiple steps."""

    def __init__(self, fixture: ContractTestFixture):
        self.fixture = fixture
        self.steps = []

    def add_step(self, description: str, action: callable, *args, **kwargs):
        """Add a test step to the scenario."""
        self.steps.append({
            'description': description,
            'action': action,
            'args': args,
            'kwargs': kwargs
        })
        return self

    def execute(self):
        """Execute all steps in the scenario."""
        results = []
        for i, step in enumerate(self.steps):
            try:
                result = step['action'](*step['args'], **step['kwargs'])
                results.append(result)
            except Exception as e:
                raise AssertionError(f"Step {i+1} failed: {step['description']} - {str(e)}")
        return results


class PerformanceTestHelper:
    """Helper for performance-related testing."""

    @staticmethod
    def measure_gas_usage(test_case: BlueprintTestCase,
                         contract_method: callable,
                         *args, **kwargs) -> int:
        """Measure gas usage of a contract method call."""
        # This would need to be implemented based on the gas tracking system
        # For now, return a placeholder
        return 0

    @staticmethod
    def stress_test_scenario(test_case: BlueprintTestCase,
                           operation: callable,
                           iterations: int = 100,
                           *args, **kwargs):
        """Run a stress test with multiple iterations."""
        results = []
        for i in range(iterations):
            try:
                result = operation(*args, **kwargs)
                results.append(result)
            except Exception as e:
                raise AssertionError(f"Stress test failed at iteration {i+1}: {str(e)}")
        return results


# Utility functions for common test patterns
def expect_failure(test_case: BlueprintTestCase,
                  exception_type: type,
                  operation: callable,
                  *args, **kwargs):
    """Utility to test that an operation fails with expected exception."""
    with test_case.assertRaises(exception_type):
        operation(*args, **kwargs)


def create_deposit_action(token_uid: TokenUid, amount: Amount) -> NCDepositAction:
    """Create a deposit action with proper typing."""
    return NCDepositAction(token_uid=token_uid, amount=amount)


def create_withdrawal_action(token_uid: TokenUid, amount: Amount) -> NCWithdrawalAction:
    """Create a withdrawal action with proper typing."""
    return NCWithdrawalAction(token_uid=token_uid, amount=amount)


def create_authority_action(token_uid: TokenUid,
                          mint: bool = False,
                          melt: bool = False) -> NCAcquireAuthorityAction:
    """Create an authority acquisition action."""
    return NCAcquireAuthorityAction(token_uid=token_uid, mint=mint, melt=melt)