from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.types import Context, NCAction, NCActionType, public
from hathor.types import Address, Amount, TokenUid
import math


class BondingCurveToken(Blueprint):
    token: TokenUid
    htr_token: TokenUid
    current_supply: int
    collected_htr: int
    a: float  # Amplitude
    b: float  # Growth rate
    c: float  # Vertical shift (initial price)
    spread_factor: float
    transaction_fee: float

    @public
    def initialize(self, ctx: Context, token: TokenUid, htr_token: TokenUid):
        self.token = token
        self.htr_token = htr_token
        self.current_supply = 0
        self.collected_htr = 0
        self.a = 0.005
        self.b = 5.3335e-7
        self.c = 0.01
        self.spread_factor = 0.99
        self.transaction_fee = 0.005

    def _calculate_price(self, supply: int) -> float:
        return self.a * (math.exp(self.b * supply) - 1) + self.c

    def calculate_with_slippage(
        self, start_tokens: int, end_tokens: int, is_buy: bool
    ) -> tuple[float, float]:
        # Integral of the price function: a * (exp(b*x) - 1) + c
        # Indefinite integral: (a/b) * exp(b*x) + (c-a)*x + C
        def integral_price(x):
            return (self.a / self.b) * math.exp(self.b * x) + (self.c - self.a) * x

        total_cost = integral_price(end_tokens) - integral_price(start_tokens)

        if not is_buy:
            total_cost *= self.spread_factor

        avg_price = total_cost / (end_tokens - start_tokens)

        return avg_price, total_cost
