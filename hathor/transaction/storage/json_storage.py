import base64
import json
import os
import re

from hathor.transaction.storage.exceptions import TransactionDoesNotExist, TransactionMetadataDoesNotExist
from hathor.transaction.storage.transaction_storage import BaseTransactionStorage, TransactionStorageAsyncFromSync
from hathor.transaction.transaction_metadata import TransactionMetadata
from hathor.util import deprecated, skip_warning


class TransactionJSONStorage(BaseTransactionStorage, TransactionStorageAsyncFromSync):
    def __init__(self, path='./', with_index=True):
        os.makedirs(path, exist_ok=True)
        self.path = path
        super().__init__(with_index=with_index)

    @deprecated('Use save_transaction_deferred instead')
    def save_transaction(self, tx, *, only_metadata=False):
        skip_warning(super().save_transaction)(tx, only_metadata=only_metadata)
        if not only_metadata:
            self._save_transaction(tx)
        self._save_metadata(tx)

    def _save_transaction(self, tx):
        data = tx.to_json()
        filepath = self.generate_filepath(tx.hash)
        self.save_to_json(filepath, data)
        if tx.is_block:
            self._save_blockhash_by_height(tx)

    def _save_metadata(self, tx):
        metadata = tx.get_metadata()
        data = self.serialize_metadata(metadata)
        filepath = self.generate_metadata_filepath(tx.hash)
        self.save_to_json(filepath, data)

    def generate_filepath(self, hash_bytes):
        filename = 'tx_{}.json'.format(hash_bytes.hex())
        filepath = os.path.join(self.path, filename)
        return filepath

    def serialize_metadata(self, metadata):
        return metadata.to_json()

    def load_metadata(self, data):
        return TransactionMetadata.create_from_json(data)

    def generate_metadata_filepath(self, hash_bytes):
        filename = 'tx_{}_metadata.json'.format(hash_bytes.hex())
        filepath = os.path.join(self.path, filename)
        return filepath

    @deprecated('Use transaction_exists_deferred instead')
    def transaction_exists(self, hash_bytes):
        genesis = self.get_genesis(hash_bytes)
        if genesis:
            return True
        filepath = self.generate_filepath(hash_bytes)
        return os.path.isfile(filepath)

    def save_to_json(self, filepath, data):
        with open(filepath, 'w') as json_file:
            json_file.write(json.dumps(data, indent=4))

    def load_from_json(self, filepath, error):
        if os.path.isfile(filepath):
            with open(filepath, 'r') as json_file:
                dict_data = json.loads(json_file.read())
                return dict_data
        else:
            raise error

    def generate_blocks_at_height_filepath(self, height):
        filename = 'blks_h_{}.json'.format(height)
        filepath = os.path.join(self.path, filename)
        return filepath

    def _save_blockhash_by_height(self, block):
        """Adds the given block's hash string to the list of block hashes at the given height.

        Input is a block object, but only the hash is saved, in a file with name based on the height of the block.
        """
        # Load existing blocks at height, if any.
        height = block.height
        data, filepath = self._get_block_hashes_at_height(height)
        hash_hex = block.hash.hex()
        if hash_hex not in data:
            data.append(hash_hex)
            self.save_to_json(filepath, data)

    def _get_block_hashes_at_height(self, height):
        """Returns a tuple of list of hashes of blocks at the given height and the storage filename."""
        filepath = self.generate_blocks_at_height_filepath(height)
        try:
            data = self.load_from_json(filepath, FileNotFoundError)
        except FileNotFoundError:
            data = []
        return data, filepath

    def load(self, data):
        from hathor.transaction.transaction import Transaction
        from hathor.transaction.block import Block
        from hathor.transaction.base_transaction import TxOutput, TxInput

        nonce = data['nonce']
        timestamp = data['timestamp']
        height = data['height']
        version = data['version']
        weight = data['weight']
        hash_bytes = bytes.fromhex(data['hash'])

        parents = []
        for parent in data['parents']:
            parents.append(bytes.fromhex(parent))

        inputs = []
        for input_tx in data['inputs']:
            tx_id = bytes.fromhex(input_tx['tx_id'])
            index = input_tx['index']
            input_data = base64.b64decode(input_tx['data'])
            inputs.append(TxInput(tx_id, index, input_data))

        outputs = []
        for output in data['outputs']:
            value = output['value']
            script = base64.b64decode(output['script'])
            outputs.append(TxOutput(value, script))

        tokens = [bytes.fromhex(uid) for uid in data['tokens']]

        kwargs = {
            'nonce': nonce,
            'timestamp': timestamp,
            'version': version,
            'height': height,
            'weight': weight,
            'outputs': outputs,
            'parents': parents,
            'storage': self,
        }

        if 'data' in data:
            kwargs['data'] = base64.b64decode(data['data'])
            tx = Block(**kwargs)
        else:
            kwargs['inputs'] = inputs
            kwargs['tokens'] = tokens
            tx = Transaction(**kwargs)
        tx.update_hash()
        assert tx.hash == hash_bytes, 'Hashes differ: {} != {}'.format(tx.hash.hex(), hash_bytes.hex())
        return tx

    @deprecated('Use get_transaction_deferred instead')
    def get_transaction(self, hash_bytes):
        genesis = self.get_genesis(hash_bytes)
        if genesis:
            return genesis

        filepath = self.generate_filepath(hash_bytes)
        data = self.load_from_json(filepath, TransactionDoesNotExist())
        tx = self.load(data)
        try:
            meta = self._get_metadata_by_hash(hash_bytes)
            tx._metadata = meta
        except TransactionMetadataDoesNotExist:
            pass
        return tx

    def _get_metadata_by_hash(self, hash_bytes):
        filepath = self.generate_metadata_filepath(hash_bytes)
        data = self.load_from_json(filepath, TransactionMetadataDoesNotExist)
        return self.load_metadata(data)

    @deprecated('Use get_all_transactions_deferred instead')
    def get_all_transactions(self):
        for tx in self.get_all_genesis():
            yield tx

        path = self.path
        pattern = r'tx_[\dabcdef]{64}\.json'
        re_pattern = re.compile(pattern)

        with os.scandir(path) as it:
            for f in it:
                if re_pattern.match(f.name):
                    # TODO Return a proxy that will load the transaction only when it is used.
                    with open(f.path, 'r') as json_file:
                        dict_data = json.loads(json_file.read())
                    transaction = self.load(dict_data)
                    yield transaction

    @deprecated('Use get_count_tx_blocks_deferred instead')
    def get_count_tx_blocks(self):
        genesis_len = len(self.get_all_genesis())
        path = self.path
        files = os.listdir(path)
        return len(files) + genesis_len
