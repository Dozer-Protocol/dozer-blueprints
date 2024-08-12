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

from collections import defaultdict, deque
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Optional

from structlog import get_logger
from twisted.internet.interfaces import IDelayedCall, IReactorFromThreads
from twisted.python.threadable import isInIOThread

from hathor.reactor import ReactorProtocol as Reactor
from hathor.utils.zope import verified_cast

if TYPE_CHECKING:
    from hathor.transaction import BaseTransaction, Block

logger = get_logger()


class HathorEvents(Enum):
    """
        NETWORK_NEW_TX_ACCEPTED:
            Triggered when a new tx/block is accepted in the network
            Publishes a tx/block object

        NETWORK_PEER_CONNECTING:
            Triggered when a peer starts connecting to the network
            Publishes the peer id and the peers count

        NETWORK_PEER_CONNECTION_FAILURE:
            Triggered when a peer connection to the network fails
            Publishes the peer id and the peers count

        NETWORK_PEER_CONNECTED:
            Triggered when a new peer connects to the network
            Publishes the peer protocol and the peers count

        NETWORK_PEER_READY:
            Triggered when a connected peer is ready
            Publishes the peer protocol and the peers count

        NETWORK_PEER_DISCONNECTED:
            Triggered when a peer disconnects from the network
            Publishes the peer protocol and the peers count

        CONSENSUS_TX_UPDATE:
            Triggered when a tx is changed by the consensus algorithm
            Publishes the tx object

        CONSENSUS_TX_REMOVED:
            Triggered when a tx is removed because it became invalid (due to a reward lock check)
            Publishes the tx object

        WALLET_OUTPUT_RECEIVED:
            Triggered when a wallet receives a new output
            Publishes an UnspentTx object and the new total number of tx in the Wallet (total=int, output=UnspentTx)

        WALLET_INPUT_SPENT:
            Triggered when a wallet spends an output
            Publishes a SpentTx object (output_spent=SpentTx)

        WALLET_BALANCE_UPDATED:
            Triggered when the balance of the wallet changes
            Publishes a hathor.wallet.base_wallet.WalletBalance namedtuple (locked, available)

        WALLET_KEYS_GENERATED:
            Triggered when new keys are generated by the wallet and returns the quantity of keys generated
            Publishes an int (keys_count=int)

        WALLET_HISTORY_UPDATED:
            Triggered when the wallet history is updated by a voided/winner transaction

        WALLET_ADDRESS_HISTORY:
            Triggered when the we receive any transaction and send input/output by each address

        WALLET_ELEMENT_WINNER:
            Triggered when a wallet element is marked as winner

        WALLET_ELEMENT_VOIDED:
            Triggered when a wallet element is marked as voided

        REORG_STARTED
            Trigerred when consensus algorithm finds that a reorg started to happen

        REORG_FINISHED
            Triggered when consensus algorithm ends all changes involved in a reorg
    """
    MANAGER_ON_START = 'manager:on_start'
    MANAGER_ON_STOP = 'manager:on_stop'

    NETWORK_PEER_CONNECTING = 'network:peer_connecting'

    NETWORK_PEER_CONNECTION_FAILED = 'network:peer_connection_failed'

    NETWORK_PEER_CONNECTED = 'network:peer_connected'

    NETWORK_PEER_READY = 'network:peer_ready'

    NETWORK_PEER_DISCONNECTED = 'network:peer_disconnected'

    NETWORK_NEW_TX_ACCEPTED = 'network:new_tx_accepted'

    CONSENSUS_TX_UPDATE = 'consensus:tx_update'

    CONSENSUS_TX_REMOVED = 'consensus:tx_removed'

    WALLET_OUTPUT_RECEIVED = 'wallet:output_received'

    WALLET_INPUT_SPENT = 'wallet:output_spent'

    WALLET_BALANCE_UPDATED = 'wallet:balance_updated'

    WALLET_KEYS_GENERATED = 'wallet:keys_generated'

    WALLET_GAP_LIMIT = 'wallet:gap_limit'

    WALLET_HISTORY_UPDATED = 'wallet:history_updated'

    WALLET_ADDRESS_HISTORY = 'wallet:address_history'

    WALLET_ELEMENT_WINNER = 'wallet:element_winner'

    WALLET_ELEMENT_VOIDED = 'wallet:element_voided'

    REORG_STARTED = 'reorg:started'

    REORG_FINISHED = 'reorg:finished'


class EventArguments:
    """Simple object for storing event arguments.
    """

    # XXX: add these as needed, these attributes don't always exist, but when they do these are their types
    tx: 'BaseTransaction'
    reorg_size: int
    old_best_block: 'Block'
    new_best_block: 'Block'
    common_block: 'Block'

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


PubSubCallable = Callable[[HathorEvents, EventArguments], None]


class PubSubManager:
    """Manages a pub/sub pattern bus.

    It is used to let independent objects respond to events.
    """

    _subscribers: dict[HathorEvents, list[PubSubCallable]]

    def __init__(self, reactor: Reactor) -> None:
        self._subscribers = defaultdict(list)
        self.queue: deque[tuple[PubSubCallable, HathorEvents, EventArguments]] = deque()
        self.reactor = reactor
        self.log = logger.new()

        self._call_later_id: Optional[IDelayedCall] = None

    def subscribe(self, key: HathorEvents, fn: PubSubCallable) -> None:
        """Subscribe to a specific event.

        :param key: Name of the key to which to subscribe.
        :type key: string

        :param fn: A function to be called when an event with `key` is published.
        :type fn: function
        """
        if fn not in self._subscribers[key]:
            self._subscribers[key].append(fn)

    def unsubscribe(self, key: HathorEvents, fn: PubSubCallable) -> None:
        """Unsubscribe from a specific event.
        """
        if fn in self._subscribers[key]:
            self._subscribers[key].remove(fn)

    def _call_next(self) -> None:
        """Execute next call if it exists."""
        if not self.queue:
            return

        self.log.debug('running pubsub call_next', len=len(self.queue))

        try:
            while self.queue:
                fn, key, args = self.queue.popleft()
                fn(key, args)
        except Exception:
            self.log.error('event processing failed', key=key, args=args)
            raise
        finally:
            self._schedule_call_next()

    def _schedule_call_next(self) -> None:
        """Schedule next call's execution."""
        assert self.reactor.running

        if not self.queue:
            return

        if not isInIOThread() and (threaded_reactor := verified_cast(IReactorFromThreads, self.reactor)):
            # We're taking a conservative approach, since not all functions might need to run
            # on the main thread [yan 2019-02-20]
            threaded_reactor.callFromThread(self._call_next)
            return

        if self._call_later_id and self._call_later_id.active():
            return

        self._call_later_id = self.reactor.callLater(0, self._call_next)

    def publish(self, key: HathorEvents, **kwargs: Any) -> None:
        """Publish a new event.

        :param key: Key of the new event.
        :type key: string

        :param **kwargs: Named arguments to be given to the functions that will be called with this event.
        :type **kwargs: dict
        """
        args = EventArguments(**kwargs)
        for fn in self._subscribers[key]:
            if not self.reactor.running:
                fn(key, args)
            else:
                self.queue.append((fn, key, args))
                self._schedule_call_next()
