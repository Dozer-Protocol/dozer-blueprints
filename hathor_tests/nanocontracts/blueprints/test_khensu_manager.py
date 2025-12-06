import inspect
import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.khensu_manager import BASIS_POINTS
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.nc_types import (
    VarInt32NCType,
    make_nc_type_for_arg_type as make_nc_type,
)
from hathor.nanocontracts.types import (
    Address,
    Amount,
    CallerId,
    ContractId,
    TokenUid,
    NCDepositAction,
    NCWithdrawalAction,
)
from hathor.conf.get_settings import HathorSettings
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from hathor.nanocontracts.blueprints import khensu_manager, dozer_pool_manager

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID

DEFAULT_MARKET_CAP = Amount(1725000_00)
DEFAULT_LIQUIDITY_AMOUNT = Amount(300000_00)
DEFAULT_INITIAL_VIRTUAL_POOL = Amount(15000_00)
DEFAULT_CURVE_CONSTANT = Amount(32190005730_0000)
INITIAL_TOKEN_RESERVE = Amount(1073000191)
BUY_FEE_RATE = Amount(200)  # 2%
SELL_FEE_RATE = Amount(300)  # 2%
GRADUATION_FEE = Amount(1000_00)  # 1,000 HTR
LRU_CACHE_CAPACITY = 150  # Default LRU cache capacity

AMOUNT_NC_TYPE = make_nc_type(Amount)
CALLER_NC_TYPE = make_nc_type(CallerId)
BOOL_NC_TYPE = make_nc_type(bool)
CONTRACT_NC_TYPE = make_nc_type(ContractId)
INT_NC_TYPE = VarInt32NCType()


class KhensuManagerTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        # Set up KhensuManager contract
        # self.blueprint_id_khensu = self.gen_random_blueprint_id()
        self.blueprint_id_khensu = self.register_blueprint_file(
            inspect.getfile(khensu_manager)
        )
        self.manager_id = self.gen_random_contract_id()

        # self.register_blueprint_class(self.blueprint_id_khensu, KhensuManager)

        # Set up Dozer Pool Manager contract
        # self.blueprint_id_dozer = self.gen_random_blueprint_id()
        self.blueprint_id_dozer = self.register_blueprint_file(
            inspect.getfile(dozer_pool_manager)
        )
        self.dozer_pool_manager_id = self.gen_random_contract_id()

        # self.register_blueprint_class(self.blueprint_id_dozer, DozerPoolManager)

        # Setup admin and user addresses
        self.admin_address = Address(self._get_any_address()[0])
        self.user_address = Address(self._get_any_address()[0])

        # Initialize base tx for contexts
        self.tx = self.get_genesis_tx()

        self.token1_uid = None
        self.token2_uid = None
        self.manager_storage = None

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_dozer(self):
        """Initialize the DozerPoolManager contract"""
        context = self.create_context()
        self.runner.create_contract(
            self.dozer_pool_manager_id,
            self.blueprint_id_dozer,
            context,
        )

    def _initialize_manager(self) -> Context:
        """Initialize KhensuManager contract"""
        ctx = self.create_context(caller_id=self.admin_address)

        self._initialize_dozer()

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
            LRU_CACHE_CAPACITY,
        )

        self.manager_storage = self.runner.get_storage(self.manager_id)
        return ctx

    def _register_token(
        self, token_name: str, token_symbol: str, creator_address=None
    ) -> TokenUid:
        """Register a new token with the manager"""
        if creator_address is None:
            creator_address = self.admin_address

        action = NCDepositAction(
            token_uid=HTR_UID, amount=int(INITIAL_TOKEN_RESERVE * float(0.02))
        )

        ctx = self.create_context(
            caller_id=creator_address,
            actions=[action],
        )

        token_uid = self.runner.call_public_method(
            self.manager_id,
            "register_token",
            ctx,
            token_name,
            token_symbol,
            "",
            "",
            "",
            "",
            "",
        )

        return token_uid

    def test_initialize(self) -> None:
        """Test basic initialization"""
        self._initialize_manager()

        storage = self.manager_storage
        self.assertEqual(
            storage.get_obj(b"admin_address", CALLER_NC_TYPE), self.admin_address
        )
        self.assertEqual(storage.get_obj(b"buy_fee_rate", INT_NC_TYPE), BUY_FEE_RATE)
        self.assertEqual(storage.get_obj(b"sell_fee_rate", INT_NC_TYPE), SELL_FEE_RATE)
        self.assertEqual(
            storage.get_obj(b"default_target_market_cap", AMOUNT_NC_TYPE),
            DEFAULT_MARKET_CAP,
        )
        self.assertEqual(
            storage.get_obj(b"default_liquidity_amount", AMOUNT_NC_TYPE),
            DEFAULT_LIQUIDITY_AMOUNT,
        )
        self.assertEqual(
            storage.get_obj(b"graduation_fee", AMOUNT_NC_TYPE), GRADUATION_FEE
        )
        self.assertEqual(storage.get_obj(b"total_tokens_created", INT_NC_TYPE), 0)
        self.assertEqual(storage.get_obj(b"total_tokens_migrated", INT_NC_TYPE), 0)

    def test_register_token(self) -> None:
        """Test token registration"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        storage = self.manager_storage
        self.assertEqual(storage.get_obj(b"total_tokens_created", INT_NC_TYPE), 1)

        # Check token exists in the registry
        all_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 1, HTR_UID
        )
        self.assertSubstring(str(self.token1_uid.hex()), all_tokens)

        fetched_token_uid = self.runner.call_view_method(
            self.manager_id, "get_token_uid", "TK1"
        )
        self.assertEqual(str(self.token1_uid.hex()), fetched_token_uid)

        # Check token data using get_token_info
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        self.assertEqual(token_info.creator, self.admin_address.hex())
        self.assertEqual(token_info.initial_virtual_pool, DEFAULT_INITIAL_VIRTUAL_POOL)
        self.assertEqual(token_info.market_cap, 0)
        self.assertEqual(
            token_info.token_reserve,
            INITIAL_TOKEN_RESERVE,
        )
        self.assertEqual(token_info.is_migrated, False)

    def test_register_token_duplicate(self) -> None:
        """Test registering a duplicate token fails"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        # Try to register the same token again
        with self.assertRaises(NCFail):
            self._register_token("token1", "TK1")

    def test_buy_tokens(self) -> None:
        """Test buying tokens with HTR"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        initial_token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        amount_in = 100000

        # Calculate expected tokens out
        quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, amount_in
        )
        expected_out = quote["amount_out"]

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out)
        ctx = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx, self.token1_uid
        )

        # Verify state changes
        storage = self.manager_storage

        # Check transaction count and volume in token statistics
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        calculated_fee = (amount_in * BUY_FEE_RATE + BASIS_POINTS - 1) // BASIS_POINTS

        self.assertEqual(token_info.transaction_count, 1)
        self.assertEqual(token_info.total_volume, amount_in)
        self.assertEqual(
            storage.get_obj(b"collected_buy_fees", AMOUNT_NC_TYPE), calculated_fee
        )

        # Check token reserve decreased and virtual pool increased
        self.assertEqual(
            token_info.token_reserve, initial_token_info.token_reserve - expected_out
        )
        self.assertEqual(
            token_info.market_cap,
            amount_in - calculated_fee,
        )

    def test_sell_tokens(self) -> None:
        """Test selling tokens for HTR"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        # First buy some tokens to ensure the contract has HTR
        buy_amount = 10000
        buy_quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, buy_amount
        )
        buy_tokens = buy_quote["amount_out"]

        deposit = NCDepositAction(token_uid=HTR_UID, amount=buy_amount)
        withdrawal = NCWithdrawalAction(token_uid=self.token1_uid, amount=buy_tokens)
        buy_ctx = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", buy_ctx, self.token1_uid
        )

        initial_token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        storage = self.manager_storage

        initial_fee_collected = storage.get_obj(b"collected_sell_fees", AMOUNT_NC_TYPE)

        # Now sell a portion of the tokens
        # Sell a smaller portion to ensure reasonable amounts
        amount_in = buy_tokens // 10  # Sell 10% of received tokens

        # Calculate expected HTR out
        quote = self.runner.call_view_method(
            self.manager_id, "quote_sell", self.token1_uid, amount_in
        )
        expected_out = max(
            1, int(quote["amount_out"])
        )  # Ensure positive integer amount

        # Verify we have positive amounts
        if amount_in <= 0 or expected_out <= 0:
            self.fail("Invalid amounts calculated")

        deposit = NCDepositAction(token_uid=self.token1_uid, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=HTR_UID, amount=expected_out)
        ctx = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "sell_tokens", ctx, self.token1_uid
        )

        # Check transaction count and volume in token statistics
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        self.assertEqual(
            token_info.transaction_count - initial_token_info.transaction_count, 1
        )

        estimated_fee = (
            expected_out * SELL_FEE_RATE + BASIS_POINTS - SELL_FEE_RATE - 1
        ) // (BASIS_POINTS - SELL_FEE_RATE)

        self.assertEqual(
            token_info.total_volume, buy_amount + expected_out + estimated_fee
        )
        self.assertEqual(
            storage.get_obj(b"collected_sell_fees", AMOUNT_NC_TYPE),
            initial_fee_collected + estimated_fee,
        )

        # Get the current pool and reserve values
        market_cap = token_info.market_cap
        token_reserve = token_info.token_reserve

        self.assertEqual(
            market_cap, initial_token_info.market_cap - expected_out - estimated_fee
        )
        self.assertEqual(token_reserve, initial_token_info.token_reserve + amount_in)

    def test_transaction_values(self) -> None:
        """Test values when buying and selling tokens"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        testing_values = 1000000
        last_token_purchase = None

        for i in range((DEFAULT_MARKET_CAP - 1) // testing_values):
            # Each iterations should start from a different point in the curve
            if i > 0:
                start_purchase = testing_values
                start_quote = self.runner.call_view_method(
                    self.manager_id, "quote_buy", self.token1_uid, start_purchase
                )
                deposit = NCDepositAction(token_uid=HTR_UID, amount=start_purchase)
                withdrawal = NCWithdrawalAction(
                    token_uid=self.token1_uid, amount=start_quote["amount_out"]
                )
                first_buy_ctx = self.create_context(
                    caller_id=self.user_address,
                    actions=[deposit, withdrawal],
                )

                self.runner.call_public_method(
                    self.manager_id, "buy_tokens", first_buy_ctx, self.token1_uid
                )

            # Calculate how the virtual pool should change for a purchase
            payed_htr = testing_values
            change_on_virtual_pool = (
                payed_htr * (BASIS_POINTS - BUY_FEE_RATE) // BASIS_POINTS
            )

            # Store how many tokens were purchased
            buy_quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", self.token1_uid, payed_htr
            )
            tokens_exchanged = buy_quote["amount_out"]

            # Make sure the price per token increased
            if last_token_purchase:
                self.assertLess(tokens_exchanged, last_token_purchase)
            last_token_purchase = tokens_exchanged

            initial_token_info = self.runner.call_view_method(
                self.manager_id, "get_token_info", self.token1_uid
            )

            initial_plat_info = self.runner.call_view_method(
                self.manager_id, "get_platform_stats"
            )

            deposit = NCDepositAction(token_uid=HTR_UID, amount=payed_htr)
            withdrawal = NCWithdrawalAction(
                token_uid=self.token1_uid, amount=tokens_exchanged
            )
            buy_ctx = self.create_context(
                caller_id=self.user_address,
                actions=[deposit, withdrawal],
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", buy_ctx, self.token1_uid
            )

            mid_plat_info = self.runner.call_view_method(
                self.manager_id, "get_platform_stats"
            )

            # Calculate how many HTR the user will receive for selling all his tokens
            # The final value can be less than the calculated due to ceiling division on both buy and sell methods
            # The difference between the real value and the expected one should be added to the virtual pool (no HTR is lost)
            should_sell_for = (
                change_on_virtual_pool * (BASIS_POINTS - SELL_FEE_RATE) // BASIS_POINTS
            )

            # Sell all the tokens purchased previously
            sell_quote = self.runner.call_view_method(
                self.manager_id, "quote_sell", self.token1_uid, tokens_exchanged
            )

            deposit = NCDepositAction(
                token_uid=self.token1_uid, amount=tokens_exchanged
            )
            withdrawal = NCWithdrawalAction(
                token_uid=HTR_UID, amount=sell_quote["amount_out"]
            )
            sell_ctx = self.create_context(
                caller_id=self.user_address,
                actions=[deposit, withdrawal],
            )

            self.runner.call_public_method(
                self.manager_id, "sell_tokens", sell_ctx, self.token1_uid
            )

            final_token_info = self.runner.call_view_method(
                self.manager_id, "get_token_info", self.token1_uid
            )

            final_plat_info = self.runner.call_view_method(
                self.manager_id, "get_platform_stats"
            )

            real_sell_fee = (
                final_plat_info["platform_fees_collected"]
                - mid_plat_info["platform_fees_collected"]
            )
            expected_sell_fee = change_on_virtual_pool - should_sell_for

            self.assertEqual(
                final_token_info.market_cap
                - initial_token_info.market_cap
                + real_sell_fee,
                should_sell_for - sell_quote["amount_out"] + expected_sell_fee,
            )
            self.assertEqual(
                payed_htr,
                final_token_info.market_cap
                - initial_token_info.market_cap
                + sell_quote["amount_out"]
                + final_plat_info["platform_fees_collected"]
                - initial_plat_info["platform_fees_collected"],
            )

            # Given that The Curve Constant > Virtual Pool at any time:
            self.assertGreaterEqual(should_sell_for - sell_quote["amount_out"], 0)
            self.assertLess(
                should_sell_for - sell_quote["amount_out"],
                1
                + (
                    initial_token_info.market_cap
                    + initial_token_info.initial_virtual_pool
                )
                ** 2
                / (
                    DEFAULT_CURVE_CONSTANT
                    - initial_token_info.market_cap
                    - initial_token_info.initial_virtual_pool
                ),
            )

    def _reach_migration_threshold(self, token_uid):
        """Helper to reach migration threshold through multiple purchases"""

        # Get token info to access target_market_cap and virtual_pool
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )
        target_market_cap = token_info.target_market_cap
        market_cap = token_info.market_cap

        remaining_amount = target_market_cap - market_cap
        while remaining_amount > 0:
            amount_in = 4000000

            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, amount_in
            )
            should_go_in = int(quote["recommended_htr_amount"])
            expected_out = int(quote["amount_out"])

            deposit = NCDepositAction(token_uid=HTR_UID, amount=should_go_in)
            withdrawal = NCWithdrawalAction(token_uid=token_uid, amount=expected_out)
            ctx = self.create_context(
                caller_id=self.user_address,
                actions=[deposit, withdrawal],
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

            # Update token data after each transaction
            token_info = self.runner.call_view_method(
                self.manager_id, "get_token_info", token_uid
            )
            market_cap = token_info.market_cap
            remaining_amount = target_market_cap - market_cap

    def test_migration(self) -> None:
        """Test token migration to Dozer Pool"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        storage = self.manager_storage

        # Get initial graduation fees
        initial_graduation_fees = self.manager_storage.get_obj(
            b"collected_graduation_fees", AMOUNT_NC_TYPE
        )

        self._reach_migration_threshold(self.token1_uid)

        # Verify migration state

        # Check if token is migrated using get_token_info
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )
        self.assertTrue(token_info.is_migrated)
        self.assertEqual(storage.get_obj(b"total_tokens_migrated", INT_NC_TYPE), 1)
        self.assertIsNotNone(token_info.pool_key)

        # Check Pool Key
        token_pools = self.runner.call_view_method(
            self.dozer_pool_manager_id, "get_pools_for_token", self.token1_uid
        )
        self.assertTrue(token_info.pool_key in token_pools)

        # Verify graduation fees were collected
        final_graduation_fees = self.manager_storage.get_obj(
            b"collected_graduation_fees", AMOUNT_NC_TYPE
        )
        self.assertGreater(final_graduation_fees, initial_graduation_fees)

        # Try to get quotes - should fail
        with self.assertNCFail("InvalidState"):
            self.runner.call_view_method(
                self.manager_id, "quote_buy", self.token1_uid, 100000
            )

        with self.assertNCFail("InvalidState"):
            self.runner.call_view_method(
                self.manager_id, "quote_sell", self.token1_uid, 100000
            )

    def test_withdraw_fees(self) -> None:
        """Test fee withdrawal functionality"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        # Generate fees by making trades
        amount_in = 500000
        quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, amount_in
        )
        expected_out = quote["amount_out"]

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out)
        ctx_buy = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx_buy, self.token1_uid
        )

        # Verify fees were collected
        storage = self.manager_storage
        collected_buy_fees = storage.get_obj(b"collected_buy_fees", AMOUNT_NC_TYPE)
        self.assertGreater(collected_buy_fees, 0)

        # Non-admin should not be able to withdraw fees
        withdrawal = NCWithdrawalAction(token_uid=HTR_UID, amount=collected_buy_fees)
        ctx_non_admin = self.create_context(
            caller_id=self.user_address,
            actions=[withdrawal],
        )

        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id, "withdraw_fees", ctx_non_admin
            )

        # Admin should be able to withdraw fees
        withdrawal = NCWithdrawalAction(token_uid=HTR_UID, amount=collected_buy_fees)
        ctx_admin = self.create_context(
            caller_id=self.admin_address,
            actions=[withdrawal],
        )

        self.runner.call_public_method(self.manager_id, "withdraw_fees", ctx_admin)

        # Verify fees were reset
        self.assertEqual(storage.get_obj(b"collected_buy_fees", AMOUNT_NC_TYPE), 0)

    def test_change_parameters(self) -> None:
        """Test changing contract parameters"""
        self._initialize_manager()

        ctx = self.create_context(caller_id=self.admin_address)

        # Change buy fee rate
        new_buy_fee = 300  # 3%
        self.runner.call_public_method(
            self.manager_id, "change_buy_fee_rate", ctx, new_buy_fee
        )

        # Change sell fee rate
        new_sell_fee = 400  # 4%
        self.runner.call_public_method(
            self.manager_id, "change_sell_fee_rate", ctx, new_sell_fee
        )

        # Change graduation fee
        new_graduation_fee = 2000
        self.runner.call_public_method(
            self.manager_id, "change_graduation_fee", ctx, new_graduation_fee
        )

        # Change bonding curve parameters
        new_target_cap = 80000
        new_liquidity = 15000
        new_initial_virtual_pool = 20000
        new_curve_constant = 33000000000
        new_token_total_supply = 1100000000

        self.runner.call_public_method(
            self.manager_id,
            "change_bonding_curve",
            ctx,
            new_target_cap,
            new_liquidity,
            new_initial_virtual_pool,
            new_curve_constant,
            new_token_total_supply,
        )

        # Verify parameters were changed
        storage = self.manager_storage
        self.assertEqual(storage.get_obj(b"buy_fee_rate", INT_NC_TYPE), new_buy_fee)
        self.assertEqual(storage.get_obj(b"sell_fee_rate", INT_NC_TYPE), new_sell_fee)
        self.assertEqual(
            storage.get_obj(b"graduation_fee", AMOUNT_NC_TYPE), new_graduation_fee
        )
        self.assertEqual(
            storage.get_obj(b"default_target_market_cap", AMOUNT_NC_TYPE),
            new_target_cap,
        )
        self.assertEqual(
            storage.get_obj(b"default_liquidity_amount", AMOUNT_NC_TYPE), new_liquidity
        )
        self.assertEqual(
            storage.get_obj(b"default_initial_virtual_pool", AMOUNT_NC_TYPE),
            new_initial_virtual_pool,
        )
        self.assertEqual(
            storage.get_obj(b"default_curve_constant", AMOUNT_NC_TYPE),
            new_curve_constant,
        )
        self.assertEqual(
            storage.get_obj(b"default_token_total_supply", AMOUNT_NC_TYPE),
            new_token_total_supply,
        )

        # Non-admin should not be able to change parameters
        ctx_non_admin = self.create_context(caller_id=self.user_address)

        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id, "change_buy_fee_rate", ctx_non_admin, 500
            )

        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id, "change_sell_fee_rate", ctx_non_admin, 500
            )

        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id, "change_graduation_fee", ctx_non_admin, 3000
            )

        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id,
                "change_bonding_curve",
                ctx_non_admin,
                new_target_cap + 1,
                new_liquidity + 1,
                new_initial_virtual_pool + 1,
                new_curve_constant + 1,
                new_token_total_supply + 1,
            )

        # Invalid values should fail
        with self.assertNCFail("InvalidParameters"):
            self.runner.call_public_method(
                self.manager_id, "change_buy_fee_rate", ctx, -1
            )

        with self.assertNCFail("InvalidParameters"):
            self.runner.call_public_method(
                self.manager_id, "change_sell_fee_rate", ctx, -1
            )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.manager_id, "change_graduation_fee", ctx, -1
            )

        with self.assertNCFail("InvalidParameters"):
            self.runner.call_public_method(
                self.manager_id,
                "change_bonding_curve",
                ctx,
                new_target_cap,
                new_target_cap + 1,
                new_initial_virtual_pool,
                new_curve_constant,
                new_token_total_supply,
            )

        # Define normal values for all parameters
        normal_values = {
            "new_target_cap": new_target_cap,
            "new_liquidity": new_liquidity,
            "new_initial_virtual_pool": new_initial_virtual_pool,
            "new_curve_constant": new_curve_constant,
            "new_token_total_supply": new_token_total_supply,
        }

        # List of all parameters to test
        parameters_to_test = [
            "new_target_cap",
            "new_liquidity",
            "new_initial_virtual_pool",
            "new_curve_constant",
            "new_token_total_supply",
        ]

        for param in parameters_to_test:
            # Create a copy of normal values
            test_params = normal_values.copy()
            # Set current parameter to 0
            test_params[param] = 0

            with self.subTest(f"Testing {param} = 0"), self.assertNCFail(
                "InvalidParameters"
            ):
                self.runner.call_public_method(
                    self.manager_id,
                    "change_bonding_curve",
                    ctx,
                    test_params["new_target_cap"],
                    test_params["new_liquidity"],
                    test_params["new_initial_virtual_pool"],
                    test_params["new_curve_constant"],
                    test_params["new_token_total_supply"],
                )

    def test_transfer_admin(self) -> None:
        """Test platform admin rights"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1", self.admin_address)

        new_admin = Address(self._get_any_address()[0])

        ctx = self.create_context(caller_id=self.admin_address)

        self.runner.call_public_method(
            self.manager_id, "transfer_admin", ctx, new_admin
        )

        # Original admin should no longer be able to manage the token
        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id,
                "transfer_admin",
                ctx,
                self.admin_address,
            )

        # New admin should be able to manage the token
        ctx_new_admin = self.create_context(caller_id=new_admin)

        self.runner.call_public_method(
            self.manager_id,
            "transfer_admin",
            ctx_new_admin,
            self.admin_address,
        )

    def test_multi_token_management(self) -> None:
        """Test managing multiple tokens"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")
        self.token2_uid = self._register_token("token2", "TK2")

        # Verify both tokens are registered
        all_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, HTR_UID
        )
        self.assertSubstring(str(self.token1_uid.hex()), all_tokens)
        self.assertSubstring(str(self.token2_uid.hex()), all_tokens)

        # Verify that token with same symbol cannot be created
        with self.assertNCFail("NCTokenAlreadyExists"):
            self._register_token("token1_2", "TK1")

        # Buy tokens for each
        amount_in = 100000

        # Token 1
        quote1 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, amount_in
        )
        expected_out1 = quote1["amount_out"]

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out1)
        ctx1 = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx1, self.token1_uid
        )

        # Token 2
        quote2 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token2_uid, amount_in
        )
        expected_out2 = quote2["amount_out"]

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=self.token2_uid, amount=expected_out2)
        ctx2 = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx2, self.token2_uid
        )

        # Verify both tokens have updated state
        token1_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )
        token2_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token2_uid
        )

        self.assertEqual(token1_info.transaction_count, 1)
        self.assertEqual(token2_info.transaction_count, 1)

        # Get all tokens
        all_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, HTR_UID
        )

        self.assertSubstring(str(self.token1_uid.hex()), all_tokens)
        self.assertSubstring(str(self.token2_uid.hex()), all_tokens)

        # Get platform stats
        stats = self.runner.call_view_method(self.manager_id, "get_platform_stats")

        self.assertEqual(stats["total_tokens_created"], 2)
        self.assertEqual(stats["total_tokens_migrated"], 0)
        self.assertGreater(stats["platform_fees_collected"], 0)

    def test_quote_methods(self) -> None:
        """Test quote methods for buy and sell"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        # Test quote_buy
        buy_amount = 10000000000
        buy_quote1 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, buy_amount
        )

        self.assertIn("amount_out", buy_quote1)
        self.assertIn("price_impact", buy_quote1)
        self.assertIn("recommended_htr_amount", buy_quote1)
        self.assertGreater(buy_amount, buy_quote1["recommended_htr_amount"])

        buy_amount = buy_quote1["recommended_htr_amount"]
        buy_quote2 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, buy_amount
        )
        self.assertEqual(buy_amount, buy_quote2["recommended_htr_amount"])
        self.assertEqual(buy_quote1["amount_out"], buy_quote2["amount_out"])

        buy_amount = buy_amount - 1
        buy_quote3 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, buy_amount
        )
        self.assertGreater(buy_quote1["amount_out"], buy_quote3["amount_out"])

        # Test quote_sell
        token_amount = 100000
        sell_quote = self.runner.call_view_method(
            self.manager_id, "quote_sell", self.token1_uid, token_amount
        )

        self.assertIn("amount_out", sell_quote)
        self.assertIn("price_impact", sell_quote)

    def test_get_token_info(self) -> None:
        """Test getting token information"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        # Verify values
        self.assertEqual(token_info.creator, self.admin_address.hex())
        self.assertEqual(token_info.initial_virtual_pool, DEFAULT_INITIAL_VIRTUAL_POOL)
        self.assertEqual(token_info.market_cap, 0)
        self.assertEqual(token_info.token_reserve, INITIAL_TOKEN_RESERVE)
        self.assertEqual(token_info.total_supply, INITIAL_TOKEN_RESERVE)
        self.assertEqual(token_info.is_migrated, False)

        # Test non-existent token
        random_token_uid = self.gen_random_token_uid()
        with self.assertNCFail("TokenNotFound"):
            self.runner.call_view_method(
                self.manager_id, "get_token_info", random_token_uid
            )

    def test_user_balances(self) -> None:
        """Test user balance tracking from slippage"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        # Initially zero balance
        token_balance = self.runner.call_view_method(
            self.manager_id, "get_user_balance", self.user_address, self.token1_uid
        )
        htr_balance = self.runner.call_view_method(
            self.manager_id, "get_user_balance", self.user_address, HTR_UID
        )

        self.assertEqual(token_balance, 0)
        self.assertEqual(htr_balance, 0)

        # Buy tokens with slippage
        amount_in = 100000
        quote_buy = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, amount_in
        )

        # Request less than available (to create slippage)
        expected_out = int(quote_buy["amount_out"] * 0.9)  # 90% of available

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out)
        ctx = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx, self.token1_uid
        )

        # Check updated balance (should have slippage amount)
        first_balance = self.runner.call_view_method(
            self.manager_id, "get_user_balance", self.user_address, self.token1_uid
        )
        self.assertEqual(first_balance, quote_buy["amount_out"] - expected_out)

        # Repeat the process for sell_tokens

        sell_amount = int(expected_out * 0.5)

        quote_sell = self.runner.call_view_method(
            self.manager_id, "quote_sell", self.token1_uid, sell_amount
        )

        decreased_output = int(quote_sell["amount_out"] * 0.8)  # 80% of available

        deposit = NCDepositAction(token_uid=self.token1_uid, amount=sell_amount)
        withdrawal = NCWithdrawalAction(token_uid=HTR_UID, amount=decreased_output)
        ctx = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "sell_tokens", ctx, self.token1_uid
        )

        # Check updated balance (should have slippage amount)
        second_balance = self.runner.call_view_method(
            self.manager_id, "get_user_balance", self.user_address, HTR_UID
        )
        self.assertEqual(second_balance, quote_sell["amount_out"] - decreased_output)

    def test_lru_cache_initialization(self) -> None:
        """Test LRU cache is properly initialized"""
        self._initialize_manager()

        storage = self.manager_storage

        # Check LRU cache capacity
        self.assertEqual(
            storage.get_obj(b"lru_cache_capacity", INT_NC_TYPE), LRU_CACHE_CAPACITY
        )

        # Check initial LRU cache size is 0
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 0)

    def test_lru_cache_updates_on_operations(self) -> None:
        """Test LRU cache updates when tokens are registered and traded"""
        self._initialize_manager()

        storage = self.manager_storage

        # Register first token
        self.token1_uid = self._register_token("token1", "TK1")

        # LRU cache size should be 1
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 1)

        # get_last_n_tokens should return token1
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 1, HTR_UID
        )
        self.assertEqual(last_tokens, self.token1_uid.hex())

        # Register second token
        self.token2_uid = self._register_token("token2", "TK2")

        # LRU cache size should be 2
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 2)

        # get_last_n_tokens should return token2 first (most recent)
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(tokens_list[0], self.token2_uid.hex())
        self.assertEqual(tokens_list[1], self.token1_uid.hex())

        # Buy token1 - should move it to front of LRU
        amount_in = 100000
        quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, amount_in
        )

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(
            token_uid=self.token1_uid, amount=quote["amount_out"]
        )
        ctx = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx, self.token1_uid
        )

        # Now token1 should be first (most recent)
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(tokens_list[0], self.token1_uid.hex())
        self.assertEqual(tokens_list[1], self.token2_uid.hex())

    def test_lru_cache_capacity_and_eviction(self) -> None:
        """Test LRU cache eviction when capacity is reached"""
        self._initialize_manager()

        # Set a small capacity for testing
        ctx = self.create_context(caller_id=self.admin_address)

        # Change capacity to 3
        self.runner.call_public_method(self.manager_id, "change_lru_capacity", ctx, 3)

        storage = self.manager_storage
        self.assertEqual(storage.get_obj(b"lru_cache_capacity", INT_NC_TYPE), 3)

        # Register 3 tokens
        token1 = self._register_token("token1", "TK1")
        token2 = self._register_token("token2", "TK2")
        token3 = self._register_token("token3", "TK3")

        # Cache size should be 3
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 3)

        # get_last_n_tokens should return all 3 in reverse order
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 3, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 3)
        self.assertEqual(tokens_list[0], token3.hex())
        self.assertEqual(tokens_list[1], token2.hex())
        self.assertEqual(tokens_list[2], token1.hex())

        # Register 4th token - should evict token1 (oldest)
        token4 = self._register_token("token4", "TK4")

        # Cache size should still be 3
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 3)

        # get_last_n_tokens should NOT contain token1 anymore
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 4, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 3)
        self.assertNotIn(token1.hex(), tokens_list)
        self.assertEqual(tokens_list[0], token4.hex())
        self.assertEqual(tokens_list[1], token3.hex())
        self.assertEqual(tokens_list[2], token2.hex())

    def test_change_lru_capacity(self) -> None:
        """Test changing LRU cache capacity"""
        self._initialize_manager()

        storage = self.manager_storage

        # Register 5 tokens
        tokens = []
        for i in range(5):
            token = self._register_token(f"token{i}", f"TK{i}")
            tokens.append(token)

        # Cache size should be 5
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 5)

        # Reduce capacity to 3 (should evict 2 oldest)
        ctx = self.create_context(caller_id=self.admin_address)

        self.runner.call_public_method(self.manager_id, "change_lru_capacity", ctx, 3)

        # Cache size should now be 3
        self.assertEqual(storage.get_obj(b"lru_cache_size", INT_NC_TYPE), 3)
        self.assertEqual(storage.get_obj(b"lru_cache_capacity", INT_NC_TYPE), 3)

        # get_last_n_tokens should return only 3 most recent
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 5, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 3)
        # Should contain token4, token3, token2 (most recent 3)
        self.assertEqual(tokens_list[0], tokens[4].hex())
        self.assertEqual(tokens_list[1], tokens[3].hex())
        self.assertEqual(tokens_list[2], tokens[2].hex())

        # Non-admin should not be able to change capacity
        ctx_non_admin = self.create_context(caller_id=self.user_address)

        with self.assertNCFail("Unauthorized"):
            self.runner.call_public_method(
                self.manager_id, "change_lru_capacity", ctx_non_admin, 10
            )

        # Invalid capacity should fail
        with self.assertNCFail("InvalidParameters"):
            self.runner.call_public_method(
                self.manager_id, "change_lru_capacity", ctx, 0
            )

        with self.assertNCFail("InvalidParameters"):
            self.runner.call_public_method(
                self.manager_id, "change_lru_capacity", ctx, -1
            )

    def test_lru_get_last_n_tokens(self) -> None:
        """Test get_last_n_tokens returns tokens in LRU order and supports pagination"""
        self._initialize_manager()

        # Register 5 tokens
        tokens = []
        for i in range(5):
            token = self._register_token(f"token{i}", f"TK{i}")
            tokens.append(token)

        # Initial order: token4, token3, token2, token1, token0 (reverse registration order)
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 5, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 5)
        self.assertEqual(tokens_list[0], tokens[4].hex())
        self.assertEqual(tokens_list[1], tokens[3].hex())
        self.assertEqual(tokens_list[2], tokens[2].hex())
        self.assertEqual(tokens_list[3], tokens[1].hex())
        self.assertEqual(tokens_list[4], tokens[0].hex())

        # Buy token1 - moves it to front
        amount_in = 100000
        quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", tokens[1], amount_in
        )

        deposit = NCDepositAction(token_uid=HTR_UID, amount=amount_in)
        withdrawal = NCWithdrawalAction(token_uid=tokens[1], amount=quote["amount_out"])
        ctx_buy = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx_buy, tokens[1]
        )

        # New order: token1, token4, token3, token2, token0
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 5, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(tokens_list[0], tokens[1].hex())
        self.assertEqual(tokens_list[1], tokens[4].hex())
        self.assertEqual(tokens_list[2], tokens[3].hex())
        self.assertEqual(tokens_list[3], tokens[2].hex())
        self.assertEqual(tokens_list[4], tokens[0].hex())

        # Sell token2 - moves it to front
        sell_quote = self.runner.call_view_method(
            self.manager_id, "quote_sell", tokens[2], quote["amount_out"] // 10
        )

        deposit = NCDepositAction(token_uid=tokens[2], amount=quote["amount_out"] // 10)
        withdrawal = NCWithdrawalAction(
            token_uid=HTR_UID, amount=sell_quote["amount_out"]
        )
        ctx_sell = self.create_context(
            caller_id=self.user_address,
            actions=[deposit, withdrawal],
        )

        self.runner.call_public_method(
            self.manager_id, "sell_tokens", ctx_sell, tokens[2]
        )

        # New order: token2, token1, token4, token3, token0
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 5, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(tokens_list[0], tokens[2].hex())
        self.assertEqual(tokens_list[1], tokens[1].hex())
        self.assertEqual(tokens_list[2], tokens[4].hex())
        self.assertEqual(tokens_list[3], tokens[3].hex())
        self.assertEqual(tokens_list[4], tokens[0].hex())

        # Test pagination with offset - get first 2 tokens
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, HTR_UID
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[2].hex())  # token2 (most recent)
        self.assertEqual(tokens_list[1], tokens[1].hex())  # token1

        # Get next 2 tokens using offset (skip token2)
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, tokens[2]
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[1].hex())  # token1
        self.assertEqual(tokens_list[1], tokens[4].hex())  # token4

        # Get next 2 tokens using offset (skip token2 and token1)
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 2, tokens[1]
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[4].hex())  # token4
        self.assertEqual(tokens_list[1], tokens[3].hex())  # token3

        # Get remaining token using offset
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 5, tokens[4]
        )
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[3].hex())  # token3
        self.assertEqual(tokens_list[1], tokens[0].hex())  # token0

        # Test getting fewer tokens than available
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 1, HTR_UID
        )
        self.assertEqual(last_tokens, tokens[2].hex())

        # Test getting 0 tokens
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 0, HTR_UID
        )
        self.assertEqual(last_tokens, "")

        # Test negative number (should return empty)
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", -1, HTR_UID
        )
        self.assertEqual(last_tokens, "")

        # Test with invalid offset (token not in LRU)
        random_token_uid = self.gen_random_token_uid()
        last_tokens = self.runner.call_view_method(
            self.manager_id, "get_last_n_tokens", 3, random_token_uid
        )
        # Should return all tokens from head since offset is invalid
        tokens_list = last_tokens.split()
        self.assertEqual(len(tokens_list), 3)
        self.assertEqual(tokens_list[0], tokens[2].hex())

    def test_get_newest_n_tokens(self) -> None:
        """Test get_newest_n_tokens returns newly created tokens in reverse chronological order"""
        self._initialize_manager()

        # Register 5 tokens
        tokens = []
        for i in range(5):
            token = self._register_token(f"token{i}", f"TK{i}")
            tokens.append(token)

        # Get all tokens (newest first)
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 5, 0
        )
        tokens_list = newest_tokens.split()
        self.assertEqual(len(tokens_list), 5)
        # Should be in reverse order (newest first)
        self.assertEqual(tokens_list[0], tokens[4].hex())
        self.assertEqual(tokens_list[1], tokens[3].hex())
        self.assertEqual(tokens_list[2], tokens[2].hex())
        self.assertEqual(tokens_list[3], tokens[1].hex())
        self.assertEqual(tokens_list[4], tokens[0].hex())

        # Get first 2 newest tokens
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 2, 0
        )
        tokens_list = newest_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[4].hex())
        self.assertEqual(tokens_list[1], tokens[3].hex())

        # Get next 2 tokens with offset=2
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 2, 2
        )
        tokens_list = newest_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[2].hex())
        self.assertEqual(tokens_list[1], tokens[1].hex())

        # Get remaining token with offset=4
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 2, 4
        )
        tokens_list = newest_tokens.split()
        self.assertEqual(len(tokens_list), 1)
        self.assertEqual(tokens_list[0], tokens[0].hex())

        # Test getting 0 tokens
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 0, 0
        )
        self.assertEqual(newest_tokens, "")

        # Test negative number (should return empty)
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", -5, 0
        )
        self.assertEqual(newest_tokens, "")

        # Test negative offset (should ignore offset)
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 2, -1
        )
        correct_output = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 2, -1
        )
        self.assertEqual(newest_tokens, correct_output)

        # Test offset beyond list size
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 5, 10
        )
        self.assertEqual(newest_tokens, "")

        # Test large number with offset
        newest_tokens = self.runner.call_view_method(
            self.manager_id, "get_newest_n_tokens", 100, 1
        )
        tokens_list = newest_tokens.split()
        # Should only return remaining 4 tokens (offset=1 skips the newest)
        self.assertEqual(len(tokens_list), 4)

    def test_get_oldest_n_tokens(self) -> None:
        """Test get_oldest_n_tokens returns oldest created tokens in chronological order"""
        self._initialize_manager()

        # Register 5 tokens
        tokens = []
        for i in range(5):
            token = self._register_token(f"token{i}", f"TK{i}")
            tokens.append(token)

        # Get all tokens (oldest first)
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 5, 0
        )
        tokens_list = oldest_tokens.split()
        self.assertEqual(len(tokens_list), 5)
        # Should be in chronological order (oldest first)
        self.assertEqual(tokens_list[0], tokens[0].hex())
        self.assertEqual(tokens_list[1], tokens[1].hex())
        self.assertEqual(tokens_list[2], tokens[2].hex())
        self.assertEqual(tokens_list[3], tokens[3].hex())
        self.assertEqual(tokens_list[4], tokens[4].hex())

        # Get first 2 oldest tokens
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 2, 0
        )
        tokens_list = oldest_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[0].hex())
        self.assertEqual(tokens_list[1], tokens[1].hex())

        # Get next 2 tokens with offset=2
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 2, 2
        )
        tokens_list = oldest_tokens.split()
        self.assertEqual(len(tokens_list), 2)
        self.assertEqual(tokens_list[0], tokens[2].hex())
        self.assertEqual(tokens_list[1], tokens[3].hex())

        # Get remaining token with offset=4
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 2, 4
        )
        tokens_list = oldest_tokens.split()
        self.assertEqual(len(tokens_list), 1)
        self.assertEqual(tokens_list[0], tokens[4].hex())

        # Test getting 0 tokens
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 0, 0
        )
        self.assertEqual(oldest_tokens, "")

        # Test negative number (should return empty)
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", -5, 0
        )
        self.assertEqual(oldest_tokens, "")

        # Test negative offset (should ignore the offset)
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 2, -1
        )
        corrent_output = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 2, 0
        )
        self.assertEqual(oldest_tokens, corrent_output)

        # Test offset beyond list size
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 5, 10
        )
        self.assertEqual(oldest_tokens, "")

        # Test large number with offset
        oldest_tokens = self.runner.call_view_method(
            self.manager_id, "get_oldest_n_tokens", 100, 1
        )
        tokens_list = oldest_tokens.split()
        # Should only return remaining 4 tokens (offset=1 skips the oldest)
        self.assertEqual(len(tokens_list), 4)

    def test_post_migration_quotes(self) -> None:
        """Test front_quote methods work correctly after token migration"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        # Before migration, these methods should fail
        with self.assertNCFail("InvalidState"):
            self.runner.call_view_method(
                self.manager_id,
                "front_quote_exact_tokens_for_tokens",
                self.token1_uid,
                100000,
                HTR_UID,
            )

        with self.assertNCFail("InvalidState"):
            self.runner.call_view_method(
                self.manager_id,
                "front_quote_tokens_for_exact_tokens",
                self.token1_uid,
                100000,
                HTR_UID,
            )

        # Reach migration threshold
        self._reach_migration_threshold(self.token1_uid)

        # Verify token is migrated and get pool reserves
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )
        self.assertTrue(token_info.is_migrated)

        # After migration, the Dozer pool reserves are ordered by token UID
        # Get the actual reserves from Dozer to know the correct ordering
        reserves = self.runner.call_view_method(
            self.dozer_pool_manager_id,
            "get_reserves",
            self.token1_uid,
            HTR_UID,
            10,  # FEE
        )
        reserve_a, reserve_b = reserves

        # Determine which is token and which is HTR based on ordering
        # Tokens are ordered: if token1_uid < HTR_UID, then reserve_a=token, reserve_b=HTR
        if self.token1_uid < HTR_UID:
            reserve_token = reserve_a
            reserve_htr = reserve_b
        else:
            reserve_token = reserve_b
            reserve_htr = reserve_a

        # Dozer fee: 10/1000 = 1%
        fee_numerator = 10
        fee_denominator = 1000
        fee_multiplier = fee_denominator - fee_numerator  # 990

        # Test 1: front_quote_exact_tokens_for_tokens for buying (HTR -> Token)
        # Given exact HTR in, calculate token out
        htr_amount = 50000
        # Formula: amount_out = (reserve_out * amount_in * (1000-10)) // (reserve_in * 1000 + amount_in * (1000-10))
        expected_token_out = (reserve_token * htr_amount * fee_multiplier) // (reserve_htr * fee_denominator + htr_amount * fee_multiplier)

        quote_buy = self.runner.call_view_method(
            self.manager_id,
            "front_quote_exact_tokens_for_tokens",
            self.token1_uid,
            htr_amount,
            HTR_UID,
        )

        self.assertIn("amount_out", quote_buy)
        self.assertEqual(quote_buy["amount_out"], expected_token_out)

        # Test 2: front_quote_exact_tokens_for_tokens for selling (Token -> HTR)
        # Given exact token in, calculate HTR out
        token_amount = 10000
        expected_htr_out = (reserve_htr * token_amount * fee_multiplier) // (reserve_token * fee_denominator + token_amount * fee_multiplier)

        quote_sell = self.runner.call_view_method(
            self.manager_id,
            "front_quote_exact_tokens_for_tokens",
            self.token1_uid,
            token_amount,
            self.token1_uid,
        )

        self.assertIn("amount_out", quote_sell)
        self.assertEqual(quote_sell["amount_out"], expected_htr_out)

        # Test 3: front_quote_tokens_for_exact_tokens for buying (HTR -> Token)
        # Given exact token out desired, calculate HTR in needed
        desired_tokens = 5000
        # Formula: amount_in = ceil_div(reserve_in * amount_out * fee_denominator, (reserve_out - amount_out) * fee_multiplier)
        numerator = reserve_htr * desired_tokens * fee_denominator
        denominator = (reserve_token - desired_tokens) * fee_multiplier
        # Ceiling division
        expected_htr_in = (numerator + denominator - 1) // denominator

        quote_buy_exact = self.runner.call_view_method(
            self.manager_id,
            "front_quote_tokens_for_exact_tokens",
            self.token1_uid,
            desired_tokens,
            HTR_UID,
        )

        self.assertIn("amount_in", quote_buy_exact)
        self.assertEqual(quote_buy_exact["amount_in"], expected_htr_in)

        # Test 4: front_quote_tokens_for_exact_tokens for selling (Token -> HTR)
        # Given exact HTR out desired, calculate token in needed
        desired_htr = 5000
        numerator = reserve_token * desired_htr * fee_denominator
        denominator = (reserve_htr - desired_htr) * fee_multiplier
        # Ceiling division
        expected_token_in = (numerator + denominator - 1) // denominator

        quote_sell_exact = self.runner.call_view_method(
            self.manager_id,
            "front_quote_tokens_for_exact_tokens",
            self.token1_uid,
            desired_htr,
            self.token1_uid,
        )

        self.assertIn("amount_in", quote_sell_exact)
        self.assertEqual(quote_sell_exact["amount_in"], expected_token_in)

        # Test with invalid token_in parameter
        random_token = self.gen_random_token_uid()
        with self.assertNCFail("InvalidParameters"):
            self.runner.call_view_method(
                self.manager_id,
                "front_quote_exact_tokens_for_tokens",
                self.token1_uid,
                100000,
                random_token,
            )

        with self.assertNCFail("InvalidParameters"):
            self.runner.call_view_method(
                self.manager_id,
                "front_quote_tokens_for_exact_tokens",
                self.token1_uid,
                100000,
                random_token,
            )

    def test_post_migration_quote_non_existent_token(self) -> None:
        """Test post-migration quote methods fail for non-existent tokens"""
        self._initialize_manager()

        random_token_uid = self.gen_random_token_uid()

        with self.assertNCFail("TokenNotFound"):
            self.runner.call_view_method(
                self.manager_id,
                "front_quote_exact_tokens_for_tokens",
                random_token_uid,
                100000,
                HTR_UID,
            )

        with self.assertNCFail("TokenNotFound"):
            self.runner.call_view_method(
                self.manager_id,
                "front_quote_tokens_for_exact_tokens",
                random_token_uid,
                100000,
                HTR_UID,
            )
