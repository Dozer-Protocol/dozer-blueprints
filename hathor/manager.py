import datetime
import random
import time
from enum import Enum
from math import log
from typing import Any, List, Optional, cast

from twisted.internet.interfaces import IReactorCore
from twisted.logger import Logger

from hathor.constants import (
    AVG_TIME_BETWEEN_BLOCKS,
    BLOCK_DIFFICULTY_MAX_DEPTH,
    BLOCK_DIFFICULTY_MAX_DW,
    BLOCK_DIFFICULTY_N_BLOCKS,
    DECIMAL_PLACES,
    MIN_WEIGHT,
    TOKENS_PER_BLOCK,
)
from hathor.p2p.peer_discovery import PeerDiscovery
from hathor.p2p.peer_id import PeerId
from hathor.p2p.protocol import HathorProtocol
from hathor.pubsub import HathorEvents, PubSubManager
from hathor.transaction import BaseTransaction, Block, Transaction, TxOutput, sum_weights
from hathor.transaction.exceptions import TxValidationError
from hathor.transaction.storage import TransactionStorage
from hathor.wallet import BaseWallet


class HathorManager:
    """ HathorManager manages the node with the help of other specialized classes.

    Its primary objective is to handle DAG-related matters, ensuring that the DAG is always valid and connected.
    """
    log = Logger()

    class NodeState(Enum):
        # This node is still initializing
        INITIALIZING = 'INITIALIZING'

        # This node is ready to establish new connections, sync, and exchange transactions.
        READY = 'READY'

    def __init__(self, reactor: IReactorCore, peer_id: Optional[PeerId] = None, network: Optional[str] = None,
                 hostname: Optional[str] = None, pubsub: Optional[PubSubManager] = None,
                 wallet: Optional[BaseWallet] = None, tx_storage: Optional[TransactionStorage] = None,
                 peer_storage: Optional[Any] = None, default_port: int = 40403) -> None:
        """
        :param reactor: Twisted reactor which handles the mainloop and the events.
        :param peer_id: Id of this node. If not given, a new one is created.
        :param network: Name of the network this node participates. Usually it is either testnet or mainnet.
        :type network: string

        :param hostname: The hostname of this node. It is used to generate its entrypoints.
        :type hostname: string

        :param pubsub: If not given, a new one is created.
        :type pubsub: :py:class:`hathor.pubsub.PubSubManager`

        :param tx_storage: If not given, a :py:class:`TransactionMemoryStorage` one is created.
        :type tx_storage: :py:class:`hathor.transaction.storage.transaction_storage.TransactionStorage`

        :param peer_storage: If not given, a new one is created.
        :type peer_storage: :py:class:`hathor.p2p.peer_storage.PeerStorage`

        :param default_port: Network default port. It is used when only ip addresses are discovered.
        :type default_port: int
        """
        from hathor.p2p.factory import HathorServerFactory, HathorClientFactory
        from hathor.p2p.manager import ConnectionsManager
        from hathor.transaction.storage.memory_storage import TransactionMemoryStorage
        from hathor.metrics import Metrics

        self.reactor = reactor
        if hasattr(self.reactor, 'addSystemEventTrigger'):
            self.reactor.addSystemEventTrigger('after', 'shutdown', self.stop)

        self.state: Optional[HathorManager.NodeState] = None
        self.profiler: Optional[Any] = None

        # Hostname, used to be accessed by other peers.
        self.hostname = hostname

        # Remote address, which can be different from local address.
        self.remote_address = None

        self.my_peer = peer_id or PeerId()
        self.network = network or 'testnet'

        # XXX Should we use a singleton or a new PeerStorage? [msbrogli 2018-08-29]
        self.pubsub = pubsub or PubSubManager()
        self.tx_storage = tx_storage or TransactionMemoryStorage()
        self.tx_storage.pubsub = self.pubsub

        self.avg_time_between_blocks = AVG_TIME_BETWEEN_BLOCKS  # in seconds
        self.min_block_weight = MIN_WEIGHT
        self.tokens_issued_per_block = TOKENS_PER_BLOCK * (10**DECIMAL_PLACES)

        self.max_future_timestamp_allowed = 3600  # in seconds

        self.metrics = Metrics(
            pubsub=self.pubsub,
            avg_time_between_blocks=self.avg_time_between_blocks,
            tx_storage=tx_storage,
            reactor=self.reactor,
        )

        self.peer_discoveries: List[PeerDiscovery] = []

        self.server_factory = HathorServerFactory(self.network, self.my_peer, node=self)
        self.client_factory = HathorClientFactory(self.network, self.my_peer, node=self)
        self.connections = ConnectionsManager(self.reactor, self.my_peer, self.server_factory, self.client_factory,
                                              self.pubsub)

        self.wallet = wallet
        if self.wallet:
            self.wallet.pubsub = self.pubsub
            self.wallet.reactor = self.reactor

        # When manager is in test mode we exclude some verifications
        self.test_mode = False

        # Multiplier coefficient to adjust the minimum weight of a normal tx to 18
        self.min_tx_weight_coefficient = 1.6

    def start(self) -> None:
        """ A factory must be started only once. And it is usually automatically started.
        """
        self.log.info('Starting HathorManager...')
        self.state = self.NodeState.INITIALIZING
        self.pubsub.publish(HathorEvents.MANAGER_ON_START)
        self.connections.start()

        # Initialize manager's components.
        self._initialize_components()

        for peer_discovery in self.peer_discoveries:
            peer_discovery.discover_and_connect(self.connections.connect_to)

        self.start_time = time.time()

        # Metric starts to capture data
        self.metrics.start()

        if self.wallet:
            self.wallet.start()

    def stop(self) -> None:
        self.log.info('Stopping HathorManager...')
        self.connections.stop()
        self.pubsub.publish(HathorEvents.MANAGER_ON_STOP)

        # Metric stops to capture data
        self.metrics.stop()

        if self.wallet:
            self.wallet.stop()

    def start_profiler(self):
        """
        Start profiler. It can be activated from a web resource, as well.
        """
        if not self.profiler:
            import cProfile
            self.profiler = cProfile.Profile()
        self.profiler.enable()

    def stop_profiler(self, save_to=None):
        """
        Stop the profile and optionally save the results for future analysis.

        :param save_to: path where the results will be saved
        :type save_to: str
        """
        self.profiler.disable()
        if save_to:
            self.profiler.dump_stats(save_to)

    def _initialize_components(self) -> None:
        """You are not supposed to run this method manually. You should run `doStart()` to initialize the
        manager.

        This method runs through all transactions, verifying them and updating our wallet.
        """
        self.log.info('Initializing node...')
        if self.wallet:
            self.wallet._manually_initialize()
        t0 = time.time()
        t1 = t0
        cnt = 0
        for tx in self.tx_storage._topological_sort():
            t2 = time.time()
            if t2 - t1 > 5:
                # self.start_profiler()
                ts_date = datetime.datetime.fromtimestamp(self.tx_storage.latest_timestamp)
                self.log.info('Verifying transations in storage...'
                              ' avg={:.4f} tx/s total={} (latest timedate: {})'.format(cnt / (t2 - t0), cnt, ts_date))
                t1 = t2
            cnt += 1
            self.on_new_tx(tx, quiet=True)
        # self.stop_profiler(save_to='initializing.prof')
        self.state = self.NodeState.READY
        self.log.info('Node successfully initialized ({} seconds).'.format(t2 - t0))

    def add_peer_discovery(self, peer_discovery: PeerDiscovery):
        self.peer_discoveries.append(peer_discovery)

    def get_new_tx_parents(self, timestamp: Optional[float] = None) -> List[bytes]:
        """Select which transactions will be confirmed by a new transaction.

        :return: The hashes of the parents for a new transaction.
        :rtype: List[bytes(hash)]
        """
        timestamp = timestamp or self.reactor.seconds()
        ret = list(self.tx_storage.get_tx_tips(timestamp - 1))
        random.shuffle(ret)
        ret = ret[:2]
        if len(ret) == 1:
            # If there is only one tip, let's randomly choose one of its parents.
            parents = list(self.tx_storage.get_tx_tips(ret[0].begin - 1))
            ret.append(random.choice(parents))
        assert len(ret) == 2, 'timestamp={} tips={}'.format(
            timestamp, [x.hex() for x in self.tx_storage.get_tx_tips(timestamp - 1)])
        return [x.data for x in ret]

    def generate_mining_block(self, timestamp: Optional[float] = None,
                              parent_block_hash: Optional[bytes] = None, version: int = 1) -> Block:
        """ Generates a block ready to be mined. The block includes new issued tokens,
        parents, and the weight.

        :return: A block ready to be mined
        :rtype: :py:class:`hathor.transaction.Block`
        """
        from hathor.transaction.scripts import create_output_script

        assert self.wallet is not None
        address = self.wallet.get_unused_address_bytes(mark_as_used=False)
        amount = self.tokens_issued_per_block
        output_script = create_output_script(address)
        tx_outputs = [TxOutput(amount, output_script)]

        if not timestamp:
            timestamp = max(self.tx_storage.latest_timestamp, self.reactor.seconds())
        if parent_block_hash is None:
            tip_blocks = self.tx_storage.get_best_block_tips()
        else:
            tip_blocks = [parent_block_hash]
        tip_txs = self.get_new_tx_parents(timestamp)

        assert len(tip_blocks) >= 1
        assert len(tip_txs) == 2

        parents = [random.choice(tip_blocks)] + tip_txs

        parents_tx = [self.tx_storage.get_transaction(x) for x in parents]
        new_height = max(x.height for x in parents_tx) + 1

        timestamp1 = int(self.reactor.seconds())
        timestamp2 = max(x.timestamp for x in parents_tx) + 1

        blk = Block(version=version, outputs=tx_outputs, parents=parents, storage=self.tx_storage, height=new_height)
        blk.timestamp = max(timestamp1, timestamp2)
        blk.weight = self.calculate_block_difficulty(blk)
        return blk

    def validate_new_tx(self, tx: BaseTransaction) -> bool:
        """ Process incoming transaction during initialization.
        These transactions came only from storage.
        """
        assert tx.hash is not None

        if self.state != self.NodeState.INITIALIZING:
            if tx.is_genesis:
                self.log.debug('validate_new_tx(): Genesis? {}'.format(tx.hash.hex()))
                return False

            if self.tx_storage.transaction_exists(tx.hash):
                self.log.debug('validate_new_tx(): Already have transaction {}'.format(tx.hash.hex()))
                return False

        else:
            if tx.is_genesis:
                return True

        if tx.timestamp - self.reactor.seconds() > self.max_future_timestamp_allowed:
            self.log.debug('validate_new_tx(): Ignoring transaction in the future {}'.format(tx.hash.hex()))
            return False

        try:
            tx.verify()
        except TxValidationError as e:
            self.log.debug('validate_new_tx(): Error verifying transaction {} tx={}'.format(repr(e), tx.hash.hex()))
            return False

        if tx.is_block:
            tx = cast(Block, tx)
            assert tx.hash is not None  # XXX: it appears that after casting this assert "casting" is lost

            # Validate minimum block difficulty
            block_weight = self.calculate_block_difficulty(tx)
            if tx.weight < block_weight:
                self.log.debug('Invalid new block {}: weight ({}) is smaller than the minimum weight ({})'.format(
                    tx.hash.hex(), tx.weight, block_weight))
                return False
            if tx.sum_outputs != self.tokens_issued_per_block:
                self.log.info(
                    'Invalid number of issued tokens tag=invalid_issued_tokens'
                    ' tx.hash={tx.hash_hex} issued={tx.sum_outputs} allowed={allowed}',
                    tx=tx,
                    allowed=self.tokens_issued_per_block,
                )
                return False
        else:
            assert tx.hash is not None  # XXX: it appears that after casting this assert "casting" is lost

            # Validate minimum tx difficulty
            min_tx_weight = self.minimum_tx_weight(tx)
            if tx.weight < min_tx_weight:
                self.log.debug('Invalid new tx {}: weight ({}) is smaller than the minimum weight ({})'.format(
                    tx.hash.hex(), tx.weight, min_tx_weight))
                return False

        return True

    def propagate_tx(self, tx: BaseTransaction) -> bool:
        """Push a new transaction to the network. It is used by both the wallet and the mining modules.

        :return: True if the transaction was accepted
        :rtype: bool
        """
        if tx.storage:
            assert tx.storage == self.tx_storage, 'Invalid tx storage'
        else:
            tx.storage = self.tx_storage
        return self.on_new_tx(tx)

    def on_new_tx(self, tx: BaseTransaction, conn: Optional[HathorProtocol] = None, quiet: bool = False) -> bool:
        """This method is called when any transaction arrive.

        :return: True if the transaction was accepted
        :rtype: bool
        """
        if not self.validate_new_tx(tx):
            # Discard invalid Transaction/block.
            self.log.debug('Transaction/Block discarded {}'.format(tx.hash_hex))
            return False

        if self.wallet:
            self.wallet.on_new_tx(tx)

        if self.state != self.NodeState.INITIALIZING:
            self.tx_storage.save_transaction(tx)
        else:
            self.tx_storage._add_to_cache(tx)

        tx.update_parents()

        if not quiet:
            ts_date = datetime.datetime.fromtimestamp(tx.timestamp)
            if tx.is_block:
                self.log.info(
                    'New block found tag=new_block hash={tx.hash_hex}'
                    ' weight={tx.weight} timestamp={tx.timestamp} datetime={ts_date} from_now={time_from_now}', tx=tx,
                    ts_date=ts_date, time_from_now=tx.get_time_from_now())
            else:
                self.log.info(
                    'New transaction tag=new_tx hash={tx.hash_hex}'
                    ' timestamp={tx.timestamp} datetime={ts_date} from_now={time_from_now}', tx=tx, ts_date=ts_date,
                    time_from_now=tx.get_time_from_now())

        if tx.is_block:
            assert isinstance(tx, Block)
            tx.update_voided_info()
        else:
            assert isinstance(tx, Transaction)
            tx.mark_inputs_as_used()
            tx.update_voided_info()
            tx.set_conflict_twins()

        # Propagate to our peers.
        self.connections.send_tx_to_peers(tx)

        # Publish to pubsub manager the new tx accepted
        self.pubsub.publish(HathorEvents.NETWORK_NEW_TX_ACCEPTED, tx=tx)

        return True

    def calculate_block_difficulty(self, block: Block) -> float:
        """ Calculate block difficulty according to the ascendents of `block`.

        The new difficulty is calculated so that the average time between blocks will be
        `self.avg_time_between_blocks`. If the measured time between blocks is smaller than the target,
        the weight increases. If it is higher than the target, the weight decreases.

        The new difficulty cannot be smaller than `self.min_block_weight`.
        """
        assert isinstance(block, Block)

        # In test mode we don't validate the block difficulty
        if self.test_mode:
            return 1

        if block.is_genesis:
            return self.min_block_weight

        hash_algorithm = block.hash_algorithm
        algorithms_found = set()

        n_target = BLOCK_DIFFICULTY_N_BLOCKS
        max_depth = BLOCK_DIFFICULTY_MAX_DEPTH

        current = self.tx_storage.get_transaction(block.parents[0])
        blocks = []
        for _ in range(max_depth):
            assert isinstance(current, Block)
            algorithms_found.add(current.hash_algorithm)
            if current.hash_algorithm == hash_algorithm:
                blocks.append(current)
                if len(blocks) == n_target:
                    break
            if len(current.parents) == 0:
                # We've reached genesis.
                assert current.is_genesis
                break
            current = self.tx_storage.get_transaction(current.parents[0])

        if len(blocks) == 0:
            return self.min_block_weight

        if blocks[-1].is_genesis:
            blocks.pop()

        if len(blocks) <= 1:
            return self.min_block_weight

        logH = 0.0
        for blk in blocks:
            logH = sum_weights(logH, blk.weight)

        dt = blocks[0].timestamp - blocks[-1].timestamp
        assert dt > 0

        weight = logH - log(dt, 2) + log(self.avg_time_between_blocks, 2) + log(len(algorithms_found), 2)

        # Apply a maximum change in difficulty.
        max_dw = BLOCK_DIFFICULTY_MAX_DW
        dw = weight - blocks[0].weight
        if dw > max_dw:
            weight = blocks[0].weight + max_dw
        elif dw < -max_dw:
            weight = blocks[0].weight - max_dw

        if weight < self.min_block_weight:
            weight = self.min_block_weight

        return weight

    def minimum_tx_weight(self, tx: BaseTransaction) -> float:
        """ Returns the minimum weight for the param tx
            The minimum is calculated by the following function:

            w = log(size, 2) + log(amount, 2) + 0.5

            :param tx: tx to calculate the minimum weight
            :type tx: :py:class:`hathor.transaction.transaction.Transaction`

            :return: minimum weight for the tx
            :rtype: float
        """
        # In test mode we don't validate the minimum weight for tx
        # We do this to allow generating many txs for testing
        if self.test_mode:
            return 1

        if tx.is_genesis:
            return MIN_WEIGHT

        tx_size = len(tx.get_struct())

        # We need to remove the decimal places because it is in the amount
        # If you want to transfer 20 hathors, the amount will be 2000, that's why we reduce the log of decimal places
        weight = (self.min_tx_weight_coefficient * log(tx_size, 2) + log(tx.sum_outputs, 2) - log(
            10**DECIMAL_PLACES, 2) + 0.5)

        # Make sure the calculated weight is at least the minimum
        weight = max(weight, MIN_WEIGHT)

        return weight

    def listen(self, description, ssl=False):
        endpoint = self.connections.listen(description, ssl)

        if self.hostname:
            proto, _, _ = description.partition(':')
            address = '{}:{}:{}'.format(proto, self.hostname, endpoint._port)
            self.my_peer.entrypoints.append(address)
