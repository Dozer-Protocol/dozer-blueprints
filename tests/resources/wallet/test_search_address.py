from twisted.internet.defer import inlineCallbacks

from hathor.conf import HathorSettings
from hathor.crypto.util import decode_address
from hathor.transaction.scripts import parse_address_script
from hathor.wallet.resources.thin_wallet import AddressBalanceResource, AddressSearchResource
from tests.resources.base_resource import StubSite, _BaseResourceTest
from tests.utils import add_blocks_unlock_reward, add_new_blocks, create_tokens

settings = HathorSettings()


class SearchAddressTest(_BaseResourceTest._ResourceTest):
    def setUp(self):
        super().setUp()

        self.network = 'testnet'
        self.manager = self.create_peer(self.network, unlock_wallet=True, wallet_index=True)

        # Unlocking wallet
        self.manager.wallet.unlock(b'MYPASS')

        add_new_blocks(self.manager, 1, advance_clock=1)
        add_blocks_unlock_reward(self.manager)
        tx = create_tokens(self.manager, mint_amount=100, token_name='Teste', token_symbol='TST')
        self.token_uid = tx.tokens[0]

        # Create a tx with this token, so we can have more tx in the history
        output = tx.outputs[0]
        script_type_out = parse_address_script(output.script)
        self.address = script_type_out.address

        # Using token creation address as search address
        # Token creation address has change output for the genesis (1B - 0.01 HTR of token deposit)
        self.address_bytes = decode_address(self.address)
        add_new_blocks(self.manager, 5, advance_clock=1, address=self.address_bytes)

    @inlineCallbacks
    def test_search(self):
        resource = StubSite(AddressSearchResource(self.manager))

        # Invalid address
        response_error = yield resource.get('thin_wallet/address_search', {b'address': 'vvvv'.encode(), b'count': 3})
        data_error = response_error.json_value()
        self.assertFalse(data_error['success'])

        response = yield resource.get('thin_wallet/address_search', {b'address': self.address.encode(), b'count': 3})
        data = response.json_value()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['transactions']), 3)
        self.assertTrue(data['has_more'])

        # Getting next page
        response2 = yield resource.get(
            'thin_wallet/address_search',
            {
                b'address': self.address.encode(),
                b'count': 3,
                b'page': b'next',
                b'hash': data['transactions'][2]['tx_id'].encode()
            }
        )
        data2 = response2.json_value()
        self.assertTrue(data2['success'])
        self.assertEqual(len(data2['transactions']), 3)
        self.assertFalse(data2['has_more'])

        # Getting previous page from third element
        response3 = yield resource.get(
            'thin_wallet/address_search',
            {
                b'address': self.address.encode(),
                b'count': 3,
                b'page': b'previous',
                b'hash': data['transactions'][2]['tx_id'].encode()
            }
        )
        data3 = response3.json_value()
        self.assertTrue(data3['success'])
        self.assertEqual(len(data3['transactions']), 2)
        self.assertFalse(data3['has_more'])

    @inlineCallbacks
    def test_address_balance(self):
        resource = StubSite(AddressBalanceResource(self.manager))

        # Invalid address
        response_error = yield resource.get('thin_wallet/address_search', {b'address': 'vvvv'.encode(), b'count': 3})
        data_error = response_error.json_value()
        self.assertFalse(data_error['success'])

        response = yield resource.get('thin_wallet/address_balance', {b'address': self.address.encode()})
        data = response.json_value()
        self.assertTrue(data['success'])
        # Genesis - token deposit + blocks mined
        HTR_value = settings.GENESIS_TOKENS - 1 + (settings.INITIAL_TOKENS_PER_BLOCK * 5)
        self.assertEqual(data['quantity'], 6)  # 5 blocks mined + token creation tx
        self.assertIn(settings.HATHOR_TOKEN_UID.hex(), data['tokens_data'])
        self.assertIn(self.token_uid.hex(), data['tokens_data'])
        self.assertEqual(HTR_value, data['tokens_data'][settings.HATHOR_TOKEN_UID.hex()]['received'])
        self.assertEqual(0, data['tokens_data'][settings.HATHOR_TOKEN_UID.hex()]['spent'])
        self.assertEqual(100, data['tokens_data'][self.token_uid.hex()]['received'])
        self.assertEqual(0, data['tokens_data'][self.token_uid.hex()]['spent'])
