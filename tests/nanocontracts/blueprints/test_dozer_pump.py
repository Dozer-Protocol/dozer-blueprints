import os
import random
from hathor.conf import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_pump import (
    Dozer_Pump,
    HATHOR_TOKEN_UID,
    LAUNCHPAD_SUPPLY,
    DEPOSITED_AMOUNT,
    TARGET_MARKET_CAP,
)
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.runner import Runner
from hathor.nanocontracts.storage import NCMemoryStorage
from hathor.nanocontracts.storage.memory_storage import NCMemoryStorageFactory
from hathor.nanocontracts.types import Context, NCAction, NCActionType
from hathor.types import Amount
from hathor.util import not_none
from hathor.wallet import KeyPair
from tests import unittest
from logging import getLogger

settings = HathorSettings()

logger = getLogger(__name__)


class DozerPumpBlueprintTestCase(unittest.TestCase):
    _enable_sync_v1 = True
    _enable_sync_v2 = True
    use_memory_storage = True

    def setUp(self):
        super().setUp()
        self.manager = self.create_peer("testnet")
        nc_storage_factory = NCMemoryStorageFactory()
        self.nc_storage = nc_storage_factory(b"", None)
        self.runner = Runner(Dozer_Pump, b"", self.nc_storage)

        self.token = b"a" * 32

    def _get_any_tx(self):
        genesis = self.manager.tx_storage.get_all_genesis()
        tx = list(genesis)[0]
        return tx

    def _get_any_address(self):
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_contract(self, fee=5):
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, self.token, DEPOSITED_AMOUNT),
        ]
        context = Context(
            actions, tx, self._get_any_address()[0], timestamp=self.get_current_timestamp()  # type: ignore
        )
        self.runner.call_public_method(
            "initialize",
            context,
            self.token,
            fee,
        )

        # storage = self.nc_storage
        # self.assertEqual(storage.get("token_uid"), self.token)
        # self.assertEqual(storage.get("fee_numerator"), fee)

    def _prepare_buy_context(self, htr_amount, token_amount):
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, HATHOR_TOKEN_UID, htr_amount),
            NCAction(NCActionType.WITHDRAWAL, self.token, token_amount),
        ]
        address_bytes, _ = self._get_any_address()
        return Context(
            actions, tx, address_bytes, timestamp=self.get_current_timestamp()  # type: ignore
        )

    def _prepare_sell_context(self, token_amount, htr_amount):
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, self.token, token_amount),
            NCAction(NCActionType.WITHDRAWAL, HATHOR_TOKEN_UID, htr_amount),
        ]
        address_bytes, _ = self._get_any_address()
        return Context(
            actions, tx, address_bytes, timestamp=self.get_current_timestamp()  # type: ignore
        )

    def _buy(self, htr_amount, token_amount):
        context = self._prepare_buy_context(htr_amount, token_amount)
        self.runner.call_public_method("buy", context)
        return context

    def _sell(self, token_amount, htr_amount):
        context = self._prepare_sell_context(token_amount, htr_amount)
        self.runner.call_public_method("sell", context)
        return context

    def test_initialize_contract(self):
        self._initialize_contract()
        storage = self.nc_storage
        self.assertEqual(storage.get("token_uid"), self.token)
        self.assertEqual(storage.get("fee_numerator"), 5)
        self.assertEqual(storage.get("curve_token_balance"), LAUNCHPAD_SUPPLY)
        self.assertEqual(storage.get("curve_htr_balance"), 0)

    def test_buy_with_token(self):
        self._initialize_contract()
        token_amount = 1000_00
        quote = self.runner.call_private_method(
            "quote_htr_for_exact_tokens", token_amount, True
        )

        self._buy(quote["htr_amount"], token_amount)

        storage = self.nc_storage
        self.assertEqual(storage.get("curve_htr_balance"), quote["htr_amount"])
        self.assertEqual(
            storage.get("curve_token_balance"), LAUNCHPAD_SUPPLY - token_amount
        )
        self.assertEqual(storage.get("accumulated_fee"), quote["fee"])

    def test_sell(self):
        self._initialize_contract()
        # First, we need to buy some tokens
        token_amount = 1000_00
        buy_quote = self.runner.call_private_method(
            "quote_htr_for_exact_tokens", token_amount, True
        )
        self._buy(buy_quote["htr_amount"], token_amount)

        # Now, let's sell half of the tokens
        sell_token_amount = token_amount // 2
        sell_quote = self.runner.call_private_method(
            "quote_htr_for_exact_tokens", sell_token_amount, False
        )
        self._sell(sell_token_amount, sell_quote["htr_amount"])

        storage = self.nc_storage
        self.assertEqual(
            storage.get("curve_htr_balance"),
            buy_quote["htr_amount"] - sell_quote["htr_amount"],
        )
        self.assertEqual(
            storage.get("curve_token_balance"),
            LAUNCHPAD_SUPPLY - token_amount + sell_token_amount,
        )
        self.assertEqual(
            storage.get("accumulated_fee"), buy_quote["fee"] + sell_quote["fee"]
        )

    def test_multiple_buys_and_sells(self):
        self._initialize_contract()
        initial_token_balance = LAUNCHPAD_SUPPLY
        initial_htr_balance = 0
        total_fee = 0

        for _ in range(5):
            # Buy
            token_amount = random.randint(100_00, 1000_00)
            buy_quote = self.runner.call_private_method(
                "quote_htr_for_exact_tokens", token_amount, True
            )
            self._buy(buy_quote["htr_amount"], token_amount)

            initial_htr_balance += buy_quote["htr_amount"]
            initial_token_balance -= token_amount
            total_fee += buy_quote["fee"]

            # Sell
            sell_token_amount = random.randint(10_00, token_amount)
            sell_quote = self.runner.call_private_method(
                "quote_htr_for_exact_tokens", sell_token_amount, False
            )
            self._sell(sell_token_amount, sell_quote["htr_amount"])

            initial_htr_balance -= sell_quote["htr_amount"]
            initial_token_balance += sell_token_amount
            total_fee += sell_quote["fee"]

        storage = self.nc_storage
        self.assertEqual(storage.get("curve_htr_balance"), initial_htr_balance)
        self.assertEqual(storage.get("curve_token_balance"), initial_token_balance)
        self.assertEqual(storage.get("accumulated_fee"), total_fee)

    def test_transition_to_liquidity_pool(self):
        self._initialize_contract()

        storage = self.nc_storage
        while storage.get("is_launchpad_mode"):
            token_amount = random.randint(100000_00, 1000000_00)
            buy_quote = self.runner.call_private_method(
                "quote_htr_for_exact_tokens", token_amount, True
            )
            self._buy(buy_quote["htr_amount"], token_amount)

            if storage.get("curve_htr_balance") >= TARGET_MARKET_CAP:
                break

        self.assertFalse(storage.get("is_launchpad_mode"))
        self.assertGreaterEqual(storage.get("curve_htr_balance"), TARGET_MARKET_CAP)

    def test_front_end_api_pool(self):
        self._initialize_contract()
        token_amount = 1000_00
        buy_quote = self.runner.call_private_method(
            "quote_htr_for_exact_tokens", token_amount, True
        )
        self._buy(buy_quote["htr_amount"], token_amount)

        pool_info = self.runner.call_private_method("front_end_api_pool")

        self.assertIn("curve_htr_balance", pool_info)
        self.assertIn("curve_token_balance", pool_info)
        self.assertIn("fees", pool_info)
        self.assertIn("transactions", pool_info)
        self.assertIn("volume", pool_info)

        self.assertEqual(pool_info["curve_htr_balance"], buy_quote["htr_amount"])
        self.assertEqual(
            pool_info["curve_token_balance"], LAUNCHPAD_SUPPLY - token_amount
        )
        self.assertEqual(pool_info["fees"], buy_quote["fee"])
        self.assertEqual(pool_info["transactions"], 1)
        self.assertEqual(pool_info["volume"], buy_quote["htr_amount"])

    def test_change_dev_address(self):
        self._initialize_contract()
        new_dev_address, _ = self._get_any_address()

        with self.assertRaises(NCFail):
            # This should fail because the context address is not the current dev address
            self.runner.call_public_method(
                "change_dev_address",
                Context(
                    [],
                    self._get_any_tx(),
                    new_dev_address,
                    timestamp=self.get_current_timestamp(),
                ),
                new_dev_address,
            )

        storage = self.nc_storage
        # Now call with the correct dev address
        dev_address = storage.get("dev_address")
        self.runner.call_public_method(
            "change_dev_address",
            Context(
                [],
                self._get_any_tx(),
                dev_address,
                timestamp=self.get_current_timestamp(),
            ),
            new_dev_address,
        )

        self.assertEqual(storage.get("dev_address"), new_dev_address)

    def test_change_fee(self):
        self._initialize_contract()
        new_fee = 10

        with self.assertRaises(NCFail):
            # This should fail because the context address is not the dev address
            self.runner.call_public_method(
                "change_fee",
                Context(
                    [],
                    self._get_any_tx(),
                    self._get_any_address()[0],
                    timestamp=self.get_current_timestamp(),
                ),
                new_fee,
            )
        storage = self.nc_storage

        # Now call with the correct dev address
        dev_address = storage.get("dev_address")
        self.runner.call_public_method(
            "change_fee",
            Context(
                [],
                self._get_any_tx(),
                dev_address,
                timestamp=self.get_current_timestamp(),
            ),
            new_fee,
        )

        self.assertEqual(storage.get("fee_numerator"), new_fee)

    # def test_buy_with_htr(self):
    #     self._initialize_contract()
    #     htr_amount = 1000_00
    #     calculation = self.runner.call_private_method(
    #         "quote_tokens_for_htr", htr_amount
    #     )
    #     fixed_htr_amount = calculation["htr_amount"]
    #     print(fixed_htr_amount)
    #     token_amount = calculation["token_amount"]
    #     self._buy(fixed_htr_amount, token_amount)

    #     storage = self.nc_storage
    #     self.assertEqual(storage.get("curve_htr_balance"), fixed_htr_amount)
    #     self.assertEqual(
    #         storage.get("curve_token_balance"), LAUNCHPAD_SUPPLY - token_amount
    #     )
    #     self.assertGreater(storage.get("accumulated_fee"), 0)
    def test_random_buys_and_sells(self):
        self._initialize_contract()
        storage = self.nc_storage

        test_supplies = [6_000_000_00, 4_000_000_00, 2_000_000_00, 1_000_000_00]
        transaction_sizes = [10_00, 100_00, 1000_00, 10000_00, 100000_00, 350000_00]

        print("\nRandom buys and sells at different supply levels:")
        for target_supply in test_supplies:
            while storage.get("curve_token_balance") > target_supply:
                is_buy = random.choice([True, False])
                size = random.choice(transaction_sizes)
                current_price = self.runner.call_private_method("_quote_price")

                if is_buy and storage.get("curve_token_balance") > size:
                    tokens_to_buy = min(
                        size, storage.get("curve_token_balance") - target_supply
                    )
                    quote = self.runner.call_private_method(
                        "quote_htr_for_exact_tokens", tokens_to_buy, True
                    )
                    htr_amount = quote["htr_amount"]

                    ctx = self._prepare_buy_context(htr_amount, tokens_to_buy)
                    self.runner.call_public_method("buy", ctx)

                    avg_price = htr_amount / tokens_to_buy
                    elta = avg_price - current_price
                    print(
                        f"Buy  - Supply: {LAUNCHPAD_SUPPLY-storage.get('curve_token_balance')}, Size: {tokens_to_buy}, Avg Price: {avg_price:.6f}, Delta: {delta:.6f}"
                    )

                elif (
                    not is_buy and storage.get("curve_token_balance") < LAUNCHPAD_SUPPLY
                ):
                    tokens_to_sell = min(
                        size, LAUNCHPAD_SUPPLY - storage.get("curve_token_balance")
                    )
                    quote = self.runner.call_private_method(
                        "quote_htr_for_exact_tokens", tokens_to_sell, False
                    )
                    htr_amount = quote["htr_amount"]

                    ctx = self._prepare_sell_context(tokens_to_sell, htr_amount)
                    self.runner.call_public_method("sell", ctx)

                    avg_price = htr_amount / tokens_to_sell
                    delta = current_price - avg_price
                    print(
                        f"Sell - Supply: {storage.get('curve_token_balance')}, Size: {tokens_to_sell}, Avg Price: {avg_price:.6f}, Delta: {delta:.6f}"
                    )

                # Verify price is within reasonable bounds
                self.assertGreater(avg_price, 0, "Price should be positive")
                self.assertLess(
                    avg_price, 1000, "Price should not be unreasonably high"
                )

            # Check medium value per token
            circulating_supply = LAUNCHPAD_SUPPLY - storage.get("curve_token_balance")
            if circulating_supply > 0:
                medium_value = storage.get("curve_htr_balance") / circulating_supply
                curve_price = self.runner.call_private_method("_quote_price")
                print(f"\nAt circulating supply {circulating_supply}:")
                print(f"Medium value per token: {medium_value:.6f}")
                print(f"Price on the curve: {curve_price:.6f}")

                # Assertions
                self.assertGreater(medium_value, 0, "Medium value should be positive")
                self.assertLess(
                    medium_value, 1000, "Medium value should not be unreasonably high"
                )
                self.assertAlmostEqual(
                    medium_value,
                    curve_price,
                    delta=1,
                    msg="Medium value should be close to curve price",
                )

            print(f"Reached target supply: {storage.get('curve_token_balance')}\n")

        # Check contract state
        pool_info = self.runner.call_private_method("front_end_api_pool")
        self.assertEqual(
            pool_info["curve_token_balance"], storage.get("curve_token_balance")
        )
        self.assertEqual(
            pool_info["curve_htr_balance"], storage.get("curve_htr_balance")
        )
        self.assertGreater(pool_info["volume"], 0)
        self.assertGreater(pool_info["fees"], 0)
        self.assertGreater(pool_info["transactions"], 0)

        # Check if transitioned to liquidity pool mode
        if storage.get("curve_htr_balance") >= TARGET_MARKET_CAP:
            self.assertFalse(storage.get("is_launchpad_mode"))
        else:
            self.assertTrue(storage.get("is_launchpad_mode"))

        print(
            f"Final state - HTR balance: {storage.get('curve_htr_balance')}, Token balance: {storage.get('curve_token_balance')}"
        )
        print(
            f"Total volume: {pool_info['volume']}, Total fees: {pool_info['fees']}, Total transactions: {pool_info['transactions']}"
        )


if __name__ == "__main__":
    unittest.main()
