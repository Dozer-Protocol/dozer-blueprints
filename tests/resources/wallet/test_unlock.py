import unittest

from hathor.wallet.resources import UnlockWalletResource, StateWalletResource, LockWalletResource
from twisted.internet.defer import inlineCallbacks
from tests.resources.base_resource import StubSite, _BaseResourceTest
from hathor.wallet import HDWallet


class _Base:
    class _UnlockTest(_BaseResourceTest._ResourceTest):
        def setUp(self):
            super().setUp()
            self.web = StubSite(UnlockWalletResource(self.manager))
            self.web_lock = StubSite(LockWalletResource(self.manager))
            self.web_state = StubSite(StateWalletResource(self.manager))


class UnlockTest(_Base._UnlockTest):
    @inlineCallbacks
    def test_unlocking(self):
        # Wallet is locked
        response = yield self.web_state.get("wallet/state")
        data = response.json_value()
        self.assertTrue(data['is_locked'])

        # Try to unlock with wrong password

        # Options
        yield self.web.options("wallet/unlock")

        response_error = yield self.web.post("wallet/unlock", {'password': 'wrong_password'})
        data_error = response_error.json_value()
        self.assertFalse(data_error['success'])

        # Try to unlock with correct password
        response_success = yield self.web.post("wallet/unlock", {'password': 'MYPASS'})
        data_success = response_success.json_value()
        self.assertTrue(data_success['success'])

        # Wallet is unlocked
        response_unlocked = yield self.web_state.get("wallet/state")
        data_unlocked = response_unlocked.json_value()
        self.assertFalse(data_unlocked['is_locked'])


class UnlockHDTest(_Base._UnlockTest):
    def _create_test_wallet(self):
        wallet = HDWallet()
        wallet._manually_initialize()
        # wallet.unlock(tx_storage=self.manager.tx_storage)
        return wallet

    @inlineCallbacks
    def test_unlocking(self):
        # # Wallet is not locked
        # response = yield self.web_state.get("wallet/state")
        # data = response.json_value()
        # self.assertFalse(data['is_locked'])

        # # Lock the wallet
        # response_lock = yield self.web_lock.post("wallet/lock")
        # data_lock = response_lock.json_value()
        # self.assertTrue(data_lock['success'])

        # Wallet is locked
        response_locked = yield self.web_state.get("wallet/state")
        data_locked = response_locked.json_value()
        self.assertTrue(data_locked['is_locked'])

        # Unlock wallet invalid words
        response_invalid = yield self.web.post("wallet/unlock", {'words': 'abc def', 'passphrase': ''})
        data_invalid = response_invalid.json_value()
        self.assertFalse(data_invalid['success'])

        # Unlock wallet
        response_success = yield self.web.post("wallet/unlock", {'passphrase': ''})
        data_success = response_success.json_value()
        self.assertTrue(data_success['success'])

        # Wallet is unlocked
        response_unlocked = yield self.web_state.get("wallet/state")
        data_unlocked = response_unlocked.json_value()
        self.assertFalse(data_unlocked['is_locked'])

        # Lock the wallet and unlock with same words
        self.manager.wallet.lock()
        response_words = yield self.web.post("wallet/unlock", {'words': data_success['words'], 'passphrase': ''})
        data_words = response_words.json_value()
        self.assertTrue(data_words['success'])


if __name__ == '__main__':
    unittest.main()
