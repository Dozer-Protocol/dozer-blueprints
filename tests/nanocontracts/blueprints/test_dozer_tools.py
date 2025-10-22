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
)

from hathor.nanocontracts.blueprints.dozer_pool_manager import DozerPoolManager
from hathor.nanocontracts.blueprints.vesting import Vesting
from hathor.nanocontracts.blueprints.stake import Stake
from hathor.nanocontracts.blueprints.dao import DAO
from hathor.nanocontracts.blueprints.crowdsale import Crowdsale
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
from hathor.transaction.base_transaction import BaseTransaction
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase


DOZER_POOL_MANAGER_BLUEPRINT_ID = (
    "d6c09caa2f1f7ef6a6f416301c2b665e041fa819a792e53b8409c9c1aed2c89a"
)

VESTING_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0"
        )
    )
)
STAKING_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef1"
        )
    )
)
DAO_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef2"
        )
    )
)
CROWDSALE_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef3"
        )
    )
)


class DozerToolsTest(BlueprintTestCase):
    """Test cases for DozerTools blueprint."""

    def setUp(self) -> None:
        super().setUp()

        # Generate blueprint and contract IDs
        self.dozer_tools_blueprint_id = self.gen_random_blueprint_id()
        self.dozer_tools_nc_id = self.gen_random_contract_id()

        # Register all blueprint classes
        self._register_blueprint_class(DozerTools, self.dozer_tools_blueprint_id)
        self._register_blueprint_class(Vesting, VESTING_BLUEPRINT_ID)
        self._register_blueprint_class(Stake, STAKING_BLUEPRINT_ID)
        self._register_blueprint_class(DAO, DAO_BLUEPRINT_ID)
        self._register_blueprint_class(Crowdsale, CROWDSALE_BLUEPRINT_ID)
        self._register_blueprint_class(
            DozerPoolManager,
            BlueprintId(VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID)))),
        )

        # Create DozerPoolManager for testing
        self.pool_manager_nc_id = self.gen_random_contract_id()
        self.pool_manager_blueprint_id = BlueprintId(
            VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID)))
        )

        # Initialize DozerPoolManager
        pool_manager_context = self.create_context(
            actions=[],
            vertex=self._get_any_tx(),
            caller_id=Address(self._get_any_address()[0]),
            timestamp=self.get_current_timestamp(),
        )
        self.runner.create_contract(
            self.pool_manager_nc_id,
            self.pool_manager_blueprint_id,
            pool_manager_context,
        )

        # Test addresses
        self.owner_address_bytes, _ = self._get_any_address()
        self.owner_address = Address(self.owner_address_bytes)
        self.dev_address_bytes, _ = self._get_any_address()
        self.dev_address = Address(self.dev_address_bytes)
        self.user_address_bytes, _ = self._get_any_address()
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
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.owner_address,
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

        # Configure blueprint IDs
        config_context = self.create_context(
            actions=[],
            vertex=self._get_any_tx(),
            caller_id=self.owner_address,
            timestamp=self.get_current_timestamp(),
        )

        # Set vesting blueprint
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "set_vesting_blueprint_id",
            config_context,
            VESTING_BLUEPRINT_ID,
        )

        # Set staking blueprint
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "set_staking_blueprint_id",
            config_context,
            STAKING_BLUEPRINT_ID,
        )

        # Set DAO blueprint
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "set_dao_blueprint_id",
            config_context,
            DAO_BLUEPRINT_ID,
        )

        # Set crowdsale blueprint
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "set_crowdsale_blueprint_id",
            config_context,
            CROWDSALE_BLUEPRINT_ID,
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
        context = self.create_context(
            actions=[
                NCDepositAction(token_uid=htr_uid, amount=required_htr)
            ],  # 1% HTR deposit
            vertex=tx,
            caller_id=self.dev_address,
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
        context = self.create_context(
            actions=[NCDepositAction(token_uid=htr_uid, amount=required_htr)],
            vertex=tx,
            caller_id=self.dev_address,
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
        context1 = self.create_context(
            actions=[NCDepositAction(token_uid=htr_uid, amount=user1_required_htr)],
            vertex=tx1,
            caller_id=self.dev_address,  # User 1
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
        context2 = self.create_context(
            actions=[NCDepositAction(token_uid=htr_uid, amount=user2_required_htr)],
            vertex=tx2,
            caller_id=self.user_address,  # User 2 (different address)
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
        insufficient_context = self.create_context(
            actions=[
                NCDepositAction(token_uid=htr_uid, amount=Amount(2))
            ],  # Insufficient HTR
            vertex=tx3,
            caller_id=self.user_address,
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

        context = self.create_context(
            actions=[NCDepositAction(token_uid=htr_uid, amount=htr_deposit_amount)],
            vertex=tx,
            caller_id=self.dev_address,
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

        context = self.create_context(
            actions=[NCDepositAction(token_uid=htr_uid, amount=1000000)],
            vertex=tx,
            caller_id=self.user_address,  # Wrong address
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
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.owner_address,
            timestamp=self.get_current_timestamp(),
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
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.owner_address,
            timestamp=self.get_current_timestamp(),
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
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.owner_address,
            timestamp=self.get_current_timestamp(),
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

        context = self.create_context(
            actions=[NCDepositAction(token_uid=htr_uid, amount=required_htr)],
            vertex=tx,
            caller_id=dev_address,
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
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        # Configure vesting: 20% staking, 10% public sale, 5% dozer pool, 65% regular vesting
        allocation_names = ["Team", "Advisors"]
        allocation_percentages = [40, 25]  # 40% team, 25% advisors
        allocation_beneficiaries = [self.dev_address, self.user_address]
        allocation_cliff_months = [12, 6]  # 12 months cliff for team, 6 for advisors
        allocation_vesting_months = [
            36,
            24,
        ]  # 36 months vesting for team, 24 for advisors

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
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
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
        stake_context = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=Amount(stake_amount))],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
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
        self.assertEqual(vesting_info_six_months.claimable, expected_six_months_vesting)

    def test_invalid_allocation_percentages(self) -> None:
        """Test validation of allocation percentages."""
        token_uid = self._create_test_project("InvalidToken", "INV")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
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

    def test_staking_stake_routing(self) -> None:
        """Test staking through DozerTools.staking_stake() routing method."""
        # Create project and configure vesting with staking allocation
        token_uid = self._create_test_project("RoutingToken", "ROUTE")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        # Configure vesting with 30% staking allocation
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            30,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            500,  # earnings_per_day
            ["Team"],  # allocation_names
            [70],  # allocation_percentages (70% for team)
            [self.dev_address],  # allocation_beneficiaries
            [12],  # allocation_cliff_months
            [36],  # allocation_vesting_months
        )

        # Get staking contract that was auto-created
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        staking_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["staking_contract"]))
        )

        # User stakes tokens through DozerTools routing
        user_address, _ = self._get_any_address()
        stake_amount = 1000_00  # 1000 tokens

        stake_context = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=Amount(stake_amount))],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
            timestamp=self.get_current_timestamp(),
        )

        # Stake through DozerTools routing method
        self.runner.call_public_method(
            self.dozer_tools_nc_id, "staking_stake", stake_context, token_uid
        )

        # Verify user staked successfully by checking staking contract directly
        user_info = self.runner.call_view_method(
            staking_contract_id, "get_user_info", Address(user_address)
        )
        self.assertEqual(user_info.deposits, stake_amount)

    def test_staking_unstake_routing_with_view_methods(self) -> None:
        """Test complete staking workflow through DozerTools with view methods."""
        # Create project and configure staking
        token_uid = self._create_test_project("UnstakeToken", "UNST")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        earnings_per_day = 1000
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            25,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            earnings_per_day,
            ["Team"],
            [75],
            [self.dev_address],
            [12],
            [36],
        )

        # Get staking contract
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        staking_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["staking_contract"]))
        )

        # User stakes through DozerTools
        user_address, _ = self._get_any_address()
        stake_amount = 5000_00
        initial_time = self.get_current_timestamp()

        stake_context = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=Amount(stake_amount))],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
            timestamp=initial_time,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "staking_stake", stake_context, token_uid
        )

        # Advance time by 31 days (past timelock)
        time_after_timelock = initial_time + (31 * 24 * 60 * 60)

        # Use view method to get max withdrawal
        max_withdrawal = self.runner.call_view_method(
            staking_contract_id,
            "get_max_withdrawal",
            Address(user_address),
            time_after_timelock,
        )

        # Verify max_withdrawal includes stake + rewards
        self.assertGreater(max_withdrawal, stake_amount)

        # Unstake through DozerTools routing using exact amount from view method
        unstake_context = self.create_context(
            actions=[
                NCWithdrawalAction(token_uid=token_uid, amount=Amount(max_withdrawal))
            ],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
            timestamp=time_after_timelock,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "staking_unstake", unstake_context, token_uid
        )

        # Verify user has withdrawn everything
        user_info = self.runner.call_view_method(
            staking_contract_id, "get_user_info", Address(user_address)
        )
        self.assertEqual(user_info.deposits, 0)

    def test_routed_methods_authorization(self) -> None:
        """Test that only DozerTools can call routed_stake/routed_unstake."""
        # Create project and configure staking
        token_uid = self._create_test_project("AuthToken", "AUTH")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
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
            500,
            ["Team"],
            [80],
            [self.dev_address],
            [12],
            [36],
        )

        # Get staking contract
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        staking_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["staking_contract"]))
        )

        # Try to call routed_stake directly (should fail - only DozerTools can call it)
        user_address, _ = self._get_any_address()

        direct_stake_context = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=Amount(1000_00))],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
            timestamp=self.get_current_timestamp(),
        )

        # Direct call to routed_stake should fail
        from hathor.nanocontracts.exception import NCFail

        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                staking_contract_id,
                "routed_stake",
                direct_stake_context,
                Address(user_address),
            )

    def test_end_to_end_dozer_tools_staking_workflow(self) -> None:
        """Test complete end-to-end staking workflow with multiple operations."""
        # Create project
        token_uid = self._create_test_project("E2EToken", "E2E")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        earnings_per_day = 2000
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            40,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            earnings_per_day,
            ["Team"],
            [60],
            [self.dev_address],
            [12],
            [36],
        )

        # Get staking contract
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        staking_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["staking_contract"]))
        )

        # Multiple users stake through DozerTools
        users = []
        stake_amounts = [2000_00, 3000_00, 1500_00]
        initial_time = self.get_current_timestamp()

        for i, stake_amount in enumerate(stake_amounts):
            user_addr, _ = self._get_any_address()
            users.append(user_addr)

            stake_ctx = self.create_context(
                actions=[
                    NCDepositAction(token_uid=token_uid, amount=Amount(stake_amount))
                ],
                vertex=self._get_any_tx(),
                caller_id=Address(user_addr),
                timestamp=initial_time,
            )

            self.runner.call_public_method(
                self.dozer_tools_nc_id, "staking_stake", stake_ctx, token_uid
            )

        # Advance time by 35 days
        time_after = initial_time + (35 * 24 * 60 * 60)

        # Each user checks their max withdrawal and unstakes
        for i, user_addr in enumerate(users):
            # Get user info
            user_info = self.runner.call_view_method(
                staking_contract_id, "get_user_info", Address(user_addr)
            )
            self.assertEqual(user_info.deposits, stake_amounts[i])

            # Get max withdrawal
            max_withdrawal = self.runner.call_view_method(
                staking_contract_id,
                "get_max_withdrawal",
                Address(user_addr),
                time_after,
            )

            # Should have rewards accumulated
            self.assertGreater(max_withdrawal, stake_amounts[i])

            # Unstake half through DozerTools
            half_withdrawal = max_withdrawal // 2
            unstake_ctx = self.create_context(
                actions=[
                    NCWithdrawalAction(
                        token_uid=token_uid, amount=Amount(half_withdrawal)
                    )
                ],
                vertex=self._get_any_tx(),
                caller_id=Address(user_addr),
                timestamp=time_after,
            )

            self.runner.call_public_method(
                self.dozer_tools_nc_id, "staking_unstake", unstake_ctx, token_uid
            )

            # Verify partial unstake
            user_info_after = self.runner.call_view_method(
                staking_contract_id, "get_user_info", Address(user_addr)
            )
            self.assertLess(user_info_after.deposits, stake_amounts[i])
            self.assertGreater(user_info_after.deposits, 0)

    def test_staking_routing_with_nonexistent_contract(self) -> None:
        """Test routing methods fail gracefully when staking contract doesn't exist."""
        # Create project but don't configure vesting (no staking contract)
        token_uid = self._create_test_project("NoStakeToken", "NOST")

        user_address, _ = self._get_any_address()

        # Try to stake through routing (should fail - no staking contract)
        stake_context = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=Amount(1000_00))],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(ProjectNotFound):
            self.runner.call_public_method(
                self.dozer_tools_nc_id, "staking_stake", stake_context, token_uid
            )

    def test_staking_view_methods_consistency_through_routing(self) -> None:
        """Test that view methods return consistent values when using DozerTools routing."""
        # Create project with staking
        token_uid = self._create_test_project("ViewToken", "VIEW")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        earnings_per_day = 1500
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            35,  # staking_percentage
            0,  # public_sale_percentage
            0,  # dozer_pool_percentage
            earnings_per_day,
            ["Team"],
            [65],
            [self.dev_address],
            [12],
            [36],
        )

        # Get staking contract
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        staking_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["staking_contract"]))
        )

        # User stakes through DozerTools
        user_address, _ = self._get_any_address()
        stake_amount = 10000_00
        initial_time = self.get_current_timestamp()

        stake_context = self.create_context(
            actions=[NCDepositAction(token_uid=token_uid, amount=Amount(stake_amount))],
            vertex=self._get_any_tx(),
            caller_id=Address(user_address),
            timestamp=initial_time,
        )

        self.runner.call_public_method(
            self.dozer_tools_nc_id, "staking_stake", stake_context, token_uid
        )

        # Check view methods at multiple time points
        time_points = [
            initial_time + (1 * 24 * 60 * 60),  # 1 day
            initial_time + (7 * 24 * 60 * 60),  # 7 days
            initial_time + (30 * 24 * 60 * 60),  # 30 days (at timelock)
            initial_time + (31 * 24 * 60 * 60),  # 31 days (past timelock)
            initial_time + (60 * 24 * 60 * 60),  # 60 days
        ]

        for time_point in time_points:
            # Get max withdrawal
            max_withdrawal = self.runner.call_view_method(
                staking_contract_id,
                "get_max_withdrawal",
                Address(user_address),
                time_point,
            )

            # Get user info
            user_info = self.runner.call_view_method(
                staking_contract_id, "get_user_info", Address(user_address)
            )

            # Get staking stats
            stats = self.runner.call_view_method(
                staking_contract_id, "get_staking_stats"
            )

            # Verify consistency
            self.assertEqual(user_info.deposits, stake_amount)
            self.assertGreaterEqual(
                max_withdrawal, stake_amount
            )  # Always at least the deposit

            # Verify stats reflect this user's stake
            self.assertEqual(stats.total_staked, stake_amount)

    def test_crowdsale_successful_lifecycle(self) -> None:
        """Test complete successful crowdsale lifecycle through DozerTools routing."""
        # Create project and configure vesting with public sale allocation
        token_uid = self._create_test_project("CrowdsaleToken", "CROWD")

        tx = self._get_any_tx()
        context = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )

        # Configure vesting with 15% public sale allocation
        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "configure_project_vesting",
            context,
            token_uid,
            20,  # staking_percentage
            15,  # public_sale_percentage
            5,   # dozer_pool_percentage
            1000,  # earnings_per_day
            ["Team"],
            [60],  # 60% for team
            [self.dev_address],
            [12],
            [36],
        )

        # Create crowdsale contract
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        initial_time = self.get_current_timestamp()

        create_crowdsale_ctx = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=initial_time,
        )

        rate = 100  # 100 tokens per HTR
        soft_cap = 1000_00  # 1000 HTR
        hard_cap = 1500_00  # 1500 HTR
        min_deposit = 10_00  # 10 HTR
        platform_fee = 500  # 5%
        start_time = initial_time + 100
        end_time = start_time + 86400  # 24 hours

        self.runner.call_public_method(
            self.dozer_tools_nc_id,
            "create_crowdsale",
            create_crowdsale_ctx,
            token_uid,
            rate,
            soft_cap,
            hard_cap,
            min_deposit,
            start_time,
            end_time,
            platform_fee,
        )

        # Get crowdsale contract
        contracts = self.runner.call_view_method(
            self.dozer_tools_nc_id, "get_project_contracts", token_uid
        )
        crowdsale_contract_id = ContractId(
            VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
        )

        # Verify crowdsale info
        sale_info = self.runner.call_view_method(
            crowdsale_contract_id, "get_sale_info"
        )
        self.assertEqual(sale_info["rate"], rate)
        self.assertEqual(sale_info["soft_cap"], soft_cap)
        self.assertEqual(sale_info["hard_cap"], hard_cap)
        self.assertEqual(sale_info["state"], 0)  # PENDING

        # Activate the sale
        activate_ctx = self.create_context(
            actions=[],
            vertex=tx,
            caller_id=self.dev_address,
            timestamp=start_time - 1,
        )
        self.runner.call_public_method(
            crowdsale_contract_id, "early_activate", activate_ctx
        )

        # Verify sale is now ACTIVE
        sale_info = self.runner.call_view_method(
            crowdsale_contract_id, "get_sale_info"
        )
        self.assertEqual(sale_info["state"], 1)  # ACTIVE

        # Multiple users participate through DozerTools routing
        participants = []
        deposit_amounts = [500_00, 300_00, 400_00]  # Total: 1200 HTR (exceeds soft cap)

        for deposit_amount in deposit_amounts:
            user_addr, _ = self._get_any_address()
            participants.append((user_addr, deposit_amount))

            participate_ctx = self.create_context(
                actions=[NCDepositAction(token_uid=htr_uid, amount=deposit_amount)],
                vertex=self._get_any_tx(),
                caller_id=Address(user_addr),
                timestamp=start_time + 100,
            )

            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "crowdsale_participate",
                participate_ctx,
                token_uid,
            )

        # Verify sale reached SUCCESS state (soft cap exceeded)
        sale_info = self.runner.call_view_method(
            crowdsale_contract_id, "get_sale_info"
        )
        self.assertEqual(sale_info["state"], 3)  # SUCCESS
        self.assertEqual(sale_info["total_raised"], sum(deposit_amounts))
        self.assertEqual(sale_info["participants"], len(participants))

        # Check sale progress
        progress = self.runner.call_view_method(
            crowdsale_contract_id, "get_sale_progress"
        )
        self.assertTrue(progress["is_successful"])

        # Each participant claims tokens through DozerTools routing
        for user_addr, deposit_amount in participants:
            # Check participant info before claiming
            participant_info = self.runner.call_view_method(
                crowdsale_contract_id,
                "get_participant_info",
                Address(user_addr),
            )
            self.assertEqual(participant_info["deposited"], deposit_amount)
            self.assertEqual(participant_info["tokens_due"], deposit_amount * rate)
            self.assertFalse(participant_info["has_claimed"])

            # Claim tokens
            tokens_due = deposit_amount * rate
            claim_ctx = self.create_context(
                actions=[NCWithdrawalAction(token_uid=token_uid, amount=tokens_due)],
                vertex=self._get_any_tx(),
                caller_id=Address(user_addr),
                timestamp=end_time + 100,
            )

            self.runner.call_public_method(
                self.dozer_tools_nc_id,
                "crowdsale_claim_tokens",
                claim_ctx,
                token_uid,
            )

            # Verify claim status
            participant_info_after = self.runner.call_view_method(
                crowdsale_contract_id,
                "get_participant_info",
                Address(user_addr),
            )
            self.assertTrue(participant_info_after["has_claimed"])
            self.assertEqual(participant_info_after["tokens_due"], 0)

        # Owner withdraws raised HTR
        total_raised = sum(deposit_amounts)
        platform_fee_amount = (total_raised * platform_fee) // 10000
        withdrawable_htr = total_raised - platform_fee_amount

        owner_withdraw_ctx = self.create_context(
            actions=[NCWithdrawalAction(token_uid=htr_uid, amount=withdrawable_htr)],
            vertex=self._get_any_tx(),
            caller_id=self.dev_address,
            timestamp=end_time + 200,
        )

        self.runner.call_public_method(
            crowdsale_contract_id,
            "withdraw_raised_htr",
            owner_withdraw_ctx,
        )

        # Verify withdrawal info
        withdrawal_info = self.runner.call_view_method(
            crowdsale_contract_id, "get_withdrawal_info"
        )
        self.assertEqual(withdrawal_info["total_raised"], total_raised)
        self.assertEqual(withdrawal_info["platform_fees"], platform_fee_amount)
        self.assertTrue(withdrawal_info["is_withdrawn"])

