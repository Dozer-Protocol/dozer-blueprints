from typing import NamedTuple
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.blueprints.liquidity_pool import (
    InvalidActions,
    InvalidTokens,
    Unauthorized,
)
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import Context, NCAction, NCActionType, public
from hathor.types import Address, Amount, TokenUid, Timestamp
from math import floor

SECONDS_IN_DAY: int = 60 * 60 * 24


class UserData(NamedTuple):
    """Object to handle a new reward"""

    # index: TokenUid
    user_rewards: dict[TokenUid, Amount]
    user_pending: int


class PoolData(NamedTuple):

    # apr calculation utils
    total_liquidity: float
    reward_rate_per_second: dict[TokenUid, float]


class DozerRewards(Blueprint):
    """Rewards Contract for Dozer Protocol Liquidity Pools.

    The rewardable indexs for a given pool are $HTR, $DZR, token_a and token_b.
    The contract will keep track of the rewards per index, the user liquidity and the total liquidity for each active rewardable token on the pool.

    Only Dozer devs can activate and set the earnings per day for each rewardable index.

    Users can supply rewards for each rewardable index any time.
    """

    # Variables to keep track of a reward campaign, a reward campaign starts when its initialized by the dev and funded, and it ends when the rewards_reserve go to 0. At a given time, only one campaign can be running for each index.
    campaign_started: dict[int, bool]
    campaign_ended: dict[int, bool]
    campaign_end_timestamp: dict[int, Timestamp]
    rewards_reserve: dict[int, Amount]
    earnings_per_second: dict[int, float]
    rewards_per_share: dict[int, float]
    user_rewards: dict[int, dict[Address, Amount]]
    user_debt: dict[int, dict[Address, float]]
    last_pool_update: dict[int, Timestamp]
    black_list: Address

    # Variables related to the liquidity pool
    user_liquidity: dict[Address, float]
    total_liquidity: float
    owner_address: Address
    tokens: dict[int, TokenUid]

    @public
    def initialize(
        self,
        ctx: Context,
        htr: TokenUid,
        dzr: TokenUid,
        token_a: TokenUid,
        token_b: TokenUid,
    ) -> None:
        self.owner_address = ctx.address
        self.total_liquidity = 0
        for i in [0, 1, 2, 3]:
            self.campaign_started[i] = False
            self.campaign_ended[i] = False
            self.rewards_reserve[i] = 0
            self.earnings_per_second[i] = 0
            self.rewards_per_share[i] = 0
            self.user_rewards[i] = (
                {}
            )  # may this initialization be in the configure campaign?
            self.user_debt[i] = {}  # this too?
            self.campaign_end_timestamp[i] = 0  # don't know if this is right
        self.tokens[0] = htr
        self.tokens[1] = dzr
        self.tokens[2] = token_a
        self.tokens[3] = token_b

    def _get_action(
        self, ctx: Context, action_type: NCActionType, auth: bool
    ) -> NCAction:
        """Returns one action tested by type and index"""
        if len(ctx.actions) != 1:
            raise InvalidActions("only one action supported")
        # if ctx.actions.keys not in rewardable_indexs:
        #     raise InvalidTokens()
        output = ctx.actions.popitem()[1]
        if output.type != action_type:
            raise InvalidActions("invalid action")
        if auth:
            if ctx.address != self.owner_address:
                raise Unauthorized("Unauthorized")

        return output

    @public
    def configure_campaign(
        self, ctx: Context, index: int, earnings_per_day: int, address: Address
    ) -> None:
        # TODO check if caller is dev reuse liquidity_pool methods.
        # if index not in rewardable_indexs:
        #     raise InvalidTokens("invalid index")
        if index not in [0, 1, 2, 3]:
            raise InvalidTokens("invalid index")
        if self.rewards_reserve[index] < 1:
            raise NCFail("please fund rewards")
        if len(ctx.actions) != 0:
            raise InvalidActions("actions not supported")
        if ctx.address != self.owner_address:
            raise Unauthorized("Unauthorized")
        if self.total_liquidity == 0:
            raise NCFail("no liquidity")
        if (
            self.campaign_started[index]
            # and ctx.timestamp > self.campaign_end_timestamp[index]
            and not self.campaign_ended[index]
        ):
            raise NCFail("can't change a running campaign")

        earnings_per_second = earnings_per_day / SECONDS_IN_DAY
        if earnings_per_second < 0:
            raise NCFail("earnings per second must be positive")
        # if ( #TODO Extract function
        #     self.campaign_started[index]
        #     and earnings_per_second < self.earnings_per_second[index]
        # ):
        #     raise NCFail("can't decrease the earnings per seconds")
        now = ctx.timestamp
        self.campaign_started[index] = True
        self.campaign_ended[index] = False
        self.black_list = address
        if not address in self.user_liquidity:
            self.user_liquidity[address] = 0
        self.earnings_per_second[index] = earnings_per_second
        self.last_pool_update[index] = now
        self.campaign_end_timestamp[index] = now + floor(
            self.rewards_reserve[index] // self.earnings_per_second[index]
        )

    @public
    def end_campaign(self, ctx: Context, index: int) -> None:
        if ctx.timestamp < self.campaign_end_timestamp[index] + (
            SECONDS_IN_DAY * 7
        ):  # check if campaign ended more than 7 days ago
            raise NCFail("campaign running")
        if ctx.address != self.owner_address:
            raise Unauthorized("Unauthorized")

        self.user_debt[index] = {}
        self.rewards_per_share[index] = 0
        self.campaign_ended[index] = True

    @public
    def fund_rewards(self, ctx: Context) -> None:
        action = self._get_action(ctx, NCActionType.DEPOSIT, False)
        index = self._map_token(action.token_uid)
        now = ctx.timestamp
        if index == 4:
            raise NCFail("no campaign")
        self.rewards_reserve[index] += action.amount
        if (
            self.campaign_end_timestamp[index] != 0
            and now
            < self.campaign_end_timestamp[index]  # campaign running and not ended
        ):
            self.campaign_end_timestamp[index] = now + floor(
                self.rewards_reserve[index] / self.earnings_per_second[index]
            )

    @public
    def add_liquidity(self, ctx: Context, address: Address, amount: Amount) -> None:
        self._before_liquidity_change(ctx.timestamp, address, amount)
        if address in self.user_liquidity:
            self.user_liquidity[address] += amount
        else:
            self.user_liquidity[address] = amount
        self.total_liquidity += amount
        self._after_liquidity_change(address)

    @public
    def remove_liquidity(self, ctx: Context, address: Address, amount: Amount) -> None:
        self._before_liquidity_change(ctx.timestamp, address, amount)
        self.user_liquidity[address] -= amount
        self.total_liquidity -= amount
        self._after_liquidity_change(address)

    def _before_liquidity_change(
        self, now: Timestamp, address: Address, amount: Amount
    ) -> None:
        """This method needs to run before everytime an user adds or removes liquidity to update rewards variables of an activated campaign"""
        for index in [0, 1, 2, 3]:
            if self.user_debt[index].get(address) is None:
                # partial = self.user_debt.get(index, {})
                # partial.update({address: 0})
                # self.user_debt[index] = partial
                self.user_debt[index][address] = 0
            if not self.campaign_started[index]:
                continue
            self._update_pool(now, index)
            pending = self._pending_rewards(address, index)
            if self.user_rewards[index].get(address) is None:
                # partial = self.user_rewards.get(index, {})
                # partial.update({address: 0})
                # self.user_rewards[index] = partial
                self.user_rewards[index][address] = 0
            if pending != 0:
                self._safe_pay(pending, address, index)

    def _after_liquidity_change(self, address: Address) -> None:
        """This method needs to run after everytime an user adds or removes liquidity to update rewards variables of an activated campaign"""
        for index in [0, 1, 2, 3]:
            if not self.campaign_started[index]:
                continue
            # if address != self.black_list:
            self.user_debt[index][address] = (
                self.user_liquidity[address] * self.rewards_per_share[index]
            )

    def _update_pool(self, now: Timestamp, index: int) -> None:
        # check it the campaign runned out of tokens because it would change the update routine
        if (
            # self.campaign_end_timestamp[index] != 0 and
            self.earnings_per_second[index] != 0
            and now > self.campaign_end_timestamp[index]
        ):
            old_earnings_per_second = self.earnings_per_second[index]
            self.earnings_per_second[index] = 0
            self.rewards_per_share[index] += (
                (self.campaign_end_timestamp[index] - self.last_pool_update[index])
                * old_earnings_per_second
            ) / (self.total_liquidity - self.user_liquidity[self.black_list])
            # self.campaign_end_timestamp[index] = 0
        else:
            self.rewards_per_share[index] += (
                (now - self.last_pool_update[index]) * self.earnings_per_second[index]
            ) / (self.total_liquidity - self.user_liquidity[self.black_list])
        self.last_pool_update[index] = now

    def _pending_rewards(self, address: Address, index: int) -> Amount:
        if self.user_liquidity.get(address) is None or address == self.black_list:
            return 0

        return floor(
            (self.user_liquidity[address] * self.rewards_per_share[index])
            - (self.user_debt[index].get(address, 0))
        )

    def get_user_rewards(
        self, address: Address, index: int, timestamp: Timestamp
    ) -> Amount:
        if self.total_liquidity == 0:
            return 0
        if self.campaign_ended[index]:
            if self.user_rewards[index].get(address) is not None:
                return self.user_rewards[index][address]
            else:
                return 0

        if self.user_liquidity.get(address) is None or address == self.black_list:
            return 0
        rewards_per_share = self.rewards_per_share[index]
        # update the rewards_per_share using the same logic as update pool
        if (
            not self.campaign_ended[index]
            and timestamp > self.campaign_end_timestamp[index]
        ):
            rewards_per_share += (
                (self.campaign_end_timestamp[index] - self.last_pool_update[index])
                * self.earnings_per_second[index]
            ) / (self.total_liquidity - self.user_liquidity[self.black_list])
        else:
            rewards_per_share += (
                (timestamp - self.last_pool_update[index])
                * self.earnings_per_second[index]
            ) / (self.total_liquidity - self.user_liquidity[self.black_list])
        if self.user_rewards[index].get(address) is None:
            return floor(
                (self.user_liquidity[address] * rewards_per_share)
                - (self.user_debt[index].get(address, 0))
            )
        else:
            return (
                floor(
                    (self.user_liquidity[address] * rewards_per_share)
                    - (self.user_debt[index].get(address, 0))
                )
                + self.user_rewards[index][address]
            )
        # return floor(self.user_rewards[index][address])

    @public
    def withdraw_rewards(self, ctx: Context) -> None:
        action = self._get_action(ctx, NCActionType.WITHDRAWAL, False)
        index = self._map_token(action.token_uid)
        if index == 4:
            raise NCFail("no campaign")
        self._update_pool(ctx.timestamp, index)
        pending = self._pending_rewards(ctx.address, index)
        if pending != 0:
            self._safe_pay(pending, ctx.address, index)
        if action.amount > self.user_rewards[index][ctx.address]:
            raise NCFail("not enough reward")
        else:
            self.user_rewards[index][ctx.address] -= action.amount

    def _safe_pay(self, amount: Amount, address: Address, index: int) -> None:
        if amount <= self.rewards_reserve[index]:
            self.rewards_reserve[index] -= amount
            # partial = self.user_rewards.get(index, {})
            # partial.update({address: partial[address] + amount})
            # self.user_rewards[index] = partial
            self.user_rewards[index][address] += amount
        else:
            raise NCFail("not enough reward")

    def _map_token(self, token: TokenUid) -> int:
        if token == self.tokens[0]:
            return 0
        if token == self.tokens[1]:
            return 1
        if token == self.tokens[2]:
            return 2
        if token == self.tokens[3]:
            return 3
        else:
            return 4
