from typing import cast
from unittest.mock import MagicMock, patch

from twisted.test import proto_helpers

from hathor.sysctl import Sysctl
from hathor.sysctl.exception import SysctlEntryNotFound, SysctlReadOnlyEntry, SysctlWriteOnlyEntry
from hathor.sysctl.factory import SysctlFactory
from hathor.sysctl.sysctl import SysctlCommand
from tests import unittest


class SysctlTest(unittest.TestCase):
    # We need this patch because pydantic.validate_arguments fails when it gets a mock function.
    @patch('hathor.sysctl.sysctl.validate_arguments', new=lambda x: x)
    def setUp(self) -> None:
        super().setUp()

        net = Sysctl()
        net.register(
            'max_connections',
            MagicMock(return_value=3),  # int
            MagicMock(),
        )
        net.register(
            'readonly',
            MagicMock(return_value=0.25),  # float
            None,
        )
        net.register(
            'rate_limit',
            MagicMock(return_value=(4, 1)),  # Tuple[int, float]
            MagicMock(),
        )
        core = Sysctl()
        core.register(
            'loglevel',
            MagicMock(return_value='info'),  # str
            MagicMock(),
        )
        core.register(
            'writeonly',
            None,
            MagicMock(),  # int
        )

        multi = Sysctl()
        multi.register(
            'useless',
            None,
            None,
        )

        self.root = Sysctl()
        self.root.put_child('net', net)
        self.root.put_child('core', core)
        self.root.put_child('ab.bc.cd', multi)

        factory = SysctlFactory(self.root)
        self.proto = factory.buildProtocol(('127.0.0.1', 0))
        self.tr = proto_helpers.StringTransport()
        self.proto.makeConnection(self.tr)

    ##############
    # Get
    ##############

    def test_get_int(self) -> None:
        self.assertEqual(3, self.root.get('net.max_connections'))

    def test_get_str(self) -> None:
        self.assertEqual('info', self.root.get('core.loglevel'))

    def test_get_readonly(self) -> None:
        self.assertEqual(0.25, self.root.get('net.readonly'))

    def test_get_tuple(self) -> None:
        self.assertEqual((4, 1), self.root.get('net.rate_limit'))

    def test_get_unknown(self) -> None:
        with self.assertRaises(SysctlEntryNotFound):
            self.root.get('net.unknown')

    def test_get_writeonly(self) -> None:
        with self.assertRaises(SysctlWriteOnlyEntry):
            self.root.get('core.writeonly')

    ##############
    # Set
    ##############

    def test_set_int(self) -> None:
        self.root.set('net.max_connections', 3)
        setter = cast(MagicMock, self.root._get_setter('net.max_connections'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual((3,), setter.call_args.args)

    def test_set_str(self) -> None:
        self.root.set('core.loglevel', 'debug')
        setter = cast(MagicMock, self.root._get_setter('core.loglevel'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual(('debug',), setter.call_args.args)

    def test_set_readonly(self) -> None:
        with self.assertRaises(SysctlReadOnlyEntry):
            self.root.set('net.readonly', 0.50)

    def test_set_tuple(self) -> None:
        self.root.set('net.rate_limit', (8, 2))
        setter = cast(MagicMock, self.root._get_setter('net.rate_limit'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual((8, 2), setter.call_args.args)

    def test_set_unknown(self) -> None:
        with self.assertRaises(SysctlEntryNotFound):
            self.root.set('net.unknown', 1)

    def test_set_writeonly(self) -> None:
        self.root.set('core.writeonly', 1)
        setter = cast(MagicMock, self.root._get_setter('core.writeonly'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual((1,), setter.call_args.args)

    ##############
    # Others
    ##############

    def test_get_command(self) -> None:
        cmd = self.root.get_command('ab.bc.cd.useless')
        self.assertEqual(cmd, SysctlCommand(None, None))

        with self.assertRaises(SysctlEntryNotFound):
            cmd = self.root.get_command('ab.bc.c.useless')

    def test_get_all(self) -> None:
        all_items = set(self.root.get_all())
        self.assertEqual(all_items, {
            ('net.max_connections', 3),
            ('core.loglevel', 'info'),
            ('net.rate_limit', (4, 1)),
            ('net.readonly', 0.25),
        })

    ##################
    # Protocol: Get
    ##################

    def test_proto_get_int(self) -> None:
        self.proto.lineReceived(b'net.max_connections')
        self.assertEqual(b'3\n', self.tr.value())

    def test_proto_get_str(self) -> None:
        self.proto.lineReceived(b'core.loglevel')
        self.assertEqual(b'"info"\n', self.tr.value())

    def test_proto_get_tuple(self) -> None:
        self.proto.lineReceived(b'net.rate_limit')
        self.assertEqual(b'4, 1\n', self.tr.value())

    def test_proto_get_unknown(self) -> None:
        self.proto.lineReceived(b'net.unknown')
        self.assertEqual(b'[error] net.unknown not found\n', self.tr.value())

    def test_proto_get_readonly(self) -> None:
        self.proto.lineReceived(b'net.readonly')
        self.assertEqual(b'0.25\n', self.tr.value())

    def test_proto_get_writeonly(self) -> None:
        self.proto.lineReceived(b'core.writeonly')
        self.assertEqual(b'[error] cannot read from core.writeonly\n', self.tr.value())

    ##################
    # Protocol: Set
    ##################

    def test_proto_set_int(self) -> None:
        self.proto.lineReceived(b'net.max_connections=3')
        setter = cast(MagicMock, self.root._get_setter('net.max_connections'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual((3,), setter.call_args.args)

    def test_proto_set_str(self) -> None:
        self.proto.lineReceived(b'core.loglevel="debug"')
        setter = cast(MagicMock, self.root._get_setter('core.loglevel'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual(('debug',), setter.call_args.args)

    def test_proto_set_readonly(self) -> None:
        self.proto.lineReceived(b'net.readonly=0.50')
        self.assertEqual(b'[error] cannot write to net.readonly\n', self.tr.value())

    def test_proto_set_unknown(self) -> None:
        self.proto.lineReceived(b'net.unknown=0.50')
        self.assertEqual(b'[error] net.unknown not found\n', self.tr.value())

    def test_proto_set_tuple(self) -> None:
        self.proto.lineReceived(b'net.rate_limit=8, 2')
        setter = cast(MagicMock, self.root._get_setter('net.rate_limit'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual((8, 2), setter.call_args.args)

    def test_proto_set_writeonly(self) -> None:
        self.proto.lineReceived(b'core.writeonly=1')
        setter = cast(MagicMock, self.root._get_setter('core.writeonly'))
        self.assertEqual(1, setter.call_count)
        self.assertEqual((1,), setter.call_args.args)

    def test_set_invalid_value(self) -> None:
        self.proto.lineReceived(b'net.max_connections=(3')
        self.assertEqual(b'[error] value: wrong format\n', self.tr.value())

    def test_set_invalid_json(self) -> None:
        self.proto.lineReceived(b'net.max_connections=\'a\'')
        self.assertEqual(b'[error] value: wrong format\n', self.tr.value())

    ##################
    # Protocol: Others
    ##################

    def test_proto_backup(self) -> None:
        self.proto.lineReceived(b'!backup')
        output = self.tr.value()
        lines = set(output.split(b'\n'))
        self.assertEqual(lines, {
            b'core.loglevel="info"',
            b'net.max_connections=3',
            b'net.rate_limit=4, 1',
            b'net.readonly=0.25',
            b'',    # output ends with a new line (\n)
        })
