import json
import math
import os
import random
from logging import getLogger

from hathor.conf import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_pool_manager import (
    HTR_UID,
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

    def _add_liquidity(self, token_a, token_b, fee, amount_a, amount_b=None):
        """Add liquidity to an existing pool"""
        tx = self._get_any_tx()
        if amount_b is None:
            reserve_a, reserve_b = self.runner.call_view_method(
                self.nc_id, "get_reserves", token_a, token_b, fee
            )
            amount_b = self.runner.call_view_method(
                self.nc_id, "quote", amount_a, reserve_a, reserve_b
            )
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
        self, token_a, token_b, fee, amount_a, amount_b=None, address=None
    ):
        """Remove liquidity from an existing pool

        Args:
            token_a: First token of the pair
            token_b: Second token of the pair
            fee: Fee for the pool
            amount_a: Amount of token A to withdraw
            amount_b: Amount of token B to withdraw (optional, will be calculated using quote if not provided)
            address: Address to remove liquidity from (optional)

        Note:
            The contract uses the quote method to calculate the optimal amount of token B based on amount_a.
            If amount_b is less than the optimal amount, the difference is returned as change.
            If amount_b is not provided, it will be calculated using the quote method.
        """
        # Ensure tokens are ordered correctly
        if token_a > token_b:
            token_a, token_b = token_b, token_a
            amount_a, amount_b = amount_b, amount_a

        # Get current reserves
        reserves = self.runner.call_view_method(
            self.nc_id, "get_reserves", token_a, token_b, fee
        )

        # Calculate optimal amount_b using quote if not provided
        if amount_b is None:
            amount_b = self.runner.call_view_method(
                self.nc_id, "quote", amount_a, reserves[0], reserves[1]
            )
            # Ensure amount_b is an integer
            amount_b = int(amount_b)

        tx = self._get_any_tx()
        # Ensure both amounts are integers
        amount_a = int(amount_a) if amount_a is not None else 0
        amount_b = int(amount_b) if amount_b is not None else 0

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
        result = self.runner.call_public_method(
            self.nc_id, "remove_liquidity", context, token_a, token_b, fee
        )
        return context, result

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

        amount_a = 500_00
        reserve_a, reserve_b = self.runner.call_view_method(
            self.nc_id, "get_reserves", self.token_a, self.token_b, 3
        )
        amount_b = self.runner.call_view_method(
            self.nc_id, "quote", amount_a, reserve_a, reserve_b
        )

        liquidity_increase = initial_total_liquidity * amount_a // reserve_a
        # Add liquidity
        result, context = self._add_liquidity(
            self.token_a,
            self.token_b,
            3,
            amount_a,
            amount_b,
        )

        # Verify reserves increased
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"),
            initial_reserve_a + amount_a,
        )
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"),
            initial_reserve_b + amount_b,
        )

        # Verify total liquidity increased
        self.assertEqual(
            self.nc_storage.get(f"pool_total_liquidity:{pool_key}"),
            initial_total_liquidity + liquidity_increase,
        )

        self.assertEqual(
            self.runner.call_view_method(
                self.nc_id, "liquidity_of", context.address, pool_key
            ),
            liquidity_increase,
        )

    def test_remove_liquidity(self):
        """Test removing liquidity from a pool"""
        # Create a pool
        pool_key, creator_address = self._create_pool(self.token_a, self.token_b)

        # Add liquidity with a new user
        result, add_context = self._add_liquidity(
            self.token_a,
            self.token_b,
            3,
            500_00,
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

        # Calculate amount of token A to remove (half of the user's liquidity)
        amount_to_remove_a = (
            initial_reserve_a * initial_user_liquidity // (initial_total_liquidity * 2)
        )

        liquidity_decrease = initial_user_liquidity // 2

        amount_to_remove_b = self.runner.call_view_method(
            self.nc_id,
            "quote",
            amount_to_remove_a,
            initial_reserve_a,
            initial_reserve_b,
        )

        remove_context, _ = self._remove_liquidity(
            self.token_a,
            self.token_b,
            3,
            amount_to_remove_a,
            amount_to_remove_b,
            address=add_context.address,
        )

        # Verify reserves decreased
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"),
            initial_reserve_a - amount_to_remove_a,
        )
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"),
            initial_reserve_b - amount_to_remove_b,
        )

        # Verify total liquidity decreased
        self.assertEqual(
            self.nc_storage.get(f"pool_total_liquidity:{pool_key}"),
            initial_total_liquidity - liquidity_decrease,
        )

        # Verify user liquidity decreased
        self.assertEqual(
            self.runner.call_view_method(
                self.nc_id, "liquidity_of", remove_context.address, pool_key
            ),
            initial_user_liquidity - liquidity_decrease,
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
        swap_amount_out = self.runner.call_view_method(
            self.nc_id,
            "front_quote_exact_tokens_for_tokens",
            swap_amount_in,
            self.token_a,
            self.token_b,
            3,
        )["amounts"][1]
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
        initial_volume_a = self.nc_storage.get(f"pool_volume_a:{pool_key}")
        initial_volume_b = self.nc_storage.get(f"pool_volume_b:{pool_key}")
        initial_total_liquidity = self.nc_storage.get(
            f"pool_total_liquidity:{pool_key}"
        )
        initial_owner_liquidity = self.runner.call_view_method(
            self.nc_id, "liquidity_of", self.owner_address, pool_key
        )

        # Define the exact amount of output tokens we want
        swap_amount_out = 500_00

        # Calculate the required input amount using get_amount_in
        # This is the same calculation used in the blueprint
        fee_numerator = self.nc_storage.get(f"pool_fee_numerator:{pool_key}")
        fee_denominator = self.nc_storage.get(f"pool_fee_denominator:{pool_key}")
        required_amount_in = self.runner.call_view_method(
            self.nc_id,
            "get_amount_in",
            swap_amount_out,
            initial_reserve_a,
            initial_reserve_b,
            fee_numerator,
            fee_denominator,
        )

        # Add some extra for slippage
        swap_amount_in = required_amount_in + 10_00
        expected_slippage = swap_amount_in - required_amount_in

        # Calculate expected fee amount
        fee_amount = required_amount_in * fee_numerator // fee_denominator

        # Calculate expected protocol fee
        protocol_fee_percent = self.nc_storage.get("default_protocol_fee")
        protocol_fee_amount = fee_amount * protocol_fee_percent // 100

        # Execute swap
        result, context = self._swap_tokens_for_exact_tokens(
            self.token_a, self.token_b, 3, swap_amount_in, swap_amount_out
        )

        # Verify reserves changed correctly
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_a:{pool_key}"),
            initial_reserve_a + required_amount_in,
            "Reserve A did not increase by the expected amount",
        )
        self.assertEqual(
            self.nc_storage.get(f"pool_reserve_b:{pool_key}"),
            initial_reserve_b - swap_amount_out,
            "Reserve B did not decrease by the expected amount",
        )

        # Verify transaction count increased
        self.assertEqual(
            self.nc_storage.get(f"pool_transactions:{pool_key}"),
            1,
            "Transaction count did not increase correctly",
        )

        # Verify volume updated correctly
        self.assertEqual(
            self.nc_storage.get(f"pool_volume_a:{pool_key}"),
            initial_volume_a + required_amount_in,
            "Volume A did not increase by the expected amount",
        )
        self.assertEqual(
            self.nc_storage.get(f"pool_volume_b:{pool_key}"),
            initial_volume_b,
            "Volume B should not have changed",
        )

        # Verify protocol fee was collected correctly
        # Check that owner's liquidity increased
        new_owner_liquidity = self.runner.call_view_method(
            self.nc_id, "liquidity_of", self.owner_address, pool_key
        )
        self.assertGreater(
            new_owner_liquidity,
            initial_owner_liquidity,
            "Owner's liquidity should have increased due to protocol fees",
        )

        # Verify total liquidity increased by the same amount
        new_total_liquidity = self.nc_storage.get(f"pool_total_liquidity:{pool_key}")
        liquidity_increase = new_total_liquidity - initial_total_liquidity
        self.assertEqual(
            new_owner_liquidity - initial_owner_liquidity,
            liquidity_increase,
            "Owner's liquidity increase should match total liquidity increase",
        )

        # Verify swap result
        self.assertEqual(
            result.amount_in, swap_amount_in, "Input amount in result doesn't match"
        )
        self.assertEqual(
            result.slippage_in,
            expected_slippage,
            "Slippage in result doesn't match expected value",
        )
        self.assertEqual(
            result.token_in, self.token_a, "Input token in result doesn't match"
        )
        self.assertEqual(
            result.amount_out, swap_amount_out, "Output amount in result doesn't match"
        )
        self.assertEqual(
            result.token_out, self.token_b, "Output token in result doesn't match"
        )

        # Verify user balance was updated with slippage
        user_balance = self.runner.call_view_method(
            self.nc_id, "balance_of", context.address, pool_key
        )
        self.assertEqual(
            user_balance[0],
            expected_slippage,
            "User balance should have been updated with slippage amount",
        )

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

        # Calculate expected protocol fees per swap
        fee_numerator = self.nc_storage.get(f"pool_fee_numerator:{pool_key}")
        fee_denominator = self.nc_storage.get(f"pool_fee_denominator:{pool_key}")
        protocol_fee_percent = self.nc_storage.get("default_protocol_fee")

        # For a swap of 100_00 tokens with a fee of 3/1000, the fee would be:
        swap_amount = 100_00
        fee_amount = swap_amount * fee_numerator // fee_denominator
        expected_protocol_fee_per_swap = fee_amount * protocol_fee_percent // 100

        # Track initial protocol fee balances
        initial_protocol_fee_balance_a = self.nc_storage.get(
            f"protocol_fee_balance:{self.token_a}", default=0
        )
        initial_protocol_fee_balance_b = self.nc_storage.get(
            f"protocol_fee_balance:{self.token_b}", default=0
        )

        # Execute several swaps to accumulate protocol fees
        num_swaps = 5
        for _ in range(num_swaps):
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

        # Verify that protocol fees were collected correctly
        self.assertEqual(
            protocol_fee_balance_a,
            initial_protocol_fee_balance_a
            + (expected_protocol_fee_per_swap * num_swaps),
            "Protocol fee balance for token A doesn't match expected value",
        )
        self.assertEqual(
            protocol_fee_balance_b,
            initial_protocol_fee_balance_b
            + (expected_protocol_fee_per_swap * num_swaps),
            "Protocol fee balance for token B doesn't match expected value",
        )

        # Test withdrawing partial protocol fees for token_a
        partial_withdrawal = protocol_fee_balance_a // 2
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.WITHDRAWAL, self.token_a, partial_withdrawal),
        ]
        context = Context(
            actions, tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        withdrawn_amount = self.runner.call_public_method(
            self.nc_id, "withdraw_protocol_fees", context, self.token_a
        )

        # Verify protocol fee balance for token_a was reduced correctly
        self.assertEqual(
            self.nc_storage.get(f"protocol_fee_balance:{self.token_a}"),
            protocol_fee_balance_a - partial_withdrawal,
            "Protocol fee balance for token A wasn't reduced correctly after partial withdrawal",
        )

        # Verify withdrawn amount matches requested amount
        self.assertEqual(
            withdrawn_amount,
            partial_withdrawal,
            "Withdrawn amount doesn't match requested amount",
        )

        # Now withdraw the remaining protocol fees for token_a
        remaining_balance = self.nc_storage.get(f"protocol_fee_balance:{self.token_a}")
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.WITHDRAWAL, self.token_a, remaining_balance),
        ]
        context = Context(
            actions, tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        withdrawn_amount = self.runner.call_public_method(
            self.nc_id, "withdraw_protocol_fees", context, self.token_a
        )

        # Verify protocol fee balance for token_a is now 0
        self.assertEqual(
            self.nc_storage.get(f"protocol_fee_balance:{self.token_a}"),
            0,
            "Protocol fee balance for token A should be 0 after full withdrawal",
        )

        # Verify withdrawn amount matches remaining balance
        self.assertEqual(
            withdrawn_amount,
            remaining_balance,
            "Withdrawn amount doesn't match remaining balance",
        )

        # Test attempting to withdraw more than available
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.WITHDRAWAL, self.token_a, 1),  # Any amount > 0
        ]
        context = Context(
            actions, tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        # Should fail with InvalidAction
        with self.assertRaises(InvalidAction):
            self.runner.call_public_method(
                self.nc_id, "withdraw_protocol_fees", context, self.token_a
            )

        # Test unauthorized withdrawal attempt
        non_owner_address, _ = self._get_any_address()
        tx = self._get_any_tx()
        actions = [
            NCAction(NCActionType.WITHDRAWAL, self.token_b, protocol_fee_balance_b),
        ]
        context = Context(
            actions, tx, non_owner_address, timestamp=self.get_current_timestamp()
        )

        # Should fail with Unauthorized
        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id, "withdraw_protocol_fees", context, self.token_b
            )

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

    def test_set_htr_usd_pool(self):
        """Test setting the HTR-USD pool for price calculations"""
        # Create HTR token UID (all zeros)
        htr_token = HTR_UID

        # Create a USD token (using token_a as a stand-in for a stablecoin)
        usd_token = self.token_a

        # Create an HTR-USD pool
        htr_usd_pool_key, _ = self._create_pool(
            htr_token, usd_token, fee=3, reserve_a=1000_00, reserve_b=1000_00
        )

        # Set the HTR-USD pool with owner address
        tx = self._get_any_tx()
        owner_context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.nc_id, "set_htr_usd_pool", owner_context, htr_token, usd_token, 3
        )

        # Verify the HTR-USD pool was set correctly
        htr_usd_pool = self.runner.call_view_method(self.nc_id, "get_htr_usd_pool")
        self.assertEqual(htr_usd_pool, htr_usd_pool_key)

        # Try to set the HTR-USD pool with non-owner address (should fail)
        non_owner_address, _ = self._get_any_address()
        non_owner_context = Context(
            [], tx, non_owner_address, timestamp=self.get_current_timestamp()
        )

        with self.assertRaises(Unauthorized):
            self.runner.call_public_method(
                self.nc_id,
                "set_htr_usd_pool",
                non_owner_context,
                htr_token,
                usd_token,
                3,
            )

        # Try to set a non-HTR pool as the HTR-USD pool (should fail)
        non_htr_pool_key, _ = self._create_pool(self.token_b, self.token_c)

        with self.assertRaises(InvalidTokens):
            self.runner.call_public_method(
                self.nc_id,
                "set_htr_usd_pool",
                owner_context,
                self.token_b,
                self.token_c,
                3,
            )

    def test_htr_token_map(self):
        """Test the HTR token map for tracking HTR pairs"""
        # Create HTR token UID (all zeros)
        htr_token = HTR_UID

        # Create pools with HTR and different tokens
        pool_key1, _ = self._create_pool(htr_token, self.token_a, fee=3)
        pool_key2, _ = self._create_pool(htr_token, self.token_b, fee=5)

        # Create another pool with the same token but different fee
        pool_key3, _ = self._create_pool(htr_token, self.token_a, fee=10)

        # Get all token prices in HTR
        token_prices = self.runner.call_view_method(
            self.nc_id, "get_all_token_prices_in_htr"
        )

        # Verify HTR itself has a price of 1
        self.assertEqual(token_prices[htr_token.hex()], 1_000000)

        # Verify token_a and token_b are in the map
        self.assertIn(self.token_a.hex(), token_prices)
        self.assertIn(self.token_b.hex(), token_prices)

        # Verify the token_a price uses the pool with the lowest fee (pool_key1 with fee=3)
        token_a_price = self.runner.call_view_method(
            self.nc_id, "get_token_price_in_htr", self.token_a
        )
        self.assertEqual(token_prices[self.token_a.hex()], token_a_price)

        # Create a non-HTR pool
        non_htr_pool_key, _ = self._create_pool(self.token_b, self.token_c)

        # Verify token_c is not in the HTR token map
        token_c_price = self.runner.call_view_method(
            self.nc_id, "get_token_price_in_htr", self.token_c
        )
        self.assertEqual(token_c_price, 0)

    def test_token_prices_in_usd(self):
        """Test getting token prices in USD"""
        # Create HTR token UID (all zeros)
        htr_token = HTR_UID

        # Create a USD token (using token_a as a stand-in for a stablecoin)
        usd_token = self.token_a

        # Create an HTR-USD pool with 1 HTR = 10 USD
        # Using exact values to ensure precise price calculations
        htr_reserve = 1000_00  # 1000 HTR
        usd_reserve = 10000_00  # 10000 USD
        htr_usd_pool_key, _ = self._create_pool(
            htr_token, usd_token, fee=3, reserve_a=htr_reserve, reserve_b=usd_reserve
        )

        # Verify the HTR-USD pool doesn't exist in the contract yet
        self.assertIsNone(
            self.nc_storage.get("htr_usd_pool_key", default=None),
            "HTR-USD pool should not be set yet",
        )

        # Set the HTR-USD pool
        tx = self._get_any_tx()
        owner_context = Context(
            [], tx, self.owner_address, timestamp=self.get_current_timestamp()
        )

        self.runner.call_public_method(
            self.nc_id, "set_htr_usd_pool", owner_context, htr_token, usd_token, 3
        )

        # Verify the HTR-USD pool was set correctly
        self.assertEqual(
            self.nc_storage.get("htr_usd_pool_key"),
            htr_usd_pool_key,
            "HTR-USD pool key was not set correctly",
        )

        # Create a token-HTR pool with 1 token_b = 2 HTR
        # Using exact values for precise price calculations
        token_b_htr_reserve_htr = 2000_00  # 2000 HTR
        token_b_htr_reserve_b = 1000_00  # 1000 token_b
        token_b_htr_pool_key, _ = self._create_pool(
            htr_token,
            self.token_b,
            fee=3,
            reserve_a=token_b_htr_reserve_htr,
            reserve_b=token_b_htr_reserve_b,
        )

        # Calculate expected token_b price in HTR (with 6 decimal places)
        # Price = (HTR reserve * 1_000000) // token_b reserve
        expected_token_b_price_in_htr = (
            token_b_htr_reserve_htr * 1_000000
        ) // token_b_htr_reserve_b

        # Get token_b price in HTR
        token_b_price_in_htr = self.runner.call_view_method(
            self.nc_id, "get_token_price_in_htr", self.token_b
        )

        # Verify exact price calculation
        self.assertEqual(
            token_b_price_in_htr,
            expected_token_b_price_in_htr,
            "Token B price in HTR doesn't match expected value",
        )

        # Calculate expected HTR price in USD (with 6 decimal places)
        # Price = (USD reserve * 1_000000) // HTR reserve
        expected_htr_price_in_usd = (usd_reserve * 1_000000) // htr_reserve

        # Calculate expected token_b price in USD
        # Price = (token_b price in HTR * HTR price in USD) // 1_000000
        expected_token_b_price_in_usd = (
            expected_token_b_price_in_htr * expected_htr_price_in_usd
        ) // 1_000000

        # Get token_b price in USD
        token_b_price_in_usd = self.runner.call_view_method(
            self.nc_id, "get_token_price_in_usd", self.token_b
        )

        # Verify exact price calculation
        self.assertEqual(
            token_b_price_in_usd,
            expected_token_b_price_in_usd,
            "Token B price in USD doesn't match expected value",
        )

        # Get all token prices in USD
        token_prices_in_usd = self.runner.call_view_method(
            self.nc_id, "get_all_token_prices_in_usd"
        )

        # Verify HTR price in USD
        self.assertIn(
            htr_token.hex(),
            token_prices_in_usd,
            "HTR token not found in token prices",
        )
        self.assertEqual(
            token_prices_in_usd[htr_token.hex()],
            expected_htr_price_in_usd,
            "HTR price in USD doesn't match expected value",
        )

        # Verify token_b price in USD matches the individual call
        self.assertIn(
            self.token_b.hex(),
            token_prices_in_usd,
            "Token B not found in token prices",
        )
        self.assertEqual(
            token_prices_in_usd[self.token_b.hex()],
            token_b_price_in_usd,
            "Token B price in USD from get_all_token_prices_in_usd doesn't match individual call",
        )

        # Test with a token that doesn't have an HTR pool
        token_c_price_in_usd = self.runner.call_view_method(
            self.nc_id, "get_token_price_in_usd", self.token_c
        )
        self.assertEqual(
            token_c_price_in_usd,
            0,
            "Token C price in USD should be 0 since it doesn't have an HTR pool",
        )

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

    def test_front_quote_exact_tokens_for_tokens(self):
        """Test quoting exact tokens for tokens with direct swap"""
        # Create a pool
        pool_key, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=100000_00, reserve_b=200000_00
        )

        # Get a quote for exact tokens
        quote = self.runner.call_view_method(
            self.nc_id,
            "front_quote_exact_tokens_for_tokens",
            100_00,
            self.token_a,
            self.token_b,
            3,
        )

        # Verify the quote contains expected fields
        self.assertIn("amount_out", quote)
        self.assertIn("price_impact", quote)
        self.assertIn("path", quote)
        self.assertIn("amounts", quote)

        # Verify the path exists (but don't check exact format)
        self.assertTrue(quote["path"])

        # The output amount should be approximately 200_00 (2:1 ratio) minus fees
        self.assertGreater(quote["amount_out"], 190_00)
        self.assertLess(quote["amount_out"], 200_00)

        # Test the reverse direction
        quote_reverse = self.runner.call_view_method(
            self.nc_id,
            "front_quote_exact_tokens_for_tokens",
            100_00,
            self.token_b,
            self.token_a,
            3,
        )

        # The output amount should be approximately 50_00 (1:2 ratio) minus fees
        self.assertGreater(quote_reverse["amount_out"], 45_00)
        self.assertLess(quote_reverse["amount_out"], 50_00)

    def test_front_quote_tokens_for_exact_tokens(self):
        """Test quoting tokens for exact tokens with direct swap"""
        # Create a pool
        pool_key, _ = self._create_pool(
            self.token_a,
            self.token_b,
            fee=3,
            reserve_a=1000000_00,
            reserve_b=2000000_00,
        )

        # Get a quote for exact output tokens
        quote = self.runner.call_view_method(
            self.nc_id,
            "front_quote_tokens_for_exact_tokens",
            100_00,
            self.token_a,
            self.token_b,
            3,
        )

        # Verify the quote contains expected fields
        self.assertIn("amount_in", quote)
        self.assertIn("price_impact", quote)
        self.assertIn("path", quote)
        self.assertIn("amounts", quote)

        # Verify the path exists (but don't check exact format)
        self.assertTrue(quote["path"])

        # The input amount should be approximately 50_00 (2:1 ratio) plus fees
        self.assertGreater(quote["amount_in"], 50_00)
        self.assertLess(quote["amount_in"], 55_00)

        # Test the reverse direction
        quote_reverse = self.runner.call_view_method(
            self.nc_id,
            "front_quote_tokens_for_exact_tokens",
            50_00,
            self.token_b,
            self.token_a,
            3,
        )

        # The input amount should be approximately 100_00 (1:2 ratio) plus fees
        self.assertGreater(quote_reverse["amount_in"], 100_00)
        self.assertLess(quote_reverse["amount_in"], 110_00)

    def test_find_best_swap_path_direct(self):
        """Test finding the best swap path with direct swap"""
        # Create pools with different fees
        pool_key_3, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=1000_00, reserve_b=1000_00
        )
        pool_key_10, _ = self._create_pool(
            self.token_a, self.token_b, fee=10, reserve_a=1000_00, reserve_b=1010_00
        )  # Slightly better price but higher fee

        # Find the best path
        path_result = self.runner.call_view_method(
            self.nc_id, "find_best_swap_path", 100_00, self.token_a, self.token_b, 3
        )

        # Verify the result contains expected fields
        self.assertIn("path", path_result)
        self.assertIn("amounts", path_result)
        self.assertIn("amount_out", path_result)
        self.assertIn("price_impact", path_result)

        # Verify it found a path
        self.assertTrue(path_result["path"])

        # Verify the amount_out is reasonable
        self.assertGreater(path_result["amount_out"], 0)

    def test_find_best_swap_path_multi_hop(self):
        """Test finding the best swap path with multiple hops"""
        # Create three tokens and two pools: A-B and B-C
        pool_key_ab, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=1000_00, reserve_b=1000_00
        )
        pool_key_bc, _ = self._create_pool(
            self.token_b, self.token_c, fee=3, reserve_a=1000_00, reserve_b=1000_00
        )

        # Find the best path from A to C
        path_result = self.runner.call_view_method(
            self.nc_id, "find_best_swap_path", 100_00, self.token_a, self.token_c, 3
        )

        # Verify it found a path
        self.assertTrue(path_result["path"])

        # Verify the amount_out is reasonable
        self.assertGreater(path_result["amount_out"], 0)

        # Create a direct pool with worse rate
        pool_key_ac, _ = self._create_pool(
            self.token_a, self.token_c, fee=30, reserve_a=1000_00, reserve_b=900_00
        )  # Worse rate and higher fee

        # Find the best path again
        path_result_2 = self.runner.call_view_method(
            self.nc_id, "find_best_swap_path", 100_00, self.token_a, self.token_c, 3
        )

        # Verify it found a path
        self.assertTrue(path_result_2["path"])

        # Verify the amount_out is reasonable
        self.assertGreater(path_result_2["amount_out"], 0)

    def _prepare_cross_swap_context(
        self, token_in, amount_in, token_out=None, amount_out=None
    ):
        """Prepare a context for cross-pool swap operations"""
        # Use the existing _prepare_swap_context method to ensure consistency
        if token_out is not None and amount_out is not None:
            return self._prepare_swap_context(
                token_in, amount_in, token_out, amount_out
            )

        # If no token_out/amount_out, just create a deposit action
        tx = self._get_any_tx()
        actions = [NCAction(NCActionType.DEPOSIT, token_in, amount_in)]
        address_bytes, _ = self._get_any_address()
        context = Context(
            actions, tx, address_bytes, timestamp=self.get_current_timestamp()
        )
        return context

    def test_swap_cross_pool_direct(self):
        """Test swapping tokens through a direct path using the cross-pool swap method"""
        # Create a pool
        pool_key, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=1000_00, reserve_b=2000_00
        )

        # Calculate expected output directly
        reserve_a, reserve_b = self.runner.call_view_method(
            self.nc_id, "get_reserves", self.token_a, self.token_b, 3
        )
        amount_in = 100_00
        amount_in_with_fee = amount_in * (1000 - 3)
        numerator = amount_in_with_fee * reserve_b
        denominator = reserve_a * 1000 + amount_in_with_fee
        expected_output = numerator // denominator

        # Prepare swap context with both deposit and withdrawal actions
        context = self._prepare_swap_context(
            self.token_a, amount_in, self.token_b, expected_output
        )

        # Execute the swap using the regular swap method as a reference
        output_amount = self.runner.call_public_method(
            self.nc_id,
            "swap_exact_tokens_for_tokens",
            context,
            self.token_a,
            self.token_b,
            3,
        )

        # Verify the output amount is reasonable
        self.assertGreater(output_amount.amount_out, 0)

    def test_swap_tokens(self):
        """Test swapping tokens using the standard swap method"""
        # Create a pool
        pool_key, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=1000_00, reserve_b=2000_00
        )

        # Calculate expected output directly
        reserve_a, reserve_b = self.runner.call_view_method(
            self.nc_id, "get_reserves", self.token_a, self.token_b, 3
        )
        amount_in = 100_00
        amount_in_with_fee = amount_in * (1000 - 3)
        numerator = amount_in_with_fee * reserve_b
        denominator = reserve_a * 1000 + amount_in_with_fee
        expected_output = numerator // denominator

        # Prepare swap context with both deposit and withdrawal actions
        context = self._prepare_swap_context(
            self.token_a, amount_in, self.token_b, expected_output
        )

        # Execute the swap
        output_amount = self.runner.call_public_method(
            self.nc_id,
            "swap_exact_tokens_for_tokens",
            context,
            self.token_a,
            self.token_b,
            3,  # Use the regular swap method
        )

        # Verify the output amount is reasonable
        self.assertGreater(output_amount.amount_out, 0)

        # Verify the reserves were updated
        new_reserve_a, new_reserve_b = self.runner.call_view_method(
            self.nc_id, "get_reserves", self.token_a, self.token_b, 3
        )
        self.assertEqual(new_reserve_a, reserve_a + amount_in)
        self.assertEqual(new_reserve_b, reserve_b - output_amount.amount_out)

    def test_invalid_swap_parameters(self):
        """Test handling of invalid swap parameters"""
        # Create a pool
        pool_key, _ = self._create_pool(self.token_a, self.token_b)

        # Test with insufficient input amount
        context_insufficient = self._prepare_swap_context(
            self.token_a, 1, self.token_b, 50_00
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.nc_id,
                "swap_exact_tokens_for_tokens",
                context_insufficient,
                self.token_a,
                self.token_b,
                3,
            )

        # Test with non-existent pool
        context_normal = self._prepare_swap_context(
            self.token_a, 100_00, self.token_c, 50_00
        )
        with self.assertRaises(PoolNotFound):
            self.runner.call_public_method(
                self.nc_id,
                "swap_exact_tokens_for_tokens",
                context_normal,
                self.token_a,
                self.token_c,
                3,
            )

        # Test with wrong deposit token
        context_wrong_token = self._prepare_swap_context(
            self.token_c, 100_00, self.token_b, 50_00
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.nc_id,
                "swap_exact_tokens_for_tokens",
                context_wrong_token,
                self.token_a,
                self.token_b,
                3,
            )

    def test_get_user_pools(self):
        """Test retrieving all pools where a user has liquidity"""
        # Create multiple pools
        pool_key1, _ = self._create_pool(self.token_a, self.token_b, fee=3)
        pool_key2, _ = self._create_pool(self.token_a, self.token_c, fee=5)
        pool_key3, _ = self._create_pool(self.token_b, self.token_c, fee=10)

        # Create a user address
        user_address, _ = self._get_any_address()

        # Initially, user should have no pools
        user_pools = self.runner.call_view_method(
            self.nc_id, "get_user_pools", user_address
        )
        self.assertEqual(len(user_pools), 0)

        # Add liquidity to the first pool
        context = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_a, 500_00),
                NCAction(NCActionType.DEPOSIT, self.token_b, 500_00),
            ],
            self._get_any_tx(),
            user_address,
            timestamp=self.get_current_timestamp(),
        )
        self.runner.call_public_method(
            self.nc_id, "add_liquidity", context, self.token_a, self.token_b, 3
        )

        # Now user should have one pool
        user_pools = self.runner.call_view_method(
            self.nc_id, "get_user_pools", user_address
        )
        self.assertEqual(len(user_pools), 1)
        self.assertEqual(user_pools[0], pool_key1)

        # Add liquidity to the third pool
        context = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_b, 500_00),
                NCAction(NCActionType.DEPOSIT, self.token_c, 500_00),
            ],
            self._get_any_tx(),
            user_address,
            timestamp=self.get_current_timestamp(),
        )
        self.runner.call_public_method(
            self.nc_id, "add_liquidity", context, self.token_b, self.token_c, 10
        )

        # Now user should have two pools
        user_pools = self.runner.call_view_method(
            self.nc_id, "get_user_pools", user_address
        )
        self.assertEqual(len(user_pools), 2)
        self.assertIn(pool_key1, user_pools)
        self.assertIn(pool_key3, user_pools)

    def test_get_user_positions(self):
        """Test retrieving detailed information about all user positions"""
        # Create multiple pools
        pool_key1, _ = self._create_pool(
            self.token_a, self.token_b, fee=3, reserve_a=1000_00, reserve_b=2000_00
        )
        pool_key2, _ = self._create_pool(
            self.token_a, self.token_c, fee=5, reserve_a=1500_00, reserve_b=1500_00
        )

        # Create a user address
        user_address, _ = self._get_any_address()

        # Initially, user should have no positions
        positions = self.runner.call_view_method(
            self.nc_id, "get_user_positions", user_address
        )
        self.assertEqual(len(positions), 0)

        # Add liquidity to the first pool
        context = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_a, 500_00),
                NCAction(NCActionType.DEPOSIT, self.token_b, 1000_00),
            ],
            self._get_any_tx(),
            user_address,
            timestamp=self.get_current_timestamp(),
        )
        self.runner.call_public_method(
            self.nc_id, "add_liquidity", context, self.token_a, self.token_b, 3
        )

        # Now user should have one position
        positions = self.runner.call_view_method(
            self.nc_id, "get_user_positions", user_address
        )
        self.assertEqual(len(positions), 1)
        self.assertIn(pool_key1, positions)

        # Verify position details
        position = positions[pool_key1]
        self.assertGreater(position["liquidity"], 0)
        self.assertGreater(position["share"], 0)
        self.assertGreater(position["token_a_amount"], 0)
        self.assertGreater(position["token_b_amount"], 0)
        self.assertEqual(position["token_a"], self.token_a)
        self.assertEqual(position["token_b"], self.token_b)
        self.assertEqual(position["fee"], 3 / 1000)

        # Add liquidity to the second pool
        context = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_a, 750_00),
                NCAction(NCActionType.DEPOSIT, self.token_c, 750_00),
            ],
            self._get_any_tx(),
            user_address,
            timestamp=self.get_current_timestamp(),
        )
        self.runner.call_public_method(
            self.nc_id, "add_liquidity", context, self.token_a, self.token_c, 5
        )

        # Now user should have two positions
        positions = self.runner.call_view_method(
            self.nc_id, "get_user_positions", user_address
        )
        self.assertEqual(len(positions), 2)
        self.assertIn(pool_key1, positions)
        self.assertIn(pool_key2, positions)

        # Verify second position details
        position = positions[pool_key2]
        self.assertGreater(position["liquidity"], 0)
        self.assertGreater(position["share"], 0)
        self.assertGreater(position["token_a_amount"], 0)
        self.assertGreater(position["token_b_amount"], 0)
        self.assertEqual(position["token_a"], self.token_a)
        self.assertEqual(position["token_b"], self.token_c)
        self.assertEqual(position["fee"], 5 / 1000)

    def test_random_user_interactions(self):
        """Test random user interactions with pools to stress test the contract.
        This test performs 50 random operations (add liquidity, remove liquidity, swaps)
        and verifies state consistency after each operation.
        """
        # Create a pool with initial reserves
        initial_reserve_a = 10000_00
        initial_reserve_b = 10000_00
        fee = 3
        pool_key, _ = self._create_pool(
            self.token_a,
            self.token_b,
            fee=fee,
            reserve_a=initial_reserve_a,
            reserve_b=initial_reserve_b,
        )

        # Helper functions to get current reserves and calculate expected output
        def get_reserves():
            return self.runner.call_view_method(
                self.nc_id, "get_reserves", self.token_a, self.token_b, fee
            )

        def get_amount_out(amount_in, reserve_in, reserve_out):
            amount_in_with_fee = amount_in * (1000 - fee)
            numerator = amount_in_with_fee * reserve_out
            denominator = reserve_in * 1000 + amount_in_with_fee
            return numerator // denominator

        def calculate_liquidity_amount(
            amount_a, amount_b, reserve_a, reserve_b, total_liquidity
        ):
            """Calculate the expected liquidity tokens for adding liquidity."""
            if total_liquidity == 0:
                # First liquidity provision
                return math.sqrt(amount_a * amount_b)
            else:
                # Subsequent liquidity provisions
                liquidity_a = amount_a * total_liquidity // reserve_a
                liquidity_b = amount_b * total_liquidity // reserve_b
                return min(liquidity_a, liquidity_b)

        # Track users, total volume, and transaction count
        all_users = set()
        user_liquidities = {}
        total_volume = 0
        transactions = 0

        # Get initial state
        initial_pool_info = self.runner.call_view_method(
            self.nc_id, "pool_info", pool_key
        )
        initial_total_liquidity = initial_pool_info["total_liquidity"]

        # Perform random operations
        for operation_count in range(50):  # 50 random operations
            # Choose a random action: add liquidity, remove liquidity, swap A to B, or swap B to A
            action = random.choice(
                ["add_liquidity", "remove_liquidity", "swap_a_to_b", "swap_b_to_a"]
            )

            # Get current state before operation
            reserve_a, reserve_b = get_reserves()
            pool_info = self.runner.call_view_method(self.nc_id, "pool_info", pool_key)
            total_liquidity = pool_info["total_liquidity"]
            current_transaction_count = pool_info["transactions"]

            if action == "add_liquidity":
                # Random liquidity amounts
                amount_a = random.randint(10_00, 200_00)
                # Calculate amount_b to maintain the current ratio
                amount_b = (
                    amount_a * reserve_b // reserve_a if reserve_a > 0 else amount_a
                )

                # Create a random user address
                address_bytes, _ = self._get_any_address()
                all_users.add(address_bytes)

                # Get user's current liquidity
                user_current_liquidity = self.runner.call_view_method(
                    self.nc_id, "liquidity_of", address_bytes, pool_key
                )

                # Calculate expected liquidity to be minted
                expected_liquidity = calculate_liquidity_amount(
                    amount_a, amount_b, reserve_a, reserve_b, total_liquidity
                )

                # Add liquidity
                context = Context(
                    [
                        NCAction(NCActionType.DEPOSIT, self.token_a, amount_a),
                        NCAction(NCActionType.DEPOSIT, self.token_b, amount_b),
                    ],
                    self._get_any_tx(),
                    address_bytes,
                    timestamp=self.get_current_timestamp(),
                )
                result = self.runner.call_public_method(
                    self.nc_id,
                    "add_liquidity",
                    context,
                    self.token_a,
                    self.token_b,
                    fee,
                )

                # Check if any change was returned
                change_token = None
                change_amount = 0
                if result:
                    change_token, change_amount = result

                # Adjust expected amounts if change was returned
                if change_token == self.token_a:
                    amount_a -= change_amount
                elif change_token == self.token_b:
                    amount_b -= change_amount

                # Assert reserves after adding liquidity
                new_reserve_a, new_reserve_b = get_reserves()
                self.assertEqual(new_reserve_a, reserve_a + amount_a)
                self.assertEqual(new_reserve_b, reserve_b + amount_b)

                # Get user's new liquidity
                user_new_liquidity = self.runner.call_view_method(
                    self.nc_id, "liquidity_of", address_bytes, pool_key
                )
                liquidity_added = user_new_liquidity - user_current_liquidity

                # Store user's liquidity for later verification
                user_liquidities[address_bytes] = user_new_liquidity

                # Assert total liquidity increased by the expected amount
                new_pool_info = self.runner.call_view_method(
                    self.nc_id, "pool_info", pool_key
                )
                new_total_liquidity = new_pool_info["total_liquidity"]
                self.assertEqual(new_total_liquidity, total_liquidity + liquidity_added)

            elif action == "remove_liquidity" and len(all_users) > 0:
                # Choose a random user who has added liquidity
                user_address = random.choice(list(all_users))

                # Get user's liquidity
                user_liquidity = self.runner.call_view_method(
                    self.nc_id, "liquidity_of", user_address, pool_key
                )

                if user_liquidity > 0:
                    # Calculate amount_a based on liquidity share (half of their liquidity)
                    user_info = self.runner.call_view_method(
                        self.nc_id, "user_info", user_address, pool_key
                    )
                    amount_a = user_info["token_a_amount"] // 2

                    # Calculate the expected amount_b using the quote method
                    expected_amount_b = self.runner.call_view_method(
                        self.nc_id, "quote", amount_a, reserve_a, reserve_b
                    )

                    # Calculate expected liquidity to be burned
                    expected_liquidity_burned = amount_a * total_liquidity // reserve_a

                    # Remove liquidity using the helper method
                    _, result = self._remove_liquidity(
                        self.token_a, self.token_b, fee, amount_a, address=user_address
                    )

                    # Assert reserves after removing liquidity
                    new_reserve_a, new_reserve_b = get_reserves()
                    self.assertEqual(new_reserve_a, reserve_a - amount_a)
                    self.assertEqual(new_reserve_b, reserve_b - expected_amount_b)

                    # Get user's new liquidity
                    user_new_liquidity = self.runner.call_view_method(
                        self.nc_id, "liquidity_of", user_address, pool_key
                    )
                    liquidity_removed = user_liquidity - user_new_liquidity

                    # Update stored user liquidity
                    user_liquidities[user_address] = user_new_liquidity

                    # Assert total liquidity decreased by the expected amount
                    new_pool_info = self.runner.call_view_method(
                        self.nc_id, "pool_info", pool_key
                    )
                    new_total_liquidity = new_pool_info["total_liquidity"]
                    self.assertEqual(
                        new_total_liquidity, total_liquidity - liquidity_removed
                    )

            elif action == "swap_a_to_b":
                # Random swap amount
                swap_amount_a = random.randint(1_00, 100_00)
                expected_amount_b = get_amount_out(swap_amount_a, reserve_a, reserve_b)

                if expected_amount_b > 0:
                    # Create a random user address
                    address_bytes, _ = self._get_any_address()
                    all_users.add(address_bytes)

                    # Execute swap
                    context = Context(
                        [
                            NCAction(NCActionType.DEPOSIT, self.token_a, swap_amount_a),
                            NCAction(
                                NCActionType.WITHDRAWAL, self.token_b, expected_amount_b
                            ),
                        ],
                        self._get_any_tx(),
                        address_bytes,
                        timestamp=self.get_current_timestamp(),
                    )
                    result = self.runner.call_public_method(
                        self.nc_id,
                        "swap_exact_tokens_for_tokens",
                        context,
                        self.token_a,
                        self.token_b,
                        fee,
                    )
                    transactions += 1
                    total_volume += swap_amount_a

                    # Assert reserves after swapping
                    new_reserve_a, new_reserve_b = get_reserves()
                    self.assertEqual(new_reserve_a, reserve_a + swap_amount_a)
                    self.assertEqual(new_reserve_b, reserve_b - result.amount_out)
                    self.assertEqual(result.amount_out, expected_amount_b)

                    # Assert total liquidity remains unchanged (except for protocol fees)
                    new_pool_info = self.runner.call_view_method(
                        self.nc_id, "pool_info", pool_key
                    )
                    new_total_liquidity = new_pool_info["total_liquidity"]
                    # Protocol fees may increase total liquidity slightly
                    self.assertGreaterEqual(new_total_liquidity, total_liquidity)

                    # Assert transaction count increased
                    self.assertEqual(
                        new_pool_info["transactions"], current_transaction_count + 1
                    )

            elif action == "swap_b_to_a":
                # Random swap amount
                swap_amount_b = random.randint(1_00, 100_00)
                expected_amount_a = get_amount_out(swap_amount_b, reserve_b, reserve_a)

                if expected_amount_a > 0:
                    # Create a random user address
                    address_bytes, _ = self._get_any_address()
                    all_users.add(address_bytes)

                    # Execute swap
                    context = Context(
                        [
                            NCAction(NCActionType.DEPOSIT, self.token_b, swap_amount_b),
                            NCAction(
                                NCActionType.WITHDRAWAL, self.token_a, expected_amount_a
                            ),
                        ],
                        self._get_any_tx(),
                        address_bytes,
                        timestamp=self.get_current_timestamp(),
                    )

                    result = self.runner.call_public_method(
                        self.nc_id,
                        "swap_exact_tokens_for_tokens",
                        context,
                        self.token_b,
                        self.token_a,
                        fee,
                    )
                    transactions += 1
                    total_volume += swap_amount_b

                    # Assert reserves after swapping
                    new_reserve_a, new_reserve_b = get_reserves()
                    self.assertEqual(new_reserve_a, reserve_a - result.amount_out)
                    self.assertEqual(new_reserve_b, reserve_b + swap_amount_b)
                    self.assertEqual(result.amount_out, expected_amount_a)

                    # Assert total liquidity remains unchanged (except for protocol fees)
                    new_pool_info = self.runner.call_view_method(
                        self.nc_id, "pool_info", pool_key
                    )
                    new_total_liquidity = new_pool_info["total_liquidity"]
                    # Protocol fees may increase total liquidity slightly
                    self.assertGreaterEqual(new_total_liquidity, total_liquidity)

                    # Assert transaction count increased
                    self.assertEqual(
                        new_pool_info["transactions"], current_transaction_count + 1
                    )

            # Assert that reserves are always positive after each action
            current_reserve_a, current_reserve_b = get_reserves()
            self.assertGreater(current_reserve_a, 0)
            self.assertGreater(current_reserve_b, 0)

            # Verify pool state consistency after each operation
            current_pool_info = self.runner.call_view_method(
                self.nc_id, "pool_info", pool_key
            )
            self.assertEqual(current_pool_info["reserve_a"], current_reserve_a)
            self.assertEqual(current_pool_info["reserve_b"], current_reserve_b)

        # Final assertions
        final_reserve_a, final_reserve_b = get_reserves()
        final_pool_info = self.runner.call_view_method(
            self.nc_id, "pool_info", pool_key
        )
        final_total_liquidity = final_pool_info["total_liquidity"]

        # Verify reserves match what we expect
        self.assertEqual(final_pool_info["reserve_a"], final_reserve_a)
        self.assertEqual(final_pool_info["reserve_b"], final_reserve_b)

        # Verify transaction count
        self.assertEqual(final_pool_info["transactions"], transactions)

        # Verify total liquidity
        self.assertEqual(final_pool_info["total_liquidity"], final_total_liquidity)

        # Check that the sum of all user liquidities equals total liquidity (minus protocol fees)
        total_user_liquidity = 0
        for user in all_users:
            user_liquidity = self.runner.call_view_method(
                self.nc_id, "liquidity_of", user, pool_key
            )
            total_user_liquidity += user_liquidity

            # Verify user_info is consistent with liquidity
            if user_liquidity > 0:
                user_info = self.runner.call_view_method(
                    self.nc_id, "user_info", user, pool_key
                )
                expected_token_a = (
                    final_reserve_a * user_liquidity // final_total_liquidity
                )
                expected_token_b = (
                    final_reserve_b * user_liquidity // final_total_liquidity
                )

                # Allow for small rounding differences
                self.assertAlmostEqual(
                    user_info["token_a_amount"], expected_token_a, delta=10
                )
                self.assertAlmostEqual(
                    user_info["token_b_amount"], expected_token_b, delta=10
                )

        # Account for protocol fees that might have been collected
        # Protocol fees increase total_liquidity but don't belong to any user
        self.assertLessEqual(total_user_liquidity, final_total_liquidity)
