import os
import random
from typing import Any, Optional

from hathor.conf import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.method_parser import NCMethodParser
from hathor.nanocontracts.runner import Runner
from hathor.nanocontracts.storage import NCMemoryStorageFactory
from hathor.nanocontracts.types import Context, NCAction, NCActionType
from hathor.transaction.scripts import P2PKH
from hathor.util import not_none
from hathor.wallet import KeyPair
from tests import unittest
from hathor.nanocontracts.blueprints.dozer_exp import BondingCurveToken

settings = HathorSettings()


class MyRunner(Runner):
    def call_public_method(self, method_name: str, ctx: Context, *args: Any) -> None:
        method = getattr(self.blueprint_class, method_name)
        parser = NCMethodParser(method)

        serialized_args = parser.serialize_args(list(args))
        deserialized_args = parser.parse_args_bytes(serialized_args)
        assert tuple(args) == tuple(deserialized_args)

        super().call_public_method(method_name, ctx, *args)


class NCBondingCurveTokenTestCase(unittest.TestCase):
    _enable_sync_v1 = True
    _enable_sync_v2 = True
    use_memory_storage = True

    def setUp(self):
        super().setUp()
        self.manager = self.create_peer("testnet")

        nc_storage_factory = NCMemoryStorageFactory()
        self.nc_storage = nc_storage_factory(b"", None)
        self.runner = self.get_runner(BondingCurveToken, self.nc_storage)

        self.token_uid = settings.HATHOR_TOKEN_UID
        self.token_launch = b"123123"

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

    def get_runner(self, blueprint, storage):
        runner = MyRunner(blueprint, b"", storage)
        return runner

    def initialize_contract(self):
        runner = self.runner
        storage = self.nc_storage

        tx = self._get_any_tx()
        context = Context([], tx, b"", timestamp=self.get_current_timestamp())
        runner.call_public_method("initialize", context, b"a" * 32, self.token_uid)
        self.assertEqual(storage.get("token"), b"a" * 32)
        self.assertEqual(storage.get("htr_token"), self.token_uid)

    def get_price(self, amount):
        return self.runner.call_private_method("_calculate_price", amount)

    def get_price_with_slippage(self, start_amount, end_amount, is_buy):
        return self.runner.call_private_method(
            "calculate_with_slippage", start_amount, end_amount, is_buy
        )

    # def test_token_prices(self):
    #     self.initialize_contract()

    #     test_supplies = [2_000_000, 4_000_000, 6_000_000, 8_000_000]

    #     print("\nToken prices at different supply levels:")
    #     for supply in test_supplies:
    #         price = self.get_price(supply)
    #         print(f"Supply: {supply}, Price: {price}")

    #         # Assertions to ensure the price is within a reasonable range
    #         self.assertGreater(price, 0)  # Price should be positive
    #         self.assertLess(
    #             price, 10
    #         )  # Assuming price won't exceed 10 HTR in this range

    # def test_token_prices_with_slippage(self):
    #     self.initialize_contract()
    #     test_supplies = [2_000_000, 4_000_000, 6_000_000, 8_000_000]
    #     print("\nToken prices at different supply levels:")
    #     for supply in test_supplies:
    #         price = self.get_price_with_slippage(supply, supply + 10, True)
    #         print(f"Supply: {supply}, Price: {price}")

    def test_random_buys_and_sells(self):
        self.initialize_contract()
        test_supplies = [2_000_000, 4_000_000, 6_000_000, 8_000_000]
        transaction_sizes = [10, 100, 1000, 10000, 100000, 350000]

        current_supply = 0
        total_htr = 0
        total_tokens = 0

        print("\nRandom buys and sells at different supply levels:")
        for target_supply in test_supplies:
            while current_supply < target_supply:
                is_buy = random.choice([True, False])
                size = random.choice(transaction_sizes)
                price = self.get_price(current_supply)
                if is_buy:
                    end_supply = min(current_supply + size, target_supply)
                    avg_price, total_cost = self.get_price_with_slippage(
                        current_supply, end_supply, True
                    )
                    self.assertGreater(avg_price, price)
                    tokens_bought = end_supply - current_supply
                    total_htr += total_cost
                    total_tokens += tokens_bought
                    current_supply = end_supply
                    delta = price - avg_price
                    print(
                        f"Buy  - Supply: {current_supply}, Size: {tokens_bought}, Avg Price: {avg_price} Delta: {delta}"
                    )
                    # if delta < 0:
                    #     print("strange behavior")
                else:
                    if current_supply > 0:
                        end_supply = max(current_supply - size, 0)
                        avg_price, total_value = self.get_price_with_slippage(
                            current_supply, end_supply, False
                        )
                        self.assertLess(avg_price, price)

                        tokens_sold = current_supply - end_supply
                        total_htr -= total_value
                        total_tokens -= tokens_sold
                        current_supply = end_supply
                        delta = price - avg_price
                        print(
                            f"Sell - Supply: {current_supply}, Size: {tokens_sold}, Avg Price: {avg_price} Delta: {delta}"
                        )
                        # if delta > 0:
                        #     print("strange behavior")

            # Check medium value per token
            if total_tokens > 0:
                medium_value = total_htr / total_tokens
                curve_price = self.get_price(current_supply)
                print(f"\nAt supply {current_supply}:")
                print(f"Medium value per token: {medium_value}")
                print(f"Price on the curve: {curve_price}")

                # Assertions
                self.assertGreater(medium_value, 0, "Medium value should be positive")
                self.assertLess(
                    medium_value, 10, "Medium value should not exceed 10 HTR"
                )
                self.assertGreater(
                    medium_value,
                    curve_price,
                    "Medium value should be higher than curve price due to buys",
                )

            print(f"Reached target supply: {current_supply}\n")

        # Additional assertions
        final_price = self.get_price(current_supply)
        self.assertGreater(final_price, 0, "Final price should be positive")
        self.assertLess(final_price, 10, "Final price should not exceed 10 HTR")


if __name__ == "__main__":
    unittest.main()
