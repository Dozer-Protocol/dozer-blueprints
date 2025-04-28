import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.khensu import (
    Khensu,
    MIN_PURCHASE,
    MAX_PURCHASE,
    INITIAL_VIRTUAL_POOL,
    INITIAL_TOKEN_RESERVE,
)
from hathor.nanocontracts.blueprints.dozer_pool_v1_1 import Dozer_Pool_v1_1
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.conf.get_settings import HathorSettings
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class KhensuTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        # Set up Khensu contract
        self.khensu_id = self.gen_random_nanocontract_id()
        self.runner.register_contract(Khensu, self.khensu_id)
        self.khensu_storage = self.runner.get_storage(self.khensu_id)

        # Set up DozerPool contract for migration testing
        self.dozer_id = self.gen_random_nanocontract_id()
        self.runner.register_contract(Dozer_Pool_v1_1, self.dozer_id)
        self.dozer_storage = self.runner.get_storage(self.dozer_id)

        # Setup initial values
        self.admin_address = self._get_any_address()[0]
        self.token_uid = self.gen_random_token_uid()
        self.buy_fee_rate = 200  # 2%
        self.sell_fee_rate = 500  # 5%
        self.target_market_cap = 100_000_00  # 100K HTR
        self.liquidity_amount = 50_000_00  # 50K HTR
        self.graduation_fee = 10_000_00  # 10K HTR

        # Initialize base tx for contexts
        self.tx = self.get_genesis_tx()

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        """Generate a random address and keypair for testing"""
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _initialize_khensu(self) -> Context:
        """Initialize Khensu contract with test parameters"""
        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, INITIAL_VIRTUAL_POOL),
                NCAction(NCActionType.DEPOSIT, self.token_uid, INITIAL_TOKEN_RESERVE),
            ],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(
            self.khensu_id,
            "initialize",
            ctx,
            self.admin_address,
            self.token_uid,
            self.dozer_id,
            self.buy_fee_rate,
            self.sell_fee_rate,
            self.target_market_cap,
            self.liquidity_amount,
            self.graduation_fee,
        )

        return ctx

    def _initialize_dozer_pool(self) -> None:
        """Initialize Dozer Pool for migration testing"""
        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, 1000_000_00),
                NCAction(NCActionType.DEPOSIT, self.token_uid, 1000_000_00),
            ],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(
            self.dozer_id,
            "initialize",
            ctx,
            HTR_UID,
            self.token_uid,
            0,  # fee
            50,  # protocol fee
        )

    def test_initialize(self) -> None:
        """Test basic initialization"""
        ctx = self._initialize_khensu()

        storage = self.khensu_storage
        self.assertEqual(storage.get("admin_address"), self.admin_address)
        self.assertEqual(storage.get("token_uid"), self.token_uid)
        self.assertEqual(storage.get("lp_contract"), self.dozer_id)
        self.assertEqual(storage.get("buy_fee_rate"), self.buy_fee_rate)
        self.assertEqual(storage.get("sell_fee_rate"), self.sell_fee_rate)
        self.assertEqual(storage.get("is_paused"), False)
        self.assertEqual(storage.get("is_migrated"), False)
        self.assertEqual(storage.get("virtual_pool"), INITIAL_VIRTUAL_POOL)
        self.assertEqual(storage.get("token_reserve"), INITIAL_TOKEN_RESERVE)

    def test_buy_tokens(self) -> None:
        """Test buying tokens with HTR"""
        self._initialize_khensu()
        user_address = self._get_any_address()[0]
        amount_in = 1000_00  # 1000 HTR

        # Calculate expected tokens out
        quote = self.runner.call_view_method(self.khensu_id, "quote_buy", amount_in)
        expected_out = quote["amount_out"]

        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(self.khensu_id, "buy_tokens", ctx)

        # Verify state changes
        storage = self.khensu_storage
        self.assertEqual(storage.get("transaction_count"), 1)
        self.assertEqual(storage.get("total_volume"), amount_in)
        self.assertGreater(storage.get("collected_buy_fees"), 0)

    def test_sell_tokens(self) -> None:
        """Test selling tokens for HTR"""
        ctx = self._initialize_khensu()
        user_address = self._get_any_address()[0]
        
        # First buy some tokens to ensure the contract has HTR
        buy_amount = 10000_00  # 10000 HTR
        buy_quote = self.runner.call_view_method(self.khensu_id, "quote_buy", buy_amount)
        buy_tokens = buy_quote["amount_out"]
        
        buy_ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, buy_amount),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, buy_tokens),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )
        
        self.runner.call_public_method(self.khensu_id, "buy_tokens", buy_ctx)
        
        # Now sell a portion of the tokens
        # Sell a smaller portion to ensure reasonable amounts
        amount_in = buy_tokens // 10  # Sell 10% of received tokens

        # Calculate expected HTR out
        quote = self.runner.call_view_method(self.khensu_id, "quote_sell", amount_in)
        expected_out = max(1, int(quote["amount_out"]))  # Ensure positive integer amount
        
        # Verify we have positive amounts
        if amount_in <= 0 or expected_out <= 0:
            self.fail("Invalid amounts calculated")

        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_uid, amount_in),
                NCAction(NCActionType.WITHDRAWAL, HTR_UID, expected_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(self.khensu_id, "sell_tokens", ctx)

        # Verify state changes
        storage = self.khensu_storage
        self.assertEqual(storage.get("transaction_count"), 2)  # One for buy, one for sell
        self.assertGreater(storage.get("total_volume"), 0)
        self.assertGreater(storage.get("collected_sell_fees"), 0)

    def _reach_migration_threshold(self) -> None:
        """Helper to reach migration threshold through multiple purchases"""
        user_address = self._get_any_address()[0]
        remaining_amount = self.target_market_cap
        
        while remaining_amount > 0:
            amount_in = min(5000_00, remaining_amount)
            if amount_in < MIN_PURCHASE:
                break
            remaining_amount -= amount_in
            
            if self.khensu_storage.get("is_migrated"):
                break
                
            quote = self.runner.call_view_method(self.khensu_id, "quote_buy", amount_in)
            expected_out = int(quote["amount_out"])
            
            ctx = Context(
                [
                    NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                    NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
                ],
                self.tx,
                user_address,
                timestamp=self.clock.seconds(),
            )
            
            self.runner.call_public_method(self.khensu_id, "buy_tokens", ctx)

    def test_migration(self) -> None:
        """Test contract migration to Dozer Pool"""
        self._initialize_khensu()
        self._initialize_dozer_pool()
        user_address = self._get_any_address()[0]

        self._reach_migration_threshold()

        # Verify migration state
        storage = self.khensu_storage
        self.assertTrue(storage.get("is_migrated"))

    def test_post_migration_buy(self) -> None:
        """Test buying tokens after migration"""
        self._initialize_khensu()
        self._initialize_dozer_pool()
        self._reach_migration_threshold()
        
        user_address = self._get_any_address()[0]
        amount_in = 1000_00  # 1000 HTR

        # Get quote from Dozer pool directly
        quote = self.runner.call_view_method(
            self.dozer_id,
            "front_quote_exact_tokens_for_tokens",
            amount_in,
            HTR_UID
        )
        expected_out = int(quote["amount_out"] * 99 // 100)  # Account for 1% fee

        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )

        self.runner.call_public_method(self.khensu_id, "post_migration_buy", ctx)

        # Verify state changes
        storage = self.khensu_storage
        self.assertGreater(storage.get("total_volume"), 0)
        self.assertGreater(storage.get("collected_buy_fees"), 0)

    def test_post_migration_sell(self) -> None:
        """Test selling tokens after migration"""
        self._initialize_khensu()
        self._initialize_dozer_pool()
        self._reach_migration_threshold()
        
        # First buy some tokens post-migration
        user_address = self._get_any_address()[0]
        buy_amount = 500_00  # Use a smaller amount
        
        buy_quote = self.runner.call_view_method(
            self.dozer_id,
            "front_quote_exact_tokens_for_tokens",
            buy_amount,
            HTR_UID
        )
        tokens_out = int(buy_quote["amount_out"] * 99 // 100)  # Account for 1% fee
        
        buy_ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, buy_amount),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, tokens_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )
        
        self.runner.call_public_method(self.khensu_id, "post_migration_buy", buy_ctx)
        
        # Now sell the tokens
        sell_amount = tokens_out // 2  # Sell half the tokens
        
        sell_quote = self.runner.call_view_method(
            self.dozer_id,
            "front_quote_exact_tokens_for_tokens",
            sell_amount,
            self.token_uid
        )
        htr_out = int(sell_quote["amount_out"])
        
        sell_ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_uid, sell_amount),
                NCAction(NCActionType.WITHDRAWAL, HTR_UID, htr_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )
        
        self.runner.call_public_method(self.khensu_id, "post_migration_sell", sell_ctx)
        
        # Verify state changes
        storage = self.khensu_storage
        self.assertGreater(storage.get("total_volume"), buy_amount)
        self.assertGreater(storage.get("collected_sell_fees"), 0)

    def test_withdraw_fees(self) -> None:
        """Test fee withdrawal functionality"""
        self._initialize_khensu()
        user_address = self._get_any_address()[0]
        
        # First make some trades to generate fees
        amount_in = 1000_00
        quote = self.runner.call_view_method(self.khensu_id, "quote_buy", amount_in)
        expected_out = quote["amount_out"]
        
        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )
        
        self.runner.call_public_method(self.khensu_id, "buy_tokens", ctx)
        
        # Verify fees were collected
        storage = self.khensu_storage
        initial_fees = storage.get("collected_buy_fees")
        self.assertGreater(initial_fees, 0)
        
        # Non-admin should not be able to withdraw fees
        withdraw_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, HTR_UID, initial_fees)],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )
        
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.khensu_id, "withdraw_fees", withdraw_ctx)
            
        # Admin should be able to withdraw fees
        admin_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, HTR_UID, initial_fees)],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )
        
        self.runner.call_public_method(self.khensu_id, "withdraw_fees", admin_ctx)
        
        # Verify fees were reset
        self.assertEqual(storage.get("collected_buy_fees"), 0)
        self.assertEqual(storage.get("collected_sell_fees"), 0)

    def test_withdraw_graduation_fee(self) -> None:
        """Test graduation fee withdrawal functionality"""
        self._initialize_khensu()
        self._initialize_dozer_pool()
        user_address = self._get_any_address()[0]
        
        # Reach migration threshold
        self._reach_migration_threshold()
        
        # Non-admin should not be able to withdraw graduation fee
        withdraw_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, HTR_UID, self.graduation_fee)],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )
        
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.khensu_id, "withdraw_graduation_fee", withdraw_ctx)
            
        # Admin should be able to withdraw graduation fee
        admin_ctx = Context(
            [NCAction(NCActionType.WITHDRAWAL, HTR_UID, self.graduation_fee)],
            self.tx,
            self.admin_address,
            timestamp=self.clock.seconds(),
        )
        
        self.runner.call_public_method(self.khensu_id, "withdraw_graduation_fee", admin_ctx)
        
        # Should not be able to withdraw again
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.khensu_id, "withdraw_graduation_fee", admin_ctx)

    def test_admin_functions(self) -> None:
        """Test administrative functions"""
        self._initialize_khensu()

        # Test pause/unpause
        ctx = Context([], self.tx, self.admin_address, timestamp=self.clock.seconds())
        self.runner.call_public_method(self.khensu_id, "pause", ctx)
        self.assertTrue(self.khensu_storage.get("is_paused"))

        self.runner.call_public_method(self.khensu_id, "unpause", ctx)
        self.assertFalse(self.khensu_storage.get("is_paused"))

        # Test admin transfer
        new_admin = self._get_any_address()[0]
        self.runner.call_public_method(self.khensu_id, "transfer_admin", ctx, new_admin)
        self.assertEqual(self.khensu_storage.get("admin_address"), new_admin)

    def test_purchase_limits(self) -> None:
        """Test min/max purchase limits"""
        self._initialize_khensu()
        user_address = self._get_any_address()[0]

        # Test minimum purchase
        amount_in = MIN_PURCHASE - 1
        quote = self.runner.call_view_method(self.khensu_id, "quote_buy", amount_in)
        expected_out = quote["amount_out"]

        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.khensu_id, "buy_tokens", ctx)

        # Test maximum purchase
        amount_in = MAX_PURCHASE + 1
        quote = self.runner.call_view_method(self.khensu_id, "quote_buy", amount_in)
        expected_out = quote["amount_out"]

        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
            ],
            self.tx,
            user_address,
            timestamp=self.clock.seconds(),
        )

        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.khensu_id, "buy_tokens", ctx)