#     def test_crowdsale_failed_sale_with_refunds(self) -> None:
#         """Test failed crowdsale with refund claims through DozerTools."""
#         # Create project and configure vesting
#         token_uid = self._create_test_project("FailedSaleToken", "FAIL")

#         tx = self._get_any_tx()
#         context = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=self.get_current_timestamp(),
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "configure_project_vesting",
#             context,
#             token_uid,
#             10,  # staking_percentage
#             20,  # public_sale_percentage
#             5,   # dozer_pool_percentage
#             500,
#             ["Team"],
#             [65],
#             [self.dev_address],
#             [12],
#             [36],
#         )

#         # Create crowdsale
#         htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
#         initial_time = self.get_current_timestamp()

#         create_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=initial_time,
#         )

#         rate = 50
#         soft_cap = 2000_00  # 2000 HTR
#         hard_cap = 10000_00  # 10000 HTR
#         min_deposit = 50_00
#         platform_fee = 300
#         start_time = initial_time + 100
#         end_time = start_time + 3600

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "create_crowdsale",
#             create_ctx,
#             token_uid,
#             rate,
#             soft_cap,
#             hard_cap,
#             min_deposit,
#             start_time,
#             end_time,
#             platform_fee,
#         )

#         # Get crowdsale contract
#         contracts = self.runner.call_view_method(
#             self.dozer_tools_nc_id, "get_project_contracts", token_uid
#         )
#         crowdsale_contract_id = ContractId(
#             VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
#         )

