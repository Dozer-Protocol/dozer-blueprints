from hathor import (
    Blueprint,
    Context,
    NCFail,
    Address,
    Amount,
    ContractId,
    NCAction,
    NCActionType,
    NCDepositAction,
    NCWithdrawalAction,
    Timestamp,
    TokenUid,
    public,
    view,
)
from hathor.conf.get_settings import HathorSettings

MIN_DEPOSIT = 10000_00
PRECISION = 10**20
MONTHS_IN_SECONDS = 60
HTR_UID = HathorSettings().HATHOR_TOKEN_UID  # type: ignore


class Oasis(Blueprint):
    """Oasis contract that interacts with Dozer Pool contract."""

    dozer_pool: ContractId
    protocol_fee: Amount

    owner_address: Address
    dev_address: Address
    oasis_htr_balance: Amount
    dev_deposit_amount: Amount
    user_deposit_b: dict[Address, Amount]
    htr_price_in_deposit: dict[Address, Amount]
    token_price_in_htr_in_deposit: dict[Address, Amount]
    user_liquidity: dict[Address, Amount]
    total_liquidity: Amount
    user_withdrawal_time: dict[Address, Timestamp]
    user_balances: dict[Address, dict[TokenUid, Amount]]
    token_b: TokenUid
    # Track if a user's position has been closed and is ready for withdrawal
    user_position_closed: dict[Address, bool]
    # Track withdrawn balances separately from cashback/rewards
    closed_position_balances: dict[Address, dict[TokenUid, Amount]]

    @public(allow_deposit=True)
    def initialize(
        self,
        ctx: Context,
        dozer_pool: ContractId,
        token_b: TokenUid,
        protocol_fee: Amount,
    ) -> None:
        """Initialize the contract with dozer pool set."""
        # Get pool information by calling a view method or checking tokens directly
        # Note: The SDK doesn't expose get_uuids, so we need to validate differently
        action = self._get_action(ctx, NCActionType.DEPOSIT, auth=False)
        if not isinstance(action, NCDepositAction):
            raise NCFail("Deposit action required")
        if action.amount < MIN_DEPOSIT or action.token_uid != TokenUid(HTR_UID):
            raise NCFail("Deposit amount too low or token not HATHOR")
        if protocol_fee < 0 or protocol_fee > 1000:
            raise NCFail("Protocol fee must be between 0 and 1000")
        self.token_b = token_b
        self.dev_address = Address(ctx.caller_id)
        self.dozer_pool = dozer_pool
        self.oasis_htr_balance = Amount(action.amount)
        self.dev_deposit_amount = Amount(action.amount)
        self.total_liquidity = Amount(0)
        self.protocol_fee = protocol_fee
        self.owner_address = Address(ctx.caller_id)

        # Initialize dict fields
        self.user_deposit_b = {}
        self.htr_price_in_deposit = {}
        self.token_price_in_htr_in_deposit = {}
        self.user_liquidity = {}
        self.user_withdrawal_time = {}
        self.user_balances = {}
        self.user_position_closed = {}
        self.closed_position_balances = {}

    @public(allow_deposit=True)
    def owner_deposit(self, ctx: Context) -> None:

        action = self._get_token_action(ctx, NCActionType.DEPOSIT, TokenUid(HTR_UID), auth=False)
        if Address(ctx.caller_id) not in [self.dev_address, self.owner_address]:
            raise NCFail("Only dev or owner can deposit")
        if not isinstance(action, NCDepositAction):
            raise NCFail("Deposit action required")
        if action.token_uid != TokenUid(HTR_UID):
            raise NCFail("Deposit token not HATHOR")
        self.oasis_htr_balance = Amount(self.oasis_htr_balance + action.amount)
        self.dev_deposit_amount = Amount(self.dev_deposit_amount + action.amount)

    @public(allow_deposit=True)
    def user_deposit(self, ctx: Context, timelock: int, htr_price: Amount) -> None:
        """Deposits token B with a timelock period for bonus rewards.

        Args:
            ctx: Execution context
            timelock: Lock period in months (6, 9, or 12)

        Raises:
            NCFail: If deposit requirements not met or invalid timelock
        """
        action = self._get_token_action(
            ctx, NCActionType.DEPOSIT, self.token_b, auth=False
        )
        if action.token_uid != self.token_b:
            raise NCFail("Deposit token not B")

        if self.user_position_closed.get(Address(ctx.caller_id), False):
            raise NCFail("Need to close position before deposit")

        # Calculate and deduct protocol fee
        if not isinstance(action, NCDepositAction):
            raise NCFail("Deposit action required")
        amount = action.amount
        fee_amount = (amount * self.protocol_fee) // 1000
        deposit_amount = amount - fee_amount

        # Add fee to dev balances
        partial = self.user_balances.get(self.dev_address, {})
        current_token_b_balance = partial.get(self.token_b, Amount(0))
        partial[self.token_b] = Amount(current_token_b_balance + fee_amount)
        self.user_balances[self.dev_address] = partial

        # Continue with deposit using reduced amount
        htr_amount = self._quote_add_liquidity_in(Amount(deposit_amount))
        token_price_in_htr = deposit_amount * 100 // htr_amount if htr_amount > 0 else 0
        bonus = self._get_user_bonus(timelock, htr_amount)
        now = ctx.block.timestamp
        if htr_amount + bonus > self.oasis_htr_balance:
            raise NCFail("Not enough balance")

        if self.total_liquidity == Amount(0):
            self.total_liquidity = Amount(deposit_amount * PRECISION)
            self.user_liquidity[Address(ctx.caller_id)] = Amount(deposit_amount * PRECISION)
        else:
            liquidity_increase = (
                self.total_liquidity
                * deposit_amount
                // self._get_oasis_lp_amount_b()
            )
            current_user_liquidity = self.user_liquidity.get(Address(ctx.caller_id), Amount(0))
            self.user_liquidity[Address(ctx.caller_id)] = Amount(
                current_user_liquidity + liquidity_increase
            )
            self.total_liquidity = Amount(self.total_liquidity + liquidity_increase)

        if Address(ctx.caller_id) in self.user_withdrawal_time:
            delta = self.user_withdrawal_time[Address(ctx.caller_id)] - now
            if delta > 0:
                self.user_withdrawal_time[Address(ctx.caller_id)] = Timestamp(
                    now
                    + (
                        (
                            (delta * self.user_deposit_b[Address(ctx.caller_id)])
                            + (deposit_amount * timelock * MONTHS_IN_SECONDS)
                        )
                        // (deposit_amount + self.user_deposit_b[Address(ctx.caller_id)])
                    )
                    + 1
                )
            else:
                self.user_withdrawal_time[Address(ctx.caller_id)] = Timestamp(
                    now + timelock * MONTHS_IN_SECONDS
                )
            # updating position intial price with weighted average
            self.htr_price_in_deposit[Address(ctx.caller_id)] = Amount(
                (
                    self.htr_price_in_deposit[Address(ctx.caller_id)]
                    * self.user_deposit_b[Address(ctx.caller_id)]
                    + htr_price * deposit_amount
                )
                * 100
                // (self.user_deposit_b[Address(ctx.caller_id)] + deposit_amount)
            )
            self.token_price_in_htr_in_deposit[Address(ctx.caller_id)] = Amount(
                (
                    self.token_price_in_htr_in_deposit[Address(ctx.caller_id)]
                    * self.user_deposit_b[Address(ctx.caller_id)]
                    + token_price_in_htr * deposit_amount
                )
                * 100
                // (self.user_deposit_b[Address(ctx.caller_id)] + deposit_amount)
            )

        else:
            self.htr_price_in_deposit[Address(ctx.caller_id)] = Amount(htr_price)
            self.token_price_in_htr_in_deposit[Address(ctx.caller_id)] = Amount(token_price_in_htr)
            self.user_withdrawal_time[Address(ctx.caller_id)] = Timestamp(now + timelock * MONTHS_IN_SECONDS)

        self.oasis_htr_balance = Amount(self.oasis_htr_balance - (bonus + htr_amount))
        partial = self.user_balances.get(Address(ctx.caller_id), {})
        current_htr_balance = partial.get(TokenUid(HTR_UID), Amount(0))
        partial[TokenUid(HTR_UID)] = Amount(current_htr_balance + bonus)
        self.user_balances[Address(ctx.caller_id)] = partial
        current_deposit = self.user_deposit_b.get(Address(ctx.caller_id), Amount(0))
        self.user_deposit_b[Address(ctx.caller_id)] = Amount(
            current_deposit + deposit_amount
        )

        actions = [
            NCDepositAction(token_uid=self.token_b, amount=Amount(deposit_amount)),
            NCDepositAction(token_uid=TokenUid(HTR_UID), amount=htr_amount),
        ]
        result = self.syscall.get_contract(self.dozer_pool, blueprint_id=None).public(*actions).add_liquidity()
        if result[1] > 0:
            if result[0] == self.token_b:
                adjust_actions = [
                    NCWithdrawalAction(token_uid=TokenUid(HTR_UID), amount=Amount(0)),
                    NCWithdrawalAction(token_uid=self.token_b, amount=Amount(result[1])),
                ]
            else:
                adjust_actions = [
                    NCWithdrawalAction(token_uid=TokenUid(HTR_UID), amount=Amount(result[1])),
                    NCWithdrawalAction(token_uid=self.token_b, amount=Amount(0)),
                ]
            self.syscall.get_contract(self.dozer_pool, blueprint_id=None).public(*adjust_actions).withdraw_cashback()
            partial = self.user_balances.get(Address(ctx.caller_id), {})
            current_result_balance = partial.get(result[0], Amount(0))
            partial[result[0]] = Amount(current_result_balance + result[1])
            self.user_balances[Address(ctx.caller_id)] = partial

    @public
    def close_position(self, ctx: Context) -> None:
        """Close a user's position, removing liquidity from the pool and making funds available for withdrawal.

        Args:
            ctx: Execution context

        Raises:
            NCFail: If position is still locked or already closed
        """
        # Verify position can be closed
        if ctx.block.timestamp < self.user_withdrawal_time.get(Address(ctx.caller_id), 0):
            raise NCFail("Position is still locked")

        if self.user_position_closed.get(Address(ctx.caller_id), False):
            raise NCFail("Position already closed")

        if self.user_liquidity.get(Address(ctx.caller_id), 0) == 0:
            raise NCFail("No position to close")

        # Get quote information
        oasis_quote = self._quote_remove_liquidity_oasis()
        htr_oasis_amount = oasis_quote["max_withdraw_a"]
        token_b_oasis_amount = oasis_quote["user_lp_b"]
        user_liquidity = self.user_liquidity.get(Address(ctx.caller_id), 0)
        user_lp_b = (user_liquidity) * token_b_oasis_amount // (self.total_liquidity)
        user_lp_htr = user_liquidity * htr_oasis_amount // (self.total_liquidity)

        # Create actions to remove liquidity
        actions = [
            NCWithdrawalAction(token_uid=TokenUid(HTR_UID), amount=user_lp_htr),
            NCWithdrawalAction(token_uid=self.token_b, amount=user_lp_b),
        ]

        # Handle impermanent loss calculation
        loss_htr = 0
        # Calculate max withdraw amount including existing balances
        user_token_b_balance = self.user_balances.get(Address(ctx.caller_id), {}).get(
            self.token_b, 0
        )
        max_withdraw_b = user_lp_b + user_token_b_balance

        # Check for impermanent loss
        current_user_deposit = self.user_deposit_b.get(Address(ctx.caller_id), Amount(0))
        if current_user_deposit > max_withdraw_b:
            loss = current_user_deposit - max_withdraw_b
            # Note: quote_token_b is not available in the new SDK, we need to use view methods
            # For now, calculate loss_htr proportionally
            loss_htr = (loss * user_lp_htr) // current_user_deposit if current_user_deposit > 0 else Amount(0)
            if loss_htr > user_lp_htr:
                loss_htr = user_lp_htr

        # Call dozer pool to remove liquidity
        self.syscall.get_contract(self.dozer_pool, blueprint_id=None).public(*actions).remove_liquidity()

        # Get existing cashback balances
        user_current_balance = self.user_balances.get(Address(ctx.caller_id), {})
        user_htr_current_balance = user_current_balance.get(TokenUid(HTR_UID), Amount(0))

        # First, return the user_lp_htr back to oasis_htr_balance
        self.oasis_htr_balance = Amount(self.oasis_htr_balance + user_lp_htr - loss_htr)

        # Then update closed balances without adding user_lp_htr again
        closed_balances = self.closed_position_balances.get(Address(ctx.caller_id), {})
        current_token_b_closed = closed_balances.get(self.token_b, Amount(0))
        current_htr_closed = closed_balances.get(TokenUid(HTR_UID), Amount(0))
        closed_balances[self.token_b] = Amount(
            current_token_b_closed + user_token_b_balance + user_lp_b
        )
        closed_balances[TokenUid(HTR_UID)] = Amount(
            current_htr_closed + user_htr_current_balance + loss_htr
        )
        self.closed_position_balances[Address(ctx.caller_id)] = closed_balances

        # Clear user cashback balances after moving them
        if user_token_b_balance > Amount(0) or user_htr_current_balance > Amount(0):
            self.user_balances[Address(ctx.caller_id)] = {
                TokenUid(HTR_UID): Amount(0),
                self.token_b: Amount(0)
            }

        # Mark position as closed
        self.user_position_closed[Address(ctx.caller_id)] = True

        # Keep the deposit amounts for reference, but reset liquidity
        user_liquidity_amount = self.user_liquidity[Address(ctx.caller_id)]
        self.total_liquidity = Amount(self.total_liquidity - user_liquidity_amount)
        self.user_liquidity.__delitem__(Address(ctx.caller_id))
        self.user_withdrawal_time.__delitem__(Address(ctx.caller_id))

    @public(allow_withdrawal=True)
    def user_withdraw(self, ctx: Context) -> None:
        """Withdraw funds after position is closed.

        Args:
            ctx: Execution context

        Raises:
            NCFail: If position is not closed or insufficient funds
        """
        action_token_b = self._get_token_action(
            ctx, NCActionType.WITHDRAWAL, self.token_b
        )
        if not isinstance(action_token_b, NCWithdrawalAction):
            raise NCFail("Withdrawal action required for token_b")
        action_htr = None
        if len(ctx.actions) > 1:
            action_htr = self._get_token_action(ctx, NCActionType.WITHDRAWAL, TokenUid(HTR_UID))
            if not isinstance(action_htr, NCWithdrawalAction):
                raise NCFail("Withdrawal action required for HTR")

        # Check if the position is unlocked
        if ctx.block.timestamp < self.user_withdrawal_time.get(Address(ctx.caller_id), 0):
            raise NCFail("Withdrawal locked")

        # For positions that haven't been closed yet, automatically close them first
        if (
            not self.user_position_closed.get(Address(ctx.caller_id), False)
            and self.user_liquidity.get(Address(ctx.caller_id), 0) > 0
        ):
            raise NCFail("Position must be closed before withdrawal")

        # Check token_b withdrawal amount from closed_position_balances
        available_token_b = self.closed_position_balances.get(Address(ctx.caller_id), {}).get(
            self.token_b, Amount(0)
        )
        if action_token_b.amount > available_token_b:
            raise NCFail(
                f"Not enough balance. Available: {available_token_b}, Requested: {action_token_b.amount}"
            )

        # Update token_b balance in closed_position_balances
        closed_balances = self.closed_position_balances.get(Address(ctx.caller_id), {})
        closed_balances[self.token_b] = Amount(available_token_b - action_token_b.amount)

        # Check HTR withdrawal if requested
        if action_htr:
            available_htr = self.closed_position_balances.get(Address(ctx.caller_id), {}).get(
                TokenUid(HTR_UID), Amount(0)
            )
            if action_htr.amount > available_htr:
                raise NCFail(
                    f"Not enough HTR balance. Available: {available_htr}, Requested: {action_htr.amount}"
                )

            closed_balances[TokenUid(HTR_UID)] = Amount(available_htr - action_htr.amount)

        # Update closed position balances
        self.closed_position_balances[Address(ctx.caller_id)] = closed_balances

        # If all funds withdrawn, clean up user data
        if (
            closed_balances.get(self.token_b, Amount(0)) == Amount(0)
            and closed_balances.get(TokenUid(HTR_UID), Amount(0)) == Amount(0)
        ):
            self.user_deposit_b.__delitem__(Address(ctx.caller_id))
            self.user_withdrawal_time.__delitem__(Address(ctx.caller_id))
            self.htr_price_in_deposit.__delitem__(Address(ctx.caller_id))
            self.token_price_in_htr_in_deposit.__delitem__(Address(ctx.caller_id))
            self.user_position_closed.__delitem__(Address(ctx.caller_id))

    @public(allow_withdrawal=True)
    def user_withdraw_bonus(self, ctx: Context) -> None:
        action = self._get_action(ctx, NCActionType.WITHDRAWAL, auth=False)
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Withdrawal action required")
        if action.token_uid != TokenUid(HTR_UID):
            raise NCFail("Withdrawal token not HATHOR")
        available_bonus = self.user_balances.get(Address(ctx.caller_id), {}).get(
            TokenUid(HTR_UID), Amount(0)
        )
        if action.amount > available_bonus:
            raise NCFail("Withdrawal amount too high")
        partial = self.user_balances.get(Address(ctx.caller_id), {})
        current_bonus = partial.get(TokenUid(HTR_UID), Amount(0))
        partial[TokenUid(HTR_UID)] = Amount(current_bonus - action.amount)
        self.user_balances[Address(ctx.caller_id)] = partial

    @public
    def update_protocol_fee(self, ctx: Context, new_fee: Amount) -> None:
        """Update the protocol fee percentage (in thousandths).

        Args:
            ctx: Execution context
            new_fee: New fee value in thousandths (e.g. 500 = 0.5%)

        Raises:
            NCFail: If caller is not dev or fee exceeds maximum
        """
        if Address(ctx.caller_id) != self.dev_address:
            raise NCFail("Only dev can update protocol fee")
        if new_fee > 1000 or new_fee < 0:
            raise NCFail("Protocol fee cannot exceed 100%")

        self.protocol_fee = new_fee

    def _get_oasis_lp_amount_b(self) -> Amount:
        # Note: max_withdraw_b is not directly accessible in the new SDK
        # We need to call a view method instead
        pool_info = self.syscall.get_contract(self.dozer_pool, blueprint_id=None).view().quote_remove_liquidity(
            self.syscall.get_contract_id()
        )
        return Amount(pool_info.get("user_lp_b", 0))

    def _quote_add_liquidity_in(self, amount: Amount) -> Amount:
        result = self.syscall.get_contract(self.dozer_pool, blueprint_id=None).view().front_quote_add_liquidity_in(
            amount, self.token_b, self._get_pool_key()
        )
        return Amount(result)

    def _get_pool_key(self) -> str:
        """Get pool key for the dozer pool - assumed to be token_a/token_b/fee format"""
        # This would need to be stored or derived based on the pool structure
        # For now returning a placeholder - this should be set during initialization
        return ""

    def _quote_remove_liquidity_oasis(self) -> dict[str, int]:
        return self.syscall.get_contract(self.dozer_pool, blueprint_id=None).view().quote_remove_liquidity(
            self.syscall.get_contract_id()
        )

    def _get_user_bonus(self, timelock: int, amount: Amount) -> Amount:
        """Calculates the bonus for a user based on the timelock and amount"""
        if timelock not in [6, 9, 12]:  # Assuming these are the only valid values
            raise NCFail("Invalid timelock value")
        bonus_multiplier = {6: 0.1, 9: 0.15, 12: 0.2}

        return Amount(int(bonus_multiplier[timelock] * amount))

    @public(allow_withdrawal=True)
    def owner_withdraw(self, ctx: Context) -> None:
        """Allows owner to withdraw HTR from their balance.

        Args:
            ctx: Execution context

        Raises:
            NCFail: If caller is not owner or withdraw amount exceeds available balance
        """
        if Address(ctx.caller_id) != self.owner_address:
            raise NCFail("Only owner can withdraw")
        action = self._get_token_action(
            ctx, NCActionType.WITHDRAWAL, TokenUid(HTR_UID), auth=False
        )
        if not isinstance(action, NCWithdrawalAction):
            raise NCFail("Withdrawal action required")
        if action.amount > self.oasis_htr_balance:
            raise NCFail("Withdrawal amount too high")
        self.oasis_htr_balance = Amount(self.oasis_htr_balance - action.amount)

    @public(allow_withdrawal=True)
    def dev_withdraw_fee(self, ctx: Context) -> None:
        """Allows dev to withdraw collected protocol fees.

        Args:
            ctx: Execution context

        Raises:
            NCFail: If caller is not dev or withdraw amount exceeds available balance
        """
        if Address(ctx.caller_id) != self.dev_address:
            raise NCFail("Only dev can withdraw fees")

        token_b_action = self._get_token_action(
            ctx, NCActionType.WITHDRAWAL, self.token_b
        )
        if not isinstance(token_b_action, NCWithdrawalAction):
            raise NCFail("Withdrawal action required")
        available_fee = self.user_balances.get(self.dev_address, {}).get(
            self.token_b, Amount(0)
        )
        if token_b_action.amount > available_fee:
            raise NCFail("Withdrawal amount too high")

        partial = self.user_balances.get(self.dev_address, {})
        current_token_b = partial.get(self.token_b, Amount(0))
        partial[self.token_b] = Amount(current_token_b - token_b_action.amount)
        self.user_balances[self.dev_address] = partial

    @public
    def update_owner_address(self, ctx: Context, new_owner: Address) -> None:
        """Updates the owner address. Can be called by dev or current owner.

        Args:
            ctx: Execution context
            new_owner: New owner address

        Raises:
            NCFail: If caller is not dev or current owner
        """
        if Address(ctx.caller_id) not in [self.dev_address, self.owner_address]:
            raise NCFail("Only dev or owner can update owner address")
        self.owner_address = new_owner

    def _get_action(
        self, ctx: Context, action_type: NCActionType, auth: bool
    ) -> NCAction:
        """Returns one action tested by type and index"""
        if len(ctx.actions) != 1:
            raise NCFail("Expected exactly one action")

        # Get the single action from HTR_UID
        action = ctx.get_single_action(TokenUid(HTR_UID))
        if action.type != action_type:
            raise NCFail("Wrong action type")
        if auth:
            if Address(ctx.caller_id) != self.dev_address:
                raise NCFail("Unauthorized")

        return action

    def _get_token_action(
        self,
        ctx: Context,
        action_type: NCActionType,
        token: TokenUid,
        auth: bool = False,
    ) -> NCAction:
        """Returns one action tested by type and index"""
        if len(ctx.actions) > 2:
            raise NCFail("Too many actions")

        # Get the single action for the specified token
        action = ctx.get_single_action(token)

        if action.type != action_type:
            raise NCFail("Wrong action type")
        if auth:
            if Address(ctx.caller_id) != self.dev_address:
                raise NCFail("Unauthorized")

        return action

    @view
    def user_info(
        self,
        address: Address,
    ) -> dict[str, float | bool]:
        remove_liquidity_oasis_quote = self.get_remove_liquidity_oasis_quote(address)
        user_balances_dict = self.user_balances.get(address, {})
        closed_balances_dict = self.closed_position_balances.get(address, {})

        return {
            "user_deposit_b": self.user_deposit_b.get(address, Amount(0)),
            "user_liquidity": self.user_liquidity.get(address, Amount(0)),
            "user_withdrawal_time": self.user_withdrawal_time.get(address, Timestamp(0)),
            "oasis_htr_balance": self.oasis_htr_balance,
            "total_liquidity": self.total_liquidity,
            "user_balance_a": user_balances_dict.get(
                TokenUid(HTR_UID), Amount(0)
            ),
            "user_balance_b": user_balances_dict.get(
                self.token_b, Amount(0)
            ),
            "closed_balance_a": closed_balances_dict.get(
                TokenUid(HTR_UID), Amount(0)
            ),
            "closed_balance_b": closed_balances_dict.get(
                self.token_b, Amount(0)
            ),
            "user_lp_b": remove_liquidity_oasis_quote.get("user_lp_b", 0),
            "user_lp_htr": remove_liquidity_oasis_quote.get("user_lp_htr", 0),
            "max_withdraw_b": remove_liquidity_oasis_quote.get("max_withdraw_b", 0),
            "max_withdraw_htr": remove_liquidity_oasis_quote.get("max_withdraw_htr", 0),
            "htr_price_in_deposit": self.htr_price_in_deposit.get(address, Amount(0)),
            "token_price_in_htr_in_deposit": self.token_price_in_htr_in_deposit.get(
                address, Amount(0)
            ),
            "position_closed": self.user_position_closed.get(address, False),
        }

    @view
    def oasis_info(self) -> dict[str, float | str]:
        return {
            "total_liquidity": self.total_liquidity,
            "oasis_htr_balance": self.oasis_htr_balance,
            "token_b": self.token_b.hex(),
            "protocol_fee": self.protocol_fee,
            "dev_deposit_amount": self.dev_deposit_amount,
        }

    @view
    def front_quote_add_liquidity_in(
        self, amount: int, timelock: int, now: Timestamp, address: Address
    ) -> dict[str, float | bool]:
        """Calculates the bonus for a user based on the timelock and amount"""
        fee_amount = (amount * self.protocol_fee) // 1000
        deposit_amount = amount - fee_amount

        htr_amount = self._quote_add_liquidity_in(Amount(deposit_amount))
        bonus = self._get_user_bonus(timelock, htr_amount)

        if address in self.user_withdrawal_time:
            delta = self.user_withdrawal_time[address] - now
            if delta > 0:
                withdrawal_time = (
                    now
                    + (
                        (
                            (delta * self.user_deposit_b[address])
                            + (deposit_amount * timelock * MONTHS_IN_SECONDS)
                        )
                        // (self.user_deposit_b[address] + deposit_amount)
                    )
                    + 1
                )
            else:
                withdrawal_time = now + timelock * MONTHS_IN_SECONDS
        else:
            withdrawal_time = now + timelock * MONTHS_IN_SECONDS

        return {
            "bonus": bonus,
            "htr_amount": htr_amount,
            "withdrawal_time": withdrawal_time,
            "has_position": address in self.user_withdrawal_time,
            "fee_amount": fee_amount,
            "deposit_amount": deposit_amount,
            "protocol_fee": self.protocol_fee,
        }

    @view
    def get_remove_liquidity_oasis_quote(
        self, address: Address
    ) -> dict[str, float | bool]:
        # If position is already closed, return the available balances from closed_position_balances
        if self.user_position_closed.get(address, False):
            closed_balances = self.closed_position_balances.get(address, {})
            return {
                "user_lp_b": 0,
                "user_lp_htr": 0,
                "max_withdraw_b": closed_balances.get(
                    self.token_b, Amount(0)
                ),
                "max_withdraw_htr": closed_balances.get(
                    TokenUid(HTR_UID), Amount(0)
                ),
                "position_closed": True,
            }

        # Otherwise calculate withdrawal amounts based on current pool state
        oasis_quote = self._quote_remove_liquidity_oasis()
        htr_oasis_amount = oasis_quote["max_withdraw_a"]
        token_b_oasis_amount = oasis_quote["user_lp_b"]
        user_liquidity = self.user_liquidity.get(address, 0)

        if self.total_liquidity > 0:
            user_lp_b = (
                (user_liquidity) * token_b_oasis_amount // (self.total_liquidity)
            )
            user_lp_htr = (user_liquidity) * htr_oasis_amount // (self.total_liquidity)
        else:
            user_lp_b = 0
            user_lp_htr = 0

        # Calculate total available amounts including existing balances
        user_balances_dict = self.user_balances.get(address, {})
        user_balance_b = user_balances_dict.get(self.token_b, Amount(0))
        user_balance_htr = user_balances_dict.get(TokenUid(HTR_UID), Amount(0))
        max_withdraw_b = user_lp_b + user_balance_b

        # Calculate impermanent loss compensation if needed
        loss_htr = Amount(0)
        current_user_deposit = self.user_deposit_b.get(address, Amount(0))
        if current_user_deposit > max_withdraw_b:
            loss = current_user_deposit - max_withdraw_b
            # Note: quote_token_b is not available in the new SDK
            # Calculate loss_htr proportionally
            loss_htr = (loss * user_lp_htr) // current_user_deposit if current_user_deposit > Amount(0) else Amount(0)
            if loss_htr > user_lp_htr:
                loss_htr = user_lp_htr
            max_withdraw_htr = user_balance_htr + loss_htr
        else:
            max_withdraw_htr = user_balance_htr

        return {
            "user_lp_b": user_lp_b,
            "user_lp_htr": user_lp_htr,
            "max_withdraw_b": max_withdraw_b,
            "max_withdraw_htr": max_withdraw_htr,
            "loss_htr": loss_htr,
            "position_closed": False,
        }
