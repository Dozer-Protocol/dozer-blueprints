import os
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_pool import Dozer_Pool
from hathor.nanocontracts.blueprints.dozer_oasis import Oasis
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.util import not_none
from hathor.conf.get_settings import HathorSettings
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

from hathor.nanocontracts import Context, NCFail

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class OasisTestCase(BlueprintTestCase):
    _enable_sync_v1 = True
    _enable_sync_v2 = True

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
        self.dev_address = self._get_any_address()
        self.token_b = self.gen_random_token_uid()
        # Initialize base tx for contexts
        self.tx = self.get_genesis_tx()

    def _get_any_address(self):
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def initialize_oasis(self, amount: int = 10000000) -> None:
        """Test basic initialization"""
        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount),  # type: ignore
            ],
            self.tx,
            self.dev_address,  # type: ignore
            timestamp=0,
        )
        self.runner.call_public_method(
            self.oasis_id, "initialize", ctx, self.dozer_id, self.token_b
        )
        # self.assertIsNone(self.oasis_storage.get("dozer_pool"))

    def initialiaze_pool(
        self, amount_htr: int = 1000000, amount_b: int = 7000000
    ) -> None:
        """Test basic initialization"""
        # Initialize dozer pool first
        actions = [
            NCAction(NCActionType.DEPOSIT, HTR_UID, amount_htr),  # type: ignore
            NCAction(NCActionType.DEPOSIT, self.token_b, amount_htr),  # type: ignore
        ]
        pool_ctx = Context(actions, self.tx, self.dev_address, timestamp=0)  # type: ignore
        self.runner.call_public_method(
            self.dozer_id,
            "initialize",
            pool_ctx,
            HTR_UID,
            self.token_b,
            0,  # fee
            50,  # protocol fee
        )

    def test_initialize(self) -> None:
        self.initialiaze_pool()
        self.initialize_oasis()
        self.assertEqual(self.oasis_storage.get("dev_balance"), 10000000)

    # def test_set_dozer_pool(self):
    #     """Test setting dozer pool contract"""
    #     # Initialize first
    #     ctx = Context([], self.tx, b"", timestamp=0)  # type: ignore
    #     self.runner.call_public_method(self.oasis_id, "initialize", ctx)

    #     # Set dozer pool
    #     self.runner.call_public_method(
    #         self.oasis_id, "set_dozer_pool", ctx, self.dozer_id
    #     )
    #     self.assertEqual(self.oasis_storage.get("dozer_pool"), self.dozer_id)

    # def test_check_liquidity(self):
    #     """Test checking liquidity from dozer pool"""
    #     # Initialize contracts
    #     ctx = Context([], self.tx, b"", timestamp=0)  # type: ignore
    #     self.runner.call_public_method(self.oasis_id, "initialize", ctx)

    #     # Initialize dozer pool first
    #     actions = [
    #         NCAction(NCActionType.DEPOSIT, HTR_UID, 1000),  # type: ignore
    #         NCAction(NCActionType.DEPOSIT, self.token_b, 1000),  # type: ignore
    #     ]
    #     pool_ctx = Context(actions, self.tx, self._get_any_address()[0], timestamp=0)  # type: ignore
    #     self.runner.call_public_method(
    #         self.dozer_id,
    #         "initialize",
    #         pool_ctx,
    #         HTR_UID,
    #         self.token_b,
    #         0,  # fee
    #         50,  # protocol fee
    #     )

    #     # Set dozer pool in oasis
    #     self.runner.call_public_method(
    #         self.oasis_id, "set_dozer_pool", ctx, self.dozer_id
    #     )

    #     # Test checking liquidity
    #     result = self.runner.call_public_method(self.oasis_id, "return_ctx", ctx)
    #     self.log.info(f"algumacoisa{result=}")
    #     self.log.info(f"id{self.oasis_id=}")
