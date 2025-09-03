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

import os
import unittest
from typing import Optional

from hathor.conf import settings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_tools import (
    DozerTools,
    ProjectNotFound,
    Unauthorized,
    InsufficientCredits,
    InvalidAllocation,
    VESTING_BLUEPRINT_ID,
    STAKING_BLUEPRINT_ID,
    DAO_BLUEPRINT_ID,
    CROWDSALE_BLUEPRINT_ID,
)
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
    NCAcquireAuthorityAction,
    NCDepositAction,
    NCWithdrawalAction,
    TokenUid,
    VertexId,
)
from hathor.nanocontracts.exception import NCForbiddenAction, NCInvalidAction
from hathor.transaction.base_transaction import BaseTransaction
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

DOZER_POOL_MANAGER_BLUEPRINT_ID = (
    "d6c09caa2f1f7ef6a6f416301c2b665e041fa819a792e53b8409c9c1aed2c89a"
)


class DozerToolsTest(BlueprintTestCase):
    """Test cases for DozerTools blueprint."""

    def setUp(self) -> None:
        super().setUp()

        # Generate blueprint and contract IDs
        self.dozer_tools_blueprint_id = self.gen_random_blueprint_id()
        self.dozer_tools_nc_id = self.gen_random_contract_id()

        # Register all blueprint classes
        self._register_blueprint_class( DozerTools,self.dozer_tools_blueprint_id)
        self._register_blueprint_class(Vesting,VESTING_BLUEPRINT_ID)
        self._register_blueprint_class(Stake,STAKING_BLUEPRINT_ID)
        self._register_blueprint_class(DAO,DAO_BLUEPRINT_ID)
        self._register_blueprint_class(Crowdsale,CROWDSALE_BLUEPRINT_ID)
        self._register_blueprint_class(
            DozerPoolManager,BlueprintId(VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID))))
        )

        # Create DozerPoolManager for testing
        self.pool_manager_nc_id = self.gen_random_contract_id()
        self.pool_manager_blueprint_id = BlueprintId(
            VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID)))
        )

        # Initialize DozerPoolManager
        pool_manager_context = Context(
            [],
            self._get_any_tx(),
            Address(self._get_any_address()[0]),
            timestamp=self.get_current_timestamp(),
        )
        self.runner.create_contract(
            self.pool_manager_nc_id,
            self.pool_manager_blueprint_id,
            pool_manager_context,
        )

        # Test addresses
        self.owner_address_bytes, self.owner_key = self._get_any_address()
        self.owner_address = Address(self.owner_address_bytes)
        self.dev_address_bytes, self.dev_key = self._get_any_address()
        self.dev_address = Address(self.dev_address_bytes)
        self.user_address_bytes, self.user_key = self._get_any_address()
        self.user_address = Address(self.user_address_bytes)

        # DZR token parameters (placeholder)
        self.dzr_token_uid = TokenUid(VertexId(b"\x01" * 32))
        self.minimum_deposit = Amount(100)  # 1 HTR

        # Initialize DozerTools
        self._initialize_dozer_tools()

    def _get_any_tx(self) -> BaseTransaction:
        genesis = self.manager.tx_storage.get_all_genesis()
        tx = [t for t in genesis if t.is_transaction][0]
        return tx

    def _get_any_address(self):
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_dozer_tools(self):
        """Initialize the DozerTools contract"""
        tx = self._get_any_tx()
        # Add HTR deposit for initialization fee (0.01 HTR = 1000000 satoshis)
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        context = Context(
            [],
            tx,
            self.owner_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.create_contract(
            self.dozer_tools_nc_id,
            self.dozer_tools_blueprint_id,
            context,
            self.pool_manager_nc_id,
            self.dzr_token_uid,
            self.minimum_deposit,
        )

        self.dozer_tools_storage = self.runner.get_storage(self.dozer_tools_nc_id)

    def test_initialize_dozer_tools(self) -> None:
        """Test DozerTools initialization."""
        # Verify initialization
        contract_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_contract_info"
        )

        self.assertEqual(contract_info["owner"], self.owner_address.hex())
        self.assertEqual(
            contract_info["dozer_pool_manager_id"], self.pool_manager_nc_id.hex()
        )
        self.assertEqual(contract_info["dzr_token_uid"], self.dzr_token_uid.hex())
        self.assertEqual(contract_info["minimum_deposit"], str(self.minimum_deposit))
        self.assertEqual(contract_info["total_projects"], "0")

    def test_create_project(self) -> None:
        """Test creating a new project with token."""
        # Project parameters
        token_name = "TestToken"
        token_symbol = "TEST"
        total_supply = Amount(10000000)  # 10M tokens
        required_htr = total_supply // 100  # 1% of total supply

        # Create project
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=required_htr)],  # 1% HTR deposit
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        token_uid = self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_project",
            context,
            token_name,
            token_symbol,
            total_supply,
            "A test token project",  # description
            "https://test.com",  # website
            "https://logo.com",  # logo_url
            "@test",  # twitter
            "https://t.me/test",  # telegram
            "https://discord.gg/test",  # discord
            "https://github.com/test",  # github
            "DeFi",  # category
            "https://whitepaper.com",  # whitepaper_url
        )

        # Verify the new token was created - tokens are now in vesting contract
        dozer_tools_balance = self.runner.get_current_balance(
            self.dozer_tools_nc_id, token_uid
        )
        self.assertEqual(
            dozer_tools_balance.value, Amount(0)
        )  # DozerTools has no tokens

        # Get vesting contract and verify it has all tokens
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        vesting_contract_hex = contracts["vesting_contract"]
        self.assertNotEqual(vesting_contract_hex, "")
        vesting_contract_id = ContractId(VertexId(bytes.fromhex(vesting_contract_hex)))

        vesting_balance = self.runner.get_current_balance(
            vesting_contract_id, token_uid
        )
        self.assertEqual(vesting_balance.value, total_supply)

        # Verify HTR balance is zero after token creation (HTR was consumed)
        htr_balance = self.runner.get_current_balance(self.dozer_tools_nc_id, htr_uid)
        self.assertEqual(htr_balance.value, Amount(0))

        # Verify project was created
        project_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_info", token_uid
        )

        self.assertEqual(project_info["name"], token_name)
        self.assertEqual(project_info["symbol"], token_symbol)
        self.assertEqual(project_info["dev"], self.dev_address.hex())
        self.assertEqual(project_info["description"], "A test token project")
        self.assertEqual(project_info["website"], "https://test.com")
        self.assertEqual(project_info["category"], "DeFi")

        # Verify project appears in all projects
        all_projects = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_all_projects"
        )
        self.assertIn(token_uid.hex(), all_projects)
        self.assertEqual(all_projects[token_uid.hex()], token_name)

    def test_create_project_with_minimal_metadata(self) -> None:
        """Test creating a project with only required fields."""
        # Project parameters
        token_name = "MinimalToken"
        token_symbol = "MIN"
        total_supply = Amount(5000000)

        # Create project with empty optional fields
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        required_htr = total_supply // 100  # 1% of total supply
        context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=required_htr)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        token_uid = self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_project",
            context,
            token_name,
            token_symbol,
            total_supply,
            "",  # description - empty
            "",  # website - empty
            "",  # logo_url - empty
            "",  # twitter - empty
            "",  # telegram - empty
            "",  # discord - empty
            "",  # github - empty
            "",  # category - empty
            "",  # whitepaper_url - empty
        )

        # Verify project was created with empty optional fields
        project_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_info", token_uid
        )

        self.assertEqual(project_info["name"], token_name)
        self.assertEqual(project_info["symbol"], token_symbol)
        self.assertEqual(project_info["description"], "")
        self.assertEqual(project_info["website"], "")
        self.assertEqual(project_info["category"], "")

    def test_create_project_multiple_users_htr_isolation(self) -> None:
        """Test that users can't consume each other's HTR when creating projects."""
        # User 1 creates a project
        user1_token_name = "User1Token"
        user1_token_symbol = "U1T"
        user1_total_supply = Amount(100_000)
        user1_required_htr = user1_total_supply // 100

        tx1 = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)

        # User 1 deposits HTR and creates project
        context1 = Context(
            [NCDepositAction(token_uid=htr_uid, amount=user1_required_htr)],
            tx1,
            self.dev_address,  # User 1
            timestamp=self.get_current_timestamp(),
        )

        user1_token_uid = self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_project",
            context1,
            user1_token_name,
            user1_token_symbol,
            user1_total_supply,
            "User 1 project",
            "",  # website
            "",  # logo_url
            "",  # twitter
            "",  # telegram
            "",  # discord
            "",  # github
            "DeFi",  # category
            "",  # whitepaper_url
        )

        # Verify User 1's token was created - tokens are in vesting contract
        user1_dozer_balance = self.runner.get_current_balance(
            self.dozer_tools_nc_id, user1_token_uid
        )
        self.assertEqual(
            user1_dozer_balance.value, Amount(0)
        )  # DozerTools has no tokens

        # Get vesting contract and verify it has all tokens
        user1_contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", user1_token_uid
        )
        user1_vesting_hex = user1_contracts["vesting_contract"]
        self.assertNotEqual(user1_vesting_hex, "")
        user1_vesting_id = ContractId(VertexId(bytes.fromhex(user1_vesting_hex)))

        user1_vesting_balance = self.runner.get_current_balance(
            user1_vesting_id, user1_token_uid
        )
        self.assertEqual(user1_vesting_balance.value, user1_total_supply)

        # User 2 creates a project with different supply
        user2_token_name = "User2Token"
        user2_token_symbol = "U2T"
        user2_total_supply = Amount(200_000)
        user2_required_htr = user2_total_supply // 100

        tx2 = self._get_any_tx()

        # User 2 deposits HTR and creates project
        context2 = Context(
            [NCDepositAction(token_uid=htr_uid, amount=user2_required_htr)],
            tx2,
            self.user_address,  # User 2 (different address)
            timestamp=self.get_current_timestamp(),
        )

        user2_token_uid = self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_project",
            context2,
            user2_token_name,
            user2_token_symbol,
            user2_total_supply,
            "User 2 project",
            "",  # website
            "",  # logo_url
            "",  # twitter
            "",  # telegram
            "",  # discord
            "",  # github
            "Gaming",  # category
            "",  # whitepaper_url
        )

        # Verify User 2's token was created - tokens are in vesting contract
        user2_dozer_balance = self.runner.get_current_balance(
            self.dozer_tools_nc_id, user2_token_uid
        )
        self.assertEqual(
            user2_dozer_balance.value, Amount(0)
        )  # DozerTools has no tokens

        # Get vesting contract and verify it has all tokens
        user2_contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", user2_token_uid
        )
        user2_vesting_hex = user2_contracts["vesting_contract"]
        self.assertNotEqual(user2_vesting_hex, "")
        user2_vesting_id = ContractId(VertexId(bytes.fromhex(user2_vesting_hex)))

        user2_vesting_balance = self.runner.get_current_balance(
            user2_vesting_id, user2_token_uid
        )
        self.assertEqual(user2_vesting_balance.value, user2_total_supply)

        # Verify both projects exist and have correct owners
        user1_project_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_info", user1_token_uid
        )
        user2_project_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_info", user2_token_uid
        )

        self.assertEqual(user1_project_info["dev"], self.dev_address.hex())
        self.assertEqual(user1_project_info["category"], "DeFi")
        self.assertEqual(user2_project_info["dev"], self.user_address.hex())
        self.assertEqual(user2_project_info["category"], "Gaming")

        # Verify total projects count
        contract_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_contract_info"
        )
        # Should be 2
        self.assertEqual(contract_info["total_projects"], "2")

        # Test that User 2 cannot create project without proper HTR deposit
        tx3 = self._get_any_tx()
        insufficient_context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=Amount(2))],  # Insufficient HTR
            tx3,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(InsufficientCredits):
            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "create_project",
                insufficient_context,
                "Fail",
                "FAIL",
                Amount(1000),
                "Should fail",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )

    def test_deposit_credits(self) -> None:
        """Test depositing HTR and DZR credits to project."""
        # First create a project
        token_uid = self._create_test_project()

        # Deposit HTR credits
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        htr_deposit_amount = Amount(5000000)  # 0.05 HTR

        context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=htr_deposit_amount)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "deposit_credits", context, token_uid
        )

        # Verify credits were deposited
        credits = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_credits", token_uid
        )
        self.assertEqual(credits["htr_balance"], str(htr_deposit_amount))
        self.assertEqual(credits["dzr_balance"], "0")

    def test_search_projects_by_category(self) -> None:
        """Test searching projects by category."""
        # Create projects with different categories
        token_uid1 = self._create_test_project("DeFi Token", "DEFI", "DeFi")
        token_uid2 = self._create_test_project("Game Token", "GAME", "Gaming")
        token_uid3 = self._create_test_project("Another DeFi", "DEFI2", "DeFi")

        # Search for DeFi projects
        defi_projects = self.runner.call_view_method(
            self.dozer_tools_nc_id, "search_projects_by_category", "DeFi"
        )

        self.assertEqual(len(defi_projects), 2)
        self.assertIn(token_uid1.hex(), defi_projects)
        self.assertIn(token_uid3.hex(), defi_projects)
        self.assertNotIn(token_uid2.hex(), defi_projects)

        # Search for Gaming projects
        gaming_projects = self.runner.call_view_method(
            self.dozer_tools_nc_id, "search_projects_by_category", "Gaming"
        )

        self.assertEqual(len(gaming_projects), 1)
        self.assertIn(token_uid2.hex(), gaming_projects)

    def test_get_projects_by_dev(self) -> None:
        """Test getting projects by developer address."""
        # Create projects with different developers
        token_uid1 = self._create_test_project(
            "Dev1 Token", "DEV1", "DeFi", self.dev_address
        )
        token_uid2 = self._create_test_project(
            "User Token", "USER", "Gaming", self.user_address
        )

        # Get projects by dev_address
        dev_projects = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_projects_by_dev", self.dev_address
        )

        self.assertEqual(len(dev_projects), 1)
        self.assertIn(token_uid1.hex(), dev_projects)
        self.assertNotIn(token_uid2.hex(), dev_projects)

    def test_unauthorized_access(self) -> None:
        """Test that only project dev can manage project."""
        token_uid = self._create_test_project()

        # Try to deposit credits from wrong address
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)

        context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=1000000)],
            tx,
            self.user_address,  # Wrong address
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "deposit_credits", context, token_uid
            )

    def test_blacklist_token(self) -> None:
        """Test admin blacklist functionality."""
        token_uid = self._create_test_project()

        # Verify token is not blacklisted initially
        is_blacklisted = self.runner.call_view_method(
            self.dozer_tools_nc_id, "is_token_blacklisted", token_uid
        )
        self.assertFalse(is_blacklisted)

        # Blacklist token (as owner)
        tx = self._get_any_tx()
        context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "blacklist_token", context, token_uid
        )

        # Verify token is now blacklisted
        is_blacklisted = self.runner.call_view_method(
            self.dozer_tools_nc_id, "is_token_blacklisted", token_uid
        )
        self.assertTrue(is_blacklisted)

        # Verify blacklisted token doesn't appear in all_projects
        all_projects = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_all_projects"
        )
        self.assertNotIn(token_uid.hex(), all_projects)

    def test_method_fees(self) -> None:
        """Test method fee management."""
        # Set fees for a method (as owner)
        tx = self._get_any_tx()
        context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        method_name = "create_liquidity_pool"
        htr_fee = Amount(10_00)  # 10 HTR
        dzr_fee = Amount(5_00)  # 5 DZR

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "update_method_fees",
            context,
            method_name,
            htr_fee,
            dzr_fee,
        )

        # Verify fees were set
        fees = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_method_fees", method_name
        )
        self.assertEqual(fees["method_name"], method_name)
        self.assertEqual(fees["htr_fee"], str(htr_fee))
        self.assertEqual(fees["dzr_fee"], str(dzr_fee))

    def test_project_not_found_error(self) -> None:
        """Test ProjectNotFound error for non-existent projects."""
        fake_token_uid = TokenUid(VertexId(b"\x99" * 32))

        with self.assertRaises(ProjectNotFound):
            self.runner.call_view_method(
                self.dozer_tools_nc_id, "get_project_info", fake_token_uid
            )

    def test_change_owner(self) -> None:
        """Test changing contract ownership."""
        # Change owner
        tx = self._get_any_tx()
        context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "change_owner", context, self.user_address
        )

        # Verify owner changed
        contract_info = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_contract_info"
        )
        self.assertEqual(contract_info["owner"], self.user_address.hex())

    def _create_test_project(
        self,
        name: str = "TestToken",
        symbol: str = "TEST",
        category: str = "DeFi",
        dev_address: Optional[Address] = None,
    ) -> TokenUid:
        """Helper method to create a test project."""
        if dev_address is None:
            dev_address = self.dev_address

        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        total_supply = Amount(10000000)
        required_htr = total_supply // 100  # 1% of total supply

        context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=required_htr)],
            tx,
            dev_address,
            timestamp=self.get_current_timestamp(),
        )

        return self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_project",
            context,
            name,
            symbol,
            total_supply,  # only total_supply now
            f"Description for {name}",  # description
            f"https://{symbol.lower()}.com",  # website
            "",  # logo_url - empty
            "",  # twitter - empty
            "",  # telegram - empty
            "",  # discord - empty
            "",  # github - empty
            category,  # category
            "",  # whitepaper_url - empty
        )

    def test_configure_project_vesting(self) -> None:
        """Test configuring project vesting with special allocations."""
        # Create a test project
        token_uid = self._create_test_project("VestingToken", "VEST")

        # Configure vesting with special allocations
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        # Configure vesting: 20% staking, 10% public sale, 5% dozer pool, 65% regular vesting
        allocation_names = ["Team", "Advisors"]
        allocation_percentages = [40, 25]  # 40% team, 25% advisors
        allocation_beneficiaries = [self.dev_address, self.user_address]
        allocation_cliff_months = [12, 6]  # 12 months cliff for team, 6 for advisors
        allocation_vesting_months = [36, 24]  # 36 months vesting for team, 24 for advisors

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            20,  # staking_percentage
            10,  # public_sale_percentage
            5,  # dozer_pool_percentage
            500,  # earnings_per_day
            allocation_names,
            allocation_percentages,
            allocation_beneficiaries,
            allocation_cliff_months,
            allocation_vesting_months,
        )

        # Verify vesting was configured and staking contract auto-created
        vesting_overview = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_vesting_overview", token_uid
        )

        self.assertEqual(vesting_overview["vesting_configured"], "true")
        self.assertEqual(vesting_overview["staking_status"], "active")  # Auto-created
        self.assertEqual(vesting_overview["staking_percentage"], "20")
        self.assertIn(
            "staking_contract", vesting_overview
        )  # Contract ID should be present
        self.assertEqual(
            vesting_overview["public_sale_status"], "allocated_not_deployed"
        )
        self.assertEqual(vesting_overview["public_sale_percentage"], "10")
        self.assertEqual(
            vesting_overview["dozer_pool_status"], "allocated_not_deployed"
        )
        self.assertEqual(vesting_overview["dozer_pool_percentage"], "5")

        # Verify token distribution
        distribution = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_token_distribution", token_uid
        )

        self.assertEqual(distribution["staking_allocation_percentage"], "20")
        self.assertEqual(distribution["public_sale_allocation_percentage"], "10")
        self.assertEqual(distribution["dozer_pool_allocation_percentage"], "5")
        self.assertEqual(distribution["regular_vesting_percentage"], "65")
        self.assertEqual(distribution["staking_deployed"], "true")  # Auto-created
        self.assertEqual(distribution["crowdsale_deployed"], "false")
        self.assertEqual(distribution["pool_deployed"], "false")
        self.assertIn("staking_contract", distribution)  # Contract ID should be present

    def test_create_staking_with_vesting_integration(self) -> None:
        """Test creating staking contract that withdraws from vesting."""
        # Create project and configure vesting
        token_uid = self._create_test_project("StakingToken", "STAKE")

        # Configure vesting with staking allocation
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            30,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            1000,  # earnings_per_day
            ["Team"],  # allocation_names
            [70],  # allocation_percentages (70% for team)
            [self.dev_address],  # allocation_beneficiaries
            [12],  # allocation_cliff_months
            [36],  # allocation_vesting_months
        )

        # Verify staking contract was automatically created during vesting configuration
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )

        self.assertNotEqual(contracts["staking_contract"], "")

        # Verify updated vesting overview shows staking as active
        vesting_overview = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_vesting_overview", token_uid
        )

        self.assertEqual(vesting_overview["staking_status"], "active")
        self.assertEqual(
            vesting_overview["staking_contract"], contracts["staking_contract"]
        )

        # Test direct user staking to the created staking contract
        # Convert hex string back to ContractId
        from hathor.nanocontracts.types import ContractId, VertexId

        staking_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["staking_contract"]))
        )

        # Create a new user for staking
        user_address, _ = self._get_any_address()
        stake_amount = 1000_00  # 1000 tokens

        # User stakes tokens directly to the staking contract
        initial_time = self.get_current_timestamp()
        stake_context = Context(
            [NCDepositAction(token_uid=token_uid, amount=Amount(stake_amount))],
            self._get_any_tx(),
            Address(user_address),
            timestamp=initial_time,
        )

        self.runner.call_public_method(staking_contract_id, "stake", stake_context)

        # Verify user staked successfully
        user_info = self.runner.call_view_method(
            staking_contract_id, "get_user_info", Address(user_address)
        )
        self.assertEqual(user_info.deposits, stake_amount)

        # Advance time by 1 day and calculate expected rewards
        one_day_later = initial_time + (24 * 60 * 60)  # 1 day in seconds
        earnings_per_day = 1000  # From the configure_project_vesting call above

        # Calculate expected rewards (using same formula as in stake.py)
        # earnings_per_second = (earnings_per_day * PRECISION) // DAY_IN_SECONDS
        # reward = (earnings_per_second * time_passed * stake_amount) // (PRECISION * stake_amount)
        PRECISION = 10**20
        DAY_IN_SECONDS = 24 * 60 * 60
        earnings_per_second = (earnings_per_day * PRECISION) // DAY_IN_SECONDS
        expected_reward = (earnings_per_second * DAY_IN_SECONDS * stake_amount) // (
            PRECISION * stake_amount
        )

        # Check max withdrawal includes stake + rewards
        max_withdrawal = self.runner.call_view_method(
            staking_contract_id,
            "get_max_withdrawal",
            Address(user_address),
            one_day_later,
        )

        expected_total = stake_amount + expected_reward
        self.assertEqual(max_withdrawal, expected_total)

        # Advance time by 2 days and verify rewards doubled
        two_days_later = initial_time + (2 * 24 * 60 * 60)  # 2 days
        expected_two_day_reward = (
            earnings_per_second * 2 * DAY_IN_SECONDS * stake_amount
        ) // (PRECISION * stake_amount)

        max_withdrawal_two_days = self.runner.call_view_method(
            staking_contract_id,
            "get_max_withdrawal",
            Address(user_address),
            two_days_later,
        )

        expected_total_two_days = stake_amount + expected_two_day_reward
        self.assertEqual(max_withdrawal_two_days, expected_total_two_days)

        # Test beneficiary checking vesting contract directly for withdrawable tokens
        vesting_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["vesting_contract"]))
        )

        # Team allocation should be at index 3 (after special allocations 0, 1, 2)
        team_allocation_index = 3

        # Advance time past the cliff period (12 months cliff for team allocation)
        cliff_months = 12
        vesting_months = 36
        month_in_seconds = 30 * 24 * 3600  # 30 days in seconds

        # Check vesting info right after cliff period
        after_cliff_time = initial_time + (cliff_months * month_in_seconds)

        vesting_info = self.runner.call_view_method(
            vesting_contract_id,
            "get_vesting_info",
            team_allocation_index,
            after_cliff_time,
        )

        # At cliff end, no tokens should be vested yet (cliff period just ended)
        self.assertEqual(vesting_info.vested, 0)
        self.assertEqual(vesting_info.claimable, 0)
        self.assertEqual(vesting_info.beneficiary, self.dev_address.hex())
        self.assertEqual(vesting_info.name, "Team")

        # Check vesting info 1 month after cliff (should have some vested tokens)
        one_month_after_cliff = after_cliff_time + month_in_seconds

        vesting_info_after = self.runner.call_view_method(
            vesting_contract_id,
            "get_vesting_info",
            team_allocation_index,
            one_month_after_cliff,
        )

        # Calculate expected vested amount (1 month out of 36 months vesting)
        total_team_allocation = vesting_info_after.amount  # 70% of total supply
        expected_monthly_vesting = total_team_allocation // vesting_months

        self.assertEqual(vesting_info_after.vested, expected_monthly_vesting)
        self.assertEqual(vesting_info_after.claimable, expected_monthly_vesting)
        self.assertEqual(vesting_info_after.withdrawn, 0)

        # Check vesting info 6 months after cliff
        six_months_after_cliff = after_cliff_time + (6 * month_in_seconds)

        vesting_info_six_months = self.runner.call_view_method(
            vesting_contract_id,
            "get_vesting_info",
            team_allocation_index,
            six_months_after_cliff,
        )

        expected_six_months_vesting = (total_team_allocation * 6) // vesting_months

        self.assertEqual(vesting_info_six_months.vested, expected_six_months_vesting)
        self.assertEqual(
            vesting_info_six_months.claimable, expected_six_months_vesting
        )

    def test_invalid_allocation_percentages(self) -> None:
        """Test validation of allocation percentages."""
        token_uid = self._create_test_project("InvalidToken", "INV")

        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        # Try to configure vesting with total > 100%
        with self.assertRaises(InvalidAllocation):
            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "configure_project_vesting",
                context,
                token_uid,
                50,  # staking_percentage
                30,  # public_sale_percentage
                20,  # dozer_pool_percentage
                1000,  # earnings_per_day
                ["Team"],
                [10],  # This makes total 110%
                [self.dev_address],
                [12],
                [36],
            )

    def test_routing_vesting_claim_allocation(self) -> None:
        """Test routing vesting claim allocation through DozerTools."""
        # Create project with vesting configuration
        token_uid = self._create_test_project("RoutingToken", "ROUTE")
        
        # Configure vesting with team allocation
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            0,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            0,  # earnings_per_day (not needed)
            ["Team"],  # allocation_names
            [100],  # allocation_percentages (100% for team)
            [self.dev_address],  # allocation_beneficiaries
            [0],  # allocation_cliff_months (no cliff)
            [0],  # allocation_vesting_months (immediately available)
        )

        # Get vesting contract info
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        vesting_contract_hex = contracts["vesting_contract"]
        vesting_contract_id = ContractId(VertexId(bytes.fromhex(vesting_contract_hex)))

        # Team allocation is at index 3 (after special allocations)
        allocation_index = 3
        
        # Check vesting info shows tokens are available (cliff=0, vesting=0)
        vesting_info = self.runner.call_view_method(
            vesting_contract_id,
            "get_vesting_info",
            allocation_index,
            self.get_current_timestamp(),
        )
        self.assertGreater(vesting_info.claimable, 0)  # Should have claimable tokens
        
        # Try to claim allocation through DozerTools routing
        claim_amount = Amount(1000_00)  # Claim 1000 tokens
        tx = self._get_any_tx()
        context = Context(
            [NCWithdrawalAction(token_uid=token_uid, amount=claim_amount)],
            tx,
            self.dev_address,  # Must be project dev
            timestamp=self.get_current_timestamp(),
        )

        # This should work since dev is both project dev and beneficiary
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "vesting_claim_allocation",
            context,
            allocation_index,
        )

        # Verify tokens were withdrawn
        updated_vesting_info = self.runner.call_view_method(
            vesting_contract_id,
            "get_vesting_info",
            allocation_index,
            self.get_current_timestamp(),
        )
        self.assertEqual(updated_vesting_info.withdrawn, claim_amount)

    def test_routing_staking_operations(self) -> None:
        """Test routing staking operations through DozerTools."""
        # Create project with staking configured
        token_uid = self._create_test_project("StakeRouteToken", "SRT")
        
        # Configure vesting with staking allocation
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            30,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            1000,  # earnings_per_day
            ["Team"],  # allocation_names
            [70],  # allocation_percentages (70% for team)
            [self.dev_address],  # allocation_beneficiaries
            [0],  # allocation_cliff_months
            [0],  # allocation_vesting_months
        )

        # Get staking contract info
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        self.assertNotEqual(contracts["staking_contract"], "")
        
        # Test staking through DozerTools routing
        stake_amount = Amount(1000_00)  # Stake 1000 tokens
        tx = self._get_any_tx()
        context = Context(
            [NCDepositAction(token_uid=token_uid, amount=stake_amount)],
            tx,
            self.user_address,  # Different user
            timestamp=self.get_current_timestamp(),
        )

        # Stake through routing
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "staking_stake",
            context,
            token_uid,
        )

        # Verify staking worked through the routing
        staking_contract_id = ContractId(VertexId(bytes.fromhex(contracts["staking_contract"])))
        user_info = self.runner.call_view_method(
            staking_contract_id, "get_user_info", self.user_address
        )
        self.assertEqual(user_info.deposits, stake_amount)

        # Test unstaking after minimum period (we need to advance time)
        future_time = self.get_current_timestamp() + (31 * 24 * 60 * 60)  # 31 days later
        unstake_amount = Amount(500_00)  # Unstake 500 tokens
        
        tx = self._get_any_tx()
        context = Context(
            [NCWithdrawalAction(token_uid=token_uid, amount=unstake_amount)],
            tx,
            self.user_address,
            timestamp=future_time,
        )

        # Unstake through routing
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "staking_unstake",
            context,
            token_uid,
        )

        # Verify unstaking worked
        updated_user_info = self.runner.call_view_method(
            staking_contract_id, "get_user_info", self.user_address
        )
        # Should have original amount - unstaked amount + any rewards
        self.assertLess(updated_user_info.deposits, stake_amount)

    def test_routing_dao_operations(self) -> None:
        """Test routing DAO operations through DozerTools."""
        # Create project with DAO
        token_uid = self._create_test_project("DAORouteToken", "DRT")
        
        # First configure vesting and create staking (DAO requires staking)
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            20,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            1000,  # earnings_per_day
            ["Team"],  # allocation_names
            [80],  # allocation_percentages
            [self.dev_address],  # allocation_beneficiaries
            [0],  # allocation_cliff_months
            [0],  # allocation_vesting_months
        )

        # Create DAO contract
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        dao_contract_id = self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_dao_contract",
            context,
            token_uid,
            "TestDAO",  # name
            "Test DAO for routing",  # description
            7,  # voting_period_days
            51,  # quorum_percentage
            Amount(100_00),  # proposal_threshold
        )

        # First stake tokens so dev has voting power for proposals
        stake_amount = Amount(1000_00)  # Stake enough tokens for proposals
        tx = self._get_any_tx()
        stake_context = Context(
            [NCDepositAction(token_uid=token_uid, amount=stake_amount)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        # Stake through routing to ensure user has voting power
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "staking_stake",
            stake_context,
            token_uid,
        )

        # Test creating proposal through routing
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,  # Now has voting power from staking
            timestamp=self.get_current_timestamp(),
        )

        proposal_id = self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "dao_create_proposal",
            context,
            token_uid,
            "Test Proposal",
            "This is a test proposal created through routing",
        )

        # Verify proposal was created
        self.assertIsInstance(proposal_id, int)
        self.assertGreater(proposal_id, 0)

        # Test voting through routing
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "dao_cast_vote",
            context,
            token_uid,
            proposal_id,
            True,  # Vote yes
        )

        # Verify vote was cast by checking proposal info
        proposal_info = self.runner.call_view_method(
            dao_contract_id, "get_proposal", proposal_id
        )
        self.assertGreater(proposal_info.for_votes, 0)

    def test_routing_unauthorized_access(self) -> None:
        """Test that routing methods properly enforce authorization."""
        token_uid = self._create_test_project("AuthTestToken", "ATT")

        # Try to use vesting routing with non-project-dev
        tx = self._get_any_tx()
        context = Context(
            [NCWithdrawalAction(token_uid=token_uid, amount=Amount(1000))],
            tx,
            self.user_address,  # Not project dev
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "vesting_claim_allocation",
                context,
                0,
            )

    def test_routing_contract_not_exists(self) -> None:
        """Test routing methods when child contracts don't exist."""
        token_uid = self._create_test_project("NoContractsToken", "NCT")

        # Try to stake when no staking contract exists
        tx = self._get_any_tx()
        context = Context(
            [NCDepositAction(token_uid=token_uid, amount=Amount(1000))],
            tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(ProjectNotFound):
            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "staking_stake",
                context,
                token_uid,
            )

        # Try to create DAO proposal when no DAO contract exists
        tx = self._get_any_tx()
        context = Context(
            [],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(ProjectNotFound):
            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "dao_create_proposal",
                context,
                token_uid,
                "Test Proposal",
                "Description",
            )

    def test_get_melt_authority_success(self) -> None:
        """Test successful melt authority transfer to project dev."""
        # Create a test project
        token_uid = self._create_test_project("MeltAuthToken", "MAT")

        # Deposit credits first to cover fee
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        htr_deposit_amount = Amount(10_00_000)  # 10 HTR to cover fees
        
        deposit_context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=htr_deposit_amount)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        self.runner.call_public_method(
            self.dozer_tools_nc_id, "deposit_credits", deposit_context, token_uid
        )

        # Verify contract has melt authority before transfer
        initial_balance = self.dozer_tools_storage.get_balance(token_uid)
        print(f"DEBUG: Initial balance for token {token_uid.hex()}: value={initial_balance.value}, can_mint={initial_balance.can_mint}, can_melt={initial_balance.can_melt}")
        self.assertTrue(initial_balance.can_melt)

        # Transfer melt authority to dev
        tx = self._get_any_tx()
        context = Context(
            [NCAcquireAuthorityAction(token_uid=token_uid, mint=False, melt=True)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "get_melt_authority", context, token_uid
        )

        # Refresh storage reference after method call
        updated_storage = self.runner.get_storage(self.dozer_tools_nc_id)
        final_balance = updated_storage.get_balance(token_uid)
        
        # Verify contract still has melt authority (authority is transferred, not revoked)
        self.assertTrue(final_balance.can_melt)
        
        # Note: The developer receives melt authority through the NCAcquireAuthorityAction
        # in the transaction context, which allows them to melt tokens in their transactions

    def test_get_melt_authority_unauthorized_user(self) -> None:
        """Test that only project dev can transfer melt authority."""
        token_uid = self._create_test_project("UnauthorizedToken", "UAT")

        # Try to get melt authority from non-dev address
        tx = self._get_any_tx()
        context = Context(
            [NCAcquireAuthorityAction(token_uid=token_uid, mint=False, melt=True)],
            tx,
            self.user_address,  # Not the project dev
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "get_melt_authority", context, token_uid
            )

    def test_get_melt_authority_insufficient_credits(self) -> None:
        """Test that method fails without sufficient credits for fees."""
        token_uid = self._create_test_project("InsufficientToken", "IT")

        # Set fees for get_melt_authority method (as owner)
        tx = self._get_any_tx()
        fee_context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        method_name = "get_melt_authority"
        htr_fee = Amount(1_00_000)  # 1 HTR
        dzr_fee = Amount(0)  # No DZR fee

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "update_method_fees",
            fee_context,
            method_name,
            htr_fee,
            dzr_fee,
        )

        # Don't deposit any credits - project starts with 0 credits after creation

        # Try to get melt authority without sufficient credits
        tx = self._get_any_tx()
        context = Context(
            [NCAcquireAuthorityAction(token_uid=token_uid, mint=False, melt=True)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(InsufficientCredits):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "get_melt_authority", context, token_uid
            )

    def test_get_melt_authority_multiple_calls(self) -> None:
        """Test that method can be called multiple times since authority isn't revoked."""
        token_uid = self._create_test_project("MultiCallToken", "MCT")

        # Deposit credits to cover fees for multiple calls
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        htr_deposit_amount = Amount(20_00_000)  # Enough for multiple calls
        
        deposit_context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=htr_deposit_amount)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        self.runner.call_public_method(
            self.dozer_tools_nc_id, "deposit_credits", deposit_context, token_uid
        )

        # First call to get_melt_authority (should succeed)
        tx1 = self._get_any_tx()
        context1 = Context(
            [NCAcquireAuthorityAction(token_uid=token_uid, mint=False, melt=True)],
            tx1,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "get_melt_authority", context1, token_uid
        )

        # Verify contract still has melt authority
        updated_storage = self.runner.get_storage(self.dozer_tools_nc_id)
        self.assertTrue(updated_storage.get_balance(token_uid).can_melt)

        # Second call to get_melt_authority (should also succeed)
        tx2 = self._get_any_tx()
        context2 = Context(
            [NCAcquireAuthorityAction(token_uid=token_uid, mint=False, melt=True)],
            tx2,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        # This should succeed since the contract still has melt authority
        self.runner.call_public_method(
            self.dozer_tools_nc_id, "get_melt_authority", context2, token_uid
        )

    def test_get_melt_authority_wrong_action_type(self) -> None:
        """Test that method fails with wrong action type."""
        token_uid = self._create_test_project("WrongActionToken", "WAT")

        # Deposit credits to cover fees
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        htr_deposit_amount = Amount(10_00_000)
        
        deposit_context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=htr_deposit_amount)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        self.runner.call_public_method(
            self.dozer_tools_nc_id, "deposit_credits", deposit_context, token_uid
        )

        # Try with deposit action instead of acquire authority action
        tx = self._get_any_tx()
        context = Context(
            [NCDepositAction(token_uid=token_uid, amount=Amount(1))],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(NCForbiddenAction):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "get_melt_authority", context, token_uid
            )

    def test_get_melt_authority_acquire_mint_authority(self) -> None:
        """Test that method fails when trying to acquire mint authority."""
        token_uid = self._create_test_project("MintAuthToken", "MIT")

        # Deposit credits to cover fees
        tx = self._get_any_tx()
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        htr_deposit_amount = Amount(10_00_000)
        
        deposit_context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=htr_deposit_amount)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        self.runner.call_public_method(
            self.dozer_tools_nc_id, "deposit_credits", deposit_context, token_uid
        )

        # Try to acquire mint authority (should fail)
        tx = self._get_any_tx()
        context = Context(
            [NCAcquireAuthorityAction(token_uid=token_uid, mint=True, melt=False)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "get_melt_authority", context, token_uid
            )

    def test_get_melt_authority_nonexistent_project(self) -> None:
        """Test that method fails for non-existent projects."""
        fake_token_uid = TokenUid(VertexId(b"\x99" * 32))

        tx = self._get_any_tx()
        context = Context(
            [NCAcquireAuthorityAction(token_uid=fake_token_uid, mint=False, melt=True)],
            tx,
            self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(NCInvalidAction):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "get_melt_authority", context, fake_token_uid
            )


if __name__ == "__main__":
    unittest.main()
