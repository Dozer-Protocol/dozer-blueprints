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
from hathor.nanocontracts.blueprints import khensu_manager, dozer_pool_manager

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID

DEFAULT_MARKET_CAP = Amount(1725000)
DEFAULT_LIQUIDITY_AMOUNT = Amount(300000)
DEFAULT_INITIAL_VIRTUAL_POOL = Amount(15000)
DEFAULT_CURVE_CONSTANT = Amount(32190005730)
INITIAL_TOKEN_RESERVE = Amount(1073000191)
BUY_FEE_RATE = Amount(200)  # 2%
SELL_FEE_RATE = Amount(300)  # 2%
GRADUATION_FEE = Amount(1000)  # 1,000 HTR

ADDRESS_NC_TYPE = make_nc_type(Address)
AMOUNT_NC_TYPE = make_nc_type(Amount)
BOOL_NC_TYPE = make_nc_type(bool)
CONTRACT_NC_TYPE = make_nc_type(ContractId)
INT_NC_TYPE = VarInt32NCType()


class KhensuManagerTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        # Set up KhensuManager contract
        # self.blueprint_id_khensu = self.gen_random_blueprint_id()
        self.blueprint_id_khensu = self.register_blueprint_file(inspect.getfile(khensu_manager))
        self.manager_id = self.gen_random_contract_id()

        # self.register_blueprint_class(self.blueprint_id_khensu, KhensuManager)

        # Set up Dozer Pool Manager contract
        # self.blueprint_id_dozer = self.gen_random_blueprint_id()
        self.blueprint_id_dozer = self.register_blueprint_file(inspect.getfile(dozer_pool_manager))
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
        context = Context(
            [],
            self.tx,
            Address(self._get_any_address()[0]),
            timestamp=self.get_current_timestamp(),
        )
        self.runner.create_contract(
            self.dozer_pool_manager_id,
            self.blueprint_id_dozer,
            context,
        )

    def _initialize_manager(self) -> Context:
        """Initialize KhensuManager contract"""
        ctx = Context(
            [],  # No actions needed for initialization
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

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
        )

        self.manager_storage = self.runner.get_storage(self.manager_id)
        return ctx

    def _register_token(
        self, token_name: str, token_symbol: str, creator_address=None
    ) -> TokenUid:
        """Register a new token with the manager"""
        if creator_address is None:
            creator_address = self.admin_address

        ctx = Context(
            [
                NCDepositAction(
                    token_uid=HTR_UID,
                    amount=int(INITIAL_TOKEN_RESERVE * float(0.02)),
                ),
            ],
            self.tx,
            creator_address,
            timestamp=self.get_current_timestamp(),
        )
        token_uid = self.runner.call_public_method(
            self.manager_id, "register_token", ctx, token_name, token_symbol
        )

        return token_uid

    def test_initialize(self) -> None:
        """Test basic initialization"""
        self._initialize_manager()

        storage = self.manager_storage
        self.assertEqual(
            storage.get_obj(b"admin_address", ADDRESS_NC_TYPE), self.admin_address
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
            self.manager_id, "get_last_n_tokens", 1
        )
        self.assertSubstring(str(self.token1_uid.hex()), all_tokens)

        # Check token data using get_token_info
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", self.token1_uid
        )

        self.assertEqual(token_info.creator, self.admin_address.hex())
        self.assertEqual(token_info.virtual_pool, DEFAULT_INITIAL_VIRTUAL_POOL)
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
        ctx = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=DEFAULT_INITIAL_VIRTUAL_POOL),
                NCDepositAction(
                    token_uid=self.token1_uid, amount=INITIAL_TOKEN_RESERVE
                ),
            ],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.manager_id, "register_token", ctx, self.token1_uid
            )

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

        ctx = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=amount_in),
                NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
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
            token_info.virtual_pool,
            DEFAULT_INITIAL_VIRTUAL_POOL + amount_in - calculated_fee,
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

        buy_ctx = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=buy_amount),
                NCWithdrawalAction(token_uid=self.token1_uid, amount=buy_tokens),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
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

        ctx = Context(
            [
                NCDepositAction(token_uid=self.token1_uid, amount=amount_in),
                NCWithdrawalAction(token_uid=HTR_UID, amount=expected_out),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
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
        virtual_pool = token_info.virtual_pool
        token_reserve = token_info.token_reserve

        self.assertEqual(
            virtual_pool, initial_token_info.virtual_pool - expected_out - estimated_fee
        )
        self.assertEqual(token_reserve, initial_token_info.token_reserve + amount_in)

    def test_transaction_values(self) -> None:
        """Test values when buying and selling tokens"""
        self._initialize_manager()
        self.token1_uid = self._register_token("token1", "TK1")

        testing_values = 10000
        last_token_purchase = None

        for i in range((DEFAULT_MARKET_CAP - 1) // testing_values):
            # Each iterations should start from a different point in the curve
            if i > 0:
                start_purchase = testing_values
                start_quote = self.runner.call_view_method(
                    self.manager_id, "quote_buy", self.token1_uid, start_purchase
                )
                first_buy_ctx = Context(
                    [
                        NCDepositAction(token_uid=HTR_UID, amount=start_purchase),
                        NCWithdrawalAction(
                            token_uid=self.token1_uid, amount=start_quote["amount_out"]
                        ),
                    ],
                    self.tx,
                    self.user_address,
                    timestamp=self.get_current_timestamp(),
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

            buy_ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=payed_htr),
                    NCWithdrawalAction(
                        token_uid=self.token1_uid, amount=tokens_exchanged
                    ),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
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

            sell_ctx = Context(
                [
                    NCWithdrawalAction(
                        token_uid=HTR_UID, amount=sell_quote["amount_out"]
                    ),
                    NCDepositAction(token_uid=self.token1_uid, amount=tokens_exchanged),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
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
                final_token_info.virtual_pool
                - initial_token_info.virtual_pool
                + real_sell_fee,
                should_sell_for - sell_quote["amount_out"] + expected_sell_fee,
            )
            self.assertEqual(
                payed_htr,
                final_token_info.virtual_pool
                - initial_token_info.virtual_pool
                + sell_quote["amount_out"]
                + final_plat_info["platform_fees_collected"]
                - initial_plat_info["platform_fees_collected"],
            )

            # Given that The Curve Constant > Virtual Pool at any time:
            self.assertGreaterEqual(should_sell_for - sell_quote["amount_out"], 0)
            self.assertLess(
                should_sell_for - sell_quote["amount_out"],
                1
                + initial_token_info.virtual_pool**2
                / (DEFAULT_CURVE_CONSTANT - initial_token_info.virtual_pool),
            )

    def _reach_migration_threshold(self, token_uid):
        """Helper to reach migration threshold through multiple purchases"""

        # Get token info to access target_market_cap and virtual_pool
        token_info = self.runner.call_view_method(
            self.manager_id, "get_token_info", token_uid
        )
        target_market_cap = token_info.target_market_cap
        virtual_pool = token_info.virtual_pool

        remaining_amount = target_market_cap - virtual_pool
        while remaining_amount > 0:
            amount_in = 4000000

            quote = self.runner.call_view_method(
                self.manager_id, "quote_buy", token_uid, amount_in
            )
            should_go_in = int(quote["recommended_htr_amount"])
            expected_out = int(quote["amount_out"])

            ctx = Context(
                [
                    NCDepositAction(token_uid=HTR_UID, amount=should_go_in),
                    NCWithdrawalAction(token_uid=token_uid, amount=expected_out),
                ],
                self.tx,
                self.user_address,
                timestamp=self.get_current_timestamp(),
            )

            self.runner.call_public_method(
                self.manager_id, "buy_tokens", ctx, token_uid
            )

            # Update token data after each transaction
            token_info = self.runner.call_view_method(
                self.manager_id, "get_token_info", token_uid
            )
            virtual_pool = token_info.virtual_pool
            remaining_amount = target_market_cap - virtual_pool

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
        with self.assertRaises(InvalidState):
            self.runner.call_view_method(
                self.manager_id, "quote_buy", self.token1_uid, 100000
            )

        with self.assertRaises(InvalidState):
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

        ctx_buy = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=amount_in),
                NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx_buy, self.token1_uid
        )

        # Verify fees were collected
        storage = self.manager_storage
        collected_buy_fees = storage.get_obj(b"collected_buy_fees", AMOUNT_NC_TYPE)
        self.assertGreater(collected_buy_fees, 0)

        # Non-admin should not be able to withdraw fees
        ctx_non_admin = Context(
            [NCWithdrawalAction(token_uid=HTR_UID, amount=collected_buy_fees)],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.manager_id, "withdraw_fees", ctx_non_admin
            )

        # Admin should be able to withdraw fees
        ctx_admin = Context(
            [NCWithdrawalAction(token_uid=HTR_UID, amount=collected_buy_fees)],
            self.tx,
            self.admin_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(self.manager_id, "withdraw_fees", ctx_admin)

        # Verify fees were reset
        self.assertEqual(storage.get_obj(b"collected_buy_fees", AMOUNT_NC_TYPE), 0)

    def test_change_parameters(self) -> None:
        """Test changing contract parameters"""
        self._initialize_manager()

        ctx = Context(
            [],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

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
        ctx_non_admin = Context(
            [],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.manager_id, "change_buy_fee_rate", ctx_non_admin, 500
            )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.manager_id, "change_sell_fee_rate", ctx_non_admin, 500
            )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.manager_id, "change_graduation_fee", ctx_non_admin, 3000
            )

        with self.assertRaises(Unauthorized):
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
        with self.assertRaises(InvalidParameters):
            self.runner.call_public_method(
                self.manager_id, "change_buy_fee_rate", ctx, -1
            )

        with self.assertRaises(InvalidParameters):
            self.runner.call_public_method(
                self.manager_id, "change_sell_fee_rate", ctx, -1
            )

        with self.assertRaises(InvalidParameters):
            self.runner.call_public_method(
                self.manager_id, "change_graduation_fee", ctx, -1
            )

        with self.assertRaises(InvalidParameters):
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

            with self.subTest(f"Testing {param} = 0"), self.assertRaises(
                InvalidParameters
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

        new_admin = self._get_any_address()[0]

        ctx = Context(
            [],
            self.tx,
            self.admin_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "transfer_admin", ctx, new_admin
        )

        # Original admin should no longer be able to manage the token
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.manager_id,
                "transfer_admin",
                ctx,
                self.admin_address,
            )

        # New admin should be able to manage the token
        ctx_new_admin = Context(
            [],
            self.tx,
            new_admin,
            timestamp=self.get_current_timestamp(),
        )

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
            self.manager_id, "get_last_n_tokens", 2
        )
        self.assertSubstring(str(self.token1_uid.hex()), all_tokens)
        self.assertSubstring(str(self.token2_uid.hex()), all_tokens)

        # Verify that token with same symbol cannot be created
        with self.assertRaises(NCTokenAlreadyExists):
            self._register_token("token1_2", "TK1")

        # Buy tokens for each
        amount_in = 100000

        # Token 1
        quote1 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, amount_in
        )
        expected_out1 = quote1["amount_out"]

        ctx1 = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=amount_in),
                NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out1),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "buy_tokens", ctx1, self.token1_uid
        )

        # Token 2
        quote2 = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token2_uid, amount_in
        )
        expected_out2 = quote2["amount_out"]

        ctx2 = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=amount_in),
                NCWithdrawalAction(token_uid=self.token2_uid, amount=expected_out2),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
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
            self.manager_id, "get_last_n_tokens", 2
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
        buy_amount = 100000
        buy_quote = self.runner.call_view_method(
            self.manager_id, "quote_buy", self.token1_uid, buy_amount
        )

        self.assertIn("amount_out", buy_quote)
        self.assertIn("price_impact", buy_quote)
        self.assertIn("recommended_htr_amount", buy_quote)

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
        self.assertEqual(token_info.virtual_pool, DEFAULT_INITIAL_VIRTUAL_POOL)
        self.assertEqual(token_info.token_reserve, INITIAL_TOKEN_RESERVE)
        self.assertEqual(token_info.total_supply, INITIAL_TOKEN_RESERVE)
        self.assertEqual(token_info.is_migrated, False)

        # Test non-existent token
        random_token_uid = self.gen_random_token_uid()
        with self.assertRaises(TokenNotFound):
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

        ctx = Context(
            [
                NCDepositAction(token_uid=HTR_UID, amount=amount_in),
                NCWithdrawalAction(token_uid=self.token1_uid, amount=expected_out),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
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

        ctx = Context(
            [
                NCWithdrawalAction(token_uid=HTR_UID, amount=decreased_output),
                NCDepositAction(token_uid=self.token1_uid, amount=sell_amount),
            ],
            self.tx,
            self.user_address,
            timestamp=self.get_current_timestamp(),
        )

        self.runner.call_public_method(
            self.manager_id, "sell_tokens", ctx, self.token1_uid
        )

        # Check updated balance (should have slippage amount)
        second_balance = self.runner.call_view_method(
            self.manager_id, "get_user_balance", self.user_address, HTR_UID
        )
        self.assertEqual(second_balance, quote_sell["amount_out"] - decreased_output)
