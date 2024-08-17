# PRECISION = 10**20
LAUNCHPAD_SUPPLY = 8_000_000_00
DEPOSITED_AMOUNT = 10_000_000_00
TARGET_MARKET_CAP = 690_420_00
# exponential curve parameters
A = 0.005
B = 5.3335e-7
C = 0.01

DEV_ADDRESS = b"1Pv7d2z6k6y6z7w8x9y0z1a2b3c4d5e6f7g8h9i0j"
HATHOR_TOKEN_UID: bytes = b"\x00"


import math
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import Context, NCAction, NCActionType, public
from hathor.types import Address, Amount, Timestamp, TokenUid


def require(condition: bool, errmsg: str) -> None:
    """Helper to fail execution if condition is false."""
    if not condition:
        raise NCFail(errmsg)


class Dozer_Pump(Blueprint):
    """Launchpad contract for Dozer Protocol Liquidity Pools."""

    # Token information
    token_uid: TokenUid

    # Curve balances
    curve_token_balance: Amount
    curve_htr_balance: Amount

    # Launchpad constants
    # launchpad_supply: Amount = LAUNCHPAD_SUPPLY
    # deposited_amount: Amount = DEPOSITED_AMOUNT
    # target_market_cap: Amount = TARGET_MARKET_CAP

    # Curve parameters
    # curve_param_a: float = A
    # curve_param_b: float = B
    # curve_param_c: float = C

    # Developer Address
    dev_address: Address

    # Fee configuration
    fee_numerator: int
    fee_denominator: int

    # Statistics
    accumulated_fee: Amount
    transactions: int
    last_activity_timestamp: Timestamp
    volume: Amount

    # Contract state
    is_launchpad_mode: bool

    @public
    def initialize(
        self,
        ctx: Context,
        token: TokenUid,
        fee: Amount,
    ) -> None:
        """Initialize the launchpad for the pair token/htr."""
        self.token_uid = token
        require(token != HATHOR_TOKEN_UID, "token must not be HTR")  # type: ignore
        action = self._get_action(ctx)
        require(
            action.amount == DEPOSITED_AMOUNT, "Must deposit exactly 10 million tokens"
        )
        require(action.token_uid == token, "token must be the same as the one provided")
        self.dev_address = DEV_ADDRESS
        self.is_launchpad_mode = True
        self.curve_token_balance = LAUNCHPAD_SUPPLY
        self.curve_htr_balance = 0

        # LP Fees.
        if fee > 50:
            raise NCFail("fee too high")
        if fee < 0:
            raise NCFail("invalid fee")

        self.fee_numerator = fee
        self.fee_denominator = 1000

        self.accumulated_fee = 0
        self.transactions = 0
        self.volume = 0

    def _get_action(self, ctx: Context) -> NCAction:
        """Return token_a and token_b actions."""
        require(self.token_uid in ctx.actions, "only token is allowed")
        action = ctx.actions[self.token_uid]
        self.last_activity_timestamp = ctx.timestamp
        return action

    def _get_action_in(self, ctx: Context) -> NCAction:
        """Return action_in and action_out, where action_in is a deposit and action_out is a withdrawal."""
        action = self._get_action(ctx)
        require(
            action.type == NCActionType.DEPOSIT
            and (
                action.token_uid == self.token_uid
                or action.token_uid == HATHOR_TOKEN_UID
            ),
            "only deposits allowed for token or HTR",
        )
        return action

    def _get_actions_a_b(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Return token and HTR actions."""
        if set(ctx.actions.keys()) != {HATHOR_TOKEN_UID, self.token_uid}:
            raise NCFail("only token_a and token_b are allowed")
        action_a = ctx.actions[self.token_uid]
        action_b = ctx.actions[HATHOR_TOKEN_UID]
        self.last_activity_timestamp = ctx.timestamp
        return action_a, action_b

    def _get_actions_in_out(self, ctx: Context) -> tuple[NCAction, NCAction]:
        """Return action_in and action_out, where action_in is a deposit and action_out is a withdrawal."""
        action_a, action_b = self._get_actions_a_b(ctx)

        if action_a.type == NCActionType.DEPOSIT:
            action_in = action_a
            action_out = action_b
        else:
            action_in = action_b
            action_out = action_a

        require(
            action_in.type == NCActionType.DEPOSIT,
            "must have one deposit and one withdrawal",
        )
        require(
            action_out.type == NCActionType.WITHDRAWAL,
            "must have one deposit and one withdrawal",
        )

        return action_in, action_out

    @public
    def change_dev_address(self, ctx: Context, new_address: Address) -> None:
        """Change the dev address"""
        require(ctx.address == self.dev_address, "only dev can change dev address")
        self.dev_address = new_address

    @public
    def change_fee(self, ctx: Context, new_fee: int) -> None:
        """Change the fee"""
        require(ctx.address == self.dev_address, "only dev can change fee")
        require(new_fee < 50, "fee too high")
        require(new_fee > 0, "invalid fee")
        self.fee_numerator = new_fee
        self.fee_denominator = 1000

    @public
    def withdraw_fees(self, ctx: Context) -> None:
        """Withdraw accumulated fees"""
        require(ctx.address == self.dev_address, "only dev can withdraw fees")
        action = self._get_action(ctx)
        require(action.type == NCActionType.WITHDRAWAL, "action must be withdrawal")
        require(action.token_uid == HATHOR_TOKEN_UID, "invalid token")
        require(action.amount <= self.accumulated_fee, "invalid amount")
        self.accumulated_fee -= action.amount

    def _quote_price(self) -> float:
        x = LAUNCHPAD_SUPPLY - self.curve_token_balance
        return A * (math.exp(B * x) - 1) + C

    def quote_htr_for_exact_tokens(
        self, token_amount: Amount, is_buy: bool
    ) -> dict[str, Amount]:
        # Integral of the price function: a * (exp(b*x) - 1) + c
        # Indefinite integral: (a/b) * exp(b*x) + (c-a)*x + C
        def integral_price(x):
            return (A / B) * math.exp(B * x) + (C - A) * x + C

        x = LAUNCHPAD_SUPPLY - self.curve_token_balance
        if is_buy:
            curve_quote = integral_price(x + token_amount) - integral_price(x)
            fee = int(self.fee_numerator / self.fee_denominator * curve_quote) + 1
            return {
                "htr_amount": int(curve_quote) + fee,
                "fee": fee,
            }
        else:
            curve_quote = integral_price(x) - integral_price(x - token_amount)
            fee = (
                int(((2 * self.fee_numerator) / self.fee_denominator) * curve_quote) + 1
            )
            return {
                "htr_amount": int(curve_quote) - fee,
                "fee": fee,
            }

    def quote_tokens_for_htr(self, htr_amount: Amount) -> dict[str, Amount]:
        """
        Calculate the amount of tokens received for a given input amount."""
        estimated_amount = int(self._quote_price() * (htr_amount))
        quote = self.quote_htr_for_exact_tokens(estimated_amount, True)
        fee = quote["fee"]
        fixed_htr_amount = quote["htr_amount"]

        return {
            "htr_amount": fixed_htr_amount,
            "token_amount": estimated_amount,
            "fee": fee,
        }

    @public
    def buy(self, ctx: Context) -> None:
        """Buy tokens with htr from the curve"""
        action_htr_in, action_token_out = self._get_actions_in_out(ctx)
        require(
            action_htr_in.token_uid == HATHOR_TOKEN_UID
            and action_token_out.token_uid == self.token_uid,
            "can only buy tokens with htr",
        )
        require(
            action_token_out.amount <= self.curve_token_balance,
            "Insufficient output amount",
        )
        quote = self.quote_htr_for_exact_tokens(action_token_out.amount, True)
        require(
            action_htr_in.amount >= quote["htr_amount"], "Insufficient output amount"
        )
        self.curve_htr_balance += action_htr_in.amount
        self.curve_token_balance -= action_token_out.amount
        self.volume += action_htr_in.amount
        self.accumulated_fee += quote["fee"]
        self.transactions += 1
        self._check_and_transition(ctx)

    @public
    def sell(self, ctx: Context) -> None:
        """Sell tokens with the curve"""
        action_token_in, action_htr_out = self._get_actions_in_out(ctx)
        require(
            action_token_in.token_uid == self.token_uid
            and action_htr_out.token_uid == HATHOR_TOKEN_UID,
            "can only sell token for htr",
        )
        require(
            action_htr_out.amount <= self.curve_htr_balance,
            "Insufficient output amount",
        )
        quote = self.quote_htr_for_exact_tokens(action_token_in.amount, False)
        require(
            action_htr_out.amount <= quote["htr_amount"], "Insufficient output amount"
        )
        self.curve_htr_balance -= action_htr_out.amount
        self.curve_token_balance += action_token_in.amount
        self.volume += action_htr_out.amount
        self.accumulated_fee += quote["fee"]
        self.transactions += 1

    def _check_and_transition(self, ctx: Context) -> None:
        """Check if it's time to transition to liquidity pool mode."""
        if self.curve_htr_balance >= TARGET_MARKET_CAP:
            self._transition_to_liquidity_pool(ctx)

    def _transition_to_liquidity_pool(self, ctx: Context) -> str:
        """Transition from launchpad mode to liquidity pool mode."""
        self.is_launchpad_mode = False
        return "Liquidity pool mode"

    def front_end_api_pool(
        self,
    ) -> dict[str, float]:
        """
        Retrieves the current state of the launchpad including reserves, fees, volume, and transactions.
        """
        return {
            "curve_htr_balance": self.curve_htr_balance,
            "curve_token_balance": self.curve_token_balance,
            "fees": self.accumulated_fee,
            "transactions": self.transactions,
            "volume": self.volume,
        }

    # def front_quote_buy_token(self, amount_out: Amount) -> dict[str, float]:
    #     """
    #     Calculate the amount of htr to buy a given amount of token."""
    #     quote = self.quote_htr_for_exact_tokens(amount_out, True)
    #     return {"htr_amount": quote["htr_amount"], "fee": quote["fee"]}

    # def front_quote_add_liquidity_in(
    #     self, amount_in: Amount, token_in: TokenUid
    # ) -> float:
    #     """
    #     Calculate the amount of other tokens to include for a given input amount in add liquidity event.

    #     Parameters:
    #     - amount_in (Amount): The amount of input tokens.
    #     - token_in (TokenUid): The token to be used as input.

    #     Returns:
    #     - Amount: The calculated amount of other tokens to include.
    #     """
    #     if token_in == self.token_a:
    #         quote = self.quote(amount_in, self.reserve_a, self.reserve_b)
    #     else:
    #         quote = self.quote(amount_in, self.reserve_b, self.reserve_a)
    #     return quote

    # def front_quote_add_liquidity_out(
    #     self, amount_out: Amount, token_in: TokenUid
    # ) -> float:
    #     """
    #     Calculate the amount of other tokens to include for a given output amount in add liquidity event.

    #     Parameters:
    #     - amount_out (Amount): The amount of output tokens.
    #     - token_in (TokenUid): The token to be used as input.

    #     Returns:
    #     - Amount: The calculated amount of other tokens to include.
    #     """
    #     if token_in == self.token_a:
    #         quote = self.quote(amount_out, self.reserve_b, self.reserve_a)
    #     else:
    #         quote = self.quote(amount_out, self.reserve_a, self.reserve_b)
    #     return quote

    # def front_quote_exact_tokens_for_tokens(
    #     self, amount_in: Amount, token_in: TokenUid
    # ) -> dict[str, float]:
    #     """
    #     Calculate the amount of tokens received for a given input amount.

    #     This method provides a quote for the exact amount of tokens one would receive
    #     for a specified amount of input tokens, based on the current reserves.

    #     Parameters:
    #     - amount_in (Amount): The amount of input tokens.

    #     Returns:
    #     - Amount: The calculated amount of tokens that would be received.
    #     """
    #     if token_in == self.token_a:
    #         amount_out = self.get_amount_out(amount_in, self.reserve_a, self.reserve_b)
    #         quote = self.quote(amount_in, self.reserve_a, self.reserve_b)
    #     else:
    #         amount_out = self.get_amount_out(amount_in, self.reserve_b, self.reserve_a)
    #         quote = self.quote(amount_in, self.reserve_b, self.reserve_a)
    #     if amount_out == 0:
    #         price_impact = 0
    #     else:
    #         price_impact = (
    #             100 * (quote - amount_out) / amount_out - self.fee_numerator / 10
    #         )
    #     if price_impact < 0:
    #         price_impact = 0
    #     return {"amount_out": amount_out, "price_impact": price_impact}

    # def front_quote_tokens_for_exact_tokens(
    #     self, amount_out: Amount, token_in: TokenUid
    # ) -> dict[str, float]:
    #     """
    #     Calculate the required amount of input tokens to obtain a specific amount of output tokens.

    #     This method uses the reserves of two tokens (A and B) to determine how much of token A is needed
    #     to receive a specific amount of token B.

    #     Parameters:
    #     - amount_out (Amount): The desired amount of output tokens.

    #     Returns:
    #     - Amount: The required amount of input tokens to achieve the desired output.
    #     """
    #     # amount_in = self.get_amount_in(amount_out, self.reserve_a, self.reserve_b)
    #     # quote = self.quote(amount_out, self.reserve_a, self.reserve_b)
    #     if token_in == self.token_a:
    #         amount_in = self.get_amount_in(amount_out, self.reserve_a, self.reserve_b)
    #         quote = self.quote(amount_in, self.reserve_a, self.reserve_b)
    #     else:
    #         amount_in = self.get_amount_in(amount_out, self.reserve_b, self.reserve_a)
    #         quote = self.quote(amount_in, self.reserve_b, self.reserve_a)

    #     price_impact = 100 * (quote - amount_out) / amount_out - self.fee_numerator / 10
    #     if price_impact < 0:
    #         price_impact = 0
    #     if price_impact >= 100:
    #         price_impact = 100
    #     return {"amount_in": amount_in, "price_impact": price_impact}

    # def pool_info(
    #     self,
    # ) -> dict[str, str]:

    #     return {
    #         # "name": self.name,
    #         "version": "0.1",
    #         # "owner": self.owner.hex(),
    #         # "fee_to": self.fee_to.hex(),
    #         "token0": self.token_a.hex(),
    #         "token1": self.token_b.hex(),
    #         "fee": str(self.fee_numerator / 10),
    #     }

    # def user_info(
    #     self,
    #     address: Address,
    # ) -> dict[str, float]:
    #     max_withdraw_a = int(
    #         (self.user_liquidity.get(address, 0) / PRECISION)
    #         * self.reserve_a
    #         / (self.total_liquidity / PRECISION)
    #     )
    #     max_withdraw_b = self.quote(max_withdraw_a, self.reserve_a, self.reserve_b)
    #     return {
    #         "balance_a": self.balance_a.get(address, 0),
    #         "balance_b": self.balance_b.get(address, 0),
    #         "user_deposited_a": self.user_deposited_a.get(address, 0),
    #         "user_deposited_b": self.user_deposited_b.get(address, 0),
    #         "liquidity": self.user_liquidity.get(address, 0),
    #         "max_withdraw_a": max_withdraw_a,
    #         "max_withdraw_b": max_withdraw_b,
    #     }

    # def pool_data(
    #     self,
    # ) -> dict[str, float]:

    #     return {
    #         "total_liquidity": self.total_liquidity,
    #         "reserve0": self.reserve_a,
    #         "reserve1": self.reserve_b,
    #         "fee": self.fee_numerator / 10,
    #         "volume0": self.volume_a,
    #         "volume1": self.volume_b,
    #         "fee0": self.accumulated_fee[self.token_a],
    #         "fee1": self.accumulated_fee[self.token_b],
    #         # "slippage0": self.balance_a,
    #         # "slippage1": self.balance_b,
    #         "dzr_rewards": 1000,
    #         "transactions": self.transactions,
    #         "last_actvity_timestamp": self.last_activity_timestamp,
    #     }
