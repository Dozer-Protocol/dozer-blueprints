import os
from hathor.nanocontracts.blueprints.dozer_pool_manager import DozerPoolManager
from hathor.nanocontracts.blueprints.oasis import Oasis
from hathor.nanocontracts.types import Address, NCActionType, NCDepositAction, NCWithdrawalAction, Amount, TokenUid
from hathor.transaction.token_info import TokenVersion
from hathor.util import not_none
from hathor.conf import HathorSettings
from hathor.wallet import KeyPair
from hathor_tests.nanocontracts.blueprints.unittest import BlueprintTestCase
from hathor.nanocontracts.exception import NCFail
from hathor.crypto.util import decode_address

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID

class OasisFixesTestCase(BlueprintTestCase):
    _enable_sync_v1 = True
    _enable_sync_v2 = True

    def setUp(self):
        super().setUp()
        self.oasis_blueprint_id = self.gen_random_blueprint_id()
        self.oasis_id = self.gen_random_contract_id()
        self._register_blueprint_class(Oasis, self.oasis_blueprint_id)

        self.dozer_manager_blueprint_id = self.gen_random_blueprint_id()
        self.dozer_manager_id = self.gen_random_contract_id()
        self._register_blueprint_class(DozerPoolManager, self.dozer_manager_blueprint_id)
        
        self.dev_address = self._get_any_address()[0]
        self.token_b = self.gen_random_token_uid()
        self.create_token(self.token_b, "token_b", "TKB", TokenVersion.DEPOSIT)
        self.pool_fee = Amount(3)
        self.tx = self._get_any_tx()

    def _get_any_tx(self):
        genesis = self.manager.tx_storage.get_all_genesis()
        tx = [t for t in genesis if t.is_transaction][0]
        return tx

    def _get_any_address(self) -> tuple[Address, KeyPair]:
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return Address(address_bytes), key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def initialize_pool_manager(self) -> None:
        ctx = self.create_context(actions=[], vertex=self.tx, caller_id=self.dev_address, timestamp=self.get_current_timestamp())
        self.runner.create_contract(
            self.dozer_manager_id,
            self.dozer_manager_blueprint_id,
            ctx,
        )
        
        # Create HTR-USD pool
        self.usd_token = self.gen_random_token_uid()
        htr_amount = 1000_00
        usd_amount = 500_00
        
        actions = [
            NCDepositAction(token_uid=TokenUid(HTR_UID), amount=htr_amount),
            NCDepositAction(token_uid=self.usd_token, amount=usd_amount),
        ]
        
        pool_ctx = self.create_context(
            actions=actions,
            vertex=self.tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp()
        )
        
        self.runner.call_public_method(
            self.dozer_manager_id,
            "create_pool",
            pool_ctx,
            Amount(3),
        )
        
        set_ctx = self.create_context(actions=[], vertex=self.tx, caller_id=self.dev_address, timestamp=self.get_current_timestamp())
        self.runner.call_public_method(
            self.dozer_manager_id,
            "set_htr_usd_pool",
            set_ctx,
            HTR_UID,
            self.usd_token,
            Amount(3),
        )

    def initialize_oasis(self) -> None:
        self.initialize_pool_manager()
        
        self.pool_fee = Amount(3) # Ensure this matches
        
        # Create HTR/TokenB pool first (needed for user_deposit liquidity calc)
        # But wait, user_deposit creates it if total_liquidity is 0?
        # "if self.total_liquidity == 0: liquidity_increase = ..."
        # But it also calls _quote_add_liquidity_in which calls pool manager
        
        # Create HTR/TokenB pool in manager
        pool_ctx = self.create_context(
            actions=[
                NCDepositAction(amount=1000000, token_uid=HTR_UID),
                NCDepositAction(amount=7000000, token_uid=self.token_b),
            ],
            vertex=self.tx, 
            caller_id=self.dev_address, 
            timestamp=self.get_current_timestamp()
        )
        self.runner.call_public_method(
            self.dozer_manager_id,
            "create_pool",
            pool_ctx,
            self.pool_fee,
        )

        actions = [NCDepositAction(token_uid=HTR_UID, amount=10_000_000_00)]
        ctx = self.create_context(
            actions=actions,
            vertex=self.tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        self.runner.create_contract(
            self.oasis_id,
            self.oasis_blueprint_id,
            ctx,
            self.dozer_manager_id,
            self.token_b,
            self.pool_fee,
            0, # protocol_fee
        )

    def test_owner_deposit_multiple_actions_fails(self) -> None:
        self.initialize_oasis()
        
        # Try to deposit with 2 actions (should fail but currently passes)
        ctx = self.create_context(
            actions=[
                NCDepositAction(amount=100_00, token_uid=HTR_UID),
                NCDepositAction(amount=100_00, token_uid=self.token_b), # Extra action
            ],
            vertex=self.tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        with self.assertRaises(NCFail):
            self.runner.call_public_method(self.oasis_id, "owner_deposit", ctx)

    def test_user_deposit_multiple_actions_fails(self) -> None:
        self.initialize_oasis()
        
        # Try to user_deposit with 2 actions (should fail but currently passes)
        # 1 TokenB (valid) + 1 HTR (extra)
        ctx = self.create_context(
            actions=[
                NCDepositAction(amount=100_00, token_uid=self.token_b),
                NCDepositAction(amount=100_00, token_uid=HTR_UID), # Extra action
            ],
            vertex=self.tx,
            caller_id=self.dev_address,
            timestamp=self.get_current_timestamp(),
        )
        
        with self.assertRaises(NCFail):
            # timelock=6 is required argument
            self.runner.call_public_method(self.oasis_id, "user_deposit", ctx, 6)

    def test_emergency_pause(self) -> None:
        self.initialize_oasis()
        
        # 2. Verify paused is false (can deposit)
        # ---------------------------------------
        ctx = self.create_context(
            actions=[NCDepositAction(amount=100_00, token_uid=self.token_b)],
            vertex=self.tx,
            caller_id=Address(decode_address(self.get_address(0))), # random user
            timestamp=self.get_current_timestamp()
        )
        # Should succeed
        self.runner.call_public_method(self.oasis_id, "user_deposit", ctx, 6)
        
        # 3. Call pause as dev
        # ----------------------
        pause_ctx = self.create_context(actions=[], vertex=self.tx, caller_id=self.dev_address, timestamp=self.get_current_timestamp())
        self.runner.call_public_method(self.oasis_id, "pause", pause_ctx)
        
        # 4. Verify paused is true (user_deposit failed)
        # ----------------------------------------------
        deposit_ctx = self.create_context(
            actions=[NCDepositAction(amount=100_00, token_uid=self.token_b)],
            vertex=self.tx,
            caller_id=Address(decode_address(self.get_address(1))), # random user
            timestamp=self.get_current_timestamp()
        )
        with self.assertRaisesRegex(NCFail, "paused"):
            self.runner.call_public_method(self.oasis_id, "user_deposit", deposit_ctx, 6)
            
        # 5. Call unpause as dev
        # ------------------------
        unpause_ctx = self.create_context(actions=[], vertex=self.tx, caller_id=self.dev_address, timestamp=self.get_current_timestamp())
        self.runner.call_public_method(self.oasis_id, "unpause", unpause_ctx)

        # 6. Verify paused is false (can deposit again)
        # ---------------------------------------------
        deposit_ctx_2 = self.create_context(
            actions=[NCDepositAction(amount=100_00, token_uid=self.token_b)],
            vertex=self.tx,
            caller_id=Address(decode_address(self.get_address(2))),
            timestamp=self.get_current_timestamp()
        )
        self.runner.call_public_method(self.oasis_id, "user_deposit", deposit_ctx_2, 6)

        # 7. Attempt pause as non-dev (fails)
        # -------------------------------------
        unauth_pause_ctx = self.create_context(actions=[], vertex=self.tx, caller_id=Address(decode_address(self.get_address(3))), timestamp=self.get_current_timestamp())
        with self.assertRaisesRegex(NCFail, "Only dev"):
             self.runner.call_public_method(self.oasis_id, "pause", unauth_pause_ctx)