#         # Activate sale
#         activate_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time - 1,
#         )
#         self.runner.call_public_method(
#             crowdsale_contract_id, "early_activate", activate_ctx
#         )

#         # Users participate but don't reach soft cap
#         participants = []
#         deposit_amounts = [500_00, 400_00, 300_00]  # Total: 1200 HTR (below soft cap of 2000)

#         for deposit_amount in deposit_amounts:
#             user_addr, _ = self._get_any_address()
#             participants.append((user_addr, deposit_amount))

#             participate_ctx = self.create_context(
#                 actions=[NCDepositAction(token_uid=htr_uid, amount=deposit_amount)],
#                 vertex=self._get_any_tx(),
#                 caller_id=Address(user_addr),
#                 timestamp=start_time + 100,
#             )

#             self.runner.call_public_method(
#                 self.dozer_tools_nc_id,
#                 "crowdsale_participate",
#                 participate_ctx,
#                 token_uid,
#             )

#         # Verify sale is still ACTIVE (not reached soft cap)
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 1)  # ACTIVE
#         self.assertEqual(sale_info["total_raised"], sum(deposit_amounts))
#         self.assertLess(sale_info["total_raised"], soft_cap)

#         # Owner finalizes sale (failed)
#         finalize_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=end_time + 100,
#         )
#         self.runner.call_public_method(
#             crowdsale_contract_id, "finalize", finalize_ctx
#         )

