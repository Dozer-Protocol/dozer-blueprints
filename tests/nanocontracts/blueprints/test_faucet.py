import os
import random
from typing import Tuple

from hathor.conf import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.faucet import Faucet
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.runner import Runner
from hathor.nanocontracts.storage.memory_storage import NCMemoryStorageFactory
from hathor.nanocontracts.types import Context, NCAction, NCActionType
from hathor.types import Address, Amount, TokenUid
from hathor.util import not_none
from hathor.wallet import KeyPair
from tests import unittest

settings = HathorSettings()


class FaucetTestCase(unittest.TestCase):
    _enable_sync_v1 = True
    _enable_sync_v2 = True
    use_memory_storage = True

    def setUp(self):
        super().setUp()
        self.manager = self.create_peer("testnet")
        nc_storage_factory = NCMemoryStorageFactory()
        self.nc_storage = nc_storage_factory(b"", None)
        self.runner = Runner(Faucet, b"", self.nc_storage)

        self.token_uid = os.urandom(32)  # Random token UID for testing

    def _get_any_tx(self):
        genesis = self.manager.tx_storage.get_all_genesis()
        tx = list(genesis)[0]
        return tx

    def _get_any_address(self) -> Tuple[Address, KeyPair]:
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_faucet(
        self, initial_supply: Amount, max_withdrawal: Amount
    ) -> Address:
        tx = self._get_any_tx()
        admin_address, _ = self._get_any_address()
        actions = [NCAction(NCActionType.DEPOSIT, self.token_uid, initial_supply)]
        context = Context(
            actions, tx, admin_address, timestamp=self.get_current_timestamp()
        )
        self.runner.call_public_method(
            "initialize", context, self.token_uid, max_withdrawal
        )
        return admin_address

    def _deposit(self, address: Address, amount: Amount):
        tx = self._get_any_tx()
        actions = [NCAction(NCActionType.DEPOSIT, self.token_uid, amount)]
        context = Context(actions, tx, address, timestamp=self.get_current_timestamp())
        self.runner.call_public_method("deposit", context)

    def _withdraw(self, user_address: Address, amount: Amount):
        tx = self._get_any_tx()
        actions = [NCAction(NCActionType.WITHDRAWAL, self.token_uid, amount)]
        context = Context(
            actions, tx, user_address, timestamp=self.get_current_timestamp()
        )
        self.runner.call_public_method("withdraw", context)

    def _admin_withdraw(self, admin_address: Address, amount: Amount):
        tx = self._get_any_tx()
        actions = [NCAction(NCActionType.WITHDRAWAL, self.token_uid, amount)]
        context = Context(
            actions, tx, admin_address, timestamp=self.get_current_timestamp()
        )
        self.runner.call_public_method("admin_withdraw", context)

    def _set_max_withdrawal(self, admin_address: Address, new_max: Amount):
        tx = self._get_any_tx()
        context = Context([], tx, admin_address, timestamp=self.get_current_timestamp())
        self.runner.call_public_method("set_max_withdrawal", context, new_max)

    def _get_user_withdrawal(self, user_address: Address) -> Amount:
        tx = self._get_any_tx()
        context = Context([], tx, user_address, timestamp=self.get_current_timestamp())
        amount = self.runner.call_private_method(
            "get_user_withdrawal", context, user_address
        )
        if amount is None:
            return 0
        return amount

    def test_initialize(self):
        initial_supply = 1000000
        max_withdrawal = 1000
        admin_address = self._initialize_faucet(initial_supply, max_withdrawal)

        self.assertEqual(self.runner.storage.get("token_uid"), self.token_uid)
        self.assertEqual(self.runner.storage.get("admin_address"), admin_address)
        self.assertEqual(self.runner.storage.get("max_withdrawal"), max_withdrawal)
        self.assertEqual(self.runner.storage.get("total_supply"), initial_supply)

    def test_deposit(self):
        initial_supply = 1000000
        admin_address = self._initialize_faucet(initial_supply, 1000)

        deposit_amount = 500000
        self._deposit(admin_address, deposit_amount)

        self.assertEqual(
            self.runner.storage.get("total_supply"), initial_supply + deposit_amount
        )

    def test_deposit_non_admin(self):
        initial_supply = 1000000
        self._initialize_faucet(initial_supply, 1000)

        non_admin_address, _ = self._get_any_address()
        deposit_amount = 500000
        self._deposit(non_admin_address, deposit_amount)

        self.assertEqual(
            self.runner.storage.get("total_supply"), initial_supply + deposit_amount
        )

    def test_withdraw(self):
        initial_supply = 1000000
        max_withdrawal = 1000
        self._initialize_faucet(initial_supply, max_withdrawal)

        user_address, _ = self._get_any_address()
        self._withdraw(user_address, max_withdrawal)

        self.assertEqual(
            self.runner.storage.get("total_supply"), initial_supply - max_withdrawal
        )
        self.assertEqual(self._get_user_withdrawal(user_address), max_withdrawal)

    def test_withdraw_exceed_max(self):
        initial_supply = 1000000
        max_withdrawal = 1000
        self._initialize_faucet(initial_supply, max_withdrawal)

        user_address, _ = self._get_any_address()
        with self.assertRaises(NCFail):
            self._withdraw(user_address, max_withdrawal + 1)

    def test_admin_withdraw(self):
        initial_supply = 1000000
        max_withdrawal = 1000
        admin_address = self._initialize_faucet(initial_supply, max_withdrawal)

        admin_withdrawal = 500000
        self._admin_withdraw(admin_address, admin_withdrawal)

        self.assertEqual(
            self.runner.storage.get("total_supply"), initial_supply - admin_withdrawal
        )

    def test_set_max_withdrawal(self):
        initial_supply = 1000000
        max_withdrawal = 1000
        admin_address = self._initialize_faucet(initial_supply, max_withdrawal)

        new_max = 2000
        self._set_max_withdrawal(admin_address, new_max)

        self.assertEqual(self.runner.storage.get("max_withdrawal"), new_max)

    def test_set_max_withdrawal_non_admin(self):
        initial_supply = 1000000
        max_withdrawal = 1000
        self._initialize_faucet(initial_supply, max_withdrawal)

        non_admin_address, _ = self._get_any_address()
        new_max = 2000
        with self.assertRaises(NCFail):
            self._set_max_withdrawal(non_admin_address, new_max)

    def test_random_user_interactions(self):
        initial_supply = 10000000
        max_withdrawal = 10000
        admin_address = self._initialize_faucet(initial_supply, max_withdrawal)

        num_users = 100
        users = [self._get_any_address()[0] for _ in range(num_users)]
        user_withdrawals = {user: 0 for user in users}

        total_withdrawn = 0
        num_actions = 1000

        for _ in range(num_actions):
            action = random.choice(
                ["withdraw", "deposit", "admin_withdraw", "set_max_withdrawal"]
            )

            if action == "withdraw":
                user = random.choice(users)
                amount = random.randint(1, max_withdrawal)
                try:
                    self._withdraw(user, amount)
                    user_withdrawals[user] += amount
                    total_withdrawn += amount
                except NCFail:
                    pass  # Withdrawal might fail due to exceeded limits or insufficient funds

            elif action == "deposit":
                user = random.choice(users + [admin_address])
                amount = random.randint(1000, 100000)
                self._deposit(user, amount)
                initial_supply += amount

            elif action == "admin_withdraw":
                amount = random.randint(1000, 100000)
                try:
                    self._admin_withdraw(admin_address, amount)
                    initial_supply -= amount
                except NCFail:
                    pass  # Admin withdrawal might fail due to insufficient funds

            elif action == "set_max_withdrawal":
                new_max = random.randint(1000, 20000)
                try:
                    self._set_max_withdrawal(admin_address, new_max)
                    max_withdrawal = new_max
                except NCFail:
                    pass  # Set max withdrawal might fail if called by non-admin (shouldn't happen in this case)

        # Final assertions
        self.assertEqual(
            self.runner.storage.get("total_supply"), initial_supply - total_withdrawn
        )

        total_contract_withdrawals = sum(
            self._get_user_withdrawal(user) for user in users
        )
        self.assertEqual(total_contract_withdrawals, total_withdrawn)

        for user, withdrawn in user_withdrawals.items():
            self.assertLessEqual(withdrawn, max_withdrawal)
            self.assertEqual(self._get_user_withdrawal(user), withdrawn)


if __name__ == "__main__":
    unittest.main()
