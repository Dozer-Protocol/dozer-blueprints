import os
import random
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.dozer_pool import Dozer_Pool
from hathor.nanocontracts.blueprints.dozer_oasis import Oasis
from hathor.nanocontracts.types import NCAction, NCActionType
from hathor.pycoin import htr
from hathor.util import not_none
from hathor.conf.get_settings import HathorSettings
from hathor.wallet.keypair import KeyPair
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

from hathor.nanocontracts import Context, NCFail

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID
PRECISION = 10**20
MONTHS_IN_SECONDS = 30 * 24 * 3600


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
        self.dev_address = self._get_any_address()[0]
        self.token_b = self.gen_random_token_uid()
        # Initialize base tx for contexts
        self.tx = self.get_genesis_tx()

    def _get_any_address(self) -> tuple[bytes, KeyPair]:
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return address_bytes, key

    def _get_user_bonus(self, timelock: int, amount: int) -> int:
        """Calculates the bonus for a user based on the timelock and amount"""
        if timelock not in [6, 9, 12]:  # Assuming these are the only valid values
            raise NCFail("Invalid timelock value")
        bonus_multiplier = {6: 0.1, 9: 0.15, 12: 0.2}

        return int(bonus_multiplier[timelock] * amount)  # type: ignore

    def _quote_add_liquidity_in(self, amount: int) -> int:
        return self.runner.call_view_method(
            self.dozer_id, "front_quote_add_liquidity_in", amount, self.token_b
        )

    def _get_oasis_lp_amount_b(self) -> int:
        return self.runner.call_view_method(
            self.dozer_id,
            "max_withdraw_b",
            self.oasis_id,
        )

    def initialize_oasis(self, amount: int = 10_000_000_00) -> None:
        """Test basic initialization"""
        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, HTR_UID, amount),  # type: ignore
            ],
            self.tx,
            self.dev_address,
            timestamp=0,
        )
        self.runner.call_public_method(
            self.oasis_id, "initialize", ctx, self.dozer_id, self.token_b
        )
        # self.assertIsNone(self.oasis_storage.get("dozer_pool"))

    def initialize_pool(
        self, amount_htr: int = 1000000, amount_b: int = 7000000
    ) -> None:
        """Test basic initialization"""
        # Initialize dozer pool first
        actions = [
            NCAction(NCActionType.DEPOSIT, HTR_UID, amount_htr),  # type: ignore
            NCAction(NCActionType.DEPOSIT, self.token_b, amount_b),  # type: ignore
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
        dev_initial_deposit = 10_000_000_00
        self.initialize_pool()
        self.initialize_oasis()
        self.assertEqual(self.oasis_storage.get("dev_balance"), dev_initial_deposit)

    def test_user_deposit(self, timelock=6) -> tuple[Context, int, int]:
        dev_initial_deposit = 10_000_000_00
        self.initialize_pool()
        self.initialize_oasis(amount=dev_initial_deposit)
        user_address = self._get_any_address()[0]
        now = self.clock.seconds()
        deposit_amount = 1_000_00
        ctx = Context(
            [
                NCAction(NCActionType.DEPOSIT, self.token_b, deposit_amount),  # type: ignore
            ],
            self.tx,
            user_address,
            timestamp=now,
        )
        self.runner.call_public_method(self.oasis_id, "user_deposit", ctx, timelock)
        user_info = self.runner.call_view_method(
            self.oasis_id, "user_info", user_address
        )
        htr_amount = self._quote_add_liquidity_in(deposit_amount)
        user_bonus = self._get_user_bonus(timelock, htr_amount)
        self.assertEqual(user_info["user_deposit_b"], deposit_amount)
        self.assertEqual(user_info["user_balance_a"], user_bonus)
        self.assertEqual(user_info["user_balance_b"], 0)
        self.assertEqual(user_info["user_liquidity"], deposit_amount * PRECISION)
        self.assertEqual(
            user_info["user_withdrawal_time"], now + timelock * MONTHS_IN_SECONDS
        )
        self.assertEqual(
            user_info["dev_balance"], dev_initial_deposit - htr_amount - user_bonus
        )
        self.assertEqual(user_info["total_liquidity"], deposit_amount * PRECISION)
        return ctx, timelock, htr_amount

    def test_multiple_user_deposit_no_repeat(self) -> None:
        dev_initial_deposit = 10_000_000_00
        self.initialize_pool()
        self.initialize_oasis(amount=dev_initial_deposit)
        n_users = 100
        user_addresses = [self._get_any_address()[0] for _ in range(n_users)]
        user_liquidity = [0] * n_users
        user_balances_a = [0] * n_users
        user_deposit_b = [0] * n_users
        total_liquidity = 0
        dev_balance = dev_initial_deposit
        for i, user_address in enumerate(user_addresses):
            now = self.clock.seconds()
            deposit_amount = 1_000_00
            ## random choice of timelock between the possibilities: 6,9 and 12
            timelock = random.choice([6, 9, 12])
            ctx = Context(
                [
                    NCAction(NCActionType.DEPOSIT, self.token_b, deposit_amount),  # type: ignore
                ],
                self.tx,
                user_address,
                timestamp=now,
            )
            lp_amount_b = self._get_oasis_lp_amount_b()
            self.runner.call_public_method(self.oasis_id, "user_deposit", ctx, timelock)
            htr_amount = self._quote_add_liquidity_in(deposit_amount)
            bonus = self._get_user_bonus(timelock, htr_amount)
            user_info = self.runner.call_view_method(
                self.oasis_id, "user_info", user_address
            )
            if total_liquidity == 0:
                total_liquidity = deposit_amount * PRECISION
                user_liquidity[i] = deposit_amount * PRECISION
            else:
                liquidity_increase = (
                    (total_liquidity / PRECISION) * deposit_amount / lp_amount_b
                )
                user_liquidity[i] = user_liquidity[i] + int(
                    PRECISION * liquidity_increase
                )
                total_liquidity += int(PRECISION * liquidity_increase)

            dev_balance -= bonus + htr_amount
            user_balances_a[i] = user_balances_a[i] + bonus
            user_deposit_b[i] = deposit_amount
            self.assertEqual(user_info["dev_balance"], dev_balance)
            self.assertEqual(user_info["user_balance_a"], user_balances_a[i])
            self.assertEqual(user_info["user_deposit_b"], user_deposit_b[i])
            self.assertEqual(user_info["user_liquidity"], user_liquidity[i])
            self.assertEqual(
                user_info["user_withdrawal_time"], now + timelock * MONTHS_IN_SECONDS
            )
            self.assertEqual(user_info["total_liquidity"], total_liquidity)
            self.assertEqual(user_info["dev_balance"], dev_balance)

    def test_multiple_user_deposit_with_repeat(self) -> None:
        dev_initial_deposit = 10_000_000_00
        self.initialize_pool()
        self.initialize_oasis(amount=dev_initial_deposit)
        n_users = 10
        n_transactions = 500
        user_addresses = [self._get_any_address()[0] for _ in range(n_users)]
        user_liquidity = [0] * n_users
        user_balances_a = [0] * n_users
        user_deposit_b = [0] * n_users
        user_withdrawal_time = [0] * n_users
        total_liquidity = 0
        dev_balance = dev_initial_deposit
        initial_time = self.clock.seconds()
        for transaction in range(n_transactions):
            i = random.randint(0, n_users - 1)
            user_address = user_addresses[i]
            now = initial_time + transaction * 50
            deposit_amount = 1_000_00
            ## random choice of timelock between the possibilities: 6,9 and 12
            timelock = random.choice([6, 9, 12])
            ctx = Context(
                [
                    NCAction(NCActionType.DEPOSIT, self.token_b, deposit_amount),  # type: ignore
                ],
                self.tx,
                user_address,
                timestamp=now,
            )
            lp_amount_b = self._get_oasis_lp_amount_b()
            self.runner.call_public_method(self.oasis_id, "user_deposit", ctx, timelock)
            htr_amount = self._quote_add_liquidity_in(deposit_amount)
            bonus = self._get_user_bonus(timelock, htr_amount)
            user_info = self.runner.call_view_method(
                self.oasis_id, "user_info", user_address
            )
            user_balances_a[i] += bonus
            if total_liquidity == 0:
                total_liquidity = deposit_amount * PRECISION
                user_liquidity[i] = deposit_amount * PRECISION
            else:
                liquidity_increase = (
                    (total_liquidity / PRECISION) * deposit_amount / lp_amount_b
                )
                user_liquidity[i] = user_liquidity[i] + int(
                    PRECISION * liquidity_increase
                )
                total_liquidity += int(PRECISION * liquidity_increase)

            if user_withdrawal_time[i] != 0:
                delta = now - user_withdrawal_time[i]
                user_withdrawal_time[i] = (
                    (
                        (delta * user_deposit_b[i])
                        + (deposit_amount * timelock * MONTHS_IN_SECONDS)
                    )
                    // (delta + timelock * MONTHS_IN_SECONDS)
                ) + 1
            else:
                user_withdrawal_time[i] = now + timelock * MONTHS_IN_SECONDS

            dev_balance -= bonus + htr_amount
            user_deposit_b[i] += deposit_amount
            self.assertEqual(user_info["dev_balance"], dev_balance)
            self.assertEqual(user_info["user_balance_a"], user_balances_a[i])
            self.assertEqual(user_info["user_deposit_b"], user_deposit_b[i])
            self.assertEqual(user_info["user_liquidity"], user_liquidity[i])
            self.assertEqual(user_info["user_withdrawal_time"], user_withdrawal_time[i])
            self.assertEqual(user_info["total_liquidity"], total_liquidity)

    def test_user_withdraw_exact_value(self):
        ctx_deposit, timelock, htr_amount = self.test_user_deposit()
        user_address = ctx_deposit.address
        action = ctx_deposit.actions.get(self.token_b) or NCAction(
            NCActionType.WITHDRAWAL, self.token_b, 0
        )
        deposit_amount = action.amount
        deposit_timestamp = ctx_deposit.timestamp
        user_info = self.runner.call_view_method(
            self.oasis_id, "user_info", user_address
        )
        bonus = self._get_user_bonus(timelock, htr_amount)
        self.assertEqual(user_info["user_deposit_b"], deposit_amount)
        self.assertEqual(user_info["user_balance_a"], bonus)
        self.assertEqual(user_info["user_balance_b"], 0)
        self.assertEqual(user_info["user_liquidity"], deposit_amount * PRECISION)
        self.assertEqual(
            user_info["user_withdrawal_time"],
            deposit_timestamp + timelock * MONTHS_IN_SECONDS,
        )
        self.assertEqual(user_info["dev_balance"], 10_000_000_00 - htr_amount - bonus)
        self.assertEqual(user_info["total_liquidity"], deposit_amount * PRECISION)
        # Withdraw exact value
        ctx = Context(
            [
                NCAction(NCActionType.WITHDRAWAL, self.token_b, deposit_amount),  # type: ignore
                NCAction(NCActionType.WITHDRAWAL, HTR_UID, bonus),  # type: ignore
            ],
            self.tx,
            user_address,
            timestamp=deposit_timestamp + timelock * MONTHS_IN_SECONDS + 1,
        )
        self.runner.call_public_method(self.oasis_id, "user_withdraw", ctx)
        user_info = self.runner.call_view_method(
            self.oasis_id, "user_info", user_address
        )
        self.assertEqual(user_info["user_deposit_b"], 0)
        self.assertEqual(user_info["user_balance_a"], 0)
        self.assertEqual(user_info["user_balance_b"], 0)
        self.assertEqual(user_info["user_liquidity"], 0)
        self.assertEqual(
            user_info["user_withdrawal_time"],
            deposit_timestamp + timelock * MONTHS_IN_SECONDS,
        )

    def test_user_withdraw_bonus(self):
        ctx_deposit, timelock, htr_amount = self.test_user_deposit()
        user_address = ctx_deposit.address
        action = ctx_deposit.actions.get(self.token_b) or NCAction(
            NCActionType.WITHDRAWAL, self.token_b, 0
        )
        deposit_amount = action.amount
        deposit_timestamp = ctx_deposit.timestamp
        user_info = self.runner.call_view_method(
            self.oasis_id, "user_info", user_address
        )
        bonus = self._get_user_bonus(timelock, htr_amount)
        self.assertEqual(user_info["user_deposit_b"], deposit_amount)
        self.assertEqual(user_info["user_balance_a"], bonus)
        self.assertEqual(user_info["user_balance_b"], 0)
        self.assertEqual(user_info["user_liquidity"], deposit_amount * PRECISION)
        self.assertEqual(
            user_info["user_withdrawal_time"],
            deposit_timestamp + timelock * MONTHS_IN_SECONDS,
        )
        ctx_withdraw_bonus = Context(
            [
                NCAction(NCActionType.WITHDRAWAL, HTR_UID, bonus),  # type: ignore
            ],
            self.tx,
            user_address,
            timestamp=deposit_timestamp + 1,
        )
        self.runner.call_public_method(
            self.oasis_id, "user_withdraw_bonus", ctx_withdraw_bonus
        )
        self.log.info(f"{bonus=}")
        user_info = self.runner.call_view_method(
            self.oasis_id, "user_info", user_address
        )
        self.assertEqual(user_info["user_balance_a"], 0)
        self.assertEqual(user_info["user_balance_b"], 0)
        ctx_withdraw_bonus_wrong = Context(
            [
                NCAction(NCActionType.WITHDRAWAL, HTR_UID, bonus + 1),  # type: ignore
            ],
            self.tx,
            user_address,
            timestamp=deposit_timestamp + 1,
        )
        with self.assertRaises(NCFail):
            self.runner.call_public_method(
                self.oasis_id, "user_withdraw_bonus", ctx_withdraw_bonus_wrong
            )

    # def test_impermanent_loss_protection_scenarios(self):
    #     """Test various impermanent loss scenarios to verify protection mechanism works correctly.

    #     Scenarios tested:
    #     1. Token B outperforms HTR by 4x (protection limit)
    #     2. Token B outperforms HTR by >4x (partial protection)
    #     3. HTR outperforms Token B (no protection needed)
    #     4. Multiple users with different IL conditions
    #     """
    #     # Initialize with substantial liquidity
    #     dev_initial_deposit = 100_000_000_00  # 100M HTR
    #     pool_initial_htr = 10_000_000_00  # 10M HTR
    #     pool_initial_token_b = 10_000_000_00  # 10M Token B

    #     # Initialize contracts
    #     self.initialize_pool(amount_htr=pool_initial_htr, amount_b=pool_initial_token_b)
    #     self.initialize_oasis(amount=dev_initial_deposit)

    #     # Setup test users
    #     users = [self._get_any_address()[0] for _ in range(4)]
    #     deposit_amounts = [1_000_000_00, 2_000_000_00, 3_000_000_00, 4_000_000_00]
    #     timelocks = [6, 9, 12, 12]  # Months

    #     # Store initial deposits for each user
    #     user_deposits = {}
    #     initial_time = self.clock.seconds()

    #     # Make initial deposits
    #     for i, user in enumerate(users):
    #         ctx = Context(
    #             [NCAction(NCActionType.DEPOSIT, self.token_b, deposit_amounts[i])],
    #             self.tx,
    #             user,
    #             timestamp=initial_time,
    #         )
    #         self.runner.call_public_method(
    #             self.oasis_id, "user_deposit", ctx, timelocks[i]
    #         )

    #         # Store deposit info
    #         htr_amount = self._quote_add_liquidity_in(deposit_amounts[i])
    #         user_deposits[user] = {
    #             "deposit_amount": deposit_amounts[i],
    #             "htr_amount": htr_amount,
    #             "timelock": timelocks[i],
    #             "timelock_end": initial_time + timelocks[i] * MONTHS_IN_SECONDS,
    #             "bonus": self._get_user_bonus(timelocks[i], htr_amount),
    #         }

    #     # Scenario 1: Make Token B outperform HTR by 4x through swaps
    #     # First add more liquidity to allow for large swaps
    #     extra_liquidity_address = self._get_any_address()[0]
    #     add_liquidity_ctx = Context(
    #         [
    #             NCAction(NCActionType.DEPOSIT, HTR_UID, pool_initial_htr),
    #             NCAction(NCActionType.DEPOSIT, self.token_b, pool_initial_token_b),
    #         ],
    #         self.tx,
    #         extra_liquidity_address,
    #         timestamp=initial_time + MONTHS_IN_SECONDS,
    #     )
    #     self.runner.call_public_method(
    #         self.dozer_id, "add_liquidity", add_liquidity_ctx
    #     )

    #     # Execute large HTR->Token B swaps to drive up Token B price
    #     for _ in range(5):  # Multiple swaps to achieve desired price ratio
    #         swap_amount = pool_initial_htr // 10
    #         reserve_a = self.dozer_storage.get("reserve_a")
    #         reserve_b = self.dozer_storage.get("reserve_b")
    #         amount_out = self.runner.call_view_method(
    #             self.dozer_id, "get_amount_out", swap_amount, reserve_a, reserve_b
    #         )

    #         swap_ctx = Context(
    #             [
    #                 NCAction(NCActionType.DEPOSIT, HTR_UID, swap_amount),
    #                 NCAction(NCActionType.WITHDRAWAL, self.token_b, amount_out),
    #             ],
    #             self.tx,
    #             extra_liquidity_address,
    #             timestamp=initial_time + MONTHS_IN_SECONDS + 100,
    #         )
    #         self.runner.call_public_method(
    #             self.dozer_id, "swap_exact_tokens_for_tokens", swap_ctx
    #         )

    #     # Test withdrawal for first user under 4x scenario
    #     withdraw_time = user_deposits[users[0]]["timelock_end"] + 1
    #     withdraw_ctx = Context(
    #         [
    #             NCAction(NCActionType.WITHDRAWAL, self.token_b, deposit_amounts[0]),
    #             NCAction(
    #                 NCActionType.WITHDRAWAL,
    #                 HTR_UID,
    #                 user_deposits[users[0]]["htr_amount"],
    #             ),
    #         ],
    #         self.tx,
    #         users[0],
    #         timestamp=withdraw_time,
    #     )

    #     self.runner.call_public_method(self.oasis_id, "user_withdraw", withdraw_ctx)

    #     # Verify user received full protection
    #     user_info = self.runner.call_view_method(self.oasis_id, "user_info", users[0])
    #     self.assertEqual(user_info["user_deposit_b"], 0)  # User withdrew everything
    #     self.assertGreater(
    #         user_info["user_balance_a"], 0
    #     )  # Should have protection payment

    #     # Scenario 2: Push Token B outperformance to 8x with more swaps
    #     for _ in range(5):
    #         swap_amount = pool_initial_htr // 8
    #         reserve_a = self.dozer_storage.get("reserve_a")
    #         reserve_b = self.dozer_storage.get("reserve_b")
    #         amount_out = self.runner.call_view_method(
    #             self.dozer_id, "get_amount_out", swap_amount, reserve_a, reserve_b
    #         )

    #         swap_ctx = Context(
    #             [
    #                 NCAction(NCActionType.DEPOSIT, HTR_UID, swap_amount),
    #                 NCAction(NCActionType.WITHDRAWAL, self.token_b, amount_out),
    #             ],
    #             self.tx,
    #             extra_liquidity_address,
    #             timestamp=initial_time + 2 * MONTHS_IN_SECONDS,
    #         )
    #         self.runner.call_public_method(
    #             self.dozer_id, "swap_exact_tokens_for_tokens", swap_ctx
    #         )

    #     # Test withdrawal for second user under 8x scenario
    #     withdraw_time = user_deposits[users[1]]["timelock_end"] + 1
    #     withdraw_ctx = Context(
    #         [
    #             NCAction(NCActionType.WITHDRAWAL, self.token_b, deposit_amounts[1]),
    #             NCAction(
    #                 NCActionType.WITHDRAWAL,
    #                 HTR_UID,
    #                 user_deposits[users[1]]["htr_amount"],
    #             ),
    #         ],
    #         self.tx,
    #         users[1],
    #         timestamp=withdraw_time,
    #     )

    #     self.runner.call_public_method(self.oasis_id, "user_withdraw", withdraw_ctx)

    #     # Verify partial protection
    #     user_info = self.runner.call_view_method(self.oasis_id, "user_info", users[1])
    #     self.assertGreater(user_info["user_balance_a"], 0)
    #     self.assertLess(
    #         user_info["user_balance_a"], user_deposits[users[1]]["htr_amount"]
    #     )

    #     # Scenario 3: Make HTR outperform Token B through reverse swaps
    #     for _ in range(5):
    #         swap_amount = pool_initial_token_b // 10
    #         reserve_b = self.dozer_storage.get("reserve_b")
    #         reserve_a = self.dozer_storage.get("reserve_a")
    #         amount_out = self.runner.call_view_method(
    #             self.dozer_id, "get_amount_out", swap_amount, reserve_b, reserve_a
    #         )

    #         swap_ctx = Context(
    #             [
    #                 NCAction(NCActionType.DEPOSIT, self.token_b, swap_amount),
    #                 NCAction(NCActionType.WITHDRAWAL, HTR_UID, amount_out),
    #             ],
    #             self.tx,
    #             extra_liquidity_address,
    #             timestamp=initial_time + 3 * MONTHS_IN_SECONDS,
    #         )
    #         self.runner.call_public_method(
    #             self.dozer_id, "swap_exact_tokens_for_tokens", swap_ctx
    #         )

    #     # Test withdrawal for third user when HTR outperforms
    #     withdraw_time = user_deposits[users[2]]["timelock_end"] + 1
    #     withdraw_ctx = Context(
    #         [
    #             NCAction(NCActionType.WITHDRAWAL, self.token_b, deposit_amounts[2]),
    #             NCAction(
    #                 NCActionType.WITHDRAWAL,
    #                 HTR_UID,
    #                 user_deposits[users[2]]["htr_amount"],
    #             ),
    #         ],
    #         self.tx,
    #         users[2],
    #         timestamp=withdraw_time,
    #     )

    #     self.runner.call_public_method(self.oasis_id, "user_withdraw", withdraw_ctx)

    #     # Verify no protection needed
    #     user_info = self.runner.call_view_method(self.oasis_id, "user_info", users[2])
    #     self.assertEqual(user_info["user_deposit_b"], 0)
    #     self.assertEqual(user_info["user_balance_a"], 0)  # No protection payment needed

    #     # Verify overall contract state
    #     total_protection_paid = sum(
    #         self.runner.call_view_method(self.oasis_id, "user_info", user)[
    #             "user_balance_a"
    #         ]
    #         for user in users
    #     )

    #     # Verify protection payments didn't exceed dev balance
    #     self.assertLess(total_protection_paid, dev_initial_deposit)
