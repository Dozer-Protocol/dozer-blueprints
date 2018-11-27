import os
import json
import base58
from abc import ABC, abstractmethod
from collections import namedtuple, defaultdict
from twisted.logger import Logger

from hathor.wallet.exceptions import WalletOutOfSync, InsuficientFunds, PrivateKeyNotFound, \
                                     InputDuplicated, InvalidAddress
from hathor.transaction import TxInput, TxOutput
from hathor.transaction.scripts import P2PKH
from hathor.pubsub import HathorEvents
from enum import Enum

WalletInputInfo = namedtuple('WalletInputInfo', ['tx_id', 'index', 'private_key'])
WalletOutputInfo = namedtuple('WalletOutputInfo', ['address', 'value'])


class BaseWallet(ABC):
    log = Logger()

    class WalletType(Enum):
        # Hierarchical Deterministic Wallet
        HD = 'hd'

        # Normal key pair wallet
        KEY_PAIR = 'keypair'

    def __init__(self, directory='./', history_file='history.json', pubsub=None):
        """ A wallet will hold the unspent and spent transactions

        All files will be stored in the same directory, and it should
        only contain wallet associated files.

        :param directory: where to store wallet associated files
        :type directory: string

        :param history_file: name of history file
        :type history_file: string

        :param pubsub: If not given, a new one is created.
        :type pubsub: :py:class:`hathor.pubsub.PubSubManager`
        """
        self.history_path = os.path.join(directory, history_file)

        # Dict[string(base58), List[UnspentTx]]
        self.unspent_txs = {}

        # Dict[Tuple(tx_id, index), List[SpentTx]]
        # We have for each output, which txs spent it
        self.spent_txs = defaultdict(list)

        # Dict[Tuple(tx_id, index), List[SpentTx]]
        # Save each spent tx that was voided and is not spending tokens from this wallet anymore
        self.voided_spent = defaultdict(list)
        # Dict[string(base58), List[UnspentTx]]
        # Save each unspent tx that was voided and is not increasing the tokens of this wallet anymore
        self.voided_unspent = defaultdict(list)

        self.balance = 0

        self.pubsub = pubsub

        self.pubsub_events = [
            HathorEvents.STORAGE_TX_VOIDED,
            HathorEvents.STORAGE_TX_WINNER,
        ]

    def start(self):
        """ Start the pubsub subscription if wallet has a pubsub
        """
        if self.pubsub:
            for event in self.pubsub_events:
                self.pubsub.subscribe(event, self.handle_publish)

    def stop(self):
        """ Stop the pubsub subscription if wallet has a pubsub
        """
        if self.pubsub:
            for event in self.pubsub_events:
                self.pubsub.unsubscribe(event, self.handle_publish)

    def handle_publish(self, key, args):
        data = args.__dict__
        if key == HathorEvents.STORAGE_TX_VOIDED:
            self.on_tx_voided(data['tx'])
        elif key == HathorEvents.STORAGE_TX_WINNER:
            self.on_tx_winner(data['tx'])
        else:
            raise NotImplementedError

    @abstractmethod
    def lock(self):
        # TODO: unify docstring
        """ Lock the wallet and all parameters to default values.
        """
        """ Lock wallet and clear all caches.
        """
        raise NotImplementedError

    @abstractmethod
    def unlock(self, tx_storage, password):
        # TODO: unify docstring
        """ Unlock the wallet

        Set all parameters to initialize the wallet and load the txs

        :param tx_storage: storage from where I should load the txs
        :type tx_storage: :py:class:`hathor.transaction.storage.transaction_storage.TransactionStorage`

        :param words: words to generate the seed. It's a string with the words separated by a single space.
        If None we generate new words when starting the wallet
        :type words: string

        :param language: language of the words
        :type language: string

        :param passphrase: one more security level to generate the seed
        :type passphrase: bytes

        :return: hd wallet words. Generated in this method or passed as parameter
        :rtype: string
        """
        """ Validates if the password is valid

        Then saves the password as bytes.

        :type password: bytes

        :raises IncorrectPassword: when the password is incorrect

        :raises ValueError: when the password parameter is not bytes
        """
        raise NotImplementedError

    @abstractmethod
    def is_locked(self):
        """ Return if wallet is currently locked

        The wallet is locked if self.words is None

        :return: if wallet is locked
        :rtype: bool
        """
        raise NotImplementedError

    @abstractmethod
    def get_unused_address(self, mark_as_used=True):
        """ Return an address that is not used yet

        :param mark_as_used: if True we consider that this address is already used
        :type mark_as_used: bool

        :return: unused address in base58
        :rtype: string

        :raises OutOfUnusedAddresses: When there is no unused address left
            to be returned and wallet is locked
        """
        raise NotImplementedError

    def decode_address(self, address58):
        """ Decode address in base58 to bytes

        :param address58: Wallet address in base58
        :type address58: string

        :raises InvalidAddress: if address58 is not a valid base58 string or not a valid address

        :return: Address in bytes
        :rtype: bytes
        """
        try:
            decoded_address = base58.b58decode(address58)
        except ValueError:
            # Invalid base58 string
            raise InvalidAddress
        # Validate address size (right now our address is 20 bytes)
        # XXX In the future we will probably change how the address is generated
        # so the byte size will also change
        if len(decoded_address) != 20:
            raise InvalidAddress
        return decoded_address

    def get_unused_address_bytes(self, mark_as_used=True):
        address_str = self.get_unused_address(mark_as_used)
        return self.decode_address(address_str)

    @abstractmethod
    def tokens_received(self, address58):
        """ Method called when the wallet receive new tokens

        We set the address as used and remove it from the unused_keys

        :param address58: address that received the token in base58
        :type address58: string
        """
        raise NotImplementedError

    @abstractmethod
    def get_private_key(self, address58):
        """ Get private key from the address58

        :param address58: address in base58
        :type address58: string

        :return: Private key object.
        :rtype: bytes
        """
        raise NotImplementedError

    @abstractmethod
    def get_input_aux_data(self, data_to_sign, private_key):
        """ Sign the data to be used in input and get public key compressed in bytes

        :param data_to_sign: Data to be signed
        :type data_to_sign: bytes

        :param private_key: private key to sign data
        :type private_key: bytes

        :return: public key compressed in bytes and signature
        :rtype: tuple[bytes, bytes]
        """
        raise NotImplementedError

    def prepare_transaction(self, cls, inputs, outputs):
        """Prepares the tx inputs and outputs.

        Can be used to create blocks by passing empty list to inputs.

        :param cls: defines if we're creating a Transaction or Block
        :type cls: :py:class:`hathor.transaction.Block` or :py:class:`hathor.transaction.Transaction`

        :param inputs: the tx inputs
        :type inputs: List[WalletInputInfo]

        :param outputs: the tx outputs
        :type inputs: List[WalletOutputInfo]
        """
        tx_outputs = []
        for txout in outputs:
            tx_outputs.append(TxOutput(txout.value, P2PKH.create_output_script(txout.address)))

        tx_inputs = []
        private_keys = []
        for txin in inputs:
            private_keys.append(txin.private_key)
            tx_inputs.append(TxInput(txin.tx_id, txin.index, b''))

        tx = cls(inputs=tx_inputs, outputs=tx_outputs)
        data_to_sign = tx.get_sighash_all(clear_input_data=True)

        for txin, privkey in zip(tx.inputs, private_keys):
            public_key_bytes, signature = self.get_input_aux_data(data_to_sign, privkey)
            txin.data = P2PKH.create_input_data(public_key_bytes, signature)

        return tx

    def prepare_transaction_incomplete_inputs(self, cls, inputs, outputs, force=False, tx_storage=None):
        """Uses prepare_transaction() to prepare transaction.

        The difference is that the inputs argument does not contain the private key
        corresponding to it.

        Consider the wallet UI scenario: the user will see all unspent txs and can select
        which ones he wants to use as input, but the wallet is responsible for managing
        the keys, so he won't be able to send the inputs with the corresponding key.

        :param cls: class to create the object
        :type cls: Transaction or Block

        :param inputs: list of inputs of the tx
        :type inputs: List[WalletInputInfo]

        :param outputs: list of outputs of the tx
        :type outputs: List[WalletOutputInfo]

        :param force: if True we will search the private key not only in the unspent txs
            this parameter, when set to True, can be used to allow a double spending
        :type force: bool

        :param tx_storage: storage to search for output tx, in case we want to allow double spending
        :type tx_storage: TransactionStorage

        :raises PrivateKeyNotFound: when trying to spend output and we don't have the corresponding
            key in our wallet
        """
        if len(inputs) != len(set(inputs)):
            # Same input is used more than once
            raise InputDuplicated
        new_inputs = []
        for _input in inputs:
            found = False
            for address_b58, utxo_list in self.unspent_txs.items():
                for utxo in utxo_list:
                    if _input.tx_id == utxo.tx_id and _input.index == utxo.index:
                        new_inputs.insert(
                            0,
                            WalletInputInfo(_input.tx_id, _input.index,
                                            self.get_private_key(address_b58))
                        )
                        found = True

            if not found and force:
                # In case we use 'force', tx_storage is not optional
                assert tx_storage is not None
                # If we force we will search this private key also in the keys
                output_tx = tx_storage.get_transaction(_input.tx_id)
                output = output_tx.outputs[_input.index]
                p2pkh = P2PKH.verify_script(output.script)

                if p2pkh:
                    address = p2pkh.address
                    if address in self.keys:
                        new_inputs.insert(
                            0,
                            WalletInputInfo(_input.tx_id, _input.index,
                                            self.get_private_key(address))
                        )
                        found = True

            if not found:
                raise PrivateKeyNotFound

        return self.prepare_transaction(cls, new_inputs, outputs)

    def prepare_transaction_compute_inputs(self, cls, outputs):
        """Calculates de inputs given the outputs. Handles change.

        :param cls: defines if we're creating a Transaction or Block
        :type cls: :py:class:`hathor.transaction.Block` or :py:class:`hathor.transaction.Transaction`

        :param outputs: the tx outputs
        :type inputs: List[WalletOutputInfo]
        """
        amount = sum(output.value for output in outputs)
        inputs, total_inputs_amount = self.get_inputs_from_amount(amount)
        change_tx = self.handle_change_tx(total_inputs_amount, outputs)
        if change_tx:
            # change is usually the first output
            outputs.insert(0, change_tx)
        return self.prepare_transaction(cls, inputs, outputs)

    def handle_change_tx(self, sum_inputs, outputs):
        """Creates an output transaction with the change value

        :param sum_inputs: Sum of the input amounts
        :type sum_inputs: int

        :param outputs: A list of WalletOutputInfo
        :type outputs: List[WalletOutputInfo]

        :return: Return an output with the change
        :rtype: :py:class:`hathor.wallet.base_wallet.WalletOutputInfo`
        """
        sum_outputs = sum([output.value for output in outputs])

        if sum_inputs > sum_outputs:
            difference = sum_inputs - sum_outputs
            address_b58 = self.get_unused_address()
            address = self.decode_address(address_b58)
            new_output = WalletOutputInfo(address, difference)
            return new_output
        return None

    def get_inputs_from_amount(self, amount):
        """Creates inputs from our pool of unspent tx given a value

        This is a very simple algorithm, so it does not try to find the best combination
        of inputs.

        :raises InsuficientFunds: if the wallet does not have enough ballance
        """
        inputs_tx = []
        total_inputs_amount = 0

        for address_b58, utxo_list in self.unspent_txs.items():
            for utxo in utxo_list:
                inputs_tx.append(WalletInputInfo(utxo.tx_id, utxo.index,
                                 self.get_private_key(address_b58)))
                total_inputs_amount += utxo.value

                if total_inputs_amount >= amount:
                    break

            if total_inputs_amount >= amount:
                break

        if total_inputs_amount < amount:
            raise InsuficientFunds('Requested amount: {} / Available: {}'.format(amount, total_inputs_amount))

        return inputs_tx, total_inputs_amount

    def on_new_tx(self, tx):
        """Checks the inputs and outputs of a transaction for matching keys.

        If an output matches, will add it to the unspent_txs dict.
        If an input matches, removes from unspent_txs dict and adds to spent_txs dict.

        :raises WalletOutOfSync: when there's an input spending an address in our wallet
            but we don't have the corresponding UTXO. This indicates the wallet may be
            missing some transactions.
        """
        updated = False

        # check outputs
        for index, output in enumerate(tx.outputs):
            p2pkh_out = P2PKH.verify_script(output.script)
            if p2pkh_out:
                if p2pkh_out.address in self.keys:
                    # this wallet received tokens
                    utxo = UnspentTx(tx.hash, index, output.value, tx.timestamp)
                    utxo_list = self.unspent_txs.pop(p2pkh_out.address, [])
                    utxo_list.append(utxo)
                    self.unspent_txs[p2pkh_out.address] = utxo_list
                    # mark key as used
                    self.tokens_received(p2pkh_out.address)
                    self.balance += output.value
                    updated = True
                    # publish new output and new balance
                    self.publish_update(
                        HathorEvents.WALLET_OUTPUT_RECEIVED,
                        total=self.get_total_tx(),
                        output=utxo
                    )
                    self.publish_update(HathorEvents.WALLET_BALANCE_UPDATED, balance=self.balance)
            else:
                # it's the only one we know, so log warning
                self.log.warn('unknown script')

        # check inputs
        for _input in tx.inputs:
            p2pkh_in = P2PKH.verify_input(_input.data)
            if p2pkh_in:
                if p2pkh_in.address in self.keys:
                    # this wallet spent tokens
                    # remove from unspent_txs
                    if p2pkh_in.address not in self.unspent_txs:
                        # If we dont have it in the unspent_txs, it must be in the spent_txs
                        # So we append this spent with the others
                        key = (_input.tx_id, _input.index)
                        if key in self.spent_txs:
                            output_tx = tx.storage.get_transaction(_input.tx_id)
                            output = output_tx.outputs[_input.index]
                            spent = SpentTx(tx.hash, _input.tx_id, _input.index, output.value, tx.timestamp)
                            self.spent_txs[key].append(spent)

                        continue
                    utxo_list = self.unspent_txs.pop(p2pkh_in.address)
                    list_index = -1
                    for i, utxo in enumerate(utxo_list):
                        if utxo.tx_id == _input.tx_id and utxo.index == _input.index:
                            list_index = i
                            break
                    if list_index == -1:
                        # the wallet does not have the output referenced by this input
                        raise WalletOutOfSync('{} {}'.format(_input.tx_id.hex(), _input.index))
                    old_utxo = utxo_list.pop(list_index)
                    if len(utxo_list) > 0:
                        self.unspent_txs[p2pkh_in.address] = utxo_list
                    # add to spent_txs
                    spent = SpentTx(tx.hash, _input.tx_id, _input.index, old_utxo.value, tx.timestamp)
                    self.spent_txs[(_input.tx_id, _input.index)].append(spent)
                    self.balance -= old_utxo.value
                    updated = True
                    # publish spent output and new balance
                    self.publish_update(HathorEvents.WALLET_INPUT_SPENT, output_spent=spent)
                    self.publish_update(HathorEvents.WALLET_BALANCE_UPDATED, balance=self.balance)
            else:
                self.log.warn('unknown input data')

        if updated:
            # TODO update history file?
            # XXX should wallet always update it or it will be called externally?
            pass

    def on_tx_voided(self, tx):
        """ This method is called when a tx is voided in a conflict
            We use it to update the balance and the variable that stores it
            We check it's outputs and inputs to update wallet information

            For outputs we have the following situations:

            . Not used yet, so it's still in the unspent_txs, we remove it from there and decrease the balance
            . Already used, so it's in spent_txs, we remove from there
            . Not found anywhere, so it was already updated in another conflict resolution

            For inputs we have the following situations:

            . If it's in the unspent_txs, we have to do nothing
            . If it's in the spent_txs, we remove from the array. If this was the last tx, we recreate in the unspent

            :param tx: Transaction that was voided
            :type tx: :py:class:`hathor.transaction.Transaction`
        """
        should_update = False
        # check outputs
        for index, output in enumerate(tx.outputs):
            p2pkh_out = P2PKH.verify_script(output.script)
            if p2pkh_out:
                if p2pkh_out.address in self.keys:
                    # Remove this output from unspent_tx, if still there
                    # Find output index
                    utxo_list = self.unspent_txs.pop(p2pkh_out.address, [])
                    list_index = -1
                    for i, utxo in enumerate(utxo_list):
                        if utxo.tx_id == tx.hash and utxo.index == index:
                            list_index = i
                            break
                    if list_index > -1:
                        # Output found: remove from list and update balance
                        utxo_list.pop(list_index)
                        self.balance -= output.value
                        should_update = True
                        # publish new balance
                        self.publish_update(HathorEvents.WALLET_BALANCE_UPDATED, balance=self.balance)
                    else:
                        # If it is in spent tx, remove from dict
                        if (tx.hash, index) in self.spent_txs:
                            should_update = True
                            del self.spent_txs[(tx.hash, index)]

                    if len(utxo_list) > 0:
                        self.unspent_txs[p2pkh_out.address] = utxo_list

                    # Save in voided_unspent, if it's not there yet
                    # First try to find it in voided_unspent
                    voided_utxo_list = self.voided_unspent.get(p2pkh_out.address, [])
                    list_index = -1
                    for i, utxo in enumerate(voided_utxo_list):
                        if utxo.tx_id == tx.hash and utxo.index == index:
                            list_index = i
                            break
                    if list_index == -1:
                        # If it's not there, we add it
                        voided = UnspentTx(tx.hash, index, output.value, tx.timestamp, voided=True)
                        self.voided_unspent[p2pkh_out.address].append(voided)
                        should_update = True

        # check inputs
        for _input in tx.inputs:
            p2pkh_in = P2PKH.verify_input(_input.data)
            if p2pkh_in:
                if p2pkh_in.address in self.keys:
                    output = None
                    # Try to find in spent tx
                    key = (_input.tx_id, _input.index)
                    if key in self.spent_txs:
                        list_index = -1
                        for i, spent in enumerate(self.spent_txs[key]):
                            if (spent.tx_id == tx.hash and
                                    spent.from_index == _input.index and
                                    spent.from_tx_id == _input.tx_id):
                                list_index = i
                                break

                        if list_index > -1:
                            # Spent found: remove from list
                            spent = self.spent_txs[key].pop(list_index)

                            if len(self.spent_txs[key]) == 0:
                                # If this was the last input that spent this output, we recreate the output
                                output_tx = tx.storage.get_transaction(spent.from_tx_id)
                                output = output_tx.outputs[spent.from_index]

                                p2pkh_out = P2PKH.verify_script(output.script)
                                if p2pkh_out and p2pkh_out.address in self.keys:
                                    utxo = UnspentTx(_input.tx_id, _input.index, output.value, output_tx.timestamp)
                                    utxo_list = self.unspent_txs.pop(p2pkh_out.address, [])
                                    utxo_list.append(utxo)
                                    self.unspent_txs[p2pkh_out.address] = utxo_list
                                    self.balance += output.value

                            should_update = True

                    # Save in voided_spent, if it's not there yet
                    # First try to find it in voided_spent
                    voided_stxi_list = self.voided_spent.get(key, [])
                    list_index = -1
                    for i, spent in enumerate(voided_stxi_list):
                        if (spent.tx_id == tx.hash and
                                spent.from_index == _input.index and
                                spent.from_tx_id == _input.tx_id):
                            list_index = i
                            break
                    if list_index == -1:
                        # If it's not there, we add it
                        if output is None:
                            output_tx = tx.storage.get_transaction(_input.tx_id)
                            output = output_tx.outputs[_input.index]

                        voided = SpentTx(tx.hash, _input.tx_id, _input.index, output.value, tx.timestamp, voided=True)
                        self.voided_spent[key].append(voided)
                        should_update = True

        if should_update:
            # publish new balance
            self.publish_update(HathorEvents.WALLET_BALANCE_UPDATED, balance=self.balance)
            # publish update history
            self.publish_update(HathorEvents.WALLET_HISTORY_UPDATED)

    def on_tx_winner(self, tx):
        """ This method is called when a tx is set as winner of a conflict
            We use it to update the balance and the variable that stores it
            We check it's outputs and inputs to update wallet information

            For outputs we have the following situations:

            . In case it's in the spent or unspent we do nothing
            . In case is not found, it was deleted because of a previous conflict, so we recreate un the unspent

            For inputs we have the following situations:

            . If it's in the unspent_txs, we remove from there and add in the spent_txs
            . If it's in the spent_txs, we do nothing
            . If it's not in both, we add in the spent_txs

            :param tx: Transaction that was voided
            :type tx: :py:class:`hathor.transaction.Transaction`
        """
        should_update = False
        # check outputs
        for index, output in enumerate(tx.outputs):
            p2pkh_out = P2PKH.verify_script(output.script)
            if p2pkh_out:
                if p2pkh_out.address in self.keys:
                    # Find output index
                    utxo_list = self.unspent_txs.pop(p2pkh_out.address, [])
                    list_utxo_index = -1
                    for i, utxo in enumerate(utxo_list):
                        if utxo.tx_id == tx.hash and utxo.index == index:
                            list_utxo_index = i
                            break
                    if list_utxo_index == -1:
                        # Not found in unspent
                        # Try to find in spent tx
                        key = (tx.hash, index)
                        if key not in self.spent_txs or len(self.spent_txs[key]) == 0:
                            # If it's not in unspet or spent it was deleted, so we create again in the unspent
                            utxo = UnspentTx(tx.hash, index, output.value, tx.timestamp)
                            utxo_list = self.unspent_txs.pop(p2pkh_out.address, [])
                            utxo_list.append(utxo)
                            # Update balance
                            self.balance += output.value
                            should_update = True

                    self.unspent_txs[p2pkh_out.address] = utxo_list

                    # Remove from voided_unspent, if it's there
                    # First try to find it in voided_unspent
                    voided_utxo_list = self.voided_unspent.get(p2pkh_out.address, [])
                    list_index = -1
                    for i, utxo in enumerate(voided_utxo_list):
                        if utxo.tx_id == tx.hash and utxo.index == index:
                            list_index = i
                            break
                    if list_index > -1:
                        # If it's there, we remove it
                        self.voided_unspent[p2pkh_out.address].pop(list_index)
                        should_update = True

        # check inputs
        for _input in tx.inputs:
            p2pkh_in = P2PKH.verify_input(_input.data)
            if p2pkh_in:
                if p2pkh_in.address in self.keys:
                    key = (_input.tx_id, _input.index)
                    # Remove from voided_spent, if it's there
                    # First try to find it in voided_spent
                    voided_stxi_list = self.voided_spent.get(key, [])
                    list_index = -1
                    for i, spent in enumerate(voided_stxi_list):
                        if (spent.tx_id == tx.hash and
                                spent.from_index == _input.index and
                                spent.from_tx_id == _input.tx_id):
                            list_index = i
                            break
                    if list_index > -1:
                        # If it's there, we remove it
                        self.voided_spent[key].pop(list_index)
                        should_update = True

                    # Remove from unspent_txs, if it's there
                    if p2pkh_in.address in self.unspent_txs:
                        utxo_list = self.unspent_txs.pop(p2pkh_in.address)
                        list_index = -1
                        for i, utxo in enumerate(utxo_list):
                            if utxo.tx_id == _input.tx_id and utxo.index == _input.index:
                                list_index = i
                                break
                        if list_index > -1:
                            old_utxo = utxo_list.pop(list_index)
                            if len(utxo_list) > 0:
                                self.unspent_txs[p2pkh_in.address] = utxo_list
                            # add to spent_txs
                            spent = SpentTx(tx.hash, _input.tx_id, _input.index, old_utxo.value, tx.timestamp)
                            self.spent_txs[(_input.tx_id, _input.index)].append(spent)
                            self.balance -= old_utxo.value
                            should_update = True
                            continue

                    # If we dont have it in the unspent_txs, we check in the spent txs
                    # Try to find in spent tx
                    found = False
                    if key in self.spent_txs:
                        list_index = -1
                        for i, spent in enumerate(self.spent_txs[key]):
                            if (spent.tx_id == tx.hash and
                                    spent.from_index == _input.index and
                                    spent.from_tx_id == _input.tx_id):
                                list_index = i
                                break

                        if list_index > -1:
                            found = True

                    if not found:
                        # If spent not found, we recreate it
                        # Get tx from output to get the value
                        output_tx = tx.storage.get_transaction(_input.tx_id)
                        output = output_tx.outputs[_input.index]

                        spent = SpentTx(tx.hash, _input.tx_id, _input.index, output.value, tx.timestamp)
                        self.spent_txs[key].append(spent)
                        should_update = True

        if should_update:
            # publish new balance
            self.publish_update(HathorEvents.WALLET_BALANCE_UPDATED, balance=self.balance)
            # publish update history
            self.publish_update(HathorEvents.WALLET_HISTORY_UPDATED)

    def save_history_to_file(self):
        data = {}
        data['balance'] = self.balance
        data['unspent_txs'] = unspent_txs = {}

        data['spent_txs'] = {}
        for k, v in self.spent_txs.items():
            k = (k[0].hex(), k[1])
            data['spent_txs'][json.dumps(list(k))] = [spent_tx.to_dict() for spent_tx in v]

        for address_b58, utxo_list in self.unspent_txs.items():
            unspent_txs[address_b58] = [utxo.to_dict() for utxo in utxo_list]

        with open(self.history_path, 'w') as json_file:
            json_file.write(json.dumps(data, indent=4))

    def read_history_from_file(self):
        json_data = {}
        with open(self.history_path, 'r') as json_file:
            json_data = json.loads(json_file.read())

        self.balance = json_data['balance']

        for k, v in json_data['spent_txs'].items():
            key = tuple(json.loads(k))
            k = (bytes.fromhex(key[0]), k[1])
            self.spent_txs[key] = [SpentTx.from_dict(data) for data in v]

        self.unspent_txs = {}
        for address_b58, utxo_list in json_data['unspent_txs'].items():
            self.unspent_txs[address_b58] = [UnspentTx.from_dict(utxo) for utxo in utxo_list]

    def replay_from_storage(self, tx_storage):
        """Builds this wallet's state based on all transactions in the storage
        """

        self.unspent_txs = {}
        self.spent_txs = defaultdict(list)
        self.balance = 0

        # TODO we won't be able to hold all transactions in memory in the future
        all_txs = tx_storage.get_all_transactions()

        # XXX can we trust tx timestamp to order the transactions? This ordering is
        # important to the wallet. If it replays the transactions in wrong order,
        # an exception may happen
        ordered_txs = sorted(all_txs, key=lambda t: t.timestamp, reverse=False)

        for tx in ordered_txs:
            self.on_new_tx(tx)

        # TODO update history file?

    def get_history(self, count=10, page=1):
        """Return the last transactions in this wallet ordered by timestamp and the total

        :rtype: tuple[list[SpentTx, UnspentTx], int]
        """
        history = []
        unspent = self.unspent_txs.values()
        for obj in unspent:
            history += obj

        for obj in self.spent_txs.values():
            history += obj

        for obj in self.voided_unspent.values():
            history += obj

        for obj in self.voided_spent.values():
            history += obj

        ordered_history = sorted(history, key=lambda el: el.timestamp, reverse=True)

        total = len(ordered_history)
        start_index = (page - 1) * count
        end_index = start_index + count

        return ordered_history[start_index:end_index], total

    def get_total_tx(self):
        """Return the total number of transactions that happened in this wallet (to and from the wallet)

        :rtype: int
        """
        total_unspent = sum([len(arr) for arr in self.unspent_txs.values()])
        return total_unspent + len(self.spent_txs)

    def publish_update(self, event, **kwargs):
        """ Executes pubsub publish if pubsub is defined in the Wallet
        """
        if self.pubsub:
            self.pubsub.publish(event, **kwargs)


class UnspentTx:
    def __init__(self, tx_id, index, value, timestamp, voided=False):
        self.tx_id = tx_id
        self.index = index
        self.value = value
        self.timestamp = timestamp
        self.voided = voided

    def to_dict(self):
        data = {}
        data['timestamp'] = self.timestamp
        data['tx_id'] = self.tx_id.hex()
        data['index'] = self.index
        data['value'] = self.value
        data['voided'] = self.voided
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(
            bytes.fromhex(data['tx_id']),
            data['index'],
            data['value'],
            data['timestamp']
        )


class SpentTx:
    def __init__(self, tx_id, from_tx_id, from_index, value, timestamp, voided=False):
        """tx_id: the tx spending the output
        from_tx_id: tx where we received the tokens
        from_index: index in the above tx
        """
        self.tx_id = tx_id
        self.from_tx_id = from_tx_id
        self.from_index = from_index
        self.value = value
        self.timestamp = timestamp
        self.voided = voided

    def to_dict(self):
        data = {}
        data['timestamp'] = self.timestamp
        data['tx_id'] = self.tx_id.hex()
        data['from_tx_id'] = self.from_tx_id.hex()
        data['from_index'] = self.from_index
        data['value'] = self.value
        data['voided'] = self.voided
        return data

    @classmethod
    def from_dict(cls, data):
        return cls(
            bytes.fromhex(data['tx_id']),
            bytes.fromhex(data['from_tx_id']),
            data['from_index'],
            data['value'],
            data['timestamp']
        )
