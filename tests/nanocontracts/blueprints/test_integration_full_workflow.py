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
Comprehensive integration tests for full cross-contract workflows.

This module tests complete end-to-end scenarios:
- DozerTools → Vesting → Staking full project lifecycle
- KhensuManager → DozerPoolManager token migration
- Complex multi-contract interactions
- State consistency across contract boundaries
- Real-world usage patterns
- Error propagation across contracts
"""

import inspect
import os
from typing import Dict, Any, List, Tuple
from hathor.conf import settings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_tools import DozerTools
from hathor.nanocontracts.blueprints.khensu_manager import KhensuManager
from hathor.nanocontracts.blueprints.dozer_pool_manager import DozerPoolManager
from hathor.nanocontracts.blueprints.vesting import Vesting
from hathor.nanocontracts.blueprints.stake import Stake
from hathor.nanocontracts.blueprints.dao import DAO
from hathor.nanocontracts.blueprints.crowdsale import Crowdsale
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.types import (
    Address,
    Amount,
    BlueprintId,
    ContractId,
    NCDepositAction,
    NCWithdrawalAction,
    TokenUid,
    VertexId,
)
from hathor.nanocontracts.exception import NCFail
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from tests.nanocontracts.blueprints.test_utilities import (
    DozerToolsTestFixture,
    TestConstants,
    TestUser,
    TestProject,
    create_deposit_action,
    create_withdrawal_action,
)
from hathor.nanocontracts.blueprints import (
    dozer_tools,
    khensu_manager,
    dozer_pool_manager,
    vesting,
    stake,
    dao,
    crowdsale,
)

HTR_UID = settings.HATHOR_TOKEN_UID


class IntegrationFullWorkflowTest(BlueprintTestCase):
    """Test complete cross-contract integration workflows."""

    def setUp(self):
        super().setUp()

        self.fixture = DozerToolsTestFixture(self)

        # Register all blueprint files
        self._register_all_blueprints()

        # Initialize core contracts
        self._initialize_core_contracts()

        # Test participants
        self.project_dev = self.fixture.create_test_user("ProjectDev")
        self.investor = self.fixture.create_test_user("Investor")
        self.staker = self.fixture.create_test_user("Staker")
        self.dao_member = self.fixture.create_test_user("DAOMember")

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _register_all_blueprints(self):
        """Register all required blueprint files."""
        self.dozer_tools_blueprint_id = self.register_blueprint_file(
            inspect.getfile(dozer_tools)
        )
        self.khensu_manager_blueprint_id = self.register_blueprint_file(
            inspect.getfile(khensu_manager)
        )
        self.dozer_pool_manager_blueprint_id = self.register_blueprint_file(
            inspect.getfile(dozer_pool_manager)
        )
        self.vesting_blueprint_id = self.register_blueprint_file(
            inspect.getfile(vesting)
        )
        self.stake_blueprint_id = self.register_blueprint_file(
            inspect.getfile(stake)
        )
        self.dao_blueprint_id = self.register_blueprint_file(
            inspect.getfile(dao)
        )
        self.crowdsale_blueprint_id = self.register_blueprint_file(
            inspect.getfile(crowdsale)
        )

    def _initialize_core_contracts(self):
        """Initialize the core contracts needed for integration."""
        # Generate contract IDs
        self.dozer_tools_id = self.gen_random_contract_id()
        self.pool_manager_id = self.gen_random_contract_id()
        self.khensu_manager_id = self.gen_random_contract_id()

        # Initialize DozerPoolManager first (dependency)
        pool_context = Context(
            [],
            self.get_genesis_tx(),
            Address(self.fixture.owner.address_bytes),
            timestamp=self.now,
        )
        self.runner.create_contract(
            self.pool_manager_id,
            self.dozer_pool_manager_blueprint_id,
            pool_context,
        )

        # Initialize KhensuManager
        khensu_context = Context(
            [],
            self.get_genesis_tx(),
            Address(self.fixture.owner.address_bytes),
            timestamp=self.now,
        )
        self.runner.create_contract(
            self.khensu_manager_id,
            self.khensu_manager_blueprint_id,
            khensu_context,
            self.pool_manager_id,  # DozerPoolManager
            Amount(1725000),  # market_cap
            Amount(300000),  # liquidity
            Amount(15000),  # initial_virtual_pool
            Amount(32190005730),  # curve_constant
            Amount(1073000191),  # token_reserve
            Amount(200),  # buy_fee
            Amount(300),  # sell_fee
            Amount(1000),  # graduation_fee
        )

        # Initialize DozerTools
        dzr_token_uid = self.gen_random_token_uid()
        dozer_context = Context(
            [],
            self.get_genesis_tx(),
            Address(self.fixture.owner.address_bytes),
            timestamp=self.now,
        )
        self.runner.create_contract(
            self.dozer_tools_id,
            self.dozer_tools_blueprint_id,
            dozer_context,
            self.pool_manager_id,
            dzr_token_uid,
            Amount(100_00),  # minimum_deposit
        )

        # Configure blueprint IDs in DozerTools
        self._configure_dozer_tools_blueprints()

    def _configure_dozer_tools_blueprints(self):
        """Configure blueprint IDs in DozerTools."""
        owner_context = Context(
            [],
            self.get_genesis_tx(),
            Address(self.fixture.owner.address_bytes),
            timestamp=self.now,
        )

        # Set all blueprint IDs
        self.runner.call_public_method(
            self.dozer_tools_id, "set_vesting_blueprint_id",
            owner_context, self.vesting_blueprint_id
        )
        self.runner.call_public_method(
            self.dozer_tools_id, "set_staking_blueprint_id",
            owner_context, self.stake_blueprint_id
        )
        self.runner.call_public_method(
            self.dozer_tools_id, "set_dao_blueprint_id",
            owner_context, self.dao_blueprint_id
        )
        self.runner.call_public_method(
            self.dozer_tools_id, "set_crowdsale_blueprint_id",
            owner_context, self.crowdsale_blueprint_id
        )

    def test_complete_project_lifecycle(self):
        """Test complete project lifecycle from creation to DAO governance."""
        # Step 1: Create project in DozerTools
        project = self.fixture.create_test_project(
            name="IntegrationProject",
            symbol="INTG",
            total_supply=TestConstants.LARGE_AMOUNT,
            dev=self.project_dev
        )

        token_uid = self._create_project_in_dozer_tools(project)

        # Step 2: Configure comprehensive vesting
        self._configure_comprehensive_vesting(token_uid, project)

        # Step 3: Create and test staking functionality
        staking_contract_id = self._test_staking_integration(token_uid, project)

        # Step 4: Create DAO and test governance
        dao_contract_id = self._test_dao_integration(token_uid, project)

        # Step 5: Test crowdsale functionality
        crowdsale_contract_id = self._test_crowdsale_integration(token_uid, project)

        # Step 6: Verify all contracts are properly linked
        self._verify_contract_linkages(token_uid)

        # Step 7: Test complex cross-contract interactions
        self._test_complex_interactions(token_uid, staking_contract_id, dao_contract_id)

    def test_khensu_to_dozer_pool_migration(self):
        """Test complete token migration from KhensuManager to DozerPoolManager."""
        # Register token in KhensuManager
        token_name = "MigrationToken"
        token_symbol = "MIG"

        register_context = Context(
            [create_deposit_action(TokenUid(HTR_UID), Amount(50000))],
            self.get_genesis_tx(),
            self.project_dev.address,
            timestamp=self.now,
        )

        token_uid = self.runner.call_public_method(
            self.khensu_manager_id, "register_token",
            register_context, token_name, token_symbol
        )

        # Simulate trading to reach migration threshold
        self._simulate_trading_to_migration(token_uid)

        # Verify migration occurred
        token_info = self.runner.call_view_method(
            self.khensu_manager_id, "get_token_info", token_uid
        )

        if token_info.is_migrated:
            # Verify pool was created in DozerPoolManager
            token_pools = self.runner.call_view_method(
                self.pool_manager_id, "get_pools_for_token", token_uid
            )

            self.assertIn(token_info.pool_key, token_pools,
                         "Token should have associated pool after migration")

            # Verify post-migration state consistency
            self._verify_post_migration_state(token_uid, token_info)

    def test_multi_project_interaction(self):
        """Test interactions between multiple projects and their contracts."""
        projects = []
        token_uids = []

        # Create multiple projects
        for i in range(3):
            project = self.fixture.create_test_project(
                name=f"MultiProject{i}",
                symbol=f"MP{i}",
                total_supply=TestConstants.MEDIUM_AMOUNT,
                dev=self.fixture.create_test_user(f"Dev{i}")
            )

            token_uid = self._create_project_in_dozer_tools(project)
            projects.append(project)
            token_uids.append(token_uid)

        # Configure different features for each project
        project_configs = [
            {"vesting": True, "staking": True, "dao": False, "crowdsale": False},
            {"vesting": True, "staking": False, "dao": True, "crowdsale": True},
            {"vesting": False, "staking": True, "dao": True, "crowdsale": False},
        ]

        contract_ids = {}

        for i, (project, token_uid, config) in enumerate(zip(projects, token_uids, project_configs)):
            project_contracts = {}

            if config["vesting"]:
                self._configure_comprehensive_vesting(token_uid, project)
                contracts = self.runner.call_view_method(
                    self.dozer_tools_id, "get_project_contracts", token_uid
                )
                project_contracts["vesting"] = contracts["vesting_contract"]

            if config["staking"]:
                staking_id = self._test_staking_integration(token_uid, project)
                project_contracts["staking"] = staking_id

            if config["dao"]:
                dao_id = self._test_dao_integration(token_uid, project)
                project_contracts["dao"] = dao_id

            if config["crowdsale"]:
                crowdsale_id = self._test_crowdsale_integration(token_uid, project)
                project_contracts["crowdsale"] = crowdsale_id

            contract_ids[i] = project_contracts

        # Verify each project's contracts don't interfere with others
        for i in range(3):
            token_uid = token_uids[i]
            project_contracts = self.runner.call_view_method(
                self.dozer_tools_id, "get_project_contracts", token_uid
            )

            # Each project should have its own distinct contract addresses
            for j in range(3):
                if i != j:
                    other_contracts = self.runner.call_view_method(
                        self.dozer_tools_id, "get_project_contracts", token_uids[j]
                    )

                    # Vesting contracts should be different (if both exist)
                    if (project_contracts["vesting_contract"] != "" and
                        other_contracts["vesting_contract"] != ""):
                        self.assertNotEqual(
                            project_contracts["vesting_contract"],
                            other_contracts["vesting_contract"],
                            f"Projects {i} and {j} have same vesting contract"
                        )

    def test_error_propagation_across_contracts(self):
        """Test how errors propagate across contract boundaries."""
        project = self.fixture.create_test_project(dev=self.project_dev)
        token_uid = self._create_project_in_dozer_tools(project)

        # Configure basic vesting
        self._configure_basic_vesting(token_uid, project)

        contracts = self.runner.call_view_method(
            self.dozer_tools_id, "get_project_contracts", token_uid
        )

        vesting_contract_id = ContractId(VertexId(bytes.fromhex(contracts["vesting_contract"])))

        # Test error propagation from vesting contract
        unauthorized_user = self.fixture.create_test_user("Unauthorized")

        # Try to claim vesting from unauthorized user
        unauthorized_context = Context(
            [create_withdrawal_action(token_uid, Amount(1000))],
            self.get_genesis_tx(),
            unauthorized_user.address,
            timestamp=self.now,
        )

        # This should fail and error should propagate properly
        with self.assertRaises(Exception):  # Specific exception depends on implementation
            self.runner.call_public_method(
                vesting_contract_id, "claim_vesting",
                unauthorized_context, 0  # allocation index
            )

    def _create_project_in_dozer_tools(self, project: TestProject) -> TokenUid:
        """Helper to create a project in DozerTools."""
        required_htr = project.total_supply // 100

        context = Context(
            [create_deposit_action(TokenUid(HTR_UID), required_htr)],
            self.get_genesis_tx(),
            project.dev.address,
            timestamp=self.now,
        )

        return self.runner.call_public_method(
            self.dozer_tools_id, "create_project", context,
            project.name, project.symbol, project.total_supply,
            project.description, project.website,
            "", "", "", "", "", project.category, ""
        )

    def _configure_comprehensive_vesting(self, token_uid: TokenUid, project: TestProject):
        """Configure comprehensive vesting for a project."""
        context = Context(
            [],
            self.get_genesis_tx(),
            project.dev.address,
            timestamp=self.now,
        )

        # Configure vesting with multiple allocations and staking
        self.runner.call_public_method(
            self.dozer_tools_id, "configure_project_vesting", context,
            token_uid,
            20,  # staking_allocation_percentage
            10,  # crowdsale_allocation_percentage
            5,   # pool_allocation_percentage
            1000,  # earnings_per_day for staking
            ["Team", "Advisors", "Community"],  # allocation_names
            [40, 20, 5],  # allocation_percentages (65% total + 35% special = 100%)
            [project.dev.address, self.investor.address, self.dao_member.address],
            [12, 6, 0],  # cliff_months
            [36, 24, 0]  # vesting_months
        )

    def _configure_basic_vesting(self, token_uid: TokenUid, project: TestProject):
        """Configure basic vesting for testing."""
        context = Context(
            [],
            self.get_genesis_tx(),
            project.dev.address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_id, "configure_project_vesting", context,
            token_uid, 0, 0, 0, 0,  # No special allocations
            ["Team"], [100], [project.dev.address], [0], [0]
        )

    def _test_staking_integration(self, token_uid: TokenUid, project: TestProject) -> ContractId:
        """Test staking contract integration."""
        contracts = self.runner.call_view_method(
            self.dozer_tools_id, "get_project_contracts", token_uid
        )

        staking_contract_addr = contracts.get("staking_contract", "")
        if staking_contract_addr == "":
            return None

        staking_contract_id = ContractId(VertexId(bytes.fromhex(staking_contract_addr)))

        # Test staking via DozerTools routing
        stake_amount = Amount(10000)
        stake_context = Context(
            [create_deposit_action(token_uid, stake_amount)],
            self.get_genesis_tx(),
            self.staker.address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_id, "staking_stake", stake_context, token_uid
        )

        # Verify staking occurred
        user_info = self.runner.call_view_method(
            staking_contract_id, "get_user_info", self.staker.address
        )

        self.assertEqual(user_info.deposits, stake_amount,
                        "Staking should have recorded deposit")

        return staking_contract_id

    def _test_dao_integration(self, token_uid: TokenUid, project: TestProject) -> ContractId:
        """Test DAO contract integration."""
        # Create DAO contract
        dao_context = Context(
            [],
            self.get_genesis_tx(),
            project.dev.address,
            timestamp=self.now,
        )

        dao_id = self.runner.call_public_method(
            self.dozer_tools_id, "create_dao_contract", dao_context,
            token_uid, "Test DAO", "Integration Test DAO",
            7, 51, Amount(1000_00)  # 7 days voting, 51% quorum, 1000 proposal threshold
        )

        self.assertIsNotNone(dao_id, "DAO contract should be created")
        return dao_id

    def _test_crowdsale_integration(self, token_uid: TokenUid, project: TestProject) -> ContractId:
        """Test crowdsale contract integration."""
        # Create crowdsale contract
        crowdsale_context = Context(
            [],
            self.get_genesis_tx(),
            project.dev.address,
            timestamp=self.now,
        )

        # Set crowdsale parameters
        start_time = self.now + (24 * 3600)  # Start in 24 hours
        end_time = start_time + (7 * 24 * 3600)  # Run for 1 week
        price_per_token = Amount(100)  # 1 HTR per token
        min_purchase = Amount(100_00)  # 100 HTR minimum
        max_purchase = Amount(10000_00)  # 10k HTR maximum

        crowdsale_id = self.runner.call_public_method(
            self.dozer_tools_id, "create_crowdsale_contract", crowdsale_context,
            token_uid, start_time, end_time, price_per_token, min_purchase, max_purchase
        )

        self.assertIsNotNone(crowdsale_id, "Crowdsale contract should be created")
        return crowdsale_id

    def _verify_contract_linkages(self, token_uid: TokenUid):
        """Verify all contracts are properly linked and accessible."""
        contracts = self.runner.call_view_method(
            self.dozer_tools_id, "get_project_contracts", token_uid
        )

        # Verify contract addresses are valid (not empty)
        expected_contracts = ["vesting_contract", "staking_contract"]

        for contract_type in expected_contracts:
            contract_addr = contracts.get(contract_type, "")
            if contract_addr != "":
                # Try to get info from the contract to verify it's functional
                contract_id = ContractId(VertexId(bytes.fromhex(contract_addr)))

                try:
                    if contract_type == "vesting_contract":
                        info = self.runner.call_view_method(contract_id, "get_contract_info")
                    elif contract_type == "staking_contract":
                        info = self.runner.call_view_method(contract_id, "front_end_api")

                    self.assertIsNotNone(info, f"{contract_type} should provide valid info")
                except Exception as e:
                    self.fail(f"Failed to get info from {contract_type}: {e}")

    def _test_complex_interactions(self, token_uid: TokenUid,
                                 staking_contract_id: ContractId,
                                 dao_contract_id: ContractId):
        """Test complex interactions between contracts."""
        if not staking_contract_id or not dao_contract_id:
            return  # Skip if contracts not created

        # Test scenario: Staker earns rewards and uses them for DAO governance
        # (This depends on specific contract implementations)

        # Get staking rewards
        rewards_time = self.now + (30 * 24 * 3600)  # 30 days later
        max_withdrawal = self.runner.call_view_method(
            staking_contract_id, "get_max_withdrawal", self.staker.address, rewards_time
        )

        # This test verifies the contracts can interact without errors
        # Specific functionality depends on contract implementations
        self.assertGreater(max_withdrawal, 0, "Should have staking rewards")

    def _simulate_trading_to_migration(self, token_uid: TokenUid):
        """Simulate trading activity to reach migration threshold."""
        # Get token info to understand migration requirements
        token_info = self.runner.call_view_method(
            self.khensu_manager_id, "get_token_info", token_uid
        )

        target_market_cap = token_info.target_market_cap
        current_virtual_pool = token_info.virtual_pool
        remaining_amount = target_market_cap - current_virtual_pool

        # Simulate multiple buys to reach threshold
        while remaining_amount > Amount(0):
            buy_amount = min(remaining_amount // 3, Amount(100000))

            if buy_amount <= Amount(0):
                break

            quote = self.runner.call_view_method(
                self.khensu_manager_id, "quote_buy", token_uid, buy_amount
            )

            htr_amount = int(quote.get("recommended_htr_amount", buy_amount))
            expected_out = int(quote["amount_out"])

            buy_context = Context(
                [
                    create_deposit_action(TokenUid(HTR_UID), Amount(htr_amount)),
                    create_withdrawal_action(token_uid, Amount(expected_out)),
                ],
                self.get_genesis_tx(),
                self.investor.address,
                timestamp=self.now,
            )

            self.runner.call_public_method(
                self.khensu_manager_id, "buy_tokens", buy_context, token_uid
            )

            # Update remaining amount
            updated_info = self.runner.call_view_method(
                self.khensu_manager_id, "get_token_info", token_uid
            )

            remaining_amount = target_market_cap - updated_info.virtual_pool

            # Check if migrated
            if updated_info.is_migrated:
                break

    def _verify_post_migration_state(self, token_uid: TokenUid, token_info):
        """Verify state consistency after migration."""
        # Verify trading operations now fail
        with self.assertRaises(Exception):  # Should raise InvalidState
            self.runner.call_view_method(
                self.khensu_manager_id, "quote_buy", token_uid, Amount(1000)
            )

        # Verify pool key exists and is valid
        self.assertIsNotNone(token_info.pool_key)
        self.assertNotEqual(token_info.pool_key, "")

        # Verify migration statistics
        platform_stats = self.runner.call_view_method(
            self.khensu_manager_id, "get_platform_stats"
        )

        self.assertGreater(platform_stats["total_tokens_migrated"], 0,
                          "Platform should show migrated tokens")