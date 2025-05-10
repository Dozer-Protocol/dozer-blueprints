import os
import random
from logging import getLogger

from hathor.conf import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_pool_manager import (
    DozerPoolManager,
    InsufficientLiquidity,
    InvalidAction,
    InvalidFee,
    InvalidTokens,
    PoolExists,
    PoolNotFound,
    Unauthorized,
)
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.storage import NCStorage
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.util import not_none
from hathor.wallet import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

PRECISION = 10**20

settings = HathorSettings()

logger = getLogger(__name__)


class DozerPoolManagerBlueprintTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        self.blueprint_id = self.gen_random_blueprint_id()
        self.nc_id = self.gen_random_nanocontract_id()
        self.register_blueprint_class(self.blueprint_id, DozerPoolManager)

        # Generate random token UIDs for testing
        self.token_a = self.gen_random_token_uid()
        self.token_b = self.gen_random_token_uid()
        self.token_c = self.gen_random_token_uid()

        # Initialize the contract
        self._initialize_contract()

    def _get_any_tx(self):
        genesis = self.manager.tx_storage.get_all_genesis()
        tx = list(genesis)[0]
        return tx

    def _get_any_address(self):
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def get_current_timestamp(self):
        return int(self.clock.seconds())

    def _initialize_contract(self):
        """Initialize the DozerPoolManager contract"""
        tx = self._get_any_tx()
        context = Context(
            [], tx, self._get_any_address()[0], timestamp=self.get_current_timestamp()
        )
        self.runner.create_contract(
            self.nc_id,
            self.blueprint_id,
            context,
        )

        self.nc_storage = self.runner.get_storage(self.nc_id)
        self.owner_address = context.address

    def _create_pool(
        self, token_a, token_b, fee=3, reserve_a=1000_00, reserve_b=1000_00
    ):
        """Create a pool with the specified tokens and fee"""
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, token_a, reserve_a),
            NCAction(NCActionType.DEPOSIT, token_b, reserve_b),
        ]
        context = Context(
            actions,
            tx,
            self._get_any_address()[0],
            timestamp=self.get_current_timestamp(),
        )
        pool_key = self.runner.call_public_method(
            self.nc_id, "create_pool", context, token_a, token_b, fee
        )
        return pool_key, context.address

    def _add_liquidity(self, token_a, token_b, fee, amount_a, amount_b):
        """Add liquidity to an existing pool"""
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, token_a, amount_a),
            NCAction(NCActionType.DEPOSIT, token_b, amount_b),
        ]
        address_bytes, _ = self._get_any_address()
        context = Context(
            actions, tx, address_bytes, timestamp=self.get_current_timestamp()
        )
        result = self.runner.call_public_method(
            self.nc_id, "add_liquidity", context, token_a, token_b, fee
        )
        return result, context

    def _remove_liquidity(
        self, token_a, token_b, fee, amount_a, amount_b, address=None
    ):
        """Remove liquidity from an existing pool"""
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.WITHDRAWAL, token_a, amount_a),
            NCAction(NCActionType.WITHDRAWAL, token_b, amount_b),
        ]
        if address is None:
            address_bytes, _ = self._get_any_address()
        else:
            address_bytes = address
        context = Context(
            actions, tx, address_bytes, timestamp=self.get_current_timestamp()
        )
        self.runner.call_public_method(
            self.nc_id, "remove_liquidity", context, token_a, token_b, fee
        )
        return context

    def _prepare_swap_context(self, token_in, amount_in, token_out, amount_out):
        """Prepare a context for swap operations"""
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, token_in, amount_in),
            NCAction(NCActionType.WITHDRAWAL, token_out, amount_out),
        ]
        address_bytes, _ = self._get_any_address()
        return Context(
            actions, tx, address_bytes, timestamp=self.get_current_timestamp()
        )

    def _swap_exact_tokens_for_tokens(
        self, token_a, token_b, fee, amount_in, amount_out
    ):
        """Execute a swap_exact_tokens_for_tokens operation"""
        context = self._prepare_swap_context(token_a, amount_in, token_b, amount_out)
        result = self.runner.call_public_method(
            self.nc_id, "swap_exact_tokens_for_tokens", context, token_a, token_b, fee
        )
        return result, context

    def _swap_tokens_for_exact_tokens(
        self, token_a, token_b, fee, amount_in, amount_out
    ):
        """Execute a swap_tokens_for_exact_tokens operation"""
        context = self._prepare_swap_context(token_a, amount_in, token_b, amount_out)
        result = self.runner.call_public_method(
            self.nc_id, "swap_tokens_for_exact_tokens", context, token_a, token_b, fee
        )
        return result, context

    def test_initialize(self):
        """Test contract initialization"""
        # Verify owner is set correctly
        self.assertEqual(self.nc_storage.get("owner"), self.owner_address)

        # Verify default fee and protocol fee are set correctly
        self.assertEqual(self.nc_storage.get("default_fee"), 3)
        self.assertEqual(self.nc_storage.get("default_protocol_fee"), 10)

    def test_create_pool(self):
        """Test pool creation"""
        # Create a pool
        pool_key, creator_address = self._create_pool(self.token_a, self.token_b)

        # Verify pool exists
        self.assertTrue(self.nc_storage.get(f"pool_exists:{pool_key}"))

        # Verify tokens are stored correctly
        self.assertEqual(self.nc_storage.get(f"pool_token_a:{pool_key}"), self.token_a)
        self.assertEqual(self.nc_storage.get(f"pool_token_b:{pool_key}"), self.token_b)

        # Verify initial liquidity
        creator_liquidity = self.runner.call_view_method(
            self.nc_id, "liquidity_of", creator_address, pool_key
        )
        self.assertEqual(
            creator_liquidity,
            1000_00 * PRECISION,
        )

        # Try to create the same pool again - should fail
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.DEPOSIT, self.token_a, 1000_00),
            NCAction(NCActionType.DEPOSIT, self.token_b, 1000_00),
        ]
        context = Context(
            actions,
            tx,
            self._get_any_address()[0],
            timestamp=self.get_current_timestamp(),
        )
        with self.assertRaises(PoolExists):
            self.runner.call_public_method(
                self.nc_id, "create_pool", context, self.token_a, self.token_b, 3
            )

    def test_create_multiple_pools(self):
        """Test creating multiple pools with different tokens and fees"""
        # Create first pool with token_a and token_b
        pool_key1, _ = self._create_pool(self.token_a, self.token_b, fee=3)

        # Create second pool with token_a and token_c
        pool_key2, _ = self._create_pool(self.token_a, self.token_c, fee=5)

        # Create third pool with token_b and token_c
        pool_key3, _ = self._create_pool(self.token_b, self.token_c, fee=10)

        # Create fourth pool with token_a and token_b but different fee
        pool_key4, _ = self._create_pool(self.token_a, self.token_b, fee=20)

        # Verify all pools exist
        self.assertTrue(self.nc_storage.get(f"pool_exists:{pool_key1}"))
        self.assertTrue(self.nc_storage.get(f"pool_exists:{pool_key2}"))
        self.assertTrue(self.nc_storage.get(f"pool_exists:{pool_key3}"))
        self.assertTrue(self.nc_storage.get(f"pool_exists:{pool_key4}"))

        # Verify all pools are in the all_pools list
        all_pools = self.runner.call_view_method(self.nc_id, "get_all_pools")
        self.assertIn(pool_key1, all_pools)
        self.assertIn(pool_key2, all_pools)
        self.assertIn(pool_key3, all_pools)
        self.assertIn(pool_key4, all_pools)

        # Verify token_to_pools mapping
        token_to_pools = self.runner.call_view_method(
            self.nc_id, "get_pools_for_token", self.token_a
        )
        self.assertIn(pool_key1, token_to_pools)
        self.assertIn(pool_key2, token_to_pools)
        self.assertIn(pool_key4, token_to_pools)
        token_to_pools = self.runner.call_view_method(
            self.nc_id, "get_pools_for_token", self.token_b
        )
        self.assertIn(pool_key1, token_to_pools)
        self.assertIn(pool_key3, token_to_pools)
        self.assertIn(pool_key4, token_to_pools)
        token_to_pools = self.runner.call_view_method(
            self.nc_id, "get_pools_for_token", self.token_c
        )
        self.assertIn(pool_key2, token_to_pools)
        self.assertIn(pool_key3, token_to_pools)

    def test_add_liquidity(self):
        """Test adding liquidity to a pool"""
        # Create a pool
        pool_key, creator_address = self._create_pool(self.token_a, self.token_b)

        # Initial reserves
        initial_reserve_a = self.nc_storage.get(f"pool_reserve_a:{pool_key}")
        initial_reserve_b = self.nc_storage.get(f"pool_reserve_b:{pool_key}")
        initial_total_liquidity = self.nc_storage.get(
            f"pool_total_liquidity:{pool_key}"
        )

        # Add liquidity
        result, context = self._add_liquidity(
            self.token_a, self.token_b, 3, 500_00, 600_00
        )

        # Verify reserves increased
        self.assertGreater(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"), initial_reserve_a
        )
        self.assertGreater(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"), initial_reserve_b
        )

        # Verify total liquidity increased
        self.assertGreater(
            self.nc_storage.get(f"pool_total_liquidity:{pool_key}"),
            initial_total_liquidity,
        )

        # Verify user liquidity was updated
        self.assertGreater(
            self.runner.call_view_method(
                self.nc_id, "liquidity_of", creator_address, pool_key
            ),
            0,
        )

    def test_remove_liquidity(self):
        """Test removing liquidity from a pool"""
        # Create a pool
        pool_key, creator_address = self._create_pool(self.token_a, self.token_b)

        # Add liquidity with a new user
        result, add_context = self._add_liquidity(
            self.token_a, self.token_b, 3, 500_00, 600_00
        )

        # Initial values before removal
        initial_reserve_a = self.nc_storage.get(f"pool_reserve_a:{pool_key}")
        initial_reserve_b = self.nc_storage.get(f"pool_reserve_b:{pool_key}")
        initial_total_liquidity = self.nc_storage.get(
            f"pool_total_liquidity:{pool_key}"
        )
        initial_user_liquidity = self.runner.call_view_method(
            self.nc_id, "liquidity_of", add_context.address, pool_key
        )

        # Calculate amount to remove (half of the user's liquidity)
        amount_to_remove_a = (
            initial_reserve_a * initial_user_liquidity // (initial_total_liquidity * 2)
        )

        # Remove liquidity
        remove_context = self._remove_liquidity(
            self.token_a, self.token_b, 3, amount_to_remove_a, 0, add_context.address
        )

        # Verify reserves decreased
        self.assertLess(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"), initial_reserve_a
        )
        self.assertLess(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"), initial_reserve_b
        )

        # Verify total liquidity decreased
        self.assertLess(
            self.nc_storage.get(f"pool_total_liquidity:{pool_key}"),
            initial_total_liquidity,
        )

        # Verify user liquidity decreased
        self.assertLess(
            self.runner.call_view_method(
                self.nc_id, "liquidity_of", remove_context.address, pool_key
            ),
            initial_user_liquidity,
        )

    def test_swap_exact_tokens_for_tokens(self):
        """Test swapping an exact amount of input tokens for output tokens"""
        # Create a pool with substantial liquidity
        pool_key, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=10000_00, reserve_b=10000_00
        )

        # Initial reserves
        initial_reserve_a = self.nc_storage.get(f"pool_reserve_a:{pool_key}")
        initial_reserve_b = self.nc_storage.get(f"pool_reserve_b:{pool_key}")

        # Execute swap
        swap_amount_in = 100_00
        swap_amount_out = (
            90_00  # Less than the maximum possible to account for slippage
        )
        result, context = self._swap_exact_tokens_for_tokens(
            self.token_a, self.token_b, 3, swap_amount_in, swap_amount_out
        )

        # Verify reserves changed correctly
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"),
            initial_reserve_a + swap_amount_in,
        )
        self.assertLess(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"), initial_reserve_b
        )

        # Verify transaction count increased
        self.assertEqual(self.nc_storage.get(f"pool_transactions:{pool_key}"), 1)

        # Verify swap result
        self.assertEqual(result.amount_in, swap_amount_in)
        self.assertEqual(result.token_in, self.token_a)
        self.assertEqual(result.token_out, self.token_b)

    def test_swap_tokens_for_exact_tokens(self):
        """Test swapping tokens for an exact amount of output tokens"""
        # Create a pool with substantial liquidity
        pool_key, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=10000_00, reserve_b=10000_00
        )

        # Initial reserves
        initial_reserve_a = self.nc_storage.get(f"pool_reserve_a:{pool_key}")
        initial_reserve_b = self.nc_storage.get(f"pool_reserve_b:{pool_key}")

        # Execute swap
        swap_amount_in = 110_00  # More than needed to account for slippage
        swap_amount_out = 100_00
        result, context = self._swap_tokens_for_exact_tokens(
            self.token_a, self.token_b, 3, swap_amount_in, swap_amount_out
        )

        # Verify reserves changed correctly
        self.assertGreater(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"), initial_reserve_a
        )
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"),
            initial_reserve_b - swap_amount_out,
        )

        # Verify transaction count increased
        self.assertEqual(self.nc_storage.get(f"pool_transactions:{pool_key}"), 1)

        # Verify swap result
        self.assertEqual(result.token_in, self.token_a)
        self.assertEqual(result.token_out, self.token_b)
        self.assertEqual(result.amount_out, swap_amount_out)

    def test_change_protocol_fee(self):
        """Test changing the protocol fee"""
        # Create context with owner address
        tx = self._get_any_tx()
        context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        # Change protocol fee
        new_fee = 20
        self.runner.call_public_method(
            self.nc_id, "change_protocol_fee", context, new_fee
        )

        # Verify protocol fee changed
        self.assertEqual(self.nc_storage.get("default_protocol_fee"), new_fee)

        # Try to change protocol fee with non-owner address
        non_owner_context = Context(
            [], tx, self._get_any_address()[0], timestamp=self.get_current_timestamp()
        )

        # Should fail with Unauthorized
        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id, "change_protocol_fee", non_owner_context, 15
            )

    def test_withdraw_protocol_fees(self):
        """Test withdrawing protocol fees"""
        # Create a pool
        pool_key, _ = self._create_pool(self.token_a, self.token_b)

        # Execute several swaps to accumulate protocol fees
        for _ in range(5):
            self._swap_exact_tokens_for_tokens(
                self.token_a, self.token_b, 3, 100_00, 90_00
            )
            self._swap_exact_tokens_for_tokens(
                self.token_b, self.token_a, 3, 100_00, 90_00
            )

        # Check accumulated protocol fees
        protocol_fee_balance_a = self.nc_storage.get(
            f"protocol_fee_balance:{self.token_a}"
        )
        protocol_fee_balance_b = self.nc_storage.get(
            f"protocol_fee_balance:{self.token_b}"
        )
        self.assertGreater(protocol_fee_balance_a, 0)
        self.assertGreater(protocol_fee_balance_b, 0)

        # Withdraw protocol fees for token_a
        token_a_fee = protocol_fee_balance_a
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.WITHDRAWAL, self.token_a, token_a_fee),
        ]
        context = Context(
            actions, tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        withdrawn_amount = self.runner.call_public_method(
            self.nc_id, "withdraw_protocol_fees", context, self.token_a
        )

        # Verify protocol fee balance for token_a is now 0
        self.assertEqual(self.nc_storage.get(f"protocol_fee_balance:{self.token_a}"), 0)

        # Verify withdrawn amount
        self.assertEqual(withdrawn_amount, token_a_fee)

    def test_get_pools_for_token(self):
        """Test retrieving pools for a specific token"""
        # Create multiple pools with different tokens
        self._create_pool(self.token_a, self.token_b, fee=3)
        self._create_pool(self.token_a, self.token_c, fee=5)
        self._create_pool(self.token_b, self.token_c, fee=10)

        # Get pools for token_a
        tx = self._get_any_tx()
        context = Context(
            [], tx, self._get_any_address()[0], timestamp=self.get_current_timestamp()
        )

        token_a_pools = self.runner.call_view_method(
            self.nc_id, "get_pools_for_token", self.token_a
        )

        # Verify token_a is in 2 pools
        self.assertEqual(len(token_a_pools), 2)

        # Get pools for token_b
        token_b_pools = self.runner.call_view_method(
            self.nc_id, "get_pools_for_token", self.token_b
        )

        # Verify token_b is in 2 pools
        self.assertEqual(len(token_b_pools), 2)

        # Get pools for token_c
        token_c_pools = self.runner.call_view_method(
            self.nc_id, "get_pools_for_token", self.token_c
        )

        # Verify token_c is in 2 pools
        self.assertEqual(len(token_c_pools), 2)

    def test_front_end_api_pool(self):
        """Test the front-end API for pool information"""
        # Create a pool
        pool_key, _ = self._create_pool(self.token_a, self.token_b)

        # Execute a swap to generate some activity
        self._swap_exact_tokens_for_tokens(self.token_a, self.token_b, 3, 100_00, 90_00)

        # Get pool info
        tx = self._get_any_tx()
        context = Context(
            [], tx, self._get_any_address()[0], timestamp=self.get_current_timestamp()
        )

        pool_info = self.runner.call_view_method(
            self.nc_id, "front_end_api_pool", pool_key
        )

        # Verify pool info contains expected keys
        self.assertIn("reserve0", pool_info)
        self.assertIn("reserve1", pool_info)
        self.assertIn("fee", pool_info)
        self.assertIn("volume", pool_info)
        self.assertIn("transactions", pool_info)
        self.assertIn("is_signed", pool_info)
        self.assertIn("signer", pool_info)

        # Verify transaction count
        self.assertEqual(pool_info["transactions"], 1)

        # Verify pool is not signed by default
        self.assertFalse(pool_info["is_signed"])
        self.assertIsNone(pool_info["signer"])

    def test_add_authorized_signer(self):
        """Test adding an authorized signer"""
        # Create a new address to be an authorized signer
        signer_address, _ = self._get_any_address()

        # Create context with owner address
        tx = self._get_any_tx()
        context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        # Add the signer
        self.runner.call_public_method(
            self.nc_id, "add_authorized_signer", context, signer_address
        )

        # Verify the signer was added
        is_authorized = self.runner.call_view_method(
            self.nc_id, "is_authorized_signer", signer_address
        )
        self.assertTrue(is_authorized)

        # Try to add a signer with non-owner address (should fail)
        non_owner_address, _ = self._get_any_address()
        non_owner_context = Context(
            [], tx, non_owner_address, timestamp=self.get_current_timestamp()
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id,
                "add_authorized_signer",
                non_owner_context,
                self._get_any_address()[0],
            )

    def test_remove_authorized_signer(self):
        """Test removing an authorized signer"""
        # Create a new address to be an authorized signer
        signer_address, _ = self._get_any_address()

        # Add the signer
        tx = self._get_any_tx()
        owner_context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.nc_id, "add_authorized_signer", owner_context, signer_address
        )

        # Verify the signer was added
        is_authorized = self.runner.call_view_method(
            self.nc_id, "is_authorized_signer", signer_address
        )
        self.assertTrue(is_authorized)

        # Remove the signer
        self.runner.call_public_method(
            self.nc_id, "remove_authorized_signer", owner_context, signer_address
        )

        # Verify the signer was removed
        is_authorized = self.runner.call_view_method(
            self.nc_id, "is_authorized_signer", signer_address
        )
        self.assertFalse(is_authorized)

        # Try to remove the owner as a signer (should fail)
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.nc_id,
                "remove_authorized_signer",
                owner_context,
                self.owner_address,
            )

        # Try to remove a signer with non-owner address (should fail)
        non_owner_address, _ = self._get_any_address()
        non_owner_context = Context(
            [], tx, non_owner_address, timestamp=self.get_current_timestamp()
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id,
                "remove_authorized_signer",
                non_owner_context,
                signer_address,
            )

    def test_sign_pool(self):
        """Test signing a pool for listing in the Dozer dApp"""
        # Create a pool
        pool_key, _ = self._create_pool(self.token_a, self.token_b)

        # Sign the pool with owner address
        tx = self._get_any_tx()
        owner_context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.nc_id, "sign_pool", owner_context, self.token_a, self.token_b, 3
        )

        # Verify the pool is signed
        pool_info = self.runner.call_view_method(self.nc_id, "pool_info", pool_key)
        self.assertTrue(pool_info["is_signed"])
        self.assertEqual(pool_info["signer"], self.owner_address)

        # Get signed pools
        signed_pools = self.runner.call_view_method(self.nc_id, "get_signed_pools")
        self.assertEqual(len(signed_pools), 1)
        self.assertEqual(signed_pools[0], pool_key)

        # Try to sign a pool with unauthorized address (should fail)
        unauthorized_address, _ = self._get_any_address()
        unauthorized_context = Context(
            [], tx, unauthorized_address, timestamp=self.get_current_timestamp()
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id,
                "sign_pool",
                unauthorized_context,
                self.token_a,
                self.token_b,
                3,
            )

        # Create a new authorized signer
        signer_address, _ = self._get_any_address()
        self.runner.call_public_method(
            self.nc_id, "add_authorized_signer", owner_context, signer_address
        )

        # Create another pool
        pool_key2, _ = self._create_pool(self.token_a, self.token_c)

        # Sign the second pool with the new signer
        signer_context = Context(
            [], tx, signer_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.nc_id, "sign_pool", signer_context, self.token_a, self.token_c, 3
        )

        # Verify the second pool is signed
        pool_info2 = self.runner.call_view_method(self.nc_id, "pool_info", pool_key2)
        self.assertTrue(pool_info2["is_signed"])
        self.assertEqual(pool_info2["signer"], signer_address)

        # Get signed pools (should now have 2)
        signed_pools = self.runner.call_view_method(self.nc_id, "get_signed_pools")
        self.assertEqual(len(signed_pools), 2)
        self.assertIn(pool_key, signed_pools)
        self.assertIn(pool_key2, signed_pools)

    def test_unsign_pool(self):
        """Test unsigning a pool"""
        # Create a pool
        pool_key, _ = self._create_pool(self.token_a, self.token_b)

        # Create a new authorized signer
        tx = self._get_any_tx()
        owner_context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        signer_address, _ = self._get_any_address()
        self.runner.call_public_method(
            self.nc_id, "add_authorized_signer", owner_context, signer_address
        )

        # Sign the pool with the signer
        signer_context = Context(
            [], tx, signer_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.nc_id, "sign_pool", signer_context, self.token_a, self.token_b, 3
        )

        # Verify the pool is signed
        pool_info = self.runner.call_view_method(self.nc_id, "pool_info", pool_key)
        self.assertTrue(pool_info["is_signed"])
        self.assertEqual(pool_info["signer"], signer_address)

        # Unsign the pool with the original signer
        self.runner.call_public_method(
            self.nc_id, "unsign_pool", signer_context, self.token_a, self.token_b, 3
        )

        # Verify the pool is unsigned
        pool_info = self.runner.call_view_method(self.nc_id, "pool_info", pool_key)
        self.assertFalse(pool_info["is_signed"])
        self.assertIsNone(pool_info["signer"])

        # Sign the pool again with the signer
        self.runner.call_public_method(
            self.nc_id, "sign_pool", signer_context, self.token_a, self.token_b, 3
        )

        # Verify the pool is signed
        pool_info = self.runner.call_view_method(self.nc_id, "pool_info", pool_key)
        self.assertTrue(pool_info["is_signed"])

        # Unsign the pool with the owner (even though they didn't sign it)
        self.runner.call_public_method(
            self.nc_id, "unsign_pool", owner_context, self.token_a, self.token_b, 3
        )

        # Verify the pool is unsigned
        pool_info = self.runner.call_view_method(self.nc_id, "pool_info", pool_key)
        self.assertFalse(pool_info["is_signed"])

        # Try to unsign with unauthorized address (should fail)
        unauthorized_address, _ = self._get_any_address()
        unauthorized_context = Context(
            [], tx, unauthorized_address, timestamp=self.get_current_timestamp()
        )

        # Sign the pool first
        self.runner.call_public_method(
            self.nc_id, "sign_pool", signer_context, self.token_a, self.token_b, 3
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id,
                "unsign_pool",
                unauthorized_context,
                self.token_a,
                self.token_b,
                3,
            )
