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
from typing import Any

from hathor.conf import settings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.token_manager import (
    TokenManager,
    ContractAlreadyExists,
    ContractNotFound,
    Unauthorized,
    InvalidParameters,
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
from hathor_tests.nanocontracts.blueprints.unittest import BlueprintTestCase

DOZER_POOL_MANAGER_BLUEPRINT_ID = "d6c09caa2f1f7ef6a6f416301c2b665e041fa819a792e53b8409c9c1aed2c89a"

TOKEN_UID_TYPE = make_nc_type_for_type(TokenUid)


class TokenManagerTest(BlueprintTestCase):
    """Test cases for TokenManager blueprint."""

    def setUp(self) -> None:
        super().setUp()
        
        # Generate blueprint and contract IDs
        self.token_manager_blueprint_id = self.gen_random_blueprint_id()
        self.token_manager_nc_id = self.gen_random_contract_id()
        
        # Register all blueprint classes
        self.register_blueprint_class(self.token_manager_blueprint_id, TokenManager)
        self.register_blueprint_class(VESTING_BLUEPRINT_ID, Vesting)
        self.register_blueprint_class(STAKING_BLUEPRINT_ID, Stake)
        self.register_blueprint_class(DAO_BLUEPRINT_ID, DAO)
        self.register_blueprint_class(CROWDSALE_BLUEPRINT_ID, Crowdsale)
        self.register_blueprint_class(BlueprintId(VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID)))), DozerPoolManager)
        
        # Create DozerPoolManager for testing
        self.pool_manager_nc_id = self.gen_random_contract_id()
        self.pool_manager_blueprint_id = BlueprintId(VertexId(bytes.fromhex((DOZER_POOL_MANAGER_BLUEPRINT_ID))))
        
        # Initialize DozerPoolManager
        pool_manager_context = Context(
            [], self._get_any_tx(), Address(self._get_any_address()[0]), timestamp=self.get_current_timestamp()
        )
        self.runner.create_contract(
            self.pool_manager_nc_id,
            self.pool_manager_blueprint_id,
            pool_manager_context,
        )
        
        # Test addresses
        self.owner_address_bytes, _ = self._get_any_address()
        self.owner_address = Address(self.owner_address_bytes)
        self.user_address_bytes, _ = self._get_any_address()
        self.user_address = Address(self.user_address_bytes)
        
        # Token parameters
        self.token_name = "TestToken"
        self.token_symbol = "TEST"
        self.initial_supply = 1000000
        
        # Initialize TokenManager
        self._initialize_token_manager()

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

    def _initialize_token_manager(self):
        """Initialize the TokenManager contract"""
        tx = self._get_any_tx()
        # Add HTR deposit for token creation fee (0.01 HTR = 1000000 satoshis)
        htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        context = Context(
            [NCDepositAction(token_uid=htr_uid, amount=1000000)], 
            tx, 
            self.owner_address, 
            timestamp=self.get_current_timestamp()
        )
        self.runner.create_contract(
            self.token_manager_nc_id,
            self.token_manager_blueprint_id,
            context,
            self.token_name,
            self.token_symbol,
            self.initial_supply,
            True,  # mint_authority
            True,  # melt_authority
            self.pool_manager_nc_id,
        )
        
        self.token_manager_storage = self.runner.get_storage(self.token_manager_nc_id)
        
    def test_initialize_token_manager(self) -> None:
        """Test TokenManager initialization and token creation."""
        # Verify initialization
        main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        self.assertEqual(main_token, self.token_manager_storage.get_obj(b"main_token", TOKEN_UID_TYPE))
        
        owner = self.runner.call_view_method(self.token_manager_nc_id, "get_owner")
        self.assertEqual(owner, self.owner_address)
        
        # Check contract status - all should be False initially
        status = self.runner.call_view_method(self.token_manager_nc_id, "get_contract_status")
        expected_status = {
            "vesting_created": False,
            "staking_created": False,
            "dao_created": False,
            "crowdsale_created": False,
            "liquidity_pool_created": False,
        }
        self.assertEqual(status, expected_status)
        
        # Check token balance
        token_balance = self.runner.get_current_balance(self.token_manager_nc_id, main_token).value
        self.assertEqual(token_balance, self.initial_supply)

    # # def test_create_vesting_contract(self) -> None:
    # #     """Test creating a vesting contract."""
    # #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        
    # #     # Prepare vesting allocations
    # #     allocations = [
    # #         {
    # #             "name": "Team",
    # #             "amount": 100000,
    # #             "beneficiary": self.user_address,
    # #             "cliff_months": 6,
    # #             "vesting_months": 24,
    # #         },
    # #         {
    # #             "name": "Advisors", 
    # #             "amount": 50000,
    # #             "beneficiary": self.user_address,
    # #             "cliff_months": 3,
    # #             "vesting_months": 12,
    # #         }
    # #     ]
        
    # #     # Create vesting contract
    # #     vesting_id = self.runner.call_public_method(
    # #         self.token_manager_nc_id,
    # #         "create_vesting_contract",
    # #         Context(
    # #             [NCDepositAction(token_uid=main_token, amount=200000)],
    # #             self._get_any_tx(),
    # #             self.owner_address,
    # #             timestamp=self.get_current_timestamp()
    # #         ),
    # #         200000,  # token_amount
    # #         allocations,
    # #     )
        
    # #     self.assertIsInstance(vesting_id, ContractId)
        
    # #     # Verify vesting contract was created
    # #     status = self.runner.call_view_method(self.token_manager_nc_id, "get_contract_status")
    # #     self.assertTrue(status["vesting_created"])
        
    # #     # Verify we can get the vesting contract ID
    # #     retrieved_vesting_id = self.runner.call_view_method(self.token_manager_nc_id, "get_vesting_contract")
    # #     self.assertEqual(retrieved_vesting_id, vesting_id)

    # # def test_create_staking_contract(self) -> None:
    # #     """Test creating a staking contract."""
    # #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        
    # #     # Create staking contract
    # #     staking_id = self.runner.call_public_method(
    # #         self.token_manager_nc_id,
    # #         "create_staking_contract",
    # #         Context(
    # #             [NCDepositAction(token_uid=main_token, amount=100000)],
    # #             self._get_any_tx(),
    # #             self.owner_address,
    # #             timestamp=self.get_current_timestamp()
    # #         ),
    # #         100000,  # token_amount
    # #         1000,    # earnings_per_day
    # #     )
        
    # #     self.assertIsInstance(staking_id, ContractId)
        
    # #     # Verify staking contract was created
    # #     status = self.runner.call_view_method(self.token_manager_nc_id, "get_contract_status")
    # #     self.assertTrue(status["staking_created"])

    # # def test_create_dao_contract(self) -> None:
    # #     """Test creating a DAO contract (requires staking contract first)."""
    # #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        
    # #     # First create staking contract (required for DAO)
    # #     self.runner.call_public_method(
    # #         self.token_manager_nc_id,
    # #         "create_staking_contract",
    # #         Context(
    # #             [NCDepositAction(token_uid=main_token, amount=100000)],
    # #             self._get_any_tx(),
    # #             self.owner_address,
    # #             timestamp=self.get_current_timestamp()
    # #         ),
    # #         100000,
    # #         1000,
    # #     )
        
    # #     # Now create DAO contract
    # #     dao_id = self.runner.call_public_method(
    # #         self.token_manager_nc_id,
    # #         "create_dao_contract",
    # #         Context(
    # #             [],
    # #             self._get_any_tx(),
    # #             self.owner_address,
    # #             timestamp=self.get_current_timestamp()
    # #         ),
    # #         "TestDAO",           # name
    # #         "A test DAO",        # description
    # #         7,                   # voting_period_days
    # #         51,                  # quorum_percentage
    # #         Amount(1000),        # proposal_threshold
    # #     )
        
    # #     self.assertIsInstance(dao_id, ContractId)
        
    # #     # Verify DAO contract was created
    # #     status = self.runner.call_view_method(self.token_manager_nc_id, "get_contract_status")
    # #     self.assertTrue(status["dao_created"])

    # # def test_create_crowdsale_contract(self) -> None:
    # #     """Test creating a crowdsale contract."""
    # #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        
    # #     # Create crowdsale contract
    # #     crowdsale_id = self.runner.call_public_method(
    # #         self.token_manager_nc_id,
    # #         "create_crowdsale_contract",
    # #         Context(
    # #             [NCDepositAction(token_uid=main_token, amount=500000)],
    # #             self._get_any_tx(),
    # #             self.owner_address,
    # #             timestamp=self.get_current_timestamp()
    # #         ),
    # #         500000,              # token_amount
    # #         Amount(100),         # rate (tokens per HTR)
    # #         Amount(1000),        # soft_cap (HTR)
    # #         Amount(5000),        # hard_cap (HTR)
    # #         Amount(10),          # min_deposit (HTR)
    # #         1700000000,          # start_time
    # #         1800000000,          # end_time
    # #         Amount(500),         # platform_fee (5%)
    # #     )
        
    # #     self.assertIsInstance(crowdsale_id, ContractId)
        
    # #     # Verify crowdsale contract was created
    # #     status = self.runner.call_view_method(self.token_manager_nc_id, "get_contract_status")
    # #     self.assertTrue(status["crowdsale_created"])

    # # def test_create_liquidity_pool(self) -> None:
    # #     """Test creating a liquidity pool."""
    # #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
    # #     htr_uid = TokenUid(settings.HATHOR_TOKEN_UID)
        
    # #     # Create liquidity pool
    # #     pool_key = self.runner.call_public_method(
    # #         self.token_manager_nc_id,
    # #         "create_liquidity_pool",
    # #         Context(
    # #             [
    # #                 NCDepositAction(token_uid=main_token, amount=100000),
    # #                 NCDepositAction(token_uid=htr_uid, amount=1000),
    # #             ],
    # #             self._get_any_tx(),
    # #             self.owner_address,
    # #             timestamp=self.get_current_timestamp()
    # #         ),
    # #         100000,  # token_amount
    # #         1000,    # htr_amount
    # #         Amount(3),  # fee (0.3%)
    # #     )
        
    # #     self.assertIsInstance(pool_key, str)
        
    # #     # Verify liquidity pool was created
    # #     status = self.runner.call_view_method(self.token_manager_nc_id, "get_contract_status")
    # #     self.assertTrue(status["liquidity_pool_created"])

    # def test_unauthorized_access(self) -> None:
    #     """Test that only owner can create contracts."""
    #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        
    #     # Try to create vesting contract from non-owner address
    #     with self.assertRaises(Unauthorized):
    #         self.runner.call_public_method(
    #             self.token_manager_nc_id,
    #             "create_vesting_contract",
    #             Context(
    #                 [NCDepositAction(token_uid=main_token, amount=100000)],
    #                 self._get_any_tx(),
    #                 self.user_address,  # Wrong address
    #                 timestamp=self.get_current_timestamp()
    #             ),
    #             100000,
    #             [{"name": "Test", "amount": 100000, "beneficiary": self.user_address, "cliff_months": 6, "vesting_months": 24}],
    #         )

    # def test_duplicate_contract_creation(self) -> None:
    #     """Test that creating duplicate contracts raises error."""
    #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
        
    #     # Create first staking contract
    #     self.runner.call_public_method(
    #         self.token_manager_nc_id,
    #         "create_staking_contract",
    #         Context(
    #             [NCDepositAction(token_uid=main_token, amount=100000)],
    #             self._get_any_tx(),
    #             self.owner_address,
    #             timestamp=self.get_current_timestamp()
    #         ),
    #         100000,
    #         1000,
    #     )
        
    #     # Try to create second staking contract
    #     with self.assertRaises(ContractAlreadyExists):
    #         self.runner.call_public_method(
    #             self.token_manager_nc_id,
    #             "create_staking_contract",
    #             Context(
    #                 [NCDepositAction(token_uid=main_token, amount=100000)],
    #                 self._get_any_tx(),
    #                 self.owner_address,
    #                 timestamp=self.get_current_timestamp()
    #             ),
    #             100000,
    #             1000,
    #         )

    def test_get_project_summary(self) -> None:
        """Test getting comprehensive project summary."""
        # Get project summary
        summary = self.runner.call_view_method(self.token_manager_nc_id, "get_project_summary")
        
        # Verify summary structure
        self.assertIn("token_info", summary)
        self.assertIn("owner", summary)
        self.assertIn("contracts", summary)
        self.assertIn("status", summary)
        self.assertIn("dozer_pool_manager", summary)
        
        # Verify token info
        self.assertIn("token_uid", summary["token_info"])
        self.assertIn("current_balance", summary["token_info"])
        self.assertEqual(summary["token_info"]["current_balance"], self.initial_supply)

    # def test_change_owner(self) -> None:
    #     """Test changing TokenManager ownership."""
    #     # Change owner
    #     self.runner.call_public_method(
    #         self.token_manager_nc_id,
    #         "change_owner",
    #         Context(
    #             [],
    #             self._get_any_tx(),
    #             self.owner_address,
    #             timestamp=self.get_current_timestamp()
    #         ),
    #         self.user_address,
    #     )
        
    #     # Verify owner changed
    #     new_owner = self.runner.call_view_method(self.token_manager_nc_id, "get_owner")
    #     self.assertEqual(new_owner, self.user_address)
        
    #     # Verify old owner can't create contracts anymore
    #     main_token = self.runner.call_view_method(self.token_manager_nc_id, "get_main_token")
    #     with self.assertRaises(Unauthorized):
    #         self.runner.call_public_method(
    #             self.token_manager_nc_id,
    #             "create_staking_contract",
    #             Context(
    #                 [NCDepositAction(token_uid=main_token, amount=100000)],
    #                 self._get_any_tx(),
    #                 self.owner_address,  # Old owner
    #                 timestamp=self.get_current_timestamp()
    #             ),
    #             100000,
    #             1000,
    #         )


if __name__ == "__main__":
    unittest.main() 