#         # Verify sale is FAILED
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 4)  # FAILED

#         # Participants claim refunds through DozerTools
#         for user_addr, deposit_amount in participants:
#             # Check participant info before refund
#             participant_info = self.runner.call_view_method(
#                 crowdsale_contract_id,
#                 "get_participant_info",
#                 Address(user_addr),
#             )
#             self.assertEqual(participant_info["deposited"], deposit_amount)
#             self.assertFalse(participant_info["has_claimed"])

#             # Claim refund
#             refund_ctx = self.create_context(
#                 actions=[NCWithdrawalAction(token_uid=htr_uid, amount=deposit_amount)],
#                 vertex=self._get_any_tx(),
#                 caller_id=Address(user_addr),
#                 timestamp=end_time + 200,
#             )

#             self.runner.call_public_method(
#                 self.dozer_tools_nc_id,
#                 "crowdsale_claim_refund",
#                 refund_ctx,
#                 token_uid,
#             )

#             # Verify refund claimed
#             participant_info_after = self.runner.call_view_method(
#                 crowdsale_contract_id,
#                 "get_participant_info",
#                 Address(user_addr),
#             )
#             self.assertTrue(participant_info_after["has_claimed"])
#             self.assertEqual(participant_info_after["deposited"], 0)

#     def test_crowdsale_pause_unpause_operations(self) -> None:
#         """Test crowdsale pause/unpause functionality."""
#         # Create project and crowdsale
#         token_uid = self._create_test_project("PauseToken", "PAUSE")

