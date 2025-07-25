import copy

from hathor.nanocontracts import Blueprint, public
from hathor.nanocontracts.catalog import NCBlueprintCatalog
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.vertex_data import NanoHeaderData, VertexData
from hathor.transaction import Block, Transaction
from hathor.transaction.base_transaction import TxVersion
from tests.dag_builder.builder import TestDAGBuilder
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

GLOBAL_VERTEX_DATA: VertexData | None = None


class RememberVertexDataBlueprint(Blueprint):
    @public
    def initialize(self, ctx: Context) -> None:
        pass

    @public
    def remember_context(self, ctx: Context) -> None:
        global GLOBAL_VERTEX_DATA
        GLOBAL_VERTEX_DATA = copy.deepcopy(ctx.vertex)


class ContextTestCase(BlueprintTestCase):
    def setUp(self) -> None:
        global GLOBAL_VERTEX_DATA

        super().setUp()

        self.blueprint_id = self.gen_random_contract_id()
        self.manager.tx_storage.nc_catalog = NCBlueprintCatalog({
            self.blueprint_id: RememberVertexDataBlueprint,
        })
        self.address = self.gen_random_address()

        # clear vertex-data before and after
        GLOBAL_VERTEX_DATA = None

    def tearDown(self) -> None:
        global GLOBAL_VERTEX_DATA

        super().tearDown()
        # clear vertex-data before and after
        GLOBAL_VERTEX_DATA = None

    def test_vertex_data(self) -> None:
        global GLOBAL_VERTEX_DATA

        dag_builder = TestDAGBuilder.from_manager(self.manager)
        artifacts = dag_builder.build_from_str(f'''
            blockchain genesis b[1..12]
            b10 < dummy
            nc1.nc_id = "{self.blueprint_id.hex()}"
            nc1.nc_method = initialize()
            nc1 <-- b11
            nc2.nc_id = nc1
            nc2.nc_method = remember_context()
            nc1 <-- nc2 <-- b12
        ''')
        artifacts.propagate_with(self.manager)
        b12, = artifacts.get_typed_vertices(['b12'], Block)
        nc1, nc2 = artifacts.get_typed_vertices(['nc1', 'nc2'], Transaction)

        # this is the vertex data that was observed by nc2 when remember_context was called
        assert GLOBAL_VERTEX_DATA is not None
        vertex_data = copy.deepcopy(GLOBAL_VERTEX_DATA)

        # XXX: nonce varies, even for a weight of 1.0
        # XXX: inptus/outputs/parents ignored since the dag builder will pick whatever to fill it in

        self.assertEqual(vertex_data.version, TxVersion.REGULAR_TRANSACTION)
        self.assertEqual(vertex_data.hash, nc2.hash)
        self.assertEqual(vertex_data.signal_bits, 0)
        self.assertEqual(vertex_data.weight, 1.0)
        self.assertEqual(vertex_data.tokens, ())
        self.assertEqual(vertex_data.block.hash, b12.hash)
        self.assertEqual(vertex_data.block.timestamp, b12.timestamp)
        self.assertEqual(vertex_data.block.height, b12.get_height())
        nano_header_data, = vertex_data.headers
        assert isinstance(nano_header_data, NanoHeaderData)
        self.assertEqual(nano_header_data.nc_id, nc1.hash)
        self.assertEqual(nano_header_data.nc_method, 'remember_context')
        self.assertEqual(nano_header_data.nc_args_bytes, b'\x00')
