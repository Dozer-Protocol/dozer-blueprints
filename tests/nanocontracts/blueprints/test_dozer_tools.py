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
from typing import Any, Optional

from hathor.conf import settings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_tools import (
    DozerTools,
    ProjectNotFound,
    ProjectAlreadyExists,
    Unauthorized,
    InsufficientCredits,
    TokenBlacklisted,
    ContractAlreadyExists,
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
from hathor.nanocontracts.nc_types import make_nc_type_for_type
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

TOKEN_UID_TYPE = make_nc_type_for_type(TokenUid)


class DozerToolsTest(BlueprintTestCase):
    """Test cases for DozerTools blueprint."""

    def setUp(self) -> None:
        super().setUp()

        # Generate blueprint and contract IDs
        self.dozer_tools_blueprint_id = self.gen_random_blueprint_id()
        self.dozer_tools_nc_id = self.gen_random_contract_id()

        # Register all blueprint classes
        self.register_blueprint_class(self.dozer_tools_blueprint_id, DozerTools)
        self.register_blueprint_class(VESTING_BLUEPRINT_ID, Vesting)
        self.register_blueprint_class(STAKING_BLUEPRINT_ID, Stake)
        self.register_blueprint_class(DAO_BLUEPRINT_ID, DAO)
        self.register_blueprint_class(CROWDSALE_BLUEPRINT_ID, Crowdsale)
        self.register_blueprint_class(
            BlueprintId(VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID)))),
            DozerPoolManager,
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

        # Verify the new token was created and exists in contract balance
        token_balance = self.runner.get_current_balance(
            self.dozer_tools_nc_id, token_uid
        )
        self.assertEqual(token_balance.value, total_supply)

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

        # Verify User 1's token was created and HTR consumed
        user1_token_balance = self.runner.get_current_balance(
            self.dozer_tools_nc_id, user1_token_uid
        )
        self.assertEqual(user1_token_balance.value, user1_total_supply)

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

        # Verify User 2's token was created
        user2_token_balance = self.runner.get_current_balance(
            self.dozer_tools_nc_id, user2_token_uid
        )
        self.assertEqual(user2_token_balance.value, user2_total_supply)

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


if __name__ == "__main__":
    unittest.main()