#         tx = self._get_any_tx()
#         context = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=self.get_current_timestamp(),
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "configure_project_vesting",
#             context,
#             token_uid,
#             15,
#             25,
#             5,
#             800,
#             ["Team"],
#             [55],
#             [self.dev_address],
#             [6],
#             [24],
#         )

#         # Create crowdsale
#         htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
#         initial_time = self.get_current_timestamp()
#         start_time = initial_time + 100
#         end_time = start_time + 7200

#         create_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=initial_time,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "create_crowdsale",
#             create_ctx,
#             token_uid,
#             75,  # rate
#             800_00,  # soft_cap
#             4000_00,  # hard_cap
#             20_00,  # min_deposit
#             400,  # platform_fee (4%)
#             start_time,
#             end_time,
#             400,
#         )

#         # Get crowdsale contract
#         contracts = self.runner.call_view_method(
#             self.dozer_tools_nc_id, "get_project_contracts", token_uid
#         )
#         crowdsale_contract_id = ContractId(
#             VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
#         )

#         # Activate sale
#         activate_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time - 1,
#         )
#         self.runner.call_public_method(
#             crowdsale_contract_id, "early_activate", activate_ctx
#         )

#         # Verify ACTIVE state
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 1)  # ACTIVE

#         # User participates
#         user_addr, _ = self._get_any_address()
#         participate_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=200_00)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr),
#             timestamp=start_time + 50,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             participate_ctx,
#             token_uid,
#         )

