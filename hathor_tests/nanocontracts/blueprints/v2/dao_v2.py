from typing import NamedTuple

from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    BlueprintId,
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


class ProposalInfo(NamedTuple):
    """Information about a specific proposal."""

    title: str
    description: str
    creator: bytes
    start_time: int
    end_time: int
    for_votes: int
    against_votes: int
    total_staked: int
    quorum_reached: bool
    total_voters: int


class VoteInfo(NamedTuple):
    """Information about a specific vote."""

    support: bool
    power: int
    timestamp: int


class DAOFrontEndInfo(NamedTuple):
    """DAO statistics for frontend."""

    total_proposals: int
    active_proposals: int
    total_voters: int
    total_votes: int
    quorum_percentage: int
    proposal_threshold: int


class ProposalData(NamedTuple):
    """Detailed proposal data with state."""

    title: str
    description: str
    creator: Address
    start_time: int
    end_time: int
    for_votes: int
    against_votes: int
    total_staked: int
    quorum_reached: bool
    total_voters: int
    state: str


class ActiveProposalInfo(NamedTuple):
    """Summary information for active proposals."""

    id: int
    title: str
    end_time: int
    for_votes: int
    against_votes: int
    quorum_reached: bool


class VoteHistoryInfo(NamedTuple):
    """Vote history information."""

    voter: Address
    support: bool
    power: int
    timestamp: int


class InvalidVersion(NCFail):
    pass


