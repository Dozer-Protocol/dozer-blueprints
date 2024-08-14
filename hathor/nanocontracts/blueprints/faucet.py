from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import Context, NCAction, NCActionType, public
from hathor.types import TokenUid, Address, Amount
from typing import Dict


class Faucet(Blueprint):
    token_uid: TokenUid
    admin_address: Address
    max_withdrawal: Amount
    total_supply: Amount
    user_withdrawals: dict[Address, Amount]

    @public
    def initialize(
        self,
        ctx: Context,
        token_uid: TokenUid,
        max_withdrawal: Amount,
    ) -> None:
        action = self._get_deposit_action(ctx)
        if action.token_uid != token_uid:
            raise NCFail("Invalid deposit")

        self.token_uid = token_uid
        self.admin_address = ctx.address
        self.max_withdrawal = max_withdrawal
        self.total_supply = action.amount
        # self.user_withdrawals = {}

    @public
    def deposit(self, ctx: Context) -> None:
        # self._check_admin(ctx)
        action = self._get_deposit_action(ctx)
        if action.token_uid != self.token_uid:
            raise NCFail("Invalid token")
        self.total_supply += action.amount

    @public
    def set_max_withdrawal(self, ctx: Context, new_max: Amount) -> None:
        self._check_admin(ctx)
        if new_max <= 0:
            raise NCFail("Max withdrawal must be positive")
        self.max_withdrawal = new_max

    @public
    def withdraw(self, ctx: Context) -> None:
        if not hasattr(self, "token_uid") or self.token_uid is None:
            raise NCFail("Faucet not initialized")

        action = self._get_withdrawal_action(ctx)
        if action.token_uid != self.token_uid:
            raise NCFail("Invalid token")

        user_total_withdrawal = (
            self.user_withdrawals.get(ctx.address, 0) + action.amount
        )
        if user_total_withdrawal > self.max_withdrawal:
            raise NCFail(f"Withdrawal exceeds maximum allowed ({self.max_withdrawal})")

        if action.amount > self.total_supply:
            raise NCFail("Insufficient funds in faucet")

        self.user_withdrawals[ctx.address] = user_total_withdrawal
        self.total_supply -= action.amount

    @public
    def admin_withdraw(self, ctx: Context) -> None:
        self._check_admin(ctx)
        action = self._get_withdrawal_action(ctx)
        if action.token_uid != self.token_uid:
            raise NCFail("Invalid token")

        if action.amount > self.total_supply:
            raise NCFail("Insufficient funds in faucet")

        self.total_supply -= action.amount

    def _check_admin(self, ctx: Context) -> None:
        if ctx.address != self.admin_address:
            raise NCFail("Only admin can perform this action")

    def _get_deposit_action(self, ctx: Context) -> NCAction:
        if len(ctx.actions) != 1:
            raise NCFail("Only one action allowed")
        action = next(iter(ctx.actions.values()))
        if action.type != NCActionType.DEPOSIT:
            raise NCFail("Action must be a deposit")
        return action

    def _get_withdrawal_action(self, ctx: Context) -> NCAction:
        if len(ctx.actions) != 1:
            raise NCFail("Only one action allowed")
        action = next(iter(ctx.actions.values()))
        if action.type != NCActionType.WITHDRAWAL:
            raise NCFail("Action must be a withdrawal")
        return action

    def get_user_withdrawal(self, user: Address) -> Amount:
        return self.user_withdrawals.get(user, 0)
