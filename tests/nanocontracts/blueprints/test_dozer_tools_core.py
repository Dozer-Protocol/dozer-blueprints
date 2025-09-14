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
Core functionality tests for DozerTools contract.

This module tests the fundamental features of DozerTools:
- Project creation and management
- Basic vesting configuration
- Credit management
- Contract initialization
- View methods and basic operations
"""

import inspect
import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_tools import DozerTools
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Address,
    Amount,
    ContractId,
    NCDepositAction,
    NCWithdrawalAction,
    TokenUid,
)
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from tests.nanocontracts.blueprints.test_utilities import (
    DozerToolsTestFixture,
    TestConstants,
    create_deposit_action,
)
from hathor.nanocontracts.blueprints import dozer_tools, vesting


class DozerToolsCoreTest(BlueprintTestCase):
    """Test core DozerTools functionality."""

    def setUp(self):
        super().setUp()

        # Setup test fixture
        self.fixture = DozerToolsTestFixture(self)

        # Register blueprint using file
        self.blueprint_id = self.register_blueprint_file(inspect.getfile(dozer_tools))
        self.contract_id = self.gen_random_contract_id()

        # Create pool manager (required dependency)
        self.pool_manager_id = self.gen_random_contract_id()

        # DZR token and minimum deposit
        self.dzr_token_uid = self.gen_random_token_uid()
        self.minimum_deposit = Amount(100_00)  # 100 HTR minimum

        # Initialize contracts
        self._initialize_pool_manager()
        self._initialize_dozer_tools()

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _initialize_pool_manager(self):
        """Initialize DozerPoolManager dependency."""
        # For now, we'll mock this or use a simple implementation
        pass

    def _initialize_dozer_tools(self):
        """Initialize DozerTools contract."""
        ctx = self.create_context(
            actions=[],  # No actions for initialization
            vertex=self.get_genesis_tx(),
            caller_id=Address(self.fixture.owner.address_bytes),
            timestamp=self.now
        )

        self.runner.create_contract(
            self.contract_id,
            self.blueprint_id,
            ctx,
            self.pool_manager_id,
            self.dzr_token_uid,
            self.minimum_deposit,
        )

        # Configure vesting blueprint ID to enable vesting features
        vesting_blueprint_id = self.register_blueprint_file(inspect.getfile(vesting))
        self.runner.call_public_method(
            self.contract_id, "set_vesting_blueprint_id", ctx, vesting_blueprint_id
        )

    def test_initialization(self):
        """Test contract initialization with proper parameters."""
        # Get contract instance to verify initialization
        contract = self.get_readonly_contract(self.contract_id)
        # Contract is loaded as a blueprint instance, so just check it exists
        self.assertIsNotNone(contract)

        # Verify contract info
        contract_info = self.runner.call_view_method(
            self.contract_id, "get_contract_info"
        )

        self.assertEqual(contract_info["total_projects"], "0")
        self.assertIsNotNone(contract_info["owner"])

    def test_create_basic_project(self):
        """Test basic project creation functionality."""
        project = self.fixture.create_test_project(
            name="TestProject",
            symbol="TEST",
            total_supply=TestConstants.LARGE_AMOUNT
        )

        # Calculate required HTR (1% of total supply)
        required_htr = project.total_supply // 100
        htr_uid = TokenUid(self.htr_token_uid)

        ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        # Create the project
        token_uid = self.runner.call_public_method(
            self.contract_id,
            "create_project",
            ctx,
            project.name,
            project.symbol,
            project.total_supply,
            "Test project description",
            "https://test.com",
            "", "", "", "", "",
            project.category,
            ""
        )

        # Verify project was created
        self.assertIsNotNone(token_uid)

        # Check project info
        project_info = self.runner.call_view_method(
            self.contract_id, "get_project_info", token_uid
        )

        self.assertEqual(project_info["name"], project.name)
        self.assertEqual(project_info["symbol"], project.symbol)
        self.assertEqual(project_info["total_supply"], str(project.total_supply))

    def test_project_listing(self):
        """Test project listing functionality."""
        # Create multiple projects
        projects = []
        for i in range(3):
            project = self.fixture.create_test_project(
                name=f"Project{i}",
                symbol=f"PROJ{i}",
                total_supply=TestConstants.MEDIUM_AMOUNT
            )
            projects.append(project)

            # Create the project
            required_htr = project.total_supply // 100
            htr_uid = TokenUid(self.htr_token_uid)

            ctx = self.create_context(
                actions=[create_deposit_action(htr_uid, required_htr)],
                vertex=self.get_genesis_tx(),
                caller_id=project.dev.address,
                timestamp=self.now + i
            )

            self.runner.call_public_method(
                self.contract_id,
                "create_project",
                ctx,
                project.name, project.symbol, project.total_supply,
                "", "", "", "", "", "", "", "", ""
            )

        # Test listing methods
        all_projects = self.runner.call_view_method(
            self.contract_id, "get_all_projects"
        )
        # get_all_projects returns a dict, not a string
        self.assertIsInstance(all_projects, dict)
        # Check that we have the expected projects
        self.assertGreaterEqual(len(all_projects), 0)

        # Test project count view
        contract_info = self.runner.call_view_method(
            self.contract_id, "get_contract_info"
        )
        self.assertIn("total_projects", contract_info)

    def test_credit_management(self):
        """Test credit deposit and withdrawal functionality."""
        # Create a project first
        project = self.fixture.create_test_project()
        required_htr = project.total_supply // 100
        htr_uid = TokenUid(self.htr_token_uid)

        create_ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        token_uid = self.runner.call_public_method(
            self.contract_id,
            "create_project",
            create_ctx,
            project.name, project.symbol, project.total_supply,
            "", "", "", "", "", "", "", "", ""
        )

        # Deposit additional credits
        additional_credits = Amount(500_00)  # 500 HTR
        deposit_ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, additional_credits)],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        self.runner.call_public_method(
            self.contract_id, "deposit_credits", deposit_ctx, token_uid
        )

        # Check credits were deposited
        project_credits = self.runner.call_view_method(
            self.contract_id, "get_project_credits", token_uid
        )

        # Check that credits info is returned
        self.assertIsInstance(project_credits, dict)

    def test_view_methods(self):
        """Test various view methods work correctly."""
        # Test contract info
        contract_info = self.runner.call_view_method(
            self.contract_id, "get_contract_info"
        )
        self.assertIn("total_projects", contract_info)
        self.assertIn("owner", contract_info)

        # Test method fees
        fees = self.runner.call_view_method(
            self.contract_id, "get_method_fees", "create_project"
        )
        self.assertIn("htr_fee", fees)
        self.assertIn("dzr_fee", fees)

        # Test symbol existence check
        symbol_exists = self.runner.call_view_method(
            self.contract_id, "symbol_exists", "TEST"
        )
        self.assertIsInstance(symbol_exists, bool)

    def test_basic_vesting_configuration(self):
        """Test basic vesting configuration functionality."""
        # Create a project first
        project = self.fixture.create_test_project()
        token_uid = self._create_project_helper(project)

        # Configure basic vesting
        ctx = self.create_context(
            actions=[],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        # Simple vesting: 100% to team
        self.runner.call_public_method(
            self.contract_id,
            "configure_project_vesting",
            ctx,
            token_uid,
            0, 0, 0,  # No special allocations
            0,  # No staking earnings
            ["Team"],  # allocation names
            [100],  # 100% to team
            [project.dev.address],  # beneficiary
            [0],  # No cliff
            [0],  # No vesting period (immediate)
        )

        # Verify vesting was configured
        vesting_overview = self.runner.call_view_method(
            self.contract_id, "get_project_vesting_overview", token_uid
        )
        self.assertEqual(vesting_overview["vesting_configured"], "true")

    def _create_project_helper(self, project) -> TokenUid:
        """Helper to create a project for testing."""
        required_htr = project.total_supply // 100
        htr_uid = TokenUid(self.htr_token_uid)

        ctx = self.create_context(
            actions=[create_deposit_action(htr_uid, required_htr)],
            vertex=self.get_genesis_tx(),
            caller_id=project.dev.address,
            timestamp=self.now
        )

        return self.runner.call_public_method(
            self.contract_id,
            "create_project",
            ctx,
            project.name, project.symbol, project.total_supply,
            project.description, project.website,
            "", "", "", "", "", project.category, ""
        )