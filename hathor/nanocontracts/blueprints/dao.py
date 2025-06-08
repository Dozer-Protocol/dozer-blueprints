from typing import Dict, List

from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    TokenUid,
    Timestamp,
    ContractId,
    public,
    view,
)

# Constants
DAYS_TO_SECONDS = 24 * 60 * 60
MAX_VOTING_PERIOD_DAYS = 30
MAX_QUORUM_PERCENTAGE = 100
DEFAULT_PAGINATION_LIMIT = 20


class DAO(Blueprint):
    """DAO contract with staking-based voting power."""

    # Configuration
    name: str
    description: str
    governance_token: TokenUid
    staking_contract: ContractId
    voting_period_seconds: int
    quorum_percentage: int
    proposal_threshold: Amount

    # Proposal data
    proposal_count: int
    proposal_titles: Dict[int, str]
    proposal_descriptions: Dict[int, str]
    proposal_creators: Dict[int, Address]
    proposal_start_times: Dict[int, Timestamp]
    proposal_end_times: Dict[int, Timestamp]
    proposal_for_votes: Dict[int, Amount]
    proposal_against_votes: Dict[int, Amount]
    proposal_total_staked: Dict[int, Amount]
    proposal_quorum_reached: Dict[int, bool]
    proposal_total_voters: Dict[int, int]

    # Vote data
    vote_support: Dict[tuple[int, Address], bool]
    vote_power: Dict[tuple[int, Address], Amount]
    vote_timestamp: Dict[tuple[int, Address], Timestamp]

    @public
    def initialize(
        self,
        ctx: Context,
        name: str,
        description: str,
        governance_token: TokenUid,
        staking_contract: ContractId,
        voting_period_days: int,
        quorum_percentage: int,
        proposal_threshold: Amount,
    ) -> None:
        """Initialize DAO with configuration."""
        if voting_period_days <= 0 or voting_period_days > MAX_VOTING_PERIOD_DAYS:
            raise NCFail("Invalid voting period")
        if quorum_percentage <= 0 or quorum_percentage > MAX_QUORUM_PERCENTAGE:
            raise NCFail("Invalid quorum percentage")
        if proposal_threshold <= 0:
            raise NCFail("Invalid proposal threshold")

        self.name = name
        self.description = description
        self.governance_token = governance_token
        self.staking_contract = staking_contract
        self.voting_period_seconds = voting_period_days * DAYS_TO_SECONDS
        self.quorum_percentage = quorum_percentage
        self.proposal_threshold = proposal_threshold
        self.proposal_count = 0

    @public
    def create_proposal(self, ctx: Context, title: str, description: str) -> int:
        """Create new proposal if caller meets staking threshold."""
        staked = self._get_voting_power(ctx, ctx.address)
        if staked < self.proposal_threshold:
            raise NCFail("Insufficient staked tokens")

        proposal_id = self.proposal_count + 1
        total_staked = self._get_total_staked(ctx)

        self.proposal_titles[proposal_id] = title
        self.proposal_descriptions[proposal_id] = description
        self.proposal_creators[proposal_id] = ctx.address
        self.proposal_start_times[proposal_id] = ctx.timestamp
        self.proposal_end_times[proposal_id] = (
            ctx.timestamp + self.voting_period_seconds
        )
        self.proposal_for_votes[proposal_id] = Amount(0)
        self.proposal_against_votes[proposal_id] = Amount(0)
        self.proposal_total_staked[proposal_id] = total_staked
        self.proposal_quorum_reached[proposal_id] = False
        self.proposal_total_voters[proposal_id] = 0

        self.proposal_count = proposal_id
        return proposal_id

    @public
    def cast_vote(self, ctx: Context, proposal_id: int, support: bool) -> None:
        """Cast vote using staking-based voting power."""
        if proposal_id not in self.proposal_titles:
            raise NCFail("Proposal does not exist")
        if ctx.timestamp >= self.proposal_end_times[proposal_id]:
            raise NCFail("Voting period ended")

        vote_key = (proposal_id, ctx.address)
        if vote_key in self.vote_support:
            raise NCFail("Already voted")

        power = self._get_voting_power(ctx, ctx.address)
        if power == 0:
            raise NCFail("No voting power")

        self.vote_support[vote_key] = support
        self.vote_power[vote_key] = power
        self.vote_timestamp[vote_key] = ctx.timestamp

        if support:
            self.proposal_for_votes[proposal_id] += power
        else:
            self.proposal_against_votes[proposal_id] += power

        self.proposal_total_voters[proposal_id] += 1

        total_votes = (
            self.proposal_for_votes[proposal_id]
            + self.proposal_against_votes[proposal_id]
        )
        min_votes = (
            self.proposal_total_staked[proposal_id] * self.quorum_percentage
        ) // 100
        self.proposal_quorum_reached[proposal_id] = total_votes >= min_votes

    def _get_voting_power(self, ctx: Context, address: Address) -> Amount:
        """Get voting power from staking contract."""
        return self.call_view_method(
            self.staking_contract, "get_max_withdrawal", address, ctx.timestamp
        )

    def _get_total_staked(self, ctx: Context) -> Amount:
        """Get total staked from staking contract."""
        info = self.call_view_method(self.staking_contract, "front_end_api")
        return info["total_staked"]

    @view
    def get_proposal(self, proposal_id: int) -> dict:
        """Get proposal details."""
        if proposal_id not in self.proposal_titles:
            return {}

        return {
            "title": self.proposal_titles[proposal_id],
            "description": self.proposal_descriptions[proposal_id],
            "creator": self.proposal_creators[proposal_id],
            "start_time": self.proposal_start_times[proposal_id],
            "end_time": self.proposal_end_times[proposal_id],
            "for_votes": self.proposal_for_votes[proposal_id],
            "against_votes": self.proposal_against_votes[proposal_id],
            "total_staked": self.proposal_total_staked[proposal_id],
            "quorum_reached": self.proposal_quorum_reached[proposal_id],
            "total_voters": self.proposal_total_voters[proposal_id],
        }

    @view
    def get_vote(self, proposal_id: int, voter: Address) -> dict:
        """Get vote details."""
        vote_key = (proposal_id, voter)
        if vote_key not in self.vote_support:
            return {}

        return {
            "support": self.vote_support[vote_key],
            "power": self.vote_power[vote_key],
            "timestamp": self.vote_timestamp[vote_key],
        }

    @view
    def front_end_api_dao(self, timestamp: Timestamp) -> dict:
        """Get DAO statistics for frontend."""
        active = sum(
            1 for end_time in self.proposal_end_times.values() if end_time > timestamp
        )

        return {
            "total_proposals": self.proposal_count,
            "active_proposals": active,
            "total_voters": (
                len(self.vote_support) // self.proposal_count
                if self.proposal_count > 0
                else 0
            ),
            "total_votes": len(self.vote_support),
            "quorum_percentage": self.quorum_percentage,
            "proposal_threshold": self.proposal_threshold,
        }

    @view
    def proposal_data(self, proposal_id: int, timestamp: Timestamp) -> dict:
        """Get detailed proposal data."""
        if proposal_id not in self.proposal_titles:
            return {}

        proposal = self.get_proposal(proposal_id)
        proposal["state"] = "active" if proposal["end_time"] > timestamp else "ended"
        return proposal

    @view
    def active_proposals(
        self, timestamp: Timestamp, skip: int = 0, limit: int = DEFAULT_PAGINATION_LIMIT
    ) -> List[dict]:
        """Get paginated list of active proposals."""
        active = [
            {
                "id": pid,
                "title": self.proposal_titles[pid],
                "end_time": self.proposal_end_times[pid],
                "for_votes": self.proposal_for_votes[pid],
                "against_votes": self.proposal_against_votes[pid],
                "quorum_reached": self.proposal_quorum_reached[pid],
            }
            for pid in range(1, self.proposal_count + 1)
            if self.proposal_end_times[pid] > timestamp
        ]
        return active[skip : skip + limit]

    @view
    def proposal_vote_history(
        self, proposal_id: int, skip: int = 0, limit: int = DEFAULT_PAGINATION_LIMIT
    ) -> List[dict]:
        """Get paginated vote history for proposal."""
        votes = [
            {
                "voter": voter,
                "support": self.vote_support[(proposal_id, voter)],
                "power": self.vote_power[(proposal_id, voter)],
                "timestamp": self.vote_timestamp[(proposal_id, voter)],
            }
            for voter in {
                key[1] for key in self.vote_support.keys() if key[0] == proposal_id
            }
        ]
        votes.sort(key=lambda x: x["timestamp"])
        return votes[skip : skip + limit]