#         # Owner pauses the sale
#         pause_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time + 100,
#         )
#         self.runner.call_public_method(
#             crowdsale_contract_id, "pause", pause_ctx
#         )

#         # Verify PAUSED state
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 2)  # PAUSED

#         # Try to participate while paused (should fail)
#         from hathor.nanocontracts.exception import NCFail

#         user_addr2, _ = self._get_any_address()
#         participate_paused_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=100_00)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr2),
#             timestamp=start_time + 150,
#         )

#         with self.assertRaises(NCFail):
#             self.runner.call_public_method(
#                 self.dozer_tools_nc_id,
#                 "crowdsale_participate",
#                 participate_paused_ctx,
#                 token_uid,
#             )

#         # Owner unpauses the sale
#         unpause_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time + 200,
#         )
#         self.runner.call_public_method(
#             crowdsale_contract_id, "unpause", unpause_ctx
#         )

#         # Verify ACTIVE state again
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 1)  # ACTIVE

#         # Now participation should work again
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             participate_paused_ctx,
#             token_uid,
#         )

#         # Verify second user participated
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["total_raised"], 300_00)
#         self.assertEqual(sale_info["participants"], 2)

#     def test_crowdsale_edge_cases(self) -> None:
#         """Test crowdsale edge cases: minimum deposit, hard cap limit, multiple deposits."""
#         # Create project and crowdsale
#         token_uid = self._create_test_project("EdgeToken", "EDGE")

#         tx = self._get_any_tx()
#         context = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=self.get_current_timestamp(),
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "configure_project_vesting",
#             context,
#             token_uid,
#             10,
#             30,
#             5,
#             1200,
#             ["Team"],
#             [55],
#             [self.dev_address],
#             [9],
#             [30],
#         )

#         # Create crowdsale with specific constraints
#         htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
#         initial_time = self.get_current_timestamp()
#         start_time = initial_time + 100
#         end_time = start_time + 3600

#         create_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=initial_time,
#         )

#         min_deposit = 100_00  # 100 HTR minimum
#         soft_cap = 500_00
#         hard_cap = 1000_00

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "create_crowdsale",
#             create_ctx,
#             token_uid,
#             200,  # rate
#             soft_cap,
#             hard_cap,
#             min_deposit,
#             start_time,
#             end_time,
#             250,  # platform_fee
#         )

#         # Get crowdsale contract
#         contracts = self.runner.call_view_method(
#             self.dozer_tools_nc_id, "get_project_contracts", token_uid
#         )
#         crowdsale_contract_id = ContractId(
#             VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
#         )

#         # Activate sale
#         activate_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time - 1,
#         )
#         self.runner.call_public_method(
#             crowdsale_contract_id, "early_activate", activate_ctx
#         )

#         # Test 1: Try to participate with amount below minimum (should fail)
#         from hathor.nanocontracts.exception import NCFail

#         user_addr1, _ = self._get_any_address()
#         below_min_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=50_00)],  # Below 100 HTR
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr1),
#             timestamp=start_time + 10,
#         )

#         with self.assertRaises(NCFail):
#             self.runner.call_public_method(
#                 self.dozer_tools_nc_id,
#                 "crowdsale_participate",
#                 below_min_ctx,
#                 token_uid,
#             )

#         # Test 2: Same user makes multiple deposits
#         user_addr2, _ = self._get_any_address()
#         first_deposit = 200_00
#         second_deposit = 150_00

#         first_deposit_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=first_deposit)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr2),
#             timestamp=start_time + 20,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             first_deposit_ctx,
#             token_uid,
#         )

#         # Verify first deposit
#         participant_info = self.runner.call_view_method(
#             crowdsale_contract_id,
#             "get_participant_info",
#             Address(user_addr2),
#         )
#         self.assertEqual(participant_info["deposited"], first_deposit)

