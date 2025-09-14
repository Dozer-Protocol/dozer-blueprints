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
Bonding curve mathematical edge cases for KhensuManager contract.

This module tests complex bonding curve scenarios:
- Mathematical precision and rounding
- Boundary conditions in curve calculations
- Price impact edge cases
- Migration threshold precision
- Fee calculation accuracy
- Extreme market conditions simulation
"""

import inspect
import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.khensu_manager import (
    KhensuManager,
    BASIS_POINTS,
    Unauthorized,
    InvalidParameters,
    InvalidState,
    TokenNotFound,
)
from hathor.nanocontracts.blueprints.dozer_pool_manager import (
    DozerPoolManager,
)
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail, NCTokenAlreadyExists
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
    EdgeCaseGenerator,
    create_deposit_action,
    create_withdrawal_action,
)
from hathor.nanocontracts.blueprints import khensu_manager, dozer_pool_manager

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID

# Constants for testing different bonding curve configurations
CURVE_CONFIGURATIONS = [
    {
        "name": "conservative",
        "market_cap": Amount(500000),
        "liquidity": Amount(100000),
        "initial_virtual_pool": Amount(10000),
        "curve_constant": Amount(10000000000),  # 10B
        "token_reserve": Amount(500000000),  # 500M
        "buy_fee": Amount(100),  # 1%
        "sell_fee": Amount(150),  # 1.5%
    },
    {
        "name": "aggressive",
        "market_cap": Amount(2000000),
        "liquidity": Amount(500000),
        "initial_virtual_pool": Amount(25000),
        "curve_constant": Amount(50000000000),  # 50B
        "token_reserve": Amount(2000000000),  # 2B
        "buy_fee": Amount(250),  # 2.5%
        "sell_fee": Amount(300),  # 3%
    },
    {
        "name": "extreme",
        "market_cap": Amount(10000000),
        "liquidity": Amount(2000000),
        "initial_virtual_pool": Amount(100000),
        "curve_constant": Amount(200000000000),  # 200B
        "token_reserve": Amount(10000000000),  # 10B
        "buy_fee": Amount(500),  # 5%
        "sell_fee": Amount(600),  # 6%
    }
]

ADDRESS_NC_TYPE = make_nc_type(Address)
AMOUNT_NC_TYPE = make_nc_type(Amount)
INT_NC_TYPE = VarInt32NCType()


class KhensuManagerBondingCurvesTest(BlueprintTestCase):
    """Test bonding curve mathematical precision and edge cases."""

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

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_managers(self, config: dict):
        """Initialize KhensuManager with specific curve configuration."""
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

        # Initialize KhensuManager with configuration
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
            config["market_cap"],
            config["liquidity"],
            config["initial_virtual_pool"],
            config["curve_constant"],
            config["token_reserve"],
            config["buy_fee"],
            config["sell_fee"],
            Amount(1000),  # graduation_fee
        )

        self.manager_storage = self.runner.get_storage(self.manager_id)

    def _register_token_with_config(self, config: dict, token_name: str, token_symbol: str) -> TokenUid:
        """Register a token with specific configuration."""
        self._initialize_managers(config)

        ctx = Context(
            [
                NCDepositAction(
                    token_uid=HTR_UID,
                    amount=int(config["token_reserve"] * 0.02),
                ),
            ],
            self.tx,
            self.admin_address,
            timestamp=self.get_current_timestamp(),
        )

        return self.runner.call_public_method(
            self.manager_id, "register_token", ctx, token_name, token_symbol
        )

    def test_precision_in_small_amounts(self):
        """Test bonding curve precision with very small amounts."""
        config = CURVE_CONFIGURATIONS[0]  # Conservative config
        token_uid = self._register_token_with_config(config, "PrecisionToken", "PREC")

        # Test very small buy amounts
        small_amounts = [Amount(1), Amount(10), Amount(100), Amount(1000)]

        for amount in small_amounts:
            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, amount
            )

            expected_out = quote["amount_out"]

            # Execute the trade if we have enough to trade
            if amount >= Amount(10):  # Minimum meaningful amount
                ctx = Context(
                    [
                        NCDepositAction(token_uid=HTR_UID, amount=amount),
                        NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                    ],
                    self.tx,
                    self.user_address,
                    timestamp=self.get_current_timestamp(),
                )

                self.runner.call_public_method(
                    self.manager_id, "buy_tokens", ctx, token_uid
                )

                # Verify the transaction completed without precision loss issues
                token_info = self.runner.call_view_method(
                    self.manager_id, "get_token_info", token_uid
                )
                self.assertGreater(token_info.transaction_count, 0)

    def test_curve_constant_boundary_conditions(self):
        """Test bonding curves at various curve constant boundaries."""
        # Test different curve constants to find boundary behaviors
        curve_constants = [
            Amount(1000000),      # Very small
            Amount(100000000),    # Small
            Amount(10000000000),  # Medium
            Amount(1000000000000), # Large
        ]

        for i, curve_constant in enumerate(curve_constants):
            config = CURVE_CONFIGURATIONS[0].copy()
            config["curve_constant"] = curve_constant

            try:
                token_uid = self._register_token_with_config(
                    config, f"CurveTest{i}", f"CT{i}"
                )

                # Test a standard buy operation
                buy_amount = Amount(10000)
                quote = self.runner.call_view_method(
                    self.manager_id, "quote_buy", token_uid, buy_amount
                )

                # Verify the curve behaves reasonably
                self.assertGreater(quote["amount_out"], 0)
                self.assertLess(quote["price_impact"], 10000)  # Less than 100%

            except Exception as e:
                # Some curve constants might be invalid
                self.assertIn("InvalidParameters", str(type(e)))

    def test_migration_threshold_precision(self):
        """Test precision near migration threshold."""
        config = CURVE_CONFIGURATIONS[0]  # Conservative config
        token_uid = self._register_token_with_config(config, "MigrationToken", "MIG")

        # Get token info to understand migration threshold
        initial_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        target_market_cap = initial_info.target_market_cap
        current_virtual_pool = initial_info.virtual_pool

        # Calculate amount needed to get very close to migration
        remaining_to_migration = target_market_cap - current_virtual_pool

        # Test getting very close to migration (within 1% of threshold)
        close_amount = int(remaining_to_migration * 0.99)

        if close_amount > 0:
            # Get quote for amount that brings us close to migration
            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, close_amount
            )

            recommended_amount = quote.get("recommended_htr_amount", close_amount)
            expected_out = quote["amount_out"]

            ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=recommended_amount),
                    NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

            # Check we're close but not migrated yet
            updated_info = self.runner.call_view_method(
                self.manager_id, "get_token_info", token_uid
            )

            self.assertFalse(updated_info.is_migrated)
            self.assertLess(
                target_market_cap - updated_info.virtual_pool,
                remaining_to_migration * 0.1  # Should be much closer
            )

    def test_fee_calculation_accuracy(self):
        """Test fee calculation accuracy across different scenarios."""
        config = CURVE_CONFIGURATIONS[1]  # Aggressive config with higher fees
        token_uid = self._register_token_with_config(config, "FeeTestToken", "FEE")

        # Test various buy amounts to verify fee calculation
        test_amounts = [
            Amount(1000),    # Small
            Amount(50000),   # Medium
            Amount(200000),  # Large
        ]

        for amount in test_amounts:
            initial_platform_stats = self.runner.call_view_method(
                self.manager_id, "get_platform_stats"
            )

            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, amount
            )
            expected_out = quote["amount_out"]

            # Calculate expected fee
            expected_buy_fee = (amount * config["buy_fee"] + BASIS_POINTS - 1) // BASIS_POINTS

            ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=amount),
                    NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

            # Verify fee was collected accurately
            final_platform_stats = self.runner.call_view_method(
                self.manager_id, "get_platform_stats"
            )

            fees_collected_diff = (
                final_platform_stats["platform_fees_collected"] -
                initial_platform_stats["platform_fees_collected"]
            )

            # Allow for small rounding differences (within 1 unit)
            self.assertLessEqual(
                abs(fees_collected_diff - expected_buy_fee), 1,
                f"Fee calculation inaccurate: expected ~{expected_buy_fee}, got {fees_collected_diff}"
            )

    def test_price_impact_extreme_scenarios(self):
        """Test price impact calculations in extreme scenarios."""
        config = CURVE_CONFIGURATIONS[2]  # Extreme config
        token_uid = self._register_token_with_config(config, "ExtremeToken", "EXT")

        # Test very large buy that should have high price impact
        initial_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        # Try to buy with amount equal to 50% of current virtual pool
        large_amount = initial_info.virtual_pool // 2

        if large_amount > Amount(0):
            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, large_amount
            )

            price_impact = quote["price_impact"]

            # Large trades should have significant price impact
            self.assertGreater(price_impact, 1000)  # More than 10%

            # But not impossible (less than 100%)
            self.assertLess(price_impact, 10000)

    def test_mathematical_invariants(self):
        """Test that mathematical invariants hold across operations."""
        config = CURVE_CONFIGURATIONS[0]
        token_uid = self._register_token_with_config(config, "InvariantToken", "INV")

        # Perform a series of buy and sell operations
        operations = [
            ("buy", Amount(10000)),
            ("sell", Amount(5000)),
            ("buy", Amount(15000)),
            ("sell", Amount(8000)),
        ]

        initial_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        user_token_balance = Amount(0)
        total_htr_spent = Amount(0)
        total_htr_received = Amount(0)

        for op_type, amount in operations:
            if op_type == "buy":
                quote = self.runner.call_view_method(
                    self.manager_id, "quote_buy", token_uid, amount
                )
                expected_out = quote["amount_out"]

                ctx = Context(
                    [
                        NCDepositAction(token_uid=HTR_UID, amount=amount),
                        NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                    ],
                    self.tx,
                    self.user_address,
                    timestamp=self.get_current_timestamp(),
                )

                self.runner.call_public_method(
                    self.manager_id, "buy_tokens", ctx, token_uid
                )

                user_token_balance += expected_out
                total_htr_spent += amount

            elif op_type == "sell" and user_token_balance >= amount:
                quote = self.runner.call_view_method(
                    self.manager_id, "quote_sell", token_uid, amount
                )
                expected_htr_out = quote["amount_out"]

                ctx = Context(
                    [
                        NCDepositAction(token_uid=token_uid, amount=amount),
                        NCWithdrawalAction(token_uid=HTR_UID, amount=expected_htr_out),
                    ],
                    self.tx,
                    self.user_address,
                    timestamp=self.get_current_timestamp(),
                )

                self.runner.call_public_method(
                    self.manager_id, "sell_tokens", ctx, token_uid
                )

                user_token_balance -= amount
                total_htr_received += expected_htr_out

        # Verify conservation principles
        final_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )

        # The sum of virtual pool changes should equal net HTR flow (minus fees)
        net_htr_flow = total_htr_spent - total_htr_received
        virtual_pool_change = final_info.virtual_pool - initial_info.virtual_pool

        # Account for fees in the comparison (should be roughly equal)
        fee_tolerance = net_htr_flow * 10 // 100  # 10% tolerance for fees
        self.assertLessEqual(
            abs(virtual_pool_change - net_htr_flow), fee_tolerance,
            f"Conservation principle violation: pool change {virtual_pool_change} vs net flow {net_htr_flow}"
        )

    def test_rounding_consistency(self):
        """Test that rounding is consistent and doesn't create arbitrage."""
        config = CURVE_CONFIGURATIONS[0]
        token_uid = self._register_token_with_config(config, "RoundingToken", "RND")

        # Test buying and immediately selling same amount
        test_amount = Amount(12345)  # Odd number to test rounding

        # Buy tokens
        buy_quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", token_uid, test_amount
        )
        tokens_received = buy_quote["amount_out"]

        buy_ctx = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=test_amount),
                NCWithdrawalAction(token_uid=token_uid, amount=tokens_received),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", buy_ctx, token_uid
        )

        # Immediately sell the tokens back
        sell_quote = self.runner.call_view_method(
            self.manager_id, "quote_sell", token_uid, tokens_received
        )
        htr_received = sell_quote["amount_out"]

        sell_ctx = Context(
            [
                NCDepositAction(token_uid=token_uid, amount=tokens_received),
                NCWithdrawalAction(token_uid=HTR_UID, amount=htr_received),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "sell_tokens", sell_ctx, token_uid
        )

        # The difference should be reasonable (fees + rounding)
        loss = test_amount - htr_received
        max_expected_loss = test_amount * (config["buy_fee"] + config["sell_fee"]) // BASIS_POINTS

        # Allow for some rounding, but loss shouldn't be excessive
        self.assertLessEqual(
            loss, max_expected_loss + Amount(100),  # Small rounding tolerance
            f"Excessive loss from rounding: lost {loss}, max expected {max_expected_loss}"
        )
        self.assertGreaterEqual(
            loss, Amount(0),
            f"Negative loss (profit) suggests arbitrage opportunity: {loss}"
        )