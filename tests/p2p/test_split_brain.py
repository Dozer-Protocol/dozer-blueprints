from twisted.python import log
from twisted.internet.task import Clock

from tests.utils import FakeConnection
from tests import unittest

import sys
import random
import time


class HathorSyncMethodsTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()

        log.startLogging(sys.stdout)
        self.clock = Clock()
        self.clock.advance(time.time())
        self.network = 'testnet'

    def _add_new_tx(self, manager, address, value):
        from hathor.transaction import Transaction
        from hathor.wallet.base_wallet import WalletOutputInfo

        outputs = []
        outputs.append(WalletOutputInfo(address=manager.wallet.decode_address(address), value=int(value)))

        tx = manager.wallet.prepare_transaction_compute_inputs(Transaction, outputs)
        tx.storage = manager.tx_storage

        max_ts_spent_tx = max(tx.get_spent_tx(txin).timestamp for txin in tx.inputs)
        tx.timestamp = max(max_ts_spent_tx + 1, int(manager.reactor.seconds()))

        tx.weight = 1
        tx.parents = manager.get_new_tx_parents(tx.timestamp)
        tx.resolve()
        tx.verify()
        manager.propagate_tx(tx)

    def _add_new_transactions(self, manager, num_txs):
        txs = []
        for _ in range(num_txs):
            address = '3JEcJKVsHddj1Td2KDjowZ1JqGF1'
            value = random.choice([5, 10, 50, 100, 120])
            tx = self._add_new_tx(manager, address, value)
            txs.append(tx)
        return txs

    def _add_new_block(self, manager):
        block = manager.generate_mining_block()
        self.assertTrue(block.resolve())
        block.verify()
        manager.propagate_tx(block)
        return block

    def _add_new_blocks(self, manager, num_blocks):
        blocks = []
        for _ in range(num_blocks):
            blocks.append(self._add_new_block(manager))
        return blocks

    def assertTipsEqual(self, manager1, manager2):
        s1 = set(manager1.tx_storage.get_tx_tips())
        s1.update(manager1.tx_storage.get_block_tips())

        s2 = set(manager2.tx_storage.get_tx_tips())
        s2.update(manager2.tx_storage.get_block_tips())

        self.assertEqual(s1, s2)

    def test_split_brain(self):
        manager1 = self.create_peer(self.network, unlock_wallet=True)
        manager1.avg_time_between_blocks = 3
        manager1.reactor = self.clock

        manager2 = self.create_peer(self.network, unlock_wallet=True)
        manager2.avg_time_between_blocks = 3
        manager2.reactor = self.clock

        for _ in range(10):
            self._add_new_block(manager1)
            self._add_new_block(manager2)
            self.clock.advance(10)
            for _ in range(random.randint(3, 10)):
                self._add_new_transactions(manager1, random.randint(2, 4))
                self._add_new_transactions(manager2, random.randint(3, 7))
                self.clock.advance(10)

        self.clock.advance(20)

        # dot1 = manager1.tx_storage.graphviz(format='pdf')
        # dot1.render('dot1')

        # dot2 = manager2.tx_storage.graphviz(format='pdf')
        # dot2.render('dot2')

        conn = FakeConnection(manager1, manager2)

        conn.run_one_step()  # HELLO
        conn.run_one_step()  # PEER-ID

        empty_counter = 0
        for i in range(1000):
            if conn.is_empty():
                empty_counter += 1
                if empty_counter > 10:
                    break
            else:
                empty_counter = 0
            conn.run_one_step(debug=True)
            self.clock.advance(0.2)

        node_sync = conn.proto1.state.get_sync_plugin()
        self.assertEqual(node_sync.synced_timestamp, node_sync.peer_timestamp)
        self.assertTipsEqual(manager1, manager2)

        # dot1 = manager1.tx_storage.graphviz(format='pdf')
        # dot1.render('dot-merged')
