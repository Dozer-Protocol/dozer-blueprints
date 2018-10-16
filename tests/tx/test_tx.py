import unittest
import os
import json
import base64
from hathor.wallet import Wallet
from hathor.transaction import Transaction, TxInput, TxOutput, MAX_NUM_INPUTS, MAX_NUM_OUTPUTS
from hathor.transaction.storage import TransactionMemoryStorage
from hathor.transaction.exceptions import InputOutputMismatch, TooManyInputs, TooManyOutputs, InvalidInputData
from hathor.transaction.scripts import P2PKH
from hathor.crypto.util import get_private_key_from_bytes, get_public_key_from_bytes, get_address_from_public_key


class BasicTransaction(unittest.TestCase):
    def setUp(self):
        self.wallet = Wallet()
        self.tx_storage = TransactionMemoryStorage()
        self.genesis = self.tx_storage.get_all_genesis()
        self.genesis_blocks = [tx for tx in self.genesis if tx.is_block]
        self.genesis_txs = [tx for tx in self.genesis if not tx.is_block]

        # read genesis keys
        filepath = os.path.join(os.getcwd(), 'hathor/wallet/genesis_keys.json')
        dict_data = None
        with open(filepath, 'r') as json_file:
            dict_data = json.loads(json_file.read())
        b64_private_key = dict_data['private_key']
        b64_public_key = dict_data['public_key']
        private_key_bytes = base64.b64decode(b64_private_key)
        public_key_bytes = base64.b64decode(b64_public_key)
        self.genesis_private_key = get_private_key_from_bytes(private_key_bytes)
        self.genesis_public_key = get_public_key_from_bytes(public_key_bytes)

        # random keys to be used
        random_priv = 'MIGEAgEAMBAGByqGSM49AgEGBSuBBAAKBG0wawIBAQQgMnAHVIyj7Hym2yI' \
                      'w+JcKEfdCHByIp+FHfPoIkcnjqGyhRANCAATX76SGshGeoacUcZDhXEzERt' \
                      'AHbd30CVpUg8RRnAIhaFcuMY3G+YFr/mReAPRuiLKCnolWz3kCltTtNj36rJyd'
        random_pub = 'MFYwEAYHKoZIzj0CAQYFK4EEAAoDQgAE1++khrIRnqGnFHGQ4VxMxEbQB23d' \
                     '9AlaVIPEUZwCIWhXLjGNxvmBa/5kXgD0boiygp6JVs95ApbU7TY9+qycnQ=='
        self.private_key_random = get_private_key_from_bytes(base64.b64decode(random_priv))
        self.public_key_random = get_public_key_from_bytes(base64.b64decode(random_pub))

    # def test_wrong_weight(self):
    #     # we don't care about input data or tx id, so us anything
    #     random_bytes = bytes.fromhex('0000184e64683b966b4268f387c269915cc61f6af5329823a93e3696cb0fe902')
    #     tx_input = TxInput(
    #         tx_id=random_bytes,
    #         index=0,
    #         data=random_bytes
    #     )
    #     tx = Transaction(
    #         weight=0,
    #         hash=random_bytes,
    #         inputs=[tx_input],
    #         storage=self.tx_storage
    #     )
    #
    #     with self.assertRaises(WeightError):
    #         tx.verify_pow()

    def test_input_output_match(self):
        genesis_block = self.genesis_blocks[0]

        public_bytes, signature = self.wallet.get_input_aux_data(self.genesis_private_key)

        data = P2PKH.create_input_data(public_bytes, signature)
        _input = TxInput(genesis_block.hash, 0, data)

        # spend less than what was generated
        value = genesis_block.outputs[0].value - 1
        address = get_address_from_public_key(self.genesis_public_key)
        script = P2PKH.create_output_script(address)
        output = TxOutput(value, script)
        tx = Transaction(
            inputs=[_input],
            outputs=[output],
            storage=self.tx_storage
        )

        with self.assertRaises(InputOutputMismatch):
            tx.verify_sum()

    def test_script(self):
        genesis_block = self.genesis_blocks[0]

        # create input data with incorrect private key
        public_bytes, signature = self.wallet.get_input_aux_data(self.private_key_random)

        data_wrong = P2PKH.create_input_data(public_bytes, signature)
        _input = TxInput(genesis_block.hash, 0, data_wrong)
        value = genesis_block.outputs[0].value

        address = get_address_from_public_key(self.genesis_public_key)
        script = P2PKH.create_output_script(address)
        output = TxOutput(value, script)

        tx = Transaction(
            inputs=[_input],
            outputs=[output],
            storage=self.tx_storage
        )

        with self.assertRaises(InvalidInputData):
            tx.verify_inputs()

    def test_too_many_inputs(self):
        random_bytes = bytes.fromhex('0000184e64683b966b4268f387c269915cc61f6af5329823a93e3696cb0fe902')

        _input = TxInput(random_bytes, 0, random_bytes)
        inputs = [_input] * (MAX_NUM_INPUTS + 1)

        tx = Transaction(
            inputs=inputs,
            storage=self.tx_storage
        )

        with self.assertRaises(TooManyInputs):
            tx.verify_number_of_inputs()

    def test_too_many_outputs(self):
        random_bytes = bytes.fromhex('0000184e64683b966b4268f387c269915cc61f6af5329823a93e3696cb0fe902')

        output = TxOutput(1, random_bytes)
        outputs = [output] * (MAX_NUM_OUTPUTS + 1)

        tx = Transaction(
            outputs=outputs,
            storage=self.tx_storage
        )

        with self.assertRaises(TooManyOutputs):
            tx.verify_number_of_outputs()

    def test_struct(self):
        parents = [tx.hash for tx in self.genesis_txs]
        genesis_block = self.genesis_blocks[0]

        public_bytes, signature = self.wallet.get_input_aux_data(self.genesis_private_key)
        data = P2PKH.create_input_data(public_bytes, signature)
        _input = TxInput(genesis_block.hash, 0, data)

        value = genesis_block.outputs[0].value
        address = get_address_from_public_key(self.genesis_public_key)
        script = P2PKH.create_output_script(address)
        output = TxOutput(value, script)

        tx = Transaction(
            nonce=100,
            inputs=[_input],
            outputs=[output],
            parents=parents,
            storage=self.tx_storage
        )
        tx.update_hash()

        data = tx.get_struct()
        tx_read = Transaction.create_from_struct(data)

        self.assertEqual(tx, tx_read)

    def test_children_update(self):
        parents = [tx.hash for tx in self.genesis_txs]
        genesis_block = self.genesis_blocks[0]

        public_bytes, signature = self.wallet.get_input_aux_data(self.genesis_private_key)
        data = P2PKH.create_input_data(public_bytes, signature)
        _input = TxInput(genesis_block.hash, 0, data)

        value = genesis_block.outputs[0].value
        address = get_address_from_public_key(self.genesis_public_key)
        script = P2PKH.create_output_script(address)
        output = TxOutput(value, script)

        tx = Transaction(
            nonce=100,
            inputs=[_input],
            outputs=[output],
            parents=parents,
            storage=self.tx_storage
        )
        tx.update_hash()

        tx.update_parents()

        # genesis transactions should have only this tx in their chidlren set
        for parent_hash in parents:
            metadata = tx.storage.get_metadata_by_hash_bytes(parent_hash)
            self.assertEqual(len(metadata.children), 1)
            self.assertEqual(metadata.children.pop(), tx.hash_hex)


if __name__ == '__main__':
    unittest.main()
