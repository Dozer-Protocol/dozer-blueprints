import json

from hathor.p2p.node_sync import NodeSyncTimestamp
from hathor.p2p.peer_id import PeerId
from hathor.p2p.protocol import HathorProtocol
from tests import unittest
from tests.utils import FakeConnection, add_new_block


class HathorProtocolTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.network = 'testnet'

        self.peer_id1 = PeerId()
        self.peer_id2 = PeerId()
        self.manager1 = self.create_peer(self.network, peer_id=self.peer_id1)
        self.manager2 = self.create_peer(self.network, peer_id=self.peer_id2)

        self.conn1 = FakeConnection(self.manager1, self.manager2)

    def assertIsConnected(self, conn=None):
        if conn is None:
            conn = self.conn1
        self.assertFalse(conn.tr1.disconnecting)
        self.assertFalse(conn.tr2.disconnecting)

    def _send_cmd(self, proto, cmd, payload=None):
        if not payload:
            line = '{}\r\n'.format(cmd)
        else:
            line = '{} {}\r\n'.format(cmd, payload)

        if isinstance(line, str):
            line = line.encode('utf-8')

        proto.dataReceived(line)

    def _check_result_only_cmd(self, result, expected_cmd):
        cmd_list = []
        for line in result.split(b'\r\n'):
            cmd, _, _ = line.partition(b' ')
            cmd_list.append(cmd)
        self.assertIn(expected_cmd, cmd_list)

    def _check_cmd_and_value(self, result, expected):
        result_list = []
        for line in result.split(b'\r\n'):
            cmd, _, data = line.partition(b' ')
            result_list.append((cmd, data))
        self.assertIn(expected, result_list)

    def test_on_connect(self):
        self._check_result_only_cmd(self.conn1.tr1.value(), b'HELLO')

    def test_invalid_command(self):
        self._send_cmd(self.conn1.proto1, 'INVALID-CMD')
        self.conn1.proto1.state.handle_error('')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_rate_limit(self):
        hits = 1
        window = 60
        self.conn1.proto1.ratelimit.set_limit(HathorProtocol.RateLimitKeys.GLOBAL, hits, window)
        # First will be ignored
        self._send_cmd(self.conn1.proto1, 'HELLO')
        # Second will reach limit
        self._send_cmd(self.conn1.proto1, 'HELLO')

        self._check_cmd_and_value(
            self.conn1.tr1.value(),
            (b'THROTTLE', 'global At most {} hits every {} seconds'.format(hits, window).encode('utf-8')))

        self.conn1.proto1.state.handle_throttle(b'')

        # Test empty disconnect
        self.conn1.proto1.state = None
        self.conn1.proto1.connections = None
        self.conn1.proto1.on_disconnect('')

    def test_invalid_size(self):
        self.conn1.tr1.clear()
        # Creating big payload
        big_payload = '['
        for x in range(65536):
            big_payload = '{}{}'.format(big_payload, x)
        big_payload = '{}]'.format(big_payload)
        self._send_cmd(self.conn1.proto1, 'HELLO', big_payload)
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_invalid_payload(self):
        self.conn1.run_one_step()
        with self.assertRaises(json.decoder.JSONDecodeError):
            self._send_cmd(self.conn1.proto1, 'PEER-ID', 'abc')

    def test_invalid_hello1(self):
        self.conn1.tr1.clear()
        self._send_cmd(self.conn1.proto1, 'HELLO')
        self._check_result_only_cmd(self.conn1.tr1.value(), b'ERROR')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_invalid_hello2(self):
        self.conn1.tr1.clear()
        self._send_cmd(self.conn1.proto1, 'HELLO', 'invalid_payload')
        self._check_result_only_cmd(self.conn1.tr1.value(), b'ERROR')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_invalid_hello3(self):
        self.conn1.tr1.clear()
        self._send_cmd(self.conn1.proto1, 'HELLO', '{}')
        self._check_result_only_cmd(self.conn1.tr1.value(), b'ERROR')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_invalid_hello4(self):
        self.conn1.tr1.clear()
        self._send_cmd(self.conn1.proto1, 'HELLO', '{"app": 0, "remote_address": 1, "network": 2, "nonce": 3}')
        self._check_result_only_cmd(self.conn1.tr1.value(), b'ERROR')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_valid_hello(self):
        self.conn1.run_one_step()
        self._check_result_only_cmd(self.conn1.tr1.value(), b'PEER-ID')
        self._check_result_only_cmd(self.conn1.tr2.value(), b'PEER-ID')
        self.assertFalse(self.conn1.tr1.disconnecting)
        self.assertFalse(self.conn1.tr2.disconnecting)

    def test_invalid_peer_id(self):
        self.conn1.run_one_step()
        self._send_cmd(self.conn1.proto1, 'PEER-ID', '{"nonce": 0}')
        self._check_result_only_cmd(self.conn1.tr1.value(), b'ERROR')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_invalid_peer_id2(self):
        self.conn1.run_one_step()
        data = self.manager2.my_peer.to_json()
        data['nonce'] = self.conn1.proto1.hello_nonce_sent
        data['signature'] = 'MTIz'
        self._send_cmd(self.conn1.proto1, 'PEER-ID', json.dumps(data))
        self._check_result_only_cmd(self.conn1.tr1.value(), b'ERROR')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_invalid_same_peer_id(self):
        manager3 = self.create_peer(self.network, peer_id=self.peer_id1)
        conn = FakeConnection(self.manager1, manager3)
        conn.run_one_step()
        conn.run_one_step()
        self._check_result_only_cmd(conn.tr1.value(), b'ERROR')
        self.assertTrue(conn.tr1.disconnecting)

    def test_invalid_different_network(self):
        manager3 = self.create_peer(network='mainnet')
        conn = FakeConnection(self.manager1, manager3)
        conn.run_one_step()
        self._check_result_only_cmd(conn.tr1.value(), b'ERROR')
        self.assertTrue(conn.tr1.disconnecting)
        conn.run_one_step()

    def test_valid_hello_and_peer_id(self):
        self.conn1.run_one_step()
        self.conn1.run_one_step()
        # Originally, only a GET-PEERS message would be received, but now it is receiving two messages in a row.
        # self._check_result_only_cmd(self.tr1.value(), b'GET-PEERS')
        # self._check_result_only_cmd(self.tr2.value(), b'GET-PEERS')
        self.assertIsConnected()
        self.conn1.run_one_step()
        self.conn1.run_one_step()
        self.assertIsConnected()

    def test_send_ping(self):
        self.conn1.run_one_step()
        self.conn1.run_one_step()
        # Originally, only a GET-PEERS message would be received, but now it is receiving two messages in a row.
        # self._check_result_only_cmd(self.tr1.value(), b'GET-PEERS')
        # self._check_result_only_cmd(self.tr2.value(), b'GET-PEERS')
        self.assertIsConnected()
        self.clock.advance(5)
        self.assertTrue(b'PING\r\n' in self.conn1.tr1.value())
        self.assertTrue(b'PING\r\n' in self.conn1.tr2.value())
        self.conn1.run_one_step()
        self.assertTrue(b'PONG\r\n' in self.conn1.tr1.value())
        self.assertTrue(b'PONG\r\n' in self.conn1.tr2.value())
        while b'PONG\r\n' in self.conn1.tr1.value():
            self.conn1.run_one_step()
        self.assertEqual(self.clock.seconds(), self.conn1.proto1.last_message)

    def test_send_invalid_unicode(self):
        # \xff is an invalid unicode.
        self.conn1.proto1.dataReceived(b'\xff\r\n')
        self.assertTrue(self.conn1.tr1.disconnecting)

    def test_on_disconnect(self):
        self.assertIn(self.conn1.proto1, self.manager1.connections.handshaking_peers)
        self.conn1.disconnect('Testing')
        self.assertNotIn(self.conn1.proto1, self.manager1.connections.handshaking_peers)

    def test_on_disconnect_after_hello(self):
        self.conn1.run_one_step()
        self.assertIn(self.conn1.proto1, self.manager1.connections.handshaking_peers)
        self.conn1.disconnect('Testing')
        self.assertNotIn(self.conn1.proto1, self.manager1.connections.handshaking_peers)

    def test_on_disconnect_after_peer_id(self):
        self.conn1.run_one_step()
        self.assertIn(self.conn1.proto1, self.manager1.connections.handshaking_peers)
        self.conn1.run_one_step()
        self.assertIn(self.conn1.proto1, self.manager1.connections.connected_peers.values())
        self.assertNotIn(self.conn1.proto1, self.manager1.connections.handshaking_peers)
        self.conn1.disconnect('Testing')
        self.assertNotIn(self.conn1.proto1, self.manager1.connections.connected_peers.values())

    def test_two_connections(self):
        self.conn1.run_one_step()  # HELLO
        self.conn1.run_one_step()  # PEER-ID
        self.conn1.run_one_step()  # GET-PEERS
        self.conn1.run_one_step()  # GET-TIPS

        manager3 = self.create_peer(self.network)
        conn = FakeConnection(self.manager1, manager3)
        conn.run_one_step()  # HELLO
        conn.run_one_step()  # PEER-ID

        self._check_result_only_cmd(self.conn1.tr1.value(), b'PEERS')
        self.conn1.run_one_step()

    def test_notify_data(self):
        self.conn1.run_one_step()  # HELLO
        self.conn1.run_one_step()  # PEER-ID
        self.conn1.run_one_step()  # GET-PEERS
        self.conn1.run_one_step()  # GET-TIPS
        self.conn1.run_one_step()  # READY

        node_sync = NodeSyncTimestamp(self.conn1.proto1, reactor=self.manager1.reactor)
        block = add_new_block(self.manager2, advance_clock=1)

        node_sync.send_notify_data(block)
        full_payload = self.conn1.tr1.value().split(b'\r\n')[1]
        cmd = full_payload.split()[0].decode('utf-8')
        payload = b" ".join(full_payload.split()[1:]).decode('utf-8')
        self._send_cmd(self.conn1.proto1, cmd, payload)
        self._check_cmd_and_value(self.conn1.tr1.value(), (b'GET-DATA', block.hash.hex().encode('utf-8')))

        # Testing deferred exceptions
        node_sync.deferred_by_key['next'] = 1
        with self.assertRaises(Exception):
            node_sync.get_peer_next()

        node_sync.deferred_by_key['tips'] = 1
        with self.assertRaises(Exception):
            node_sync.get_peer_tips()

        node_sync.deferred_by_key['get-data-12'] = 1
        with self.assertRaises(Exception):
            node_sync.get_data(bytes.fromhex('12'))

        # Just to test specific part of code
        self.conn1.proto1.state.lc_ping.running = False
        self.conn1.disconnect('Testing')
