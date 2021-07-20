# Copyright 2021 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Generator, List, Optional

from mnemonic import Mnemonic
from numpy.random import PCG64, Generator as Rng, SeedSequence
from structlog import get_logger

from hathor.daa import TestMode, _set_test_mode
from hathor.manager import HathorManager
from hathor.p2p.peer_id import PeerId
from hathor.simulator.clock import HeapClock
from hathor.simulator.miner import MinerSimulator
from hathor.simulator.tx_generator import RandomTransactionGenerator
from hathor.transaction.genesis import _get_genesis_transactions_unsafe
from hathor.transaction.storage.memory_storage import TransactionMemoryStorage
from hathor.wallet import HDWallet

if TYPE_CHECKING:
    from hathor.simulator.fake_connection import FakeConnection


logger = get_logger()

DEFAULT_STEP_INTERVAL: float = 0.25
DEFAULT_STATUS_INTERVAL: float = 60.0


class Simulator:
    # used to concilite monkeypatching and multiple instances
    _patches_rc: int = 0

    @classmethod
    def _apply_patches(cls):
        """ Applies global patches on modules that aren't easy/possible to configure otherwise.

        Patches:

        - disable pow verification
        - set DAA test-mode to DISABLED (will actually run the pow function, that won't actually verify the pow)
        - override AVG_TIME_BETWEEN_BLOCKS to 64
        """
        from hathor.transaction import BaseTransaction

        def verify_pow(self: BaseTransaction) -> None:
            assert self.hash is not None

        cls._original_verify_pow = BaseTransaction.verify_pow
        BaseTransaction.verify_pow = verify_pow

        _set_test_mode(TestMode.DISABLED)

        from hathor import daa
        cls._original_avg_time_between_blocks = daa.AVG_TIME_BETWEEN_BLOCKS
        daa.AVG_TIME_BETWEEN_BLOCKS = 64

    @classmethod
    def _remove_patches(cls):
        """ Remove the patches previously applied.
        """
        from hathor.transaction import BaseTransaction
        BaseTransaction.verify_pow = cls._original_verify_pow

        from hathor import daa
        daa.AVG_TIME_BETWEEN_BLOCKS = cls._original_avg_time_between_blocks

    @classmethod
    def _patches_rc_increment(cls):
        """ This is used by when starting instances of Simulator to determine when to run _apply_patches"""
        assert cls._patches_rc >= 0
        cls._patches_rc += 1
        if cls._patches_rc < 2:
            # patches not yet applied
            cls._apply_patches()

    @classmethod
    def _patches_rc_decrement(cls):
        """ This is used by when stopping instances of Simulator to determine when to run _remove_patches"""
        assert cls._patches_rc >= 0
        cls._patches_rc -= 1
        if cls._patches_rc == 0:
            # patches not needed anymore
            cls._remove_patches()

    def __init__(self, seed: Optional[int] = None):
        self.log = logger.new()
        self._seed = SeedSequence(seed)
        self.rng = Rng(PCG64(self._seed))
        # XXX: separating the rng that is passed to the nodes so it doesn't interfere with the simulator rng,
        #      this way if the node adds or removes a rng call, a seed will generate the same simulation sequence of
        #      timings and nonces, amounts, etc, but hashes might still change if there is randomness in the selection
        #      of equally valid txs/blocks
        self._node_rng = Rng(PCG64(self._seed))
        self._network = 'testnet'
        self._clock = HeapClock()
        self._peers: OrderedDict[str, 'HathorManager'] = OrderedDict()
        self._connections: List['FakeConnection'] = []
        # XXX: erasing the types of the random states is preferred as they aren't stable and may change on any version
        self._py_random_state: Optional[Any] = None
        self._numpy_random_state: Optional[Any] = None
        self._started = False

    @property
    def seed(self) -> int:
        return self._seed.entropy

    def start(self) -> None:
        """Has to be called before any other method can be called."""
        assert not self._started
        self._started = True
        self._patches_rc_increment()
        first_timestamp = min(tx.timestamp for tx in _get_genesis_transactions_unsafe(None))
        dt = self.rng.integers(3600, 120 * 24 * 3600, endpoint=True)
        self._clock.advance(first_timestamp + dt)
        self.log.debug('randomized step: clock advance start', dt=dt)

    def stop(self) -> None:
        """Can only stop after calling start, but it doesn't matter if it's paused or not"""
        assert self._started
        self._started = False
        self._patches_rc_decrement()

    def create_peer(self, network: Optional[str] = None, peer_id: Optional[PeerId] = None) -> HathorManager:
        assert self._started
        if network is None:
            network = self._network

        wallet = HDWallet(gap_limit=2)
        wallet._manually_initialize()

        assert peer_id is not None  # XXX: temporary, for checking that tests are using the peer_id
        if peer_id is None:
            peer_id = PeerId()
        tx_storage = TransactionMemoryStorage()
        manager = HathorManager(
            self._clock,
            peer_id=peer_id,
            network=network,
            wallet=wallet,
            tx_storage=tx_storage,
            rng=self._node_rng,
        )

        manager.reactor = self._clock
        manager._full_verification = True
        manager.start()
        self.run_to_completion()

        # Don't use it anywhere else. It is unsafe to generate mnemonic words like this.
        # It should be used only for testing purposes.
        m = Mnemonic('english')
        words = m.to_mnemonic(self.rng.bytes(32))
        self.log.debug('randomized step: generate wallet', words=words)
        wallet.unlock(words=words, tx_storage=manager.tx_storage)
        return manager

    def create_tx_generator(self, *args: Any, **kwargs: Any) -> RandomTransactionGenerator:
        tx_generator = RandomTransactionGenerator(*args, **kwargs)
        tx_generator.rng = self.rng
        return tx_generator

    def create_miner(self, *args: Any, **kwargs: Any) -> MinerSimulator:
        miner = MinerSimulator(*args, **kwargs)
        miner.rng = self.rng
        return miner

    def run_to_completion(self):
        """ This will advance the test's clock until all calls scheduled are done.
        """
        assert self._started
        for call in self._clock.getDelayedCalls():
            amount = max(0, call.getTime() - self._clock.seconds())
            self._clock.advance(amount)

    def add_peer(self, name: str, peer: 'HathorManager') -> None:
        assert self._started
        if name in self._peers:
            raise ValueError('Duplicate peer name')
        self._peers[name] = peer

    def get_peer(self, name: str) -> 'HathorManager':
        return self._peers[name]

    def add_connection(self, conn: 'FakeConnection') -> None:
        self._connections.append(conn)

    def _run(self, interval: float, step: float, status_interval: float) -> Generator[None, None, None]:
        """ Implementation of run, yields at every step to allow verifications like in run_until_synced
        """
        assert self._started
        initial = self._clock.seconds()
        latest_time = self._clock.seconds()
        t0 = time.time()
        while self._clock.seconds() <= initial + interval:
            for conn in self._connections:
                conn.run_one_step()
            yield
            if self._clock.seconds() - latest_time >= status_interval:
                t1 = time.time()
                # Real elapsed time.
                real_elapsed_time = t1 - t0
                # Rate is the number of simulated seconds per real second.
                # For example, a rate of 60 means that we can simulate 1 minute per second.
                rate = (self._clock.seconds() - initial) / real_elapsed_time
                # Simulation now.
                sim_now = self._clock.seconds()
                # Simulation dt.
                sim_dt = self._clock.seconds() - initial
                # Number of simulated seconds to end this run.
                sim_remaining = interval - self._clock.seconds() + initial
                # Number of call pending to be executed.
                delayed_calls = len(self._clock.getDelayedCalls())
                self.log.info('simulator: time step', real_elapsed_time=real_elapsed_time, rate=rate, sim_now=sim_now,
                              dt_step=sim_dt, dt_remaining=sim_remaining, delayed_calls=delayed_calls)
                latest_time = self._clock.seconds()
            self._clock.advance(step)

    def run(self,
            interval: float,
            step: float = DEFAULT_STEP_INTERVAL,
            status_interval: float = DEFAULT_STATUS_INTERVAL) -> None:
        assert self._started
        for _ in self._run(interval, step, status_interval):
            pass