#         # Second deposit from same user
#         second_deposit_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=second_deposit)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr2),
#             timestamp=start_time + 30,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             second_deposit_ctx,
#             token_uid,
#         )

#         # Verify cumulative deposit
#         participant_info = self.runner.call_view_method(
#             crowdsale_contract_id,
#             "get_participant_info",
#             Address(user_addr2),
#         )
#         self.assertEqual(participant_info["deposited"], first_deposit + second_deposit)

#         # Verify only counted as 1 participant
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["participants"], 1)

#         # Test 3: Try to exceed hard cap (should fail)
#         user_addr3, _ = self._get_any_address()
#         remaining = hard_cap - (first_deposit + second_deposit)
#         exceed_hardcap_amount = remaining + 100_00  # Exceeds hard cap

#         exceed_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=exceed_hardcap_amount)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr3),
#             timestamp=start_time + 40,
#         )

#         with self.assertRaises(NCFail):
#             self.runner.call_public_method(
#                 self.dozer_tools_nc_id,
#                 "crowdsale_participate",
#                 exceed_ctx,
#                 token_uid,
#             )

#         # Test 4: Deposit exactly to reach hard cap
#         exact_remaining_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=remaining)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr3),
#             timestamp=start_time + 50,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             exact_remaining_ctx,
#             token_uid,
#         )

#         # Verify hard cap reached and sale is SUCCESS
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["total_raised"], hard_cap)
#         self.assertEqual(sale_info["state"], 3)  # SUCCESS

#     def test_crowdsale_routed_admin_methods(self) -> None:
#         """Test all crowdsale admin methods through DozerTools routing."""
#         # Create project and crowdsale
#         token_uid = self._create_test_project("AdminToken", "ADMIN")

#         tx = self._get_any_tx()
#         context = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=self.get_current_timestamp(),
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "configure_project_vesting",
#             context,
#             token_uid,
#             10,
#             20,
#             5,
#             1000,
#             ["Team"],
#             [65],
#             [self.dev_address],
#             [12],
#             [36],
#         )

#         # Create crowdsale
#         htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
#         initial_time = self.get_current_timestamp()
#         start_time = initial_time + 100
#         end_time = start_time + 7200

#         create_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=initial_time,
#         )

#         rate = 150
#         soft_cap = 800_00
#         hard_cap = 4000_00
#         min_deposit = 25_00
#         platform_fee = 400

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "create_crowdsale",
#             create_ctx,
#             token_uid,
#             rate,
#             soft_cap,
#             hard_cap,
#             min_deposit,
#             start_time,
#             end_time,
#             platform_fee,
#         )

#         # Get crowdsale contract
#         contracts = self.runner.call_view_method(
#             self.dozer_tools_nc_id, "get_project_contracts", token_uid
#         )
#         crowdsale_contract_id = ContractId(
#             VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
#         )

#         # Test routed_early_activate
#         activate_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time - 1,
#         )
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id, "crowdsale_early_activate", activate_ctx, token_uid
#         )

#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 1)  # ACTIVE

#         # User participates
#         user_addr, _ = self._get_any_address()
#         participate_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=1000_00)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr),
#             timestamp=start_time + 50,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             participate_ctx,
#             token_uid,
#         )

#         # Test routed_pause
#         pause_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time + 100,
#         )
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id, "crowdsale_pause", pause_ctx, token_uid
#         )

#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 2)  # PAUSED

#         # Test routed_unpause
#         unpause_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time + 150,
#         )
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id, "crowdsale_unpause", unpause_ctx, token_uid
#         )

#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 1)  # ACTIVE

#         # Test routed_finalize (reaching soft cap first)
#         finalize_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time + 200,
#         )
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id, "crowdsale_finalize", finalize_ctx, token_uid
#         )

#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 3)  # SUCCESS (soft cap reached)

#     def test_crowdsale_routed_withdrawal_methods(self) -> None:
#         """Test crowdsale withdrawal methods through DozerTools routing."""
#         # Create project and crowdsale
#         token_uid = self._create_test_project("WithdrawToken", "WDRW")

#         tx = self._get_any_tx()
#         context = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=self.get_current_timestamp(),
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "configure_project_vesting",
#             context,
#             token_uid,
#             15,
#             25,
#             5,
#             800,
#             ["Team"],
#             [55],
#             [self.dev_address],
#             [6],
#             [24],
#         )

#         # Create crowdsale
#         htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
#         initial_time = self.get_current_timestamp()
#         start_time = initial_time + 100
#         end_time = start_time + 3600

#         create_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=initial_time,
#         )

#         rate = 100
#         soft_cap = 500_00
#         hard_cap = 2000_00
#         min_deposit = 50_00
#         platform_fee = 300  # 3%

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "create_crowdsale",
#             create_ctx,
#             token_uid,
#             rate,
#             soft_cap,
#             hard_cap,
#             min_deposit,
#             start_time,
#             end_time,
#             platform_fee,
#         )

#         # Get crowdsale contract
#         contracts = self.runner.call_view_method(
#             self.dozer_tools_nc_id, "get_project_contracts", token_uid
#         )
#         crowdsale_contract_id = ContractId(
#             VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
#         )

