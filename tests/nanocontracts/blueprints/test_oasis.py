import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_pool import Dozer_Pool
from hathor.nanocontracts.blueprints.oasis import Oasis
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.util import not_none
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

from hathor.nanocontracts import Context, NCFail


class OasisTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        # Set up Oasis contract
        self.oasis_id = self.gen_random_nanocontract_id()
        self.runner.register_contract(Oasis, self.oasis_id)
        self.oasis_storage = self.runner.get_storage(self.oasis_id)

        # Set up Dozer Pool contract
        self.dozer_id = self.gen_random_nanocontract_id()
        self.runner.register_contract(Dozer_Pool, self.dozer_id)
        self.dozer_storage = self.runner.get_storage(self.dozer_id)

        # Initialize base tx for contexts
        self.tx = self.get_genesis_tx()

    def _get_any_address(self):
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def test_initialize(self):
        """Test basic initialization"""
        ctx = Context([], self.tx, b"", timestamp=0)
        self.runner.call_public_method(self.oasis_id, "initialize", ctx)
        # self.assertIsNone(self.oasis_storage.get("dozer_pool"))

    def test_set_dozer_pool(self):
        """Test setting dozer pool contract"""
        # Initialize first
        ctx = Context([], self.tx, b"", timestamp=0)
        self.runner.call_public_method(self.oasis_id, "initialize", ctx)

        # Set dozer pool
        self.runner.call_public_method(
            self.oasis_id, "set_dozer_pool", ctx, self.dozer_id
        )
        self.assertEqual(self.oasis_storage.get("dozer_pool"), self.dozer_id)

    def test_check_liquidity(self):
        """Test checking liquidity from dozer pool"""
        # Initialize contracts
        ctx = Context([], self.tx, b"", timestamp=0)
        self.runner.call_public_method(self.oasis_id, "initialize", ctx)

        # Initialize dozer pool first
        token_a = self.gen_random_token_uid()
        token_b = self.gen_random_token_uid()
        actions = [
            NCAction(NCActionType.DEPOSIT, token_a, 1000),
            NCAction(NCActionType.DEPOSIT, token_b, 1000),
        ]
        pool_ctx = Context(actions, self.tx, self._get_any_address()[0], timestamp=0)
        self.runner.call_public_method(
            self.dozer_id,
            "initialize",
            pool_ctx,
            token_a,
            token_b,
            0,  # fee
            50,  # protocol fee
        )

        # Set dozer pool in oasis
        self.runner.call_public_method(
            self.oasis_id, "set_dozer_pool", ctx, self.dozer_id
        )

        # Test checking liquidity
        result = self.runner.call_public_method(self.oasis_id, "return_ctx", ctx)
        self.log.info(f"algumacoisa{result=}")
        self.log.info(f"id{self.oasis_id=}")
        # Verify result contains expected quote data
        # self.assertEqual(100, result)

    # def test_check_liquidity_fails_without_pool(self):
    #     """Test liquidity check fails when pool not set"""
    #     ctx = Context([], self.tx, b"", timestamp=0)
    #     self.runner.call_public_method(self.oasis_id, "initialize", ctx)

    #     with self.assertRaises(NCFail):
    #         self.runner.call_public_method(
    #             self.oasis_id,
    #             "check_pool_liquidity",
    #             ctx,
    #             self.gen_random_token_uid(),
    #             100,
    #         )

    # def test_return_ctx(self):
    #     ctx = Context([], self.tx, b"", timestamp=0)
