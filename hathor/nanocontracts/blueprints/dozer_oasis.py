from curses.ascii import HT
from enum import Enum
from hathor.conf.get_settings import HathorSettings
from hathor.nanocontracts.context import Context
from hathor.types import Address, Amount, Timestamp, TokenUid
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.exception import NCFail
from hathor.crypto.util import get_address_b58_from_bytes
from hathor.nanocontracts.types import (
    view,
    ContractId,
    NCAction,
    NCActionType,
    public,
)

MIN_DEPOSIT = 10000_00
PRECISION = 10**20
MONTHS_IN_SECONDS = 30 * 24 * 3600
HTR_UID = HathorSettings().HATHOR_TOKEN_UID  # type: ignore


class Oasis(Blueprint):
    """Oasis contract that interacts with Dozer Pool contract."""

    dozer_pool: ContractId

    dev_address: Address
    dev_balance: Amount
    user_deposit_b: dict[Address, Amount]
    user_liquidity: dict[Address, Amount]
    total_liquidity: Amount
    user_withdrawal_time: dict[Address, Timestamp]
    user_balances: dict[Address, dict[TokenUid, Amount]]
    token_b: TokenUid

    @public
    def initialize(
        self, ctx: Context, dozer_pool: ContractId, token_b: TokenUid
    ) -> None:
        """Initialize the contract with no dozer pool set."""

        pool_token_a, pool_token_b = ctx.call_view_method(dozer_pool, "get_uuids")
        if pool_token_a != HTR_UID or pool_token_b != token_b:
            raise (NCFail)
        action = self._get_action(ctx, NCActionType.DEPOSIT, auth=False)
        if action.amount < MIN_DEPOSIT or action.token_uid != HTR_UID:
            raise NCFail("Deposit amount too low or token not HATHOR")
        self.token_b = token_b
        self.dev_address = ctx.address
        self.dozer_pool = dozer_pool
        self.dev_balance = action.amount
        self.total_liquidity = 0

    @public
    def dev_deposit(self, ctx: Context) -> None:
        """Deposits token B with a timelock period for bonus rewards.

        Args:
            ctx: Execution context
            timelock: Lock period in months (6, 9, or 12)

        Raises:
            NCFail: If deposit requirements not met or invalid timelock
        """
        action = self._get_action(ctx, NCActionType.DEPOSIT, auth=False)
        if action.token_uid != HTR_UID:
            raise NCFail("Deposit token not HATHOR")
        self.dev_balance += action.amount

    @public
    def dev_withdraw(self, ctx: Context) -> None:
        action = self._get_action(ctx, NCActionType.WITHDRAWAL, auth=True)
        if action.token_uid != HTR_UID:
            raise NCFail("Withdrawal token not HATHOR")
        if action.amount > self.dev_balance:
            raise NCFail("Withdrawal amount too high")
        self.dev_balance -= action.amount

    @public
    def user_deposit(
        self, ctx: Context, timelock: int  # 0 6 months, 1 9 months, 2 1 year
    ) -> None:
        action = self._get_token_action(
            ctx, NCActionType.DEPOSIT, self.token_b, auth=False
        )
        if action.token_uid != self.token_b:
            raise NCFail("Deposit token not B")
        amount = action.amount
        htr_amount = self._quote_add_liquidity_in(ctx, amount)
        bonus = self._get_user_bonus(timelock, htr_amount)
        now = ctx.timestamp
        if htr_amount + bonus > self.dev_balance:
            raise NCFail("Not enough balance")

        if self.total_liquidity == 0:
            self.total_liquidity = amount * PRECISION
            self.user_liquidity[ctx.address] = amount * PRECISION
        else:
            liquidity_increase = (
                (self.total_liquidity / PRECISION)
                * amount
                / self._get_oasis_lp_amount_b(ctx)
            )
            self.user_liquidity[ctx.address] = self.user_liquidity.get(
                ctx.address, 0
            ) + int(PRECISION * liquidity_increase)
            self.total_liquidity += int(PRECISION * liquidity_increase)
        if ctx.address in self.user_withdrawal_time:
            # self.log.info(
            #     f"inside user withdrawal time antes {self.user_withdrawal_time[ctx.address]}"
            # )
            delta = now - self.user_withdrawal_time[ctx.address]
            self.user_withdrawal_time[ctx.address] = (
                (
                    (delta * self.user_deposit_b[ctx.address])
                    + (amount * timelock * MONTHS_IN_SECONDS)
                )
                // (delta + timelock * MONTHS_IN_SECONDS)
            ) + 1

        else:
            self.user_withdrawal_time[ctx.address] = now + timelock * MONTHS_IN_SECONDS

        self.dev_balance -= bonus + htr_amount
        # Update dict indexed by address
        partial = self.user_balances.get(ctx.address, {})
        partial.update(
            {
                HTR_UID: partial.get(HTR_UID, 0) + bonus,
            }
        )
        self.user_balances[ctx.address] = partial
        self.user_deposit_b[ctx.address] = (
            self.user_deposit_b.get(ctx.address, 0) + amount
        )

        actions = [
            action,
            NCAction(NCActionType.DEPOSIT, HTR_UID, htr_amount),  # type: ignore
        ]
        ctx.call_public_method(self.dozer_pool, "add_liquidity", actions)

        # self.user_liquidity[self.dev_address] = (
        #     self.user_liquidity.get(self.dev_address, 0) + liquidity_increase
        # )

    @public
    def user_withdraw(self, ctx: Context) -> None:
        action_token_b = self._get_token_action(
            ctx, NCActionType.WITHDRAWAL, self.token_b
        )
        action_htr = self._get_token_action(ctx, NCActionType.WITHDRAWAL, HTR_UID)
        if ctx.timestamp < self.user_withdrawal_time[ctx.address]:
            raise NCFail("Withdrawal locked")
        oasis_quote = self._quote_remove_liquidity_oasis(ctx)
        htr_oasis_amount = oasis_quote["max_withdraw_a"]
        token_b_oasis_amount = oasis_quote["user_lp_b"]
        user_liquidity = self.user_liquidity.get(ctx.address, 0)
        user_lp_b = int(
            (user_liquidity / PRECISION)
            * token_b_oasis_amount
            / (self.total_liquidity / PRECISION)
        )
        user_lp_htr = int(
            (user_liquidity / PRECISION)
            * htr_oasis_amount
            / (self.total_liquidity / PRECISION)
        )
        actions = [
            NCAction(NCActionType.WITHDRAWAL, HTR_UID, user_lp_htr),  # type: ignore
            NCAction(NCActionType.WITHDRAWAL, self.token_b, user_lp_b),  # type: ignore
        ]
        ctx.call_public_method(self.dozer_pool, "remove_liquidity", actions)
        max_withdraw_b = user_lp_b + self.user_balances[ctx.address].get(
            self.token_b, 0
        )
        if user_lp_b >= self.user_deposit_b[ctx.address]:  # without impermanent loss

            if action_token_b.amount > max_withdraw_b:
                raise NCFail("Not enough balance")
            else:
                partial = self.user_balances.get(ctx.address, {})
                partial.update(
                    {
                        self.token_b: max_withdraw_b - action_token_b.amount,
                    }
                )
                self.user_balances[ctx.address] = partial
            # impermanent loss
            if self.user_deposit_b[ctx.address] > user_lp_b:
                loss = self.user_deposit_b[ctx.address] - user_lp_b
                loss_htr = ctx.call_view_method(self.dozer_pool, "quote_token_b", loss)
                if loss_htr > user_lp_htr:
                    loss_htr = user_lp_htr
                max_withdraw_htr = (
                    self.user_balances[ctx.address].get(HTR_UID, 0) + loss_htr
                )
            # without impermanent loss
            else:
                max_withdraw_htr = self.user_balances[ctx.address].get(HTR_UID, 0)

            if action_htr.amount > max_withdraw_htr:
                raise NCFail("Not enough balance")
            partial = self.user_balances.get(ctx.address, {})
            partial.update(
                {
                    HTR_UID: max_withdraw_htr - action_htr.amount,
                }
            )
            self.user_balances[ctx.address] = partial
            self.user_liquidity[ctx.address] = 0
            self.user_deposit_b[ctx.address] = 0

    def _get_oasis_lp_amount_b(self, ctx: Context) -> Amount:
        return ctx.call_view_method(
            self.dozer_pool,
            "max_withdraw_b",
            ctx.get_nanocontract_id(),
        )

    def _quote_add_liquidity_in(self, ctx: Context, amount: Amount) -> Amount:
        return ctx.call_view_method(
            self.dozer_pool, "front_quote_add_liquidity_in", amount, self.token_b
        )

    def _quote_remove_liquidity_oasis(self, ctx: Context) -> dict[str, float]:
        return ctx.call_view_method(
            self.dozer_pool, "quote_remove_liquidity", ctx.get_nanocontract_id()
        )

    def _get_user_bonus(self, timelock: int, amount: Amount) -> Amount:
        """Calculates the bonus for a user based on the timelock and amount"""
        if timelock not in [6, 9, 12]:  # Assuming these are the only valid values
            raise NCFail("Invalid timelock value")
        bonus_multiplier = {6: 0.1, 9: 0.15, 12: 0.2}

        return int(bonus_multiplier[timelock] * amount)  # type: ignore

    def _get_action(
        self, ctx: Context, action_type: NCActionType, auth: bool
    ) -> NCAction:
        """Returns one action tested by type and index"""
        if len(ctx.actions) != 1:
            raise NCFail
        # if ctx.actions.keys not in rewardable_indexs:
        #     raise InvalidTokens()
        keys = ctx.actions.keys()
        output = ctx.actions.get(HTR_UID)
        if output.type != action_type:
            raise NCFail
        if auth:
            if ctx.address != self.dev_address:
                raise NCFail

        return output

    def _get_token_action(
        self,
        ctx: Context,
        action_type: NCActionType,
        token: TokenUid,
        auth: bool = False,
    ) -> NCAction:
        """Returns one action tested by type and index"""
        if len(ctx.actions) > 2:
            raise NCFail
        try:
            output = ctx.actions.get(token)
        except:
            raise NCFail

        if output.type != action_type:
            raise NCFail
        if auth:
            if ctx.address != self.dev_address:
                raise NCFail

        return output

    @public
    def check_pool_liquidity(self, ctx: Context, token_uid: bytes, amount: int) -> dict:
        """Check liquidity for adding tokens to the pool.

        Args:
            ctx: The execution context
            token_uid: The token to check liquidity for
            amount: The amount to check

        Returns:
            The liquidity quote from the pool

        Raises:
            NCFail: If dozer pool is not set
        """
        if self.dozer_pool is None:
            raise NCFail("Dozer pool contract not set")

        # Call the private method on the dozer pool contract
        return ctx.call_view_method(
            self.dozer_pool, "front_quote_add_liquidity_in", amount, token_uid
        )

    @view
    def user_info(
        self,
        address: Address,
    ) -> dict[str, float]:
        return {
            "user_deposit_b": self.user_deposit_b.get(address, 0),
            "user_liquidity": self.user_liquidity.get(address, 0),
            "user_withdrawal_time": self.user_withdrawal_time.get(address, 0),
            "dev_balance": self.dev_balance,
            "total_liquidity": self.total_liquidity,
            "user_balance_a": self.user_balances.get(address, {HTR_UID: 0}).get(
                HTR_UID, 0
            ),
            "user_balance_b": self.user_balances.get(address, {self.token_b: 0}).get(
                self.token_b, 0
            ),
        }