#         # Activate and reach soft cap
#         activate_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time - 1,
#         )
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id, "crowdsale_early_activate", activate_ctx, token_uid
#         )

#         # User participates with enough to reach soft cap
#         user_addr, _ = self._get_any_address()
#         participate_ctx = self.create_context(
#             actions=[NCDepositAction(token_uid=htr_uid, amount=600_00)],
#             vertex=self._get_any_tx(),
#             caller_id=Address(user_addr),
#             timestamp=start_time + 50,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_participate",
#             participate_ctx,
#             token_uid,
#         )

#         # Verify SUCCESS state
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 3)  # SUCCESS
#         total_raised = sale_info["total_raised"]

#         # Calculate platform fee and withdrawable
#         platform_fee_amount = (total_raised * platform_fee) // 10000
#         withdrawable_htr = total_raised - platform_fee_amount

#         # Test routed_withdraw_raised_htr
#         withdraw_htr_ctx = self.create_context(
#             actions=[NCWithdrawalAction(token_uid=htr_uid, amount=withdrawable_htr)],
#             vertex=self._get_any_tx(),
#             caller_id=self.dev_address,
#             timestamp=end_time + 100,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_withdraw_raised_htr",
#             withdraw_htr_ctx,
#             token_uid,
#         )

#         # Verify withdrawal
#         withdrawal_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_withdrawal_info"
#         )
#         self.assertTrue(withdrawal_info["is_withdrawn"])
#         self.assertEqual(withdrawal_info["platform_fees"], platform_fee_amount)

#         # Get remaining tokens and test routed_withdraw_remaining_tokens
#         contract_state = self.get_readonly_contract(crowdsale_contract_id)
#         from hathor.nanocontracts.blueprints.crowdsale import Crowdsale

#         assert isinstance(contract_state, Crowdsale)
#         remaining_tokens = contract_state.sale_token_balance

#         withdraw_tokens_ctx = self.create_context(
#             actions=[NCWithdrawalAction(token_uid=token_uid, amount=remaining_tokens)],
#             vertex=self._get_any_tx(),
#             caller_id=self.dev_address,
#             timestamp=end_time + 200,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "crowdsale_withdraw_remaining_tokens",
#             withdraw_tokens_ctx,
#             token_uid,
#         )

#         # Verify remaining tokens withdrawn
#         contract_state_after = self.get_readonly_contract(crowdsale_contract_id)
#         assert isinstance(contract_state_after, Crowdsale)
#         self.assertEqual(contract_state_after.sale_token_balance, 0)

#     def test_crowdsale_unauthorized_routed_calls(self) -> None:
#         """Test that unauthorized users cannot call owner-only routed methods."""
#         # Create project and crowdsale
#         token_uid = self._create_test_project("UnauthorizedToken", "UNAUTH")

#         tx = self._get_any_tx()
#         context = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=self.get_current_timestamp(),
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "configure_project_vesting",
#             context,
#             token_uid,
#             10,
#             15,
#             5,
#             500,
#             ["Team"],
#             [70],
#             [self.dev_address],
#             [12],
#             [36],
#         )

#         # Create crowdsale
#         htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
#         initial_time = self.get_current_timestamp()
#         start_time = initial_time + 100
#         end_time = start_time + 3600

#         create_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=initial_time,
#         )

#         self.runner.call_public_method(
#             self.dozer_tools_nc_id,
#             "create_crowdsale",
#             create_ctx,
#             token_uid,
#             100,  # rate
#             500_00,  # soft_cap
#             2000_00,  # hard_cap
#             50_00,  # min_deposit
#             start_time,
#             end_time,
#             400,  # platform_fee
#         )

#         # Get crowdsale contract
#         contracts = self.runner.call_view_method(
#             self.dozer_tools_nc_id, "get_project_contracts", token_uid
#         )
#         crowdsale_contract_id = ContractId(
#             VertexId(bytes.fromhex(contracts["crowdsale_contract"]))
#         )

#         # Activate sale (as owner - should work)
#         activate_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=self.dev_address,
#             timestamp=start_time - 1,
#         )
#         self.runner.call_public_method(
#             self.dozer_tools_nc_id, "crowdsale_early_activate", activate_ctx, token_uid
#         )

#         # Try to pause as unauthorized user (should fail)
#         from hathor.nanocontracts.exception import NCFail

#         unauthorized_addr, _ = self._get_any_address()
#         unauthorized_pause_ctx = self.create_context(
#             actions=[],
#             vertex=tx,
#             caller_id=Address(unauthorized_addr),
#             timestamp=start_time + 50,
#         )

#         with self.assertRaises(NCFail) as cm:
#             self.runner.call_public_method(
#                 self.dozer_tools_nc_id,
#                 "crowdsale_pause",
#                 unauthorized_pause_ctx,
#                 token_uid,
#             )
#         # Should fail at DozerTools level (not project dev)
#         self.assertIn("Only project dev", str(cm.exception))

#         # Verify sale is still ACTIVE (pause didn't work)
#         sale_info = self.runner.call_view_method(
#             crowdsale_contract_id, "get_sale_info"
#         )
#         self.assertEqual(sale_info["state"], 1)  # ACTIVE

if __name__ == "__main__":
    unittest.main()
