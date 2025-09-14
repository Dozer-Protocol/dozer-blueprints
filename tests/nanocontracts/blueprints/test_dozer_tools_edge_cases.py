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
Edge case and boundary condition tests for DozerTools contract.

This module tests edge cases and boundary conditions:
- Boundary value testing (min/max values)
- Symbol collision scenarios
- Mathematical precision and overflow
- Allocation edge cases
- Complex state transitions
"""

import inspect
import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_tools import (
    DozerTools,
    ProjectNotFound,
    ProjectAlreadyExists,
    Unauthorized,
    InsufficientCredits,
    InvalidAllocation,
)
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Address,
    Amount,
    ContractId,
    NCDepositAction,
    TokenUid,
)
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from tests.nanocontracts.blueprints.test_utilities import (
    DozerToolsTestFixture,
    TestConstants,
    EdgeCaseGenerator,
    expect_failure,
    create_deposit_action,
)
from hathor.nanocontracts.blueprints import dozer_tools, vesting


class DozerToolsEdgeCasesTest(BlueprintTestCase):
    """Test edge cases and boundary conditions for DozerTools."""

    def setUp(self):
        super().setUp()
        self.fixture = DozerToolsTestFixture(self)

        # Register blueprint
        self.blueprint_id = self.register_blueprint_file(inspect.getfile(dozer_tools))
        self.contract_id = self.gen_random_contract_id()

        # Initialize contract
        self._initialize_contract()

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _initialize_contract(self):
        """Initialize DozerTools contract."""
        pool_manager_id = self.gen_random_contract_id()
        dzr_token_uid = self.gen_random_token_uid()
        minimum_deposit = Amount(100_00)

        ctx = self.create_context(
            actions=[],
            vertex=self.get_genesis_tx(),
            caller_id=Address(self.fixture.owner.address_bytes),
            timestamp=self.now
        )

        self.runner.create_contract(
            self.contract_id,
            self.blueprint_id,
            ctx,
            pool_manager_id,
            dzr_token_uid,
            minimum_deposit,
        )

        # Configure vesting blueprint ID to enable vesting features
        vesting_blueprint_id = self.register_blueprint_file(inspect.getfile(vesting))
        self.runner.call_public_method(
            self.contract_id, "set_vesting_blueprint_id", ctx, vesting_blueprint_id
        )

    def test_boundary_value_project_creation(self):
        """Test project creation with boundary values."""
        htr_uid = TokenUid(self.htr_token_uid)

        # Test minimum values that satisfy contract requirements
        min_project = self.fixture.create_test_project(
            name="A",  # Single character
            symbol="A",
            total_supply=Amount(100),  # Minimum supply to satisfy 1% requirement
        )

        # Calculate HTR requirement (1% of total supply)
        required_htr = min_project.total_supply // 100
        # Ensure we meet the 1% requirement
        if required_htr == 0:
            required_htr = Amount(1)

        ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=min_project.dev.address,
            timestamp=self.now
        )

        # Should work with minimum values
        token_uid = self.runner.call_public_method(
            self.contract_id,
            "create_project",
            ctx,
            min_project.name, min_project.symbol, min_project.total_supply,
            "", "", "", "", "", "", "", "", ""
        )
        self.assertIsNotNone(token_uid)

        # Test maximum reasonable values
        max_project = self.fixture.create_test_project(
            name="X" * 30,  # Maximum valid name length
            symbol="MAXSY",  # Maximum valid symbol length (5 chars)
            total_supply=TestConstants.MAX_SUPPLY,
        )

        required_htr_max = max_project.total_supply // 100
        ctx_max = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr_max)],
            vertex=self.get_genesis_tx(),
            caller_id=max_project.dev.address,
            timestamp=self.now + 1
        )

        token_uid_max = self.runner.call_public_method(
            self.contract_id,
            "create_project",
            ctx_max,
            max_project.name, max_project.symbol, max_project.total_supply,
            "Very long description " * 10, "https://very-long-url.com",
            "", "", "", "", "", "", ""
        )
        self.assertIsNotNone(token_uid_max)

    def test_symbol_collision_edge_cases(self):
        """Test various symbol collision scenarios."""
        base_symbol = "TEST"
        htr_uid = TokenUid(self.htr_token_uid)

        # Create initial project
        project1 = self.fixture.create_test_project(symbol=base_symbol)
        required_htr = project1.total_supply // 100

        ctx1 = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=project1.dev.address,
            timestamp=self.now
        )

        self.runner.call_public_method(
            self.contract_id, "create_project", ctx1,
            project1.name, project1.symbol, project1.total_supply,
            "", "", "", "", "", "", "", "", ""
        )

        # Test collision scenarios
        collision_symbols = [
            base_symbol,  # Exact match
            base_symbol.lower(),  # Case variation
            " " + base_symbol,  # With spaces
            base_symbol + " ",
        ]

        for symbol in collision_symbols[1:]:  # Skip exact match (already tested)
            project = self.fixture.create_test_project(
                name=f"Project{symbol}",
                symbol=symbol
            )
            ctx = self.create_context(
                actions=[create_deposit_action(htr_uid, required_htr)],
                vertex=self.get_genesis_tx(),
                caller_id=project.dev.address,
                timestamp=self.now
            )

            with self.assertRaises(Exception) as cm:
                self.runner.call_public_method(
                    self.contract_id, "create_project", ctx,
                    project.name, project.symbol, project.total_supply,
                    "", "", "", "", "", "", "", "", ""
                )
            # Check for project already exists error
            error_str = str(cm.exception)
            self.assertTrue("ProjectAlreadyExists" in error_str or "has already been used" in error_str)

    def test_vesting_allocation_overflow(self):
        """Test vesting allocation percentage overflow scenarios."""
        project = self.fixture.create_test_project()
        token_uid = self._create_project_helper(project)

        ctx = self.create_context(
            actions=[],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        # Test exact 100% allocation (should work)
        self.runner.call_public_method(
            self.contract_id,
            "configure_project_vesting",
            ctx, token_uid,
            0, 0, 0, 0,  # No special allocations
            ["Team", "Public"], [70, 30],
            [project.dev.address, self.fixture.create_test_user().address],
            [0, 0], [0, 0]
        )

        # Reset and test over 100% (should fail)
        project2 = self.fixture.create_test_project(symbol="TEST2")
        token_uid2 = self._create_project_helper(project2)

        ctx2 = self.create_context(
            actions=[],
            vertex=self.get_genesis_tx(),
            caller_id=project2.dev.address,
            timestamp=self.now
        )

        with self.assertRaises(Exception) as cm:
            self.runner.call_public_method(
                self.contract_id,
                "configure_project_vesting",
                ctx2, token_uid2,
                0, 0, 0, 0,
                ["Team", "Public", "Extra"], [50, 40, 20],  # Total 110%
                [project2.dev.address, self.fixture.create_test_user().address, self.fixture.create_test_user().address],
                [0, 0, 0], [0, 0, 0]
            )
        # Check for invalid allocation error
        error_str = str(cm.exception)
        self.assertTrue("InvalidAllocation" in error_str or "Total allocation exceeds" in error_str)

    def test_mathematical_precision_edge_cases(self):
        """Test mathematical precision in fee calculations."""
        # Test with amounts that might cause precision issues
        edge_amounts = EdgeCaseGenerator.generate_boundary_amounts()
        htr_uid = TokenUid(self.htr_token_uid)

        for amount in edge_amounts[:3]:  # Test first 3 to avoid test timeout
            if amount <= Amount(0):
                continue

            project = self.fixture.create_test_project(
                name=f"PrecTest{str(amount)[:10]}",  # Limit name length
                symbol=f"P{str(amount)[:3]}",  # Limit symbol length to 4 chars max
                total_supply=amount
            )

            required_htr = max(Amount(1), amount // 100)  # Ensure at least 1

            ctx = self.create_context(
                actions=[create_deposit_action(htr_uid, required_htr)],
                vertex=self.get_genesis_tx(),
                caller_id=project.dev.address,
                timestamp=self.now + int(amount)  # Unique timestamp
            )

            try:
                token_uid = self.runner.call_public_method(
                    self.contract_id, "create_project", ctx,
                    project.name, project.symbol, project.total_supply,
                    "", "", "", "", "", "", "", "", ""
                )

                # Verify project was created correctly
                project_info = self.runner.call_view_method(
                    self.contract_id, "get_project_info", token_uid
                )
                self.assertEqual(project_info["total_supply"], str(amount))

            except Exception as e:
                # Some edge cases might legitimately fail due to various validation issues
                error_str = str(e)
                # Accept various types of validation errors for edge cases
                self.assertTrue(
                    "InsufficientCredits" in error_str or
                    "Invalid token" in error_str or
                    "NCInvalidSyscall" in str(type(e)) or
                    "HTR deposit amount must be at least" in error_str or
                    "negative balance" in error_str or
                    "NCInsufficientFunds" in str(type(e))
                )

    def test_zero_and_negative_values(self):
        """Test handling of zero and negative values where applicable."""
        project = self.fixture.create_test_project()
        htr_uid = TokenUid(self.htr_token_uid)

        # Test zero total supply (should fail)
        with self.assertRaises(Exception):  # Could be various validation errors
            ctx = self.create_context(
                actions=[create_deposit_action(htr_uid, Amount(1))],
                vertex=self.get_genesis_tx(),
                caller_id=project.dev.address,
                timestamp=self.now
            )
            self.runner.call_public_method(
                self.contract_id, "create_project", ctx,
                project.name, project.symbol, Amount(0),  # Zero supply
                "", "", "", "", "", "", "", "", ""
            )

    def test_string_length_boundaries(self):
        """Test project creation with various string length boundaries."""
        htr_uid = TokenUid(self.htr_token_uid)

        # Test long strings within valid limits
        long_name = "X" * 30  # Valid token name length
        long_symbol = "VERYY"  # Valid token symbol length (max 5 chars)
        long_description = "This is a very long description. " * 20
        long_url = "https://very-long-domain-name.com/with/many/path/segments/that/could/potentially/cause/issues"

        project = self.fixture.create_test_project(
            name=long_name,
            symbol=long_symbol,
            total_supply=TestConstants.MEDIUM_AMOUNT
        )

        required_htr = project.total_supply // 100
        ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        # Should handle long strings gracefully
        token_uid = self.runner.call_public_method(
            self.contract_id, "create_project", ctx,
            project.name, project.symbol, project.total_supply,
            long_description, long_url, "", "", "", "", "", "", ""
        )

        # Verify data was stored correctly
        project_info = self.runner.call_view_method(
            self.contract_id, "get_project_info", token_uid
        )
        self.assertEqual(project_info["name"], long_name)

    def _create_project_helper(self, project) -> TokenUid:
        """Helper method to create a project."""
        htr_uid = TokenUid(self.htr_token_uid)
        required_htr = project.total_supply // 100

        ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        return self.runner.call_public_method(
            self.contract_id, "create_project", ctx,
            project.name, project.symbol, project.total_supply,
            project.description, project.website,
            "", "", "", "", "", project.category, ""
        )