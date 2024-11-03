from enum import Enum
from hathor.conf.get_settings import HathorSettings
from hathor.types import Address, Amount, Timestamp, TokenUid
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.exception import NCFail
from hathor.crypto.util import get_address_b58_from_bytes
from hathor.nanocontracts.types import (
    Context,
    ContractId,
    NCAction,
    NCActionType,
    public,
)

settings = HathorSettings()
MIN_DEPOSIT = 10000_00
PRECISION = 10**20
MONTHS_IN_SECONDS = 30 * 24 * 3600


class Oasis(Blueprint):
    """Oasis contract that interacts with Dozer Pool contract."""

    dozer_pool: ContractId

    dev_address: Address
    dev_balance: Amount
    user_balances: dict[Address, Amount]
    user_liquidity: dict[Address, Amount]
    total_liquidity: Amount
    user_withdrawal_time: dict[Address, Timestamp]
    user_bonus: dict[Address, Amount]
    token_b: TokenUid

    @public
    def initialize(
        self, ctx: Context, dozer_pool: ContractId, token_b: TokenUid
    ) -> None:
        """Initialize the contract with no dozer pool set."""

        pool_token_a, pool_token_b = ctx.call_private_method(dozer_pool, "get_uuids")
        if pool_token_a != settings.HATHOR_TOKEN_UID or pool_token_b != token_b:
            raise (NCFail)
        action = self._get_token_action(
            ctx, NCActionType.DEPOSIT, settings.HATHOR_TOKEN_UID, auth=False
        )
        if action.amount < MIN_DEPOSIT or action.token_uid != settings.HATHOR_TOKEN_UID:
            raise NCFail("Deposit amount too low or token not HATHOR")
        self.token_b = token_b
        self.dev_address = ctx.address
        self.dozer_pool = dozer_pool
        self.dev_balance = action.amount
        self.total_liquidity = 0

    @public
    def dev_deposit(self, ctx: Context) -> None:
        action = self._get_action(ctx, NCActionType.DEPOSIT, auth=False)
        if action.token_uid != settings.HATHOR_TOKEN_UID:
            raise NCFail("Deposit token not HATHOR")
        self.dev_balance += action.amount

    @public
    def dev_withdraw(self, ctx: Context) -> None:
        action = self._get_action(ctx, NCActionType.WITHDRAWAL, auth=True)
        if action.token_uid != settings.HATHOR_TOKEN_UID:
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

        self.user_balances[ctx.address] = (
            self.user_balances.get(ctx.address, 0) + amount
        )
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
            delta = now - self.user_withdrawal_time[ctx.address]
            height = delta * self.user_balances[ctx.address]
            self.user_withdrawal_time[ctx.address] = (
                height + amount
            ) * timelock * MONTHS_IN_SECONDS // (delta + 6 * MONTHS_IN_SECONDS) + 1

        else:
            self.user_withdrawal_time[ctx.address] = now + timelock * MONTHS_IN_SECONDS

        self.dev_balance -= bonus + htr_amount
        self.user_bonus[ctx.address] = self.user_bonus.get(ctx.address, 0) + bonus
        actions = [
            action,
            NCAction(NCActionType.DEPOSIT, settings.HATHOR_TOKEN_UID, htr_amount),  # type: ignore
        ]
        ctx.call_public_method(self.dozer_pool, "add_liquidity", actions)

        # self.user_liquidity[self.dev_address] = (
        #     self.user_liquidity.get(self.dev_address, 0) + liquidity_increase
        # )

    def _get_oasis_lp_amount_b(self, ctx: Context) -> Amount:
        return ctx.call_private_method(
            self.dozer_pool,
            "max_withdraw_b",
            ctx.get_nanocontract_id(),
        )

    def _quote_add_liquidity_in(self, ctx: Context, amount: Amount) -> Amount:
        return ctx.call_private_method(
            self.dozer_pool, "front_quote_add_liquidity_in", amount, self.token_b
        )

    def _get_user_bonus(self, timelock: int, amount: Amount) -> Amount:
        """Calculates the bonus for a user based on the timelock and amount"""
        if timelock == 6:
            return 0.1**amount
        elif timelock == 9:
            return 0.15**amount
        elif timelock == 12:
            return 0.2**amount
        else:
            raise NCFail("Invalid timelock")

    def _get_action(
        self, ctx: Context, action_type: NCActionType, auth: bool
    ) -> NCAction:
        """Returns one action tested by type and index"""
        if len(ctx.actions) != 1:
            raise NCFail
        # if ctx.actions.keys not in rewardable_indexs:
        #     raise InvalidTokens()
        output = ctx.actions.popitem()[1]
        if output.type != action_type:
            raise NCFail
        if auth:
            if ctx.address != self.dev_address:
                raise NCFail

        return output

    def _get_token_action(
        self, ctx: Context, action_type: NCActionType, token: TokenUid, auth: bool
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
        return ctx.call_private_method(
            self.dozer_pool, "front_quote_add_liquidity_in", amount, token_uid
        )

    def user_info(
        self,
        address: Address,
    ) -> dict[str, float]:
        return {
            "user_balance": self.user_balances.get(address, 0),
            "user_liquidity": self.user_liquidity.get(address, 0),
            "user_withdrawal_time": self.user_withdrawal_time.get(address, 0),
            "dev_balance": self.dev_balance,
            "total_liquidity": self.total_liquidity,
            "user_bonus": self.user_bonus.get(address, 0),
        }