class DAOV2(Blueprint):
    """V2 DAO contract with staking-based voting power.

    New in V2:
    - governance_version field to track governance protocol version
    - migrate_v1_to_v2() method to initialize V2 fields
    """

    # Configuration
    name: str
    description: str
    governance_token: TokenUid
    staking_contract: ContractId
    voting_period_seconds: int
    quorum_percentage: int
    proposal_threshold: Amount
    creator_contract_id: ContractId  # DozerTools contract that created this

    # Proposal data
    proposal_count: int
    proposal_titles: dict[int, str]
    proposal_descriptions: dict[int, str]
    proposal_creators: dict[int, bytes]
    proposal_start_times: dict[int, int]
    proposal_end_times: dict[int, int]
    proposal_for_votes: dict[int, Amount]
    proposal_against_votes: dict[int, Amount]
    proposal_total_staked: dict[int, Amount]
    proposal_quorum_reached: dict[int, bool]
    proposal_total_voters: dict[int, int]

    # Vote data
    vote_support: dict[tuple[int, bytes], bool]
    vote_power: dict[tuple[int, bytes], Amount]
    vote_timestamp: dict[tuple[int, bytes], int]

    # Version tracking
    contract_version: str  # Semantic version string (e.g., "1.0.0")

    # NEW IN V2: Governance version tracking
    governance_version: int  # Governance protocol version

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
        creator_contract_id: ContractId,
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
        # Set creator_contract_id (for DozerTools routing)
        self.creator_contract_id = creator_contract_id
        # Initialize version (V2 starts at 2.0.0)
        self.contract_version = "2.0.0"

        # Initialize proposal and vote dictionaries
        self.proposal_titles = {}
        self.proposal_descriptions = {}
        self.proposal_creators = {}
        self.proposal_start_times = {}
        self.proposal_end_times = {}
        self.proposal_for_votes = {}
        self.proposal_against_votes = {}
        self.proposal_total_staked = {}
        self.proposal_quorum_reached = {}
        self.proposal_total_voters = {}
        self.vote_support = {}
        self.vote_power = {}
        self.vote_timestamp = {}

        # Initialize V2 fields (not migrated yet, will be set by migrate_v1_to_v2)
        self.governance_version = 1  # Default to version 1

    @public
    def create_proposal(self, ctx: Context, title: str, description: str) -> int:
        """Create new proposal if caller meets staking threshold."""
        staked = self._get_voting_power(ctx, ctx.caller_id)
        if staked < self.proposal_threshold:
            raise NCFail("Insufficient staked tokens")

        proposal_id = self.proposal_count + 1
        total_staked = self._get_total_staked(ctx)

        self.proposal_titles[proposal_id] = title
        self.proposal_descriptions[proposal_id] = description
        self.proposal_creators[proposal_id] = ctx.caller_id
        self.proposal_start_times[proposal_id] = ctx.block.timestamp
        self.proposal_end_times[proposal_id] = (
            ctx.block.timestamp + self.voting_period_seconds
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
        if ctx.block.timestamp >= self.proposal_end_times[proposal_id]:
            raise NCFail("Voting period ended")

        vote_key = (proposal_id, ctx.caller_id)
        if vote_key in self.vote_support:
            raise NCFail("Already voted")

        power = self._get_voting_power(ctx, ctx.caller_id)
        if power == 0:
            raise NCFail("No voting power")

        self.vote_support[vote_key] = support
        self.vote_power[vote_key] = power
        self.vote_timestamp[vote_key] = ctx.block.timestamp

        if support:
            self.proposal_for_votes[proposal_id] = Amount(
                self.proposal_for_votes[proposal_id] + power
            )
        else:
            self.proposal_against_votes[proposal_id] = Amount(
                self.proposal_against_votes[proposal_id] + power
            )

        self.proposal_total_voters[proposal_id] += 1

        total_votes = (
            self.proposal_for_votes[proposal_id]
            + self.proposal_against_votes[proposal_id]
        )
        min_votes = (
            self.proposal_total_staked[proposal_id] * self.quorum_percentage
        ) // 100
        self.proposal_quorum_reached[proposal_id] = total_votes >= min_votes

    def _only_creator_contract(self, ctx: Context) -> None:
        if ContractId(ctx.caller_id) != self.creator_contract_id:
            raise NCFail("Only creator contract can call this method")

    def _get_voting_power(self, ctx: Context, address: bytes) -> Amount:
        """Get voting power from staking contract."""
        return self.syscall.call_view_method(
            self.staking_contract, "get_max_withdrawal", address, ctx.block.timestamp
        )

    def _get_total_staked(self, ctx: Context) -> Amount:
        """Get total staked from staking contract."""
        info = self.syscall.call_view_method(self.staking_contract, "front_end_api")
        return info.total_staked

    @view
    def get_proposal(self, proposal_id: int) -> ProposalInfo | None:
        """Get proposal details."""
        if proposal_id not in self.proposal_titles:
            return None

        return ProposalInfo(
            title=self.proposal_titles[proposal_id],
            description=self.proposal_descriptions[proposal_id],
            creator=self.proposal_creators[proposal_id],
            start_time=self.proposal_start_times[proposal_id],
            end_time=self.proposal_end_times[proposal_id],
            for_votes=self.proposal_for_votes[proposal_id],
            against_votes=self.proposal_against_votes[proposal_id],
            total_staked=self.proposal_total_staked[proposal_id],
            quorum_reached=self.proposal_quorum_reached[proposal_id],
            total_voters=self.proposal_total_voters[proposal_id],
        )

    @view
    def get_vote(self, proposal_id: int, voter: Address) -> VoteInfo | None:
        """Get vote details."""
        vote_key = (proposal_id, voter)
        if vote_key not in self.vote_support:
            return None

        return VoteInfo(
            support=self.vote_support[vote_key],
            power=self.vote_power[vote_key],
            timestamp=self.vote_timestamp[vote_key],
        )

    @view
    def front_end_api_dao(self, timestamp: Timestamp) -> DAOFrontEndInfo:
        """Get DAO statistics for frontend."""
        active = sum(
            1 for end_time in self.proposal_end_times.values() if end_time > timestamp
        )

        return DAOFrontEndInfo(
            total_proposals=self.proposal_count,
            active_proposals=active,
            total_voters=(
                len(self.vote_support) // self.proposal_count
                if self.proposal_count > 0
                else 0
            ),
            total_votes=len(self.vote_support),
            quorum_percentage=self.quorum_percentage,
            proposal_threshold=self.proposal_threshold,
        )

    @view
    def proposal_data(
        self, proposal_id: int, timestamp: Timestamp
    ) -> ProposalData | None:
        """Get detailed proposal data."""
        if proposal_id not in self.proposal_titles:
            return None

        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            return None

        state = "active" if proposal.end_time > timestamp else "ended"
        return ProposalData(
            title=proposal.title,
            description=proposal.description,
            creator=proposal.creator,
            start_time=proposal.start_time,
            end_time=proposal.end_time,
            for_votes=proposal.for_votes,
            against_votes=proposal.against_votes,
            total_staked=proposal.total_staked,
            quorum_reached=proposal.quorum_reached,
            total_voters=proposal.total_voters,
            state=state,
        )

    @view
    def active_proposals(
        self, timestamp: Timestamp, skip: int = 0, limit: int = DEFAULT_PAGINATION_LIMIT
    ) -> list[ActiveProposalInfo]:
        """Get paginated list of active proposals."""
        active = [
            ActiveProposalInfo(
                id=pid,
                title=self.proposal_titles[pid],
                end_time=self.proposal_end_times[pid],
                for_votes=self.proposal_for_votes[pid],
                against_votes=self.proposal_against_votes[pid],
                quorum_reached=self.proposal_quorum_reached[pid],
            )
            for pid in range(1, self.proposal_count + 1)
            if self.proposal_end_times[pid] > timestamp
        ]
        return active[skip : skip + limit]

    @view
    def proposal_vote_history(
        self, proposal_id: int, skip: int = 0, limit: int = DEFAULT_PAGINATION_LIMIT
    ) -> list[VoteHistoryInfo]:
        """Get paginated vote history for proposal."""
        votes = [
            VoteHistoryInfo(
                voter=Address(voter),
                support=self.vote_support[(proposal_id, voter)],
                power=self.vote_power[(proposal_id, voter)],
                timestamp=self.vote_timestamp[(proposal_id, voter)],
            )
            for voter in {
                key[1] for key in self.vote_support.keys() if key[0] == proposal_id
            }
        ]
        votes.sort(key=lambda x: x.timestamp)
        return votes[skip : skip + limit]

    # Routing methods for DozerTools integration
    @public
    def routed_create_proposal(
        self, ctx: Context, user_address: Address, title: str, description: str
    ) -> int:
        """Create new proposal via DozerTools routing."""
        self._only_creator_contract(ctx)

        staked = self._get_voting_power(ctx, user_address)
        if staked < self.proposal_threshold:
            raise NCFail("Insufficient staked tokens")

        proposal_id = self.proposal_count + 1
        total_staked = self._get_total_staked(ctx)

        self.proposal_titles[proposal_id] = title
        self.proposal_descriptions[proposal_id] = description
        self.proposal_creators[proposal_id] = user_address
        self.proposal_start_times[proposal_id] = ctx.block.timestamp
        self.proposal_end_times[proposal_id] = (
            ctx.block.timestamp + self.voting_period_seconds
        )
        self.proposal_for_votes[proposal_id] = Amount(0)
        self.proposal_against_votes[proposal_id] = Amount(0)
        self.proposal_total_staked[proposal_id] = total_staked
        self.proposal_quorum_reached[proposal_id] = False
        self.proposal_total_voters[proposal_id] = 0

        self.proposal_count = proposal_id
        return proposal_id

    @public
    def routed_cast_vote(
        self, ctx: Context, user_address: Address, proposal_id: int, support: bool
    ) -> None:
        """Cast vote via DozerTools routing."""
        self._only_creator_contract(ctx)

        if proposal_id not in self.proposal_titles:
            raise NCFail("Proposal does not exist")
        if ctx.block.timestamp >= self.proposal_end_times[proposal_id]:
            raise NCFail("Voting period ended")

        vote_key = (proposal_id, user_address)
        if vote_key in self.vote_support:
            raise NCFail("Already voted")

        power = self._get_voting_power(ctx, user_address)
        if power == 0:
            raise NCFail("No voting power")

        self.vote_support[vote_key] = support
        self.vote_power[vote_key] = power
        self.vote_timestamp[vote_key] = ctx.block.timestamp

        if support:
            self.proposal_for_votes[proposal_id] = Amount(
                self.proposal_for_votes[proposal_id] + power
            )
        else:
            self.proposal_against_votes[proposal_id] = Amount(
                self.proposal_against_votes[proposal_id] + power
            )

        self.proposal_total_voters[proposal_id] += 1

        total_votes = (
            self.proposal_for_votes[proposal_id]
            + self.proposal_against_votes[proposal_id]
        )
        min_votes = (
            self.proposal_total_staked[proposal_id] * self.quorum_percentage
        ) // 100
        self.proposal_quorum_reached[proposal_id] = total_votes >= min_votes

    @public
    def migrate_v1_to_v2(self, ctx: Context) -> None:
        """Migration method to initialize V2 fields after upgrade from V1.

        Args:
            ctx: Transaction context

        Raises:
            NCFail: If caller is not the creator contract
        """
        # Only creator contract can migrate (DAO has no single owner)
        if ContractId(ctx.caller_id) != self.creator_contract_id:
            raise NCFail("Only creator contract can migrate")

        # Initialize new V2 field - set to version 2
        self.governance_version = 2

    @view
    def get_governance_version(self) -> int:
        """Get governance protocol version (V2 feature).

        Returns:
            Governance protocol version number
        """
        # Handle case where field doesn't exist yet (pre-migration)
        try:
            return self.governance_version
        except (KeyError, AttributeError):
            return 0

    @public
    def upgrade_contract(self, ctx: Context, new_blueprint_id: BlueprintId, new_version: str) -> None:
        """Upgrade this contract to a new blueprint version.

        Args:
            ctx: Transaction context
            new_blueprint_id: The blueprint ID to upgrade to
            new_version: Version string for the new blueprint (e.g., "1.1.0")

        Raises:
            NCFail: If caller is not the creator contract
            InvalidVersion: If new version is not higher than current version
        """
        # Only creator contract can upgrade (DAO has no single owner)
        if ContractId(ctx.caller_id) != self.creator_contract_id:
            raise NCFail("Only creator contract can upgrade this contract")

        # Validate version is newer
        if not self._is_version_higher(new_version, self.contract_version):
            raise InvalidVersion(f"New version {new_version} must be higher than current {self.contract_version}")
        self.contract_version = new_version

        # Perform the upgrade
        self.syscall.change_blueprint(new_blueprint_id)

    def _is_version_higher(self, new_version: str, current_version: str) -> bool:
        """Compare semantic versions (e.g., "1.2.3").

        Returns True if new_version > current_version.
        Returns False if versions are malformed or equal.
        """
        # Split versions by '.'
        new_parts_str = new_version.split('.')
        current_parts_str = current_version.split('.')

        # Check if all parts are valid integers
        new_parts: list[int] = []
        for part in new_parts_str:
            # Simple check: all characters must be digits
            if not part or not all(c in '0123456789' for c in part):
                return False  # Invalid format
            new_parts.append(int(part))

        current_parts: list[int] = []
        for part in current_parts_str:
            if not part or not all(c in '0123456789' for c in part):
                return False  # Invalid format
            current_parts.append(int(part))

        # Pad shorter version with zeros
        max_len = len(new_parts) if len(new_parts) > len(current_parts) else len(current_parts)
        while len(new_parts) < max_len:
            new_parts.append(0)
        while len(current_parts) < max_len:
            current_parts.append(0)

        # Compare versions
        return new_parts > current_parts

    @view
    def get_contract_version(self) -> str:
        """Get the current contract version.

        Returns:
            Version string (e.g., "1.0.0")
        """
        return self.contract_version


__blueprint__ = DAOV2
