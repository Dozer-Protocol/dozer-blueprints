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

    def test_migration(self) -> None:
        """Test contract migration to Dozer Pool"""
        self._initialize_khensu()
        self._initialize_dozer_pool()
        user_address = self._get_any_address()[0]

        # Buy enough tokens to reach target market cap through multiple purchases
        remaining_amount = self.target_market_cap
        while remaining_amount > 0:
            # Calculate purchase amount for this iteration - increase chunk size
            amount_in = min(5000_00, remaining_amount)  # Buy in larger chunks
            if amount_in < MIN_PURCHASE:
                break
            remaining_amount -= amount_in
            
            # Break if we've reached migration threshold
            storage = self.khensu_storage
            if storage.get("is_migrated"):
                break
            
            quote = self.runner.call_view_method(self.khensu_id, "quote_buy", amount_in)
            expected_out = int(quote["amount_out"])  # Ensure integer amount
            
            # Verify we have positive amounts
            if amount_in <= 0 or expected_out <= 0:
                self.fail(f"Invalid amounts calculated: in={amount_in}, out={expected_out}")

            ctx = Context(
                [
                    NCAction(NCActionType.DEPOSIT, HTR_UID, amount_in),
                    NCAction(NCActionType.WITHDRAWAL, self.token_uid, expected_out),
                ],
                self.tx,
                user_address,
                timestamp=self.clock.seconds(),
            )

            # Execute buy operation
            self.runner.call_public_method(self.khensu_id, "buy_tokens", ctx)

        # Verify migration state
        storage = self.khensu_storage
        self.assertTrue(storage.get("is_migrated"))

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
