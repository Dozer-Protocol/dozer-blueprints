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
Comprehensive test suite for upgrade and migration functionality across all blueprints.

This test suite validates:
- Authorization for upgrades (owner, admin, creator_contract)
- Version validation (semantic versioning)
- State preservation during upgrades
- Remote upgrades via DozerTools
- Migration methods post-upgrade
"""

import unittest

from hathor.conf import settings
from hathor.nanocontracts.blueprints.crowdsale import Crowdsale, InvalidVersion as CrowdsaleInvalidVersion
from hathor.nanocontracts.blueprints.dao import DAO, InvalidVersion as DAOInvalidVersion
from hathor.nanocontracts.blueprints.dozer_pool_manager import DozerPoolManager, InvalidVersion as DozerPoolManagerInvalidVersion
from hathor.nanocontracts.blueprints.dozer_tools import DozerTools
from hathor.nanocontracts.blueprints.oasis import Oasis, InvalidVersion as OasisInvalidVersion
from hathor.nanocontracts.blueprints.stake import Stake, InvalidVersion as StakeInvalidVersion
from hathor.nanocontracts.blueprints.vesting import Vesting, InvalidVersion as VestingInvalidVersion
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    ContractId,
    NCDepositAction,
    TokenUid,
    public,
)
from hathor_tests.nanocontracts.blueprints.unittest import BlueprintTestCase

# Import V2 blueprints
from hathor_tests.nanocontracts.blueprints.v2.crowdsale_v2 import CrowdsaleV2
from hathor_tests.nanocontracts.blueprints.v2.dao_v2 import DAOV2
from hathor_tests.nanocontracts.blueprints.v2.stake_v2 import StakeV2
from hathor_tests.nanocontracts.blueprints.v2.vesting_v2 import VestingV2


class UpgradeAndMigrationTest(BlueprintTestCase):
    """Comprehensive test suite for upgrade and migration functionality."""

    def setUp(self) -> None:
        super().setUp()

        # Generate addresses
        self.owner_address = self.gen_random_address()
        self.platform_address = self.gen_random_address()
        self.user_address = self.gen_random_address()
        self.unauthorized_address = self.gen_random_address()

        # Generate tokens
        self.test_token_uid = self.gen_random_token_uid()
        self.governance_token_uid = self.gen_random_token_uid()
        self.htr_token_uid = TokenUid(settings.HATHOR_TOKEN_UID)

        # Register V1 blueprint IDs
        self.dozer_tools_blueprint_id = self._register_blueprint_class(DozerTools)
        self.crowdsale_blueprint_id = self._register_blueprint_class(Crowdsale)
        self.dao_blueprint_id = self._register_blueprint_class(DAO)
        self.stake_blueprint_id = self._register_blueprint_class(Stake)
        self.vesting_blueprint_id = self._register_blueprint_class(Vesting)
        self.dozer_pool_manager_blueprint_id = self._register_blueprint_class(DozerPoolManager)
        self.oasis_blueprint_id = self._register_blueprint_class(Oasis)

        # Register V2 blueprint IDs
        self.crowdsale_v2_blueprint_id = self._register_blueprint_class(CrowdsaleV2)
        self.dao_v2_blueprint_id = self._register_blueprint_class(DAOV2)
        self.stake_v2_blueprint_id = self._register_blueprint_class(StakeV2)
        self.vesting_v2_blueprint_id = self._register_blueprint_class(VestingV2)

        # Create DozerTools contract
        self.dozer_tools_nc_id = self.gen_random_contract_id()
        self.dozer_pool_manager_nc_id = self.gen_random_contract_id()
        self.tx = self.get_genesis_tx()
        self._create_dozer_tools()

    def _create_dozer_tools(self) -> None:
        """Create a DozerTools contract for testing."""
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.create_contract(
            self.dozer_tools_nc_id,
            self.dozer_tools_blueprint_id,
            context,
            self.dozer_pool_manager_nc_id,
            self.test_token_uid,
            Amount(1000),
        )

    def _create_crowdsale(self) -> ContractId:
        """Create a crowdsale contract for testing."""
        nc_id = self.gen_random_contract_id()
        deposit_amount = Amount(1000000)

        context = self.create_context(
            actions=[NCDepositAction(token_uid=self.test_token_uid, amount=deposit_amount)],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.create_contract(
            nc_id,
            self.crowdsale_blueprint_id,
            context,
            self.test_token_uid,
            Amount(100),  # rate
            Amount(1000),  # soft_cap
            Amount(10000),  # hard_cap
            Amount(10),  # min_deposit
            self.now + 100,  # start_time
            self.now + 1000,  # end_time
            Amount(500),  # platform_fee
            Amount(100),  # participation_fee
            self.dozer_tools_nc_id,  # creator_contract_id
        )

        return nc_id

    def _create_dao(self) -> ContractId:
        """Create a DAO contract for testing."""
        nc_id = self.gen_random_contract_id()
        stake_nc_id = self.gen_random_contract_id()

        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.create_contract(
            nc_id,
            self.dao_blueprint_id,
            context,
            "Test DAO",  # name
            "Description",  # description
            self.governance_token_uid,  # governance_token
            stake_nc_id,  # staking_contract
            7,  # voting_period_days
            51,  # quorum_percentage
            Amount(1000),  # proposal_threshold
            self.dozer_tools_nc_id,  # creator_contract_id
        )

        return nc_id

    def _create_stake(self) -> ContractId:
        """Create a stake contract for testing."""
        nc_id = self.gen_random_contract_id()
        deposit_amount = Amount(1000000)

        context = self.create_context(
            actions=[NCDepositAction(token_uid=self.test_token_uid, amount=deposit_amount)],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.create_contract(
            nc_id,
            self.stake_blueprint_id,
            context,
            100,  # earnings_per_day
            self.test_token_uid,
            self.dozer_tools_nc_id,
        )

        return nc_id

    def _create_vesting(self) -> ContractId:
        """Create a vesting contract for testing."""
        nc_id = self.gen_random_contract_id()
        deposit_amount = Amount(1000000)

        context = self.create_context(
            actions=[NCDepositAction(token_uid=self.test_token_uid, amount=deposit_amount)],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.create_contract(
            nc_id,
            self.vesting_blueprint_id,
            context,
            self.test_token_uid,
            self.dozer_tools_nc_id,
        )

        return nc_id

    # ========================================================================
    # Crowdsale Tests
    # ========================================================================

    def test_crowdsale_owner_can_upgrade(self):
        """Test that crowdsale owner can upgrade the contract."""
        nc_id = self._create_crowdsale()

        # Verify V1 version
        version_before = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_before, "1.0.0")

        # Create a new blueprint ID for V2 (simulating upgrade)
        crowdsale_v2_blueprint_id = self._register_blueprint_class(Crowdsale)

        # Upgrade to V2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            crowdsale_v2_blueprint_id,
            "2.0.0",
        )

        # Verify V2 version
        version_after = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_after, "2.0.0")

    def test_crowdsale_unauthorized_cannot_upgrade(self):
        """Test that unauthorized user cannot upgrade crowdsale."""
        nc_id = self._create_crowdsale()

        # Create a new blueprint ID for V2
        crowdsale_v2_blueprint_id = self._register_blueprint_class(Crowdsale)

        # Try to upgrade with unauthorized address
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.unauthorized_address,
            timestamp=self.now,
        )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                nc_id,
                "upgrade_contract",
                context,
                crowdsale_v2_blueprint_id,
                "2.0.0",
            )

    def test_crowdsale_version_validation_rejects_lower_version(self):
        """Test that crowdsale rejects upgrade to lower version."""
        nc_id = self._create_crowdsale()

        # Create a new blueprint ID for V2
        crowdsale_v2_blueprint_id = self._register_blueprint_class(Crowdsale)

        # Try to upgrade to lower version
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        with self.assertRaises(CrowdsaleInvalidVersion):
            self.runner.call_public_method(
                nc_id,
                "upgrade_contract",
                context,
                crowdsale_v2_blueprint_id,
                "0.9.0",  # Lower than 1.0.0
            )

    def test_crowdsale_version_validation_rejects_same_version(self):
        """Test that crowdsale rejects upgrade to same version."""
        nc_id = self._create_crowdsale()

        # Create a new blueprint ID for V2
        crowdsale_v2_blueprint_id = self._register_blueprint_class(Crowdsale)

        # Try to upgrade to same version
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        with self.assertRaises(CrowdsaleInvalidVersion):
            self.runner.call_public_method(
                nc_id,
                "upgrade_contract",
                context,
                crowdsale_v2_blueprint_id,
                "1.0.0",
            )

    def test_crowdsale_state_preservation(self):
        """Test that crowdsale state is preserved during upgrade."""
        nc_id = self._create_crowdsale()

        # Get state before upgrade
        contract_before = self.get_readonly_contract(nc_id)
        assert isinstance(contract_before, Crowdsale)
        rate_before = contract_before.rate
        soft_cap_before = contract_before.soft_cap
        hard_cap_before = contract_before.hard_cap

        # Create a new blueprint ID for V2
        crowdsale_v2_blueprint_id = self._register_blueprint_class(Crowdsale)

        # Upgrade to V2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            crowdsale_v2_blueprint_id,
            "2.0.0",
        )

        # Verify state preserved
        contract_after = self.get_readonly_contract(nc_id)
        assert isinstance(contract_after, Crowdsale)
        self.assertEqual(contract_after.rate, rate_before)
        self.assertEqual(contract_after.soft_cap, soft_cap_before)
        self.assertEqual(contract_after.hard_cap, hard_cap_before)

    # ========================================================================
    # DAO Tests
    # ========================================================================

    def test_dao_creator_contract_can_upgrade(self):
        """Test that DAO creator contract can upgrade the contract."""
        nc_id = self._create_dao()

        # Verify V1 version
        version_before = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_before, "1.0.0")

        # Create a new blueprint ID for V2
        dao_v2_blueprint_id = self._register_blueprint_class(DAO)

        # Upgrade via DozerTools (creator contract)
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "upgrade_specific_contract",
            context,
            nc_id,
            dao_v2_blueprint_id,
            "2.0.0",
        )

        # Verify V2 version
        version_after = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_after, "2.0.0")

    def test_dao_version_validation_rejects_lower_version(self):
        """Test that DAO rejects upgrade to lower version."""
        nc_id = self._create_dao()

        # Create a new blueprint ID for V2
        dao_v2_blueprint_id = self._register_blueprint_class(DAO)

        # Try to upgrade to lower version via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        with self.assertRaises(DAOInvalidVersion):
            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "upgrade_specific_contract",
                context,
                nc_id,
                dao_v2_blueprint_id,
                "0.9.0",  # Lower than 1.0.0
            )

    # ========================================================================
    # Stake Tests
    # ========================================================================

    def test_stake_owner_can_upgrade(self):
        """Test that stake owner can upgrade the contract."""
        nc_id = self._create_stake()

        # Verify V1 version
        version_before = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_before, "1.0.0")

        # Create a new blueprint ID for V2
        stake_v2_blueprint_id = self._register_blueprint_class(Stake)

        # Upgrade to V2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            stake_v2_blueprint_id,
            "2.0.0",
        )

        # Verify V2 version
        version_after = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_after, "2.0.0")

    def test_stake_version_validation_rejects_same_version(self):
        """Test that stake rejects upgrade to same version."""
        nc_id = self._create_stake()

        # Create a new blueprint ID for V2
        stake_v2_blueprint_id = self._register_blueprint_class(Stake)

        # Try to upgrade to same version
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        with self.assertRaises(StakeInvalidVersion):
            self.runner.call_public_method(
                nc_id,
                "upgrade_contract",
                context,
                stake_v2_blueprint_id,
                "1.0.0",
            )

    # ========================================================================
    # Vesting Tests
    # ========================================================================

    def test_vesting_admin_can_upgrade(self):
        """Test that vesting admin can upgrade the contract."""
        nc_id = self._create_vesting()

        # Verify V1 version
        version_before = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_before, "1.0.0")

        # Create a new blueprint ID for V2
        vesting_v2_blueprint_id = self._register_blueprint_class(Vesting)

        # Upgrade to V2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            vesting_v2_blueprint_id,
            "2.0.0",
        )

        # Verify V2 version
        version_after = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_after, "2.0.0")

    def test_vesting_version_validation_rejects_lower_version(self):
        """Test that vesting rejects upgrade to lower version."""
        nc_id = self._create_vesting()

        # Create a new blueprint ID for V2
        vesting_v2_blueprint_id = self._register_blueprint_class(Vesting)

        # Try to upgrade to lower version
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        with self.assertRaises(VestingInvalidVersion):
            self.runner.call_public_method(
                nc_id,
                "upgrade_contract",
                context,
                vesting_v2_blueprint_id,
                "0.9.0",
            )

    # ========================================================================
    # DozerTools Remote Upgrade Tests
    # ========================================================================

    def test_dozer_tools_can_upgrade_crowdsale(self):
        """Test that DozerTools can remotely upgrade a crowdsale contract."""
        nc_id = self._create_crowdsale()

        # Verify V1
        version_before = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_before, "1.0.0")

        # Create new blueprint for V2
        crowdsale_v2_blueprint_id = self._register_blueprint_class(Crowdsale)

        # Upgrade via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "upgrade_specific_contract",
            context,
            nc_id,
            crowdsale_v2_blueprint_id,
            "2.0.0",
        )

        # Verify V2
        version_after = self.runner.call_view_method(nc_id, "get_contract_version")
        self.assertEqual(version_after, "2.0.0")

    # ========================================================================
    # Migration Tests - V1 to V2
    # ========================================================================

    def test_crowdsale_migrate_v1_to_v2(self):
        """Test crowdsale migration from V1 to V2."""
        nc_id = self._create_crowdsale()

        # Upgrade to V2 blueprint
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            self.crowdsale_v2_blueprint_id,
            "2.0.0",
        )

        # Verify migration_completed is False before migration
        migration_status_before = self.runner.call_view_method(nc_id, "get_migration_status")
        self.assertFalse(migration_status_before)

        # Call migrate_v1_to_v2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "migrate_v1_to_v2",
            context,
        )

        # Verify migration_completed is True after migration
        migration_status_after = self.runner.call_view_method(nc_id, "get_migration_status")
        self.assertTrue(migration_status_after)

    def test_crowdsale_migrate_via_dozer_tools(self):
        """Test crowdsale migration via DozerTools."""
        nc_id = self._create_crowdsale()

        # Upgrade to V2 blueprint via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "upgrade_specific_contract",
            context,
            nc_id,
            self.crowdsale_v2_blueprint_id,
            "2.0.0",
        )

        # Call migrate via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "migrate_specific_contract",
            context,
            nc_id,
            "migrate_v1_to_v2",
        )

        # Verify migration_completed is True
        migration_status = self.runner.call_view_method(nc_id, "get_migration_status")
        self.assertTrue(migration_status)

    def test_crowdsale_migrate_authorization(self):
        """Test that only authorized users can migrate crowdsale."""
        nc_id = self._create_crowdsale()

        # Upgrade to V2 blueprint
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            self.crowdsale_v2_blueprint_id,
            "2.0.0",
        )

        # Try to migrate with unauthorized address
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.unauthorized_address,
            timestamp=self.now,
        )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                nc_id,
                "migrate_v1_to_v2",
                context,
            )

    def test_dao_migrate_v1_to_v2(self):
        """Test DAO migration from V1 to V2."""
        nc_id = self._create_dao()

        # Upgrade to V2 blueprint via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "upgrade_specific_contract",
            context,
            nc_id,
            self.dao_v2_blueprint_id,
            "2.0.0",
        )

        # Verify governance_version is 0 before migration
        governance_version_before = self.runner.call_view_method(nc_id, "get_governance_version")
        self.assertEqual(governance_version_before, 0)

        # Call migrate_v1_to_v2 via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "migrate_specific_contract",
            context,
            nc_id,
            "migrate_v1_to_v2",
        )

        # Verify governance_version is 2 after migration
        governance_version_after = self.runner.call_view_method(nc_id, "get_governance_version")
        self.assertEqual(governance_version_after, 2)

    def test_stake_migrate_v1_to_v2(self):
        """Test stake migration from V1 to V2."""
        nc_id = self._create_stake()

        # Upgrade to V2 blueprint
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            self.stake_v2_blueprint_id,
            "2.0.0",
        )

        # Verify reward_multiplier is 0 before migration
        reward_multiplier_before = self.runner.call_view_method(nc_id, "get_reward_multiplier")
        self.assertEqual(reward_multiplier_before, 0)

        # Call migrate_v1_to_v2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "migrate_v1_to_v2",
            context,
        )

        # Verify reward_multiplier is 10000 (1x) after migration
        reward_multiplier_after = self.runner.call_view_method(nc_id, "get_reward_multiplier")
        self.assertEqual(reward_multiplier_after, 10000)

    def test_stake_migrate_authorization(self):
        """Test that only authorized users can migrate stake."""
        nc_id = self._create_stake()

        # Upgrade to V2 blueprint
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            self.stake_v2_blueprint_id,
            "2.0.0",
        )

        # Try to migrate with unauthorized address
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.unauthorized_address,
            timestamp=self.now,
        )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                nc_id,
                "migrate_v1_to_v2",
                context,
            )

    def test_vesting_migrate_v1_to_v2(self):
        """Test vesting migration from V1 to V2."""
        nc_id = self._create_vesting()

        # Upgrade to V2 blueprint
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "upgrade_contract",
            context,
            self.vesting_v2_blueprint_id,
            "2.0.0",
        )

        # Verify max_allocations_override is 0 before migration
        max_allocations_before = self.runner.call_view_method(nc_id, "get_max_allocations_override")
        self.assertEqual(max_allocations_before, 0)

        # Call migrate_v1_to_v2
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            nc_id,
            "migrate_v1_to_v2",
            context,
        )

        # Verify max_allocations_override is 10 after migration
        max_allocations_after = self.runner.call_view_method(nc_id, "get_max_allocations_override")
        self.assertEqual(max_allocations_after, 10)

    def test_vesting_migrate_via_dozer_tools(self):
        """Test vesting migration via DozerTools."""
        nc_id = self._create_vesting()

        # Upgrade to V2 blueprint via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "upgrade_specific_contract",
            context,
            nc_id,
            self.vesting_v2_blueprint_id,
            "2.0.0",
        )

        # Call migrate via DozerTools
        context = self.create_context(
            actions=[],
            vertex=self.tx,
            caller_id=self.owner_address,
            timestamp=self.now,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "migrate_specific_contract",
            context,
            nc_id,
            "migrate_v1_to_v2",
        )

        # Verify max_allocations_override is 10
        max_allocations = self.runner.call_view_method(nc_id, "get_max_allocations_override")
        self.assertEqual(max_allocations, 10)


if __name__ == "__main__":
    unittest.main()
