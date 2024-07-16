#  Copyright 2024 Hathor Labs
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from __future__ import annotations

from typing import TYPE_CHECKING

from structlog import get_logger
from twisted.internet.task import LoopingCall

from hathor.conf.settings import HathorSettings
from hathor.consensus import poa
from hathor.consensus.consensus_settings import PoaSettings
from hathor.crypto.util import get_public_key_bytes_compressed
from hathor.reactor import ReactorProtocol

if TYPE_CHECKING:
    from hathor.consensus.poa import PoaSigner
    from hathor.manager import HathorManager
    from hathor.transaction import Block
    from hathor.transaction.poa import PoaBlock

logger = get_logger()

# Number of seconds to wait for a sync to finish before trying to produce blocks
_WAIT_SYNC_DELAY: int = 30

# Number of seconds used in random delay calculation
_RANDOM_DELAY_MULTIPLIER: int = 1


class PoaBlockProducer:
    """
    This class is analogous to mining classes, but for Proof-of-Authority networks.
    It waits for blocks to arrive, gets templates, and propagates new blocks accordingly.
    """
    __slots__ = (
        '_log',
        '_settings',
        '_poa_settings',
        '_reactor',
        '_manager',
        '_poa_signer',
        '_signer_index',
        '_started_producing',
        '_start_producing_lc',
        '_schedule_block_lc',
        '_last_seen_best_block',
    )

    def __init__(
        self,
        *,
        settings: HathorSettings,
        reactor: ReactorProtocol,
        manager: 'HathorManager',
        poa_signer: PoaSigner,
    ) -> None:
        assert isinstance(settings.CONSENSUS_ALGORITHM, PoaSettings)
        self._log = logger.new()
        self._settings = settings
        self._poa_settings = settings.CONSENSUS_ALGORITHM
        self._reactor = reactor
        self._manager = manager
        self._poa_signer = poa_signer
        self._signer_index = self._calculate_signer_index(self._poa_settings, self._poa_signer)
        self._last_seen_best_block: Block | None = None

        self._started_producing = False
        self._start_producing_lc = LoopingCall(self._start_producing)
        self._start_producing_lc.clock = self._reactor

        self._schedule_block_lc = LoopingCall(self._schedule_block)
        self._schedule_block_lc.clock = self._reactor

    def start(self) -> None:
        self._start_producing_lc.start(_WAIT_SYNC_DELAY)
        self._schedule_block_lc.start(self._settings.AVG_TIME_BETWEEN_BLOCKS)

    def stop(self) -> None:
        if self._start_producing_lc.running:
            self._start_producing_lc.stop()

        if self._schedule_block_lc.running:
            self._schedule_block_lc.stop()

    @staticmethod
    def _calculate_signer_index(settings: PoaSettings, poa_signer: PoaSigner) -> int:
        """Return the signer index for the given private key."""
        public_key = poa_signer.get_public_key()
        public_key_bytes = get_public_key_bytes_compressed(public_key)
        sorted_signers = sorted(settings.signers)
        try:
            return sorted_signers.index(public_key_bytes)
        except ValueError:
            raise ValueError(f'Public key "{public_key_bytes.hex()}" not in list of PoA signers')

    def _start_producing(self) -> None:
        """Start producing new blocks."""
        if not self._manager.can_start_mining():
            # We're syncing, so we'll try again later
            self._log.warn('cannot start producing new blocks, node not synced')
            return

        self._log.info('started producing new blocks')
        self._started_producing = True
        self._start_producing_lc.stop()

    def _schedule_block(self) -> None:
        """Schedule propagation of a new block."""
        previous_block = self._manager.tx_storage.get_best_block()
        if not self._started_producing or previous_block == self._last_seen_best_block:
            return

        self._last_seen_best_block = previous_block
        now = self._reactor.seconds()
        expected_timestamp = self._expected_block_timestamp(previous_block)
        propagation_delay = 0 if expected_timestamp < now else expected_timestamp - now

        self._reactor.callLater(propagation_delay, self._produce_block, previous_block)
        self._log.debug(
            'scheduling block production',
            previous_block=previous_block.hash_hex,
            previous_block_height=previous_block.get_height(),
            delay=propagation_delay,
        )

    def _produce_block(self, previous_block: PoaBlock) -> None:
        """Create and propagate a new block."""
        from hathor.transaction.poa import PoaBlock
        block_templates = self._manager.get_block_templates(parent_block_hash=previous_block.hash)
        block = block_templates.generate_mining_block(self._manager.rng, cls=PoaBlock)
        assert isinstance(block, PoaBlock)
        block.weight = poa.calculate_weight(self._poa_settings, block, self._signer_index)
        self._poa_signer.sign_block(block)
        block.update_hash()

        self._manager.on_new_tx(block, propagate_to_peers=False, fails_silently=False)
        if not block.get_metadata().voided_by:
            self._manager.connections.send_tx_to_peers(block)

        self._log.debug(
            'produced new block',
            block=block.hash_hex,
            height=block.get_height(),
            weight=block.weight,
            parent=block.get_block_parent_hash().hex(),
            voided=bool(block.get_metadata().voided_by),
        )

    def _expected_block_timestamp(self, previous_block: Block) -> int:
        """Calculate the expected timestamp for a new block."""
        height = previous_block.get_height() + 1
        is_in_turn = poa.is_in_turn(settings=self._poa_settings, height=height, signer_index=self._signer_index)
        timestamp = previous_block.timestamp + self._settings.AVG_TIME_BETWEEN_BLOCKS
        if is_in_turn:
            return timestamp

        signer_count = len(self._poa_settings.signers)
        assert signer_count >= 1
        random_offset = self._manager.rng.choice(range(signer_count * _RANDOM_DELAY_MULTIPLIER)) + 1
        return timestamp + random_offset