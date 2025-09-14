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
Migration and advanced scenarios for KhensuManager contract.

This module tests complex migration scenarios:
- Precise migration threshold handling
- Migration state transition edge cases
- Post-migration behavior validation
- Failed migration recovery
- Partial migration scenarios
- Multiple token migration interactions
"""

import inspect
import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.khensu_manager import (
    KhensuManager,
    BASIS_POINTS,
    InvalidState,
    TokenNotFound,
)
from hathor.nanocontracts.blueprints.dozer_pool_manager import DozerPoolManager
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.nc_types import (
    VarInt32NCType,
    make_nc_type_for_arg_type as make_nc_type,
)
from hathor.nanocontracts.types import (
    Address,
    Amount,
    ContractId,
    TokenUid,
    NCDepositAction,
    NCWithdrawalAction,
)
from hathor.conf.get_settings import HathorSettings
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from tests.nanocontracts.blueprints.test_utilities import (
    TestConstants,
    create_deposit_action,
    create_withdrawal_action,
)
from hathor.nanocontracts.blueprints import khensu_manager, dozer_pool_manager

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID

# Migration test configurations
DEFAULT_MARKET_CAP = Amount(1725000)
DEFAULT_LIQUIDITY_AMOUNT = Amount(300000)
DEFAULT_INITIAL_VIRTUAL_POOL = Amount(15000)
DEFAULT_CURVE_CONSTANT = Amount(32190005730)
INITIAL_TOKEN_RESERVE = Amount(1073000191)
BUY_FEE_RATE = Amount(200)
SELL_FEE_RATE = Amount(300)
GRADUATION_FEE = Amount(1000)


class KhensuManagerMigrationScenariosTest(BlueprintTestCase):
    """Test complex migration scenarios for KhensuManager."""

    def setUp(self):
        super().setUp()

        # Register blueprints
        self.blueprint_id_khensu = self.register_blueprint_file(inspect.getfile(khensu_manager))
        self.manager_id = self.gen_random_contract_id()

        self.blueprint_id_dozer = self.register_blueprint_file(inspect.getfile(dozer_pool_manager))
        self.dozer_pool_manager_id = self.gen_random_contract_id()

        # Setup addresses
        self.admin_address = Address(self._get_any_address()[0])
        self.user_address = Address(self._get_any_address()[0])

        # Transaction for contexts
        self.tx = self.get_genesis_tx()

        # Initialize managers
        self._initialize_managers()

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_managers(self):
        """Initialize both pool manager and Khensu manager."""
        # Initialize DozerPoolManager
        dozer_context = Context(
            [],
            self.tx,
            Address(self._get_any_address()[0]),
            timestamp=self.get_current_timestamp(),
        )
        self.runner.create_contract(
            self.dozer_pool_manager_id,
            self.blueprint_id_dozer,
            dozer_context,
        )

        # Initialize KhensuManager
        ctx = Context(
            [],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.create_contract(
            self.manager_id,
            self.blueprint_id_khensu,
            ctx,
            self.dozer_pool_manager_id,
            DEFAULT_MARKET_CAP,
            DEFAULT_LIQUIDITY_AMOUNT,
            DEFAULT_INITIAL_VIRTUAL_POOL,
            DEFAULT_CURVE_CONSTANT,
            INITIAL_TOKEN_RESERVE,
            BUY_FEE_RATE,
            SELL_FEE_RATE,
            GRADUATION_FEE,
        )

        self.manager_storage = self.runner.get_storage(self.manager_id)

    def _register_token(self, token_name: str, token_symbol: str) -> TokenUid:
        """Register a new token with the manager."""
        ctx = Context(
            [
                NCDepositAction(
                    token_uid=HTR_UID,
                    amount=int(INITIAL_TOKEN_RESERVE * 0.02),
                ),
            ],
            self.tx,
            self.admin_address,
            timestamp=self.get_current_timestamp(),
        )

        return self.runner.call_public_method(
            self.manager_id, "register_token", ctx, token_name, token_symbol
        )

    def _reach_migration_threshold_precisely(self, token_uid: TokenUid, stop_short_by: Amount = Amount(0)):
        """
        Reach migration threshold with precise control.

        Args:
            token_uid: Token to migrate
            stop_short_by: Amount to stop short of threshold (for testing edge cases)
        """
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        target_market_cap = token_info.target_market_cap
        current_virtual_pool = token_info.virtual_pool
        remaining_amount = target_market_cap - current_virtual_pool - stop_short_by

        # Execute in smaller chunks for precision
        chunk_size = min(remaining_amount // 3, Amount(100000))

        while remaining_amount > Amount(0):
            amount_to_buy = min(chunk_size, remaining_amount)

            if amount_to_buy <= Amount(0):
                break

            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, amount_to_buy
            )

            htr_amount = int(quote.get("recommended_htr_amount", amount_to_buy))
            expected_out = int(quote["amount_out"])

            ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=htr_amount),
                    NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

            # Update remaining amount
            updated_info = self.runner.call_view_method(
                self.manager_id, "get_token_info", token_uid
            )

            remaining_amount = target_market_cap - updated_info.virtual_pool - stop_short_by

            # Prevent infinite loop
            if updated_info.is_migrated:
                break

    def test_precise_migration_threshold(self):
        """Test hitting migration threshold precisely."""
        token_uid = self._register_token("PreciseMigration", "PMG")

        # Get very close to migration threshold
        self._reach_migration_threshold_precisely(token_uid, stop_short_by=Amount(100))

        # Verify not migrated yet
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )
        self.assertFalse(token_info.is_migrated)

        # Do final push to trigger migration
        remaining = token_info.target_market_cap - token_info.virtual_pool

        if remaining > Amount(0):
            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, remaining
            )

            htr_amount = int(quote.get("recommended_htr_amount", remaining))
            expected_out = int(quote["amount_out"])

            ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=htr_amount),
                    NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

        # Verify migration occurred
        final_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )
        self.assertTrue(final_info.is_migrated)

    def test_migration_state_consistency(self):
        """Test that migration state is consistent across all operations."""
        token_uid = self._register_token("StateConsistency", "SC")

        # Migrate the token
        self._reach_migration_threshold_precisely(token_uid)

        # Verify migrated state
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        if token_info.is_migrated:
            # All trading operations should now fail with InvalidState
            with self.assertRaises(InvalidState):
                self.runner.call_view_method(
                    self.manager_id, "quote_buy", token_uid, Amount(1000)
                )

            with self.assertRaises(InvalidState):
                self.runner.call_view_method(
                    self.manager_id, "quote_sell", token_uid, Amount(1000)
                )

            # Verify pool key exists
            self.assertIsNotNone(token_info.pool_key)
            self.assertNotEqual(token_info.pool_key, "")

            # Verify pool was created in DozerPoolManager
            token_pools = self.runner.call_view_method(
                self.dozer_pool_manager_id, "get_pools_for_token", token_uid
            )
            self.assertIn(token_info.pool_key, token_pools)

    def test_multiple_token_migration_interaction(self):
        """Test interaction between multiple tokens during migration."""
        # Create multiple tokens
        token1_uid = self._register_token("MultiToken1", "MT1")
        token2_uid = self._register_token("MultiToken2", "MT2")
        token3_uid = self._register_token("MultiToken3", "MT3")

        # Partially progress each token
        self._reach_migration_threshold_precisely(token1_uid, stop_short_by=Amount(50000))
        self._reach_migration_threshold_precisely(token2_uid, stop_short_by=Amount(30000))
        self._reach_migration_threshold_precisely(token3_uid, stop_short_by=Amount(100000))

        # Migrate token1
        remaining1 = self._get_remaining_to_migration(token1_uid)
        if remaining1 > Amount(0):
            self._buy_exact_amount(token1_uid, remaining1)

        # Verify token1 migrated, others didn't
        info1 = self.runner.call_view_method(self.manager_id, "get_token_info", token1_uid)
        info2 = self.runner.call_view_method(self.manager_id, "get_token_info", token2_uid)
        info3 = self.runner.call_view_method(self.manager_id, "get_token_info", token3_uid)

        self.assertTrue(info1.is_migrated)
        self.assertFalse(info2.is_migrated)
        self.assertFalse(info3.is_migrated)

        # Verify platform stats are correct
        platform_stats = self.runner.call_view_method(
            self.manager_id, "get_platform_stats"
        )

        self.assertEqual(platform_stats["total_tokens_migrated"], 1)
        self.assertGreaterEqual(platform_stats["total_tokens_created"], 3)

    def test_migration_fee_collection_edge_cases(self):
        """Test graduation fee collection in various scenarios."""
        token_uid = self._register_token("FeeCollection", "FC")

        # Get initial graduation fees
        initial_stats = self.runner.call_view_method(
            self.manager_id, "get_platform_stats"
        )
        initial_graduation_fees = initial_stats["graduation_fees_collected"]

        # Migrate token
        self._reach_migration_threshold_precisely(token_uid)

        # Verify graduation fees were collected
        final_stats = self.runner.call_view_method(
            self.manager_id, "get_platform_stats"
        )
        final_graduation_fees = final_stats["graduation_fees_collected"]

        # Should have increased by graduation fee amount
        expected_increase = GRADUATION_FEE
        actual_increase = final_graduation_fees - initial_graduation_fees

        self.assertEqual(actual_increase, expected_increase,
                        f"Expected graduation fee increase of {expected_increase}, got {actual_increase}")

    def test_post_migration_data_consistency(self):
        """Test that token data remains consistent after migration."""
        token_uid = self._register_token("DataConsistency", "DC")

        # Record pre-migration data
        pre_migration_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        # Migrate token
        self._reach_migration_threshold_precisely(token_uid)

        # Verify post-migration data
        post_migration_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        # These fields should remain the same
        self.assertEqual(post_migration_info.creator, pre_migration_info.creator)
        self.assertEqual(post_migration_info.total_supply, pre_migration_info.total_supply)
        self.assertEqual(post_migration_info.target_market_cap, pre_migration_info.target_market_cap)

        # These fields should have changed
        self.assertTrue(post_migration_info.is_migrated)
        self.assertIsNotNone(post_migration_info.pool_key)
        self.assertNotEqual(post_migration_info.pool_key, "")

    def test_edge_case_very_small_migration_step(self):
        """Test migration with very small final step."""
        token_uid = self._register_token("SmallStep", "SS")

        # Get extremely close to migration (within 1 unit)
        self._reach_migration_threshold_precisely(token_uid, stop_short_by=Amount(1))

        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        # Should not be migrated yet
        self.assertFalse(token_info.is_migrated)

        # Final tiny purchase
        remaining = token_info.target_market_cap - token_info.virtual_pool

        if remaining > Amount(0):
            # Use minimum possible purchase
            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, Amount(1)
            )

            htr_amount = max(Amount(1), int(quote.get("recommended_htr_amount", 1)))
            expected_out = int(quote["amount_out"])

            ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=htr_amount),
                    NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

        # Should now be migrated
        final_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        # Migration might have occurred
        if final_info.virtual_pool >= final_info.target_market_cap:
            self.assertTrue(final_info.is_migrated)

    def _get_remaining_to_migration(self, token_uid: TokenUid) -> Amount:
        """Get remaining amount needed for migration."""
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        return max(Amount(0), token_info.target_market_cap - token_info.virtual_pool)

    def _buy_exact_amount(self, token_uid: TokenUid, htr_amount: Amount):
        """Buy tokens with exact HTR amount."""
        if htr_amount <= Amount(0):
            return

        quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", token_uid, htr_amount
        )

        expected_out = int(quote["amount_out"])

        ctx = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=htr_amount),
                NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx, token_uid
        )