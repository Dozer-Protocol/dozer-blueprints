# Copyright 2025 Hathor Labs
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    Address,
    Amount,
    BlueprintId,
    ContractId,
    NCAction,
    NCDepositAction,
    NCWithdrawalAction,
    Timestamp,
    TokenUid,
    VertexId,
    public,
    view,
    NCActionType,
)

# Blueprint IDs from nano_testnet.yml
VESTING_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "42e7f272b6b966f26576a5c1d0c9637f456168c85e18a3e86c0c60e909a93275"
        )
    )
)
STAKING_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "ac6bf4f6a89a34e81a21a6e07e24f07739af5c3d6f4c15e16c5ae4e4108aaa48"
        )
    )
)
DAO_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "6cfdd13e8b9c689b8d87bb8100b4e580e0e9d20ee75a8c5aee9e7bef51e0b1a0"
        )
    )
)
CROWDSALE_BLUEPRINT_ID = BlueprintId(
    VertexId(
        bytes.fromhex(
            "7b3ae18c763b2254baf8b9801bc0dcd3e77db57d7de7fd34cc62b526aa91d9fb"
        )
    )
)

# Special allocation indices for vesting contract
STAKING_ALLOCATION_INDEX = 0  # "Staking" - for staking contract
PUBLIC_SALE_ALLOCATION_INDEX = 1  # "Public Sale" - for crowdsale contract
DOZER_POOL_ALLOCATION_INDEX = 2  # "Dozer Pool" - for liquidity pool
# Indices 3-9 available for regular time-locked vesting schedules

# HTR token UID
HTR_UID = b'\x00'

# Null contract ID for initialization
NULL_CONTRACT_ID = ContractId(VertexId(b"\x00" * 32))

# Placeholder DZR token UID (to be updated later)
DZR_UID = TokenUid(VertexId(b"\x01" * 32))


class DozerToolsError(NCFail):
    """Base error for DozerTools operations."""

    pass


class ProjectNotFound(DozerToolsError):
    """Raised when trying to access a project that doesn't exist."""

    pass


class ProjectAlreadyExists(DozerToolsError):
    """Raised when trying to create a project that already exists."""

    pass


class Unauthorized(DozerToolsError):
    """Raised when unauthorized address tries to perform an action."""

    pass


class InsufficientCredits(DozerToolsError):
    """Raised when project has insufficient credits for operation."""

    pass


class TokenBlacklisted(DozerToolsError):
    """Raised when trying to use a blacklisted token."""

    pass


class ContractAlreadyExists(DozerToolsError):
    """Raised when trying to create a contract that already exists."""

    pass


class VestingNotConfigured(DozerToolsError):
    """Raised when trying to access vesting that is not configured."""

    pass


class InvalidAllocation(DozerToolsError):
    """Raised when allocation percentages are invalid."""

    pass


class DozerTools(Blueprint):
    """Singleton contract for managing token projects with credit-based fee system.

    This contract manages multiple token projects in a centralized way, allowing
    project creators to create tokens and manage their entire ecosystem through
    a single interface with a credit-based fee system.
    """

    # Global administration
    owner: Address
    dozer_pool_manager_id: ContractId
    dzr_token_uid: TokenUid

    # Legacy token permissions (admin-controlled)
    legacy_token_permissions: dict[TokenUid, Address]  # token -> authorized_creator
    blacklisted_tokens: dict[TokenUid, bool]  # token -> blacklisted

    # Project registry (using TokenUid as key)
    project_exists: dict[TokenUid, bool]  # token_uid -> exists
    all_projects: list[TokenUid]  # Ordered list of all project tokens
    total_projects_count: int  # Total number of projects created

    # Project basic information
    project_name: dict[TokenUid, str]  # token_uid -> name
    project_symbol: dict[TokenUid, str]  # token_uid -> symbol
    project_dev: dict[TokenUid, Address]  # token_uid -> dev_address
    project_created_at: dict[TokenUid, Timestamp]  # token_uid -> created_at
    project_total_supply: dict[TokenUid, Amount]  # token_uid -> total_supply

    # Project optional metadata
    project_description: dict[TokenUid, str]  # token_uid -> description
    project_website: dict[TokenUid, str]  # token_uid -> website
    project_logo_url: dict[TokenUid, str]  # token_uid -> logo_url
    project_twitter: dict[TokenUid, str]  # token_uid -> twitter
    project_telegram: dict[TokenUid, str]  # token_uid -> telegram
    project_discord: dict[TokenUid, str]  # token_uid -> discord
    project_github: dict[TokenUid, str]  # token_uid -> github
    project_category: dict[TokenUid, str]  # token_uid -> category
    project_whitepaper_url: dict[TokenUid, str]  # token_uid -> whitepaper_url

    # Credit system per project
    project_htr_balance: dict[TokenUid, Amount]  # token_uid -> HTR balance
    project_dzr_balance: dict[TokenUid, Amount]  # token_uid -> DZR balance
    minimum_deposit: Amount  # Minimum deposit to enable contract usage

    # Fee structure for method calls
    method_fees_htr: dict[str, Amount]  # method_name -> HTR cost
    method_fees_dzr: dict[str, Amount]  # method_name -> DZR cost

    # Contract ecosystem per project
    project_vesting_contract: dict[
        TokenUid, ContractId
    ]  # token_uid -> vesting_contract
    project_staking_contract: dict[
        TokenUid, ContractId
    ]  # token_uid -> staking_contract
    project_dao_contract: dict[TokenUid, ContractId]  # token_uid -> dao_contract
    project_crowdsale_contract: dict[
        TokenUid, ContractId
    ]  # token_uid -> crowdsale_contract

    # TODO: Change project_pools to support multiple pools (list[str]) later
    project_pools: dict[TokenUid, str]  # token_uid -> pool_key (single pool for now)

    # Special allocation percentages (0 means not configured)
    project_staking_percentage: dict[TokenUid, int]  # token_uid -> staking %
    project_public_sale_percentage: dict[TokenUid, int]  # token_uid -> public sale %
    project_dozer_pool_percentage: dict[TokenUid, int]  # token_uid -> dozer pool %

    # Vesting configuration status
    project_vesting_configured: dict[TokenUid, bool]  # token_uid -> is_configured

    def _only_owner(self, ctx: Context) -> None:
        """Ensure only the contract owner can call this method."""
        if Address(ctx.caller_id) != self.owner:
            raise Unauthorized("Only contract owner can call this method")

    def _only_project_dev(self, ctx: Context, token_uid: TokenUid) -> None:
        """Ensure only the project dev can call this method."""
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        project_dev = self.project_dev.get(token_uid, Address(b"\x00" * 25))
        if Address(ctx.caller_id) != project_dev:
            raise Unauthorized("Only project dev can call this method")

    def _validate_token_not_blacklisted(self, token_uid: TokenUid) -> None:
        """Ensure token is not blacklisted."""
        if self.blacklisted_tokens.get(token_uid, False):
            raise TokenBlacklisted("Token is blacklisted")

    def _validate_legacy_token_permission(
        self, ctx: Context, token_uid: TokenUid
    ) -> None:
        """Validate permission for legacy tokens."""
        if token_uid in self.legacy_token_permissions:
            authorized_address = self.legacy_token_permissions[token_uid]
            if Address(ctx.caller_id) != authorized_address:
                raise Unauthorized(
                    "Not authorized to create project for this legacy token"
                )

    def _charge_fee(self, ctx: Context, token_uid: TokenUid, method_name: str) -> None:
        """Charge fee from project balance (DZR preferred over HTR)."""
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        dzr_fee = self.method_fees_dzr.get(method_name, Amount(0))
        htr_fee = self.method_fees_htr.get(method_name, Amount(0))

        project_dzr_balance = self.project_dzr_balance.get(token_uid, Amount(0))
        project_htr_balance = self.project_htr_balance.get(token_uid, Amount(0))

        # Try to use DZR first (cheaper)
        if dzr_fee > 0 and project_dzr_balance >= dzr_fee:
            self.project_dzr_balance[token_uid] = Amount(project_dzr_balance - dzr_fee)
        elif htr_fee > 0 and project_htr_balance >= htr_fee:
            self.project_htr_balance[token_uid] = Amount(project_htr_balance - htr_fee)
        elif dzr_fee > 0 or htr_fee > 0:
            raise InsufficientCredits("Insufficient credits for this operation")

    def _generate_salt(
        self, ctx: Context, token_uid: TokenUid, contract_type: str
    ) -> bytes:
        """Generate a unique salt for contract creation."""
        return (
            token_uid
            + bytes(contract_type, "utf-8")
            + bytes(str(ctx.timestamp), "utf-8")
        )

    @public(allow_deposit=True)
    def initialize(
        self,
        ctx: Context,
        dozer_pool_manager_id: ContractId,
        dzr_token_uid: TokenUid,
        minimum_deposit: Amount,
    ) -> None:
        """Initialize the DozerTools contract.

        Args:
            ctx: Transaction context
            dozer_pool_manager_id: ContractId of the DozerPoolManager
            dzr_token_uid: TokenUid of the DZR token for cheaper fees
            minimum_deposit: Minimum deposit required to enable contract usage
        """

        self.owner = Address(ctx.caller_id)
        self.dozer_pool_manager_id = dozer_pool_manager_id
        self.dzr_token_uid = dzr_token_uid
        self.minimum_deposit = minimum_deposit

        # Initialize all fees to 0 (free initially)
        # self.method_fees_htr = {}
        # self.method_fees_dzr = {}

        # Initialize project counter
        self.total_projects_count = 0

    @public(allow_deposit=True)
    def create_project(
        self,
        ctx: Context,
        token_name: str,
        token_symbol: str,
        total_supply: Amount,
        description: str,
        website: str,
        logo_url: str,
        twitter: str,
        telegram: str,
        discord: str,
        github: str,
        category: str,
        whitepaper_url: str,
    ) -> TokenUid:
        """Create new token project with metadata.

        Args:
            ctx: Transaction context
            token_name: Human-readable name of the token
            token_symbol: Symbol/ticker of the token
            initial_supply: Initial amount to mint
            total_supply: Maximum total supply
            description: Project description (optional)
            website: Official website (optional)
            logo_url: Logo image URL (optional)
            twitter: Twitter handle (optional)
            telegram: Telegram link (optional)
            discord: Discord link (optional)
            github: GitHub link (optional)
            category: Project category (optional)
            whitepaper_url: Whitepaper link (optional)

        Returns:
            TokenUid of the created token
        """
        # validate if this user is depositing the HTR to create the token(because if we not check it it will consume the HTR from other users)
        if len(ctx.actions) != 1:
            raise InsufficientCredits("Exactly one HTR deposit action required")

        htr_action = ctx.get_single_action(TokenUid(HTR_UID))
        if htr_action.type != NCActionType.DEPOSIT:
            raise InsufficientCredits("HTR deposit required for token creation")

        # Cast to deposit action to access amount safely
        if isinstance(htr_action, NCDepositAction):
            required_htr = total_supply // 100  # 1% of total supply
            if htr_action.amount != required_htr:
                raise InsufficientCredits(
                    "HTR deposit amount must be at least 1 percent of total supply"
                )
        else:
            raise InsufficientCredits("HTR deposit required for token creation")

        # Create the token
        token_uid = self.syscall.create_token(
            token_name,
            token_symbol,
            total_supply,
            True,  # mint_authority
            True,  # melt_authority
        )

        # Validate token permissions for legacy tokens
        self._validate_legacy_token_permission(ctx, token_uid)
        self._validate_token_not_blacklisted(token_uid)

        if self.project_exists.get(token_uid, False):
            raise ProjectAlreadyExists("Project already exists for this token")

        # Store basic project information
        self.project_exists[token_uid] = True
        self.all_projects.append(token_uid)
        self.total_projects_count += 1
        self.project_name[token_uid] = token_name
        self.project_symbol[token_uid] = token_symbol
        self.project_dev[token_uid] = Address(ctx.caller_id)
        self.project_created_at[token_uid] = Timestamp(ctx.timestamp)
        self.project_total_supply[token_uid] = total_supply

        # Store optional metadata (only if provided and not empty)
        if description != "":
            self.project_description[token_uid] = description
        if website != "":
            self.project_website[token_uid] = website
        if logo_url != "":
            self.project_logo_url[token_uid] = logo_url
        if twitter != "":
            self.project_twitter[token_uid] = twitter
        if telegram != "":
            self.project_telegram[token_uid] = telegram
        if discord != "":
            self.project_discord[token_uid] = discord
        if github != "":
            self.project_github[token_uid] = github
        if category != "":
            self.project_category[token_uid] = category
        if whitepaper_url != "":
            self.project_whitepaper_url[token_uid] = whitepaper_url

        # Create vesting contract and deposit ALL tokens
        vesting_salt = self._generate_salt(ctx, token_uid, "vesting")
        vesting_actions: list[NCAction] = [
            NCDepositAction(token_uid=token_uid, amount=total_supply)
        ]

        vesting_id, _ = self.syscall.create_contract(
            VESTING_BLUEPRINT_ID,
            vesting_salt,
            vesting_actions,
            token_uid,  # Initialize vesting with the token
        )

        # Initialize contract references
        self.project_vesting_contract[token_uid] = vesting_id
        self.project_staking_contract[token_uid] = NULL_CONTRACT_ID
        self.project_dao_contract[token_uid] = NULL_CONTRACT_ID
        self.project_crowdsale_contract[token_uid] = NULL_CONTRACT_ID
        self.project_pools[token_uid] = ""

        # Initialize special allocation percentages (0 means not configured)
        self.project_staking_percentage[token_uid] = 0
        self.project_public_sale_percentage[token_uid] = 0
        self.project_dozer_pool_percentage[token_uid] = 0

        # Initialize vesting configuration status
        self.project_vesting_configured[token_uid] = False

        # Initialize credit balances
        self.project_htr_balance[token_uid] = Amount(0)
        self.project_dzr_balance[token_uid] = Amount(0)

        return token_uid

    @public(allow_deposit=True)
    def deposit_credits(self, ctx: Context, token_uid: TokenUid) -> None:
        """Deposit HTR/DZR credits to project balance.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "deposit_credits")

        # Expect exactly one deposit action
        if len(ctx.actions) != 1:
            raise InsufficientCredits("Exactly one deposit action allowed")

        # Get the single action based on token type and cast to deposit action
        if TokenUid(HTR_UID) in ctx.actions:
            deposit_action = ctx.get_single_action(TokenUid(HTR_UID))
            if deposit_action.type != NCActionType.DEPOSIT:
                raise InsufficientCredits("Only deposits allowed")
            action_amount = Amount(
                deposit_action.amount
                if isinstance(deposit_action, NCDepositAction)
                else 0
            )
            current_balance = self.project_htr_balance.get(token_uid, Amount(0))
            self.project_htr_balance[token_uid] = Amount(
                current_balance + action_amount
            )
        elif self.dzr_token_uid in ctx.actions:
            deposit_action = ctx.get_single_action(self.dzr_token_uid)
            if deposit_action.type != NCActionType.DEPOSIT:
                raise InsufficientCredits("Only deposits allowed")
            action_amount = Amount(
                deposit_action.amount
                if isinstance(deposit_action, NCDepositAction)
                else 0
            )
            current_balance = self.project_dzr_balance.get(token_uid, Amount(0))
            self.project_dzr_balance[token_uid] = Amount(
                current_balance + action_amount
            )
        else:
            raise InsufficientCredits("Only HTR and DZR deposits accepted")

    @public
    def create_staking_contract(
        self,
        ctx: Context,
        token_uid: TokenUid,
        earnings_per_day: int,
    ) -> ContractId:
        """Create staking contract with tokens from vesting contract.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
            earnings_per_day: Daily earnings rate for staking rewards

        Returns:
            ContractId of the created staking contract
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "create_staking_contract")

        if (
            self.project_staking_contract.get(token_uid, NULL_CONTRACT_ID)
            != NULL_CONTRACT_ID
        ):
            raise ContractAlreadyExists("Staking contract already exists")

        if not self.project_vesting_configured.get(token_uid, False):
            raise VestingNotConfigured("Vesting must be configured first")

        staking_percentage = self.project_staking_percentage.get(token_uid, 0)
        if staking_percentage == 0:
            raise InvalidAllocation("No staking allocation configured")

        return self._create_staking(ctx, token_uid, earnings_per_day)

    def _create_staking(
        self, ctx: Context, token_uid: TokenUid, earnings_per_day: int
    ) -> ContractId:
        """Helper method to create staking contract with tokens from vesting."""
        vesting_contract = self.project_vesting_contract[token_uid]
        total_supply = self.project_total_supply[token_uid]
        staking_percentage = self.project_staking_percentage[token_uid]

        # Calculate staking allocation amount
        staking_amount = Amount((total_supply * staking_percentage) // 100)

        # Withdraw tokens from vesting contract (staking allocation)
        withdraw_actions: list[NCAction] = [
            NCWithdrawalAction(token_uid=token_uid, amount=staking_amount)
        ]

        self.syscall.call_public_method(
            vesting_contract,
            "claim_allocation",
            withdraw_actions,
            STAKING_ALLOCATION_INDEX,
        )

        # Create staking contract with withdrawn tokens
        salt = self._generate_salt(ctx, token_uid, "staking")
        staking_actions: list[NCAction] = [
            NCDepositAction(token_uid=token_uid, amount=staking_amount)
        ]

        staking_id, _ = self.syscall.create_contract(
            STAKING_BLUEPRINT_ID, salt, staking_actions, earnings_per_day, token_uid
        )

        self.project_staking_contract[token_uid] = staking_id
        return staking_id

    @public
    def create_dao_contract(
        self,
        ctx: Context,
        token_uid: TokenUid,
        name: str,
        description: str,
        voting_period_days: int,
        quorum_percentage: int,
        proposal_threshold: Amount,
    ) -> ContractId:
        """Create DAO contract.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
            name: DAO name
            description: DAO description
            voting_period_days: Voting period in days
            quorum_percentage: Minimum quorum percentage
            proposal_threshold: Minimum tokens needed to create proposals

        Returns:
            ContractId of the created DAO contract
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "create_dao_contract")

        if (
            self.project_dao_contract.get(token_uid, NULL_CONTRACT_ID)
            != NULL_CONTRACT_ID
        ):
            raise ContractAlreadyExists("DAO contract already exists")

        staking_contract = self.project_staking_contract.get(
            token_uid, NULL_CONTRACT_ID
        )
        if staking_contract == NULL_CONTRACT_ID:
            raise ProjectNotFound("Staking contract must be created first")

        # Generate salt and create DAO contract
        salt = self._generate_salt(ctx, token_uid, "dao")

        dao_id, _ = self.syscall.create_contract(
            DAO_BLUEPRINT_ID,
            salt,
            [],
            name,
            description,
            token_uid,
            staking_contract,
            voting_period_days,
            quorum_percentage,
            proposal_threshold,
        )

        self.project_dao_contract[token_uid] = dao_id
        return dao_id

    @public
    def create_crowdsale_contract(
        self,
        ctx: Context,
        token_uid: TokenUid,
        rate: Amount,
        soft_cap: Amount,
        hard_cap: Amount,
        min_deposit: Amount,
        start_time: Timestamp,
        end_time: Timestamp,
        platform_fee: Amount,
    ) -> ContractId:
        """Create crowdsale contract.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
            rate: Tokens per HTR
            soft_cap: Minimum goal in HTR
            hard_cap: Maximum cap in HTR
            min_deposit: Minimum purchase in HTR
            start_time: Sale start time
            end_time: Sale end time
            platform_fee: Platform fee in basis points

        Returns:
            ContractId of the created crowdsale contract
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "create_crowdsale_contract")

        if (
            self.project_crowdsale_contract.get(token_uid, NULL_CONTRACT_ID)
            != NULL_CONTRACT_ID
        ):
            raise ContractAlreadyExists("Crowdsale contract already exists")

        if not self.project_vesting_configured.get(token_uid, False):
            raise VestingNotConfigured("Vesting must be configured first")

        public_sale_percentage = self.project_public_sale_percentage.get(token_uid, 0)
        if public_sale_percentage == 0:
            raise InvalidAllocation("No public sale allocation configured")

        return self._create_crowdsale(
            ctx,
            token_uid,
            rate,
            soft_cap,
            hard_cap,
            min_deposit,
            start_time,
            end_time,
            platform_fee,
        )

    def _create_crowdsale(
        self,
        ctx: Context,
        token_uid: TokenUid,
        rate: Amount,
        soft_cap: Amount,
        hard_cap: Amount,
        min_deposit: Amount,
        start_time: Timestamp,
        end_time: Timestamp,
        platform_fee: Amount,
    ) -> ContractId:
        """Helper method to create crowdsale contract with tokens from vesting."""
        vesting_contract = self.project_vesting_contract[token_uid]
        total_supply = self.project_total_supply[token_uid]
        public_sale_percentage = self.project_public_sale_percentage[token_uid]

        # Calculate public sale allocation amount
        public_sale_amount = Amount((total_supply * public_sale_percentage) // 100)

        # Withdraw tokens from vesting contract (public sale allocation)
        withdraw_actions: list[NCAction] = [
            NCWithdrawalAction(token_uid=token_uid, amount=public_sale_amount)
        ]

        self.syscall.call_public_method(
            vesting_contract,
            "claim_allocation",
            withdraw_actions,
            PUBLIC_SALE_ALLOCATION_INDEX,
        )

        # Generate salt and create crowdsale contract
        salt = self._generate_salt(ctx, token_uid, "crowdsale")
        crowdsale_actions: list[NCAction] = [
            NCDepositAction(token_uid=token_uid, amount=public_sale_amount)
        ]

        crowdsale_id, _ = self.syscall.create_contract(
            CROWDSALE_BLUEPRINT_ID,
            salt,
            crowdsale_actions,
            token_uid,
            rate,
            soft_cap,
            hard_cap,
            min_deposit,
            start_time,
            end_time,
            platform_fee,
        )

        self.project_crowdsale_contract[token_uid] = crowdsale_id
        return crowdsale_id

    @public(allow_deposit=True)
    def create_liquidity_pool(
        self,
        ctx: Context,
        token_uid: TokenUid,
        htr_amount: Amount,
        fee: Amount,
    ) -> str:
        """Create a liquidity pool in DozerPoolManager.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
            htr_amount: Amount of HTR to add to pool (user must deposit)
            fee: Pool fee (e.g., 3 for 0.3%)

        Returns:
            Pool key from DozerPoolManager
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "create_liquidity_pool")

        if self.project_pools.get(token_uid, "") != "":
            raise ContractAlreadyExists("Liquidity pool already exists")

        if not self.project_vesting_configured.get(token_uid, False):
            raise VestingNotConfigured("Vesting must be configured first")

        dozer_pool_percentage = self.project_dozer_pool_percentage.get(token_uid, 0)
        if dozer_pool_percentage == 0:
            raise InvalidAllocation("No dozer pool allocation configured")

        return self._create_liquidity_pool(ctx, token_uid, htr_amount, fee)

    def _create_liquidity_pool(
        self, ctx: Context, token_uid: TokenUid, htr_amount: Amount, fee: Amount
    ) -> str:
        """Helper method to create liquidity pool with tokens from vesting."""
        vesting_contract = self.project_vesting_contract[token_uid]
        total_supply = self.project_total_supply[token_uid]
        dozer_pool_percentage = self.project_dozer_pool_percentage[token_uid]

        # Calculate dozer pool allocation amount
        dozer_pool_amount = Amount((total_supply * dozer_pool_percentage) // 100)

        # Withdraw tokens from vesting contract (dozer pool allocation)
        withdraw_actions: list[NCAction] = [
            NCWithdrawalAction(token_uid=token_uid, amount=dozer_pool_amount)
        ]

        self.syscall.call_public_method(
            vesting_contract,
            "claim_allocation",
            withdraw_actions,
            DOZER_POOL_ALLOCATION_INDEX,
        )

        # Prepare actions for pool creation (tokens from vesting + HTR from user)
        pool_actions: list[NCAction] = [
            NCDepositAction(token_uid=token_uid, amount=dozer_pool_amount),
            NCDepositAction(token_uid=TokenUid(HTR_UID), amount=htr_amount),
        ]

        # Call DozerPoolManager to create pool
        pool_key = self.syscall.call_public_method(
            self.dozer_pool_manager_id, "create_pool", pool_actions, fee
        )

        self.project_pools[token_uid] = pool_key
        return pool_key

    @public
    def get_melt_authority(self, ctx: Context, token_uid: TokenUid) -> None:
        """Transfer melt authority of project token to project owner.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "get_melt_authority")

        # Check if the contract still has melt authority
        if not self.syscall.can_melt(token_uid):
            raise Unauthorized("Contract does not have melt authority for this token")

        # Get project dev address
        dev_address = self.project_dev[token_uid]

        # Revoke melt authority from contract and give to dev
        # Note: This transfers the authority from the contract to the specified address
        self.syscall.revoke_authorities(token_uid, revoke_mint=False, revoke_melt=True)

        # TODO: Implement authority transfer to dev when available in syscall
        # Currently, revoking from contract releases the authority

    # Admin Methods

    @public
    def update_method_fees(
        self,
        ctx: Context,
        method_name: str,
        htr_fee: Amount,
        dzr_fee: Amount,
    ) -> None:
        """Admin method to update fees for specific methods.

        Args:
            ctx: Transaction context
            method_name: Name of the method
            htr_fee: Fee in HTR
            dzr_fee: Fee in DZR
        """
        self._only_owner(ctx)
        self.method_fees_htr[method_name] = htr_fee
        self.method_fees_dzr[method_name] = dzr_fee

    @public
    def blacklist_token(self, ctx: Context, token_uid: TokenUid) -> None:
        """Admin method to blacklist a token from UI.

        Args:
            ctx: Transaction context
            token_uid: Token UID to blacklist
        """
        self._only_owner(ctx)
        self.blacklisted_tokens[token_uid] = True

    @public
    def unblacklist_token(self, ctx: Context, token_uid: TokenUid) -> None:
        """Admin method to remove token from blacklist.

        Args:
            ctx: Transaction context
            token_uid: Token UID to unblacklist
        """
        self._only_owner(ctx)
        self.blacklisted_tokens[token_uid] = False

    @public
    def set_legacy_token_permission(
        self,
        ctx: Context,
        token_uid: TokenUid,
        authorized_address: Address,
    ) -> None:
        """Admin method to set who can create project for legacy tokens.

        Args:
            ctx: Transaction context
            token_uid: Legacy token UID
            authorized_address: Address authorized to create project
        """
        self._only_owner(ctx)
        self.legacy_token_permissions[token_uid] = authorized_address

    @public
    def change_owner(self, ctx: Context, new_owner: Address) -> None:
        """Change the contract owner.

        Args:
            ctx: Transaction context
            new_owner: New owner address
        """
        self._only_owner(ctx)
        self.owner = new_owner

    # View Methods (JSON Structure)

    @view
    def get_all_projects(self) -> dict[str, str]:
        """Get all projects with basic information in JSON format.

        Returns:
            Dictionary with project information
        """
        projects = {}
        for token_uid in self.all_projects:
            if not self.blacklisted_tokens.get(token_uid, False):
                projects[token_uid.hex()] = self.project_name.get(token_uid, "")
        return projects

    @view
    def get_project_info(self, token_uid: TokenUid) -> dict[str, str]:
        """Get complete project information in JSON format.

        Args:
            token_uid: Project token UID

        Returns:
            Dictionary with complete project information
        """
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        project_info = {
            "token_uid": token_uid.hex(),
            "name": self.project_name.get(token_uid, ""),
            "symbol": self.project_symbol.get(token_uid, ""),
            "dev": self.project_dev.get(token_uid, Address(b"\x00" * 25)).hex(),
            "created_at": str(self.project_created_at.get(token_uid, 0)),
            "total_supply": str(self.project_total_supply.get(token_uid, 0)),
            "description": self.project_description.get(token_uid, ""),
            "website": self.project_website.get(token_uid, ""),
            "logo_url": self.project_logo_url.get(token_uid, ""),
            "twitter": self.project_twitter.get(token_uid, ""),
            "telegram": self.project_telegram.get(token_uid, ""),
            "discord": self.project_discord.get(token_uid, ""),
            "github": self.project_github.get(token_uid, ""),
            "category": self.project_category.get(token_uid, ""),
            "whitepaper_url": self.project_whitepaper_url.get(token_uid, ""),
        }

        return project_info

    @view
    def get_project_contracts(self, token_uid: TokenUid) -> dict[str, str]:
        """Get project contract information.

        Args:
            token_uid: Project token UID

        Returns:
            Dictionary with contract information
        """
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        vesting_contract = self.project_vesting_contract.get(
            token_uid, NULL_CONTRACT_ID
        )
        staking_contract = self.project_staking_contract.get(
            token_uid, NULL_CONTRACT_ID
        )
        dao_contract = self.project_dao_contract.get(token_uid, NULL_CONTRACT_ID)
        crowdsale_contract = self.project_crowdsale_contract.get(
            token_uid, NULL_CONTRACT_ID
        )

        return {
            "vesting_contract": (
                vesting_contract.hex() if vesting_contract != NULL_CONTRACT_ID else ""
            ),
            "staking_contract": (
                staking_contract.hex() if staking_contract != NULL_CONTRACT_ID else ""
            ),
            "dao_contract": (
                dao_contract.hex() if dao_contract != NULL_CONTRACT_ID else ""
            ),
            "crowdsale_contract": (
                crowdsale_contract.hex()
                if crowdsale_contract != NULL_CONTRACT_ID
                else ""
            ),
            "liquidity_pool": self.project_pools.get(token_uid, ""),
        }

    @view
    def get_project_credits(self, token_uid: TokenUid) -> dict[str, str]:
        """Get project credit balances.

        Args:
            token_uid: Project token UID

        Returns:
            Dictionary with credit information
        """
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        return {
            "htr_balance": str(self.project_htr_balance.get(token_uid, Amount(0))),
            "dzr_balance": str(self.project_dzr_balance.get(token_uid, Amount(0))),
            "minimum_deposit": str(self.minimum_deposit),
        }

    @view
    def search_projects_by_category(self, category: str) -> dict[str, str]:
        """Search projects by category.

        Args:
            category: Project category to search for

        Returns:
            Dictionary with matching projects
        """
        projects = {}
        for token_uid in self.all_projects:
            if not self.blacklisted_tokens.get(token_uid, False):
                project_category = self.project_category.get(token_uid, "")
                if project_category == category:
                    projects[token_uid.hex()] = self.project_name.get(token_uid, "")
        return projects

    @view
    def get_projects_by_dev(self, dev_address: Address) -> dict[str, str]:
        """Get all projects by a specific developer.

        Args:
            dev_address: Developer address

        Returns:
            Dictionary with projects by the developer
        """
        projects = {}
        for token_uid in self.all_projects:
            if not self.blacklisted_tokens.get(token_uid, False):
                project_dev = self.project_dev.get(token_uid, Address(b"\x00" * 25))
                if project_dev == dev_address:
                    projects[token_uid.hex()] = self.project_name.get(token_uid, "")
        return projects

    @view
    def get_method_fees(self, method_name: str) -> dict[str, str]:
        """Get fees for a specific method.

        Args:
            method_name: Name of the method to get fees for

        Returns:
            Dictionary with fee information for the method
        """
        htr_fee = self.method_fees_htr.get(method_name, Amount(0))
        dzr_fee = self.method_fees_dzr.get(method_name, Amount(0))
        return {
            "method_name": method_name,
            "htr_fee": str(htr_fee),
            "dzr_fee": str(dzr_fee),
        }

    @view
    def is_token_blacklisted(self, token_uid: TokenUid) -> bool:
        """Check if token is blacklisted.

        Args:
            token_uid: Token UID to check

        Returns:
            True if blacklisted, False otherwise
        """
        return self.blacklisted_tokens.get(token_uid, False)

    @view
    def get_contract_info(self) -> dict[str, str]:
        """Get contract configuration information.

        Returns:
            Dictionary with contract information
        """
        return {
            "owner": self.owner.hex(),
            "dozer_pool_manager_id": self.dozer_pool_manager_id.hex(),
            "dzr_token_uid": self.dzr_token_uid.hex(),
            "minimum_deposit": str(self.minimum_deposit),
            "total_projects": str(self.total_projects_count),
        }

    @view
    def get_project_vesting_overview(self, token_uid: TokenUid) -> dict[str, str]:
        """Get comprehensive vesting information with special allocation rules.

        Args:
            token_uid: Project token UID

        Returns:
            Dictionary with combined vesting information
        """
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        if not self.project_vesting_configured.get(token_uid, False):
            return {
                "vesting_configured": "false",
                "vesting_contract": self.project_vesting_contract.get(
                    token_uid, NULL_CONTRACT_ID
                ).hex(),
                "message": "Vesting not configured yet",
            }

        vesting_contract = self.project_vesting_contract[token_uid]
        current_timestamp = 0  # In real usage, this would be ctx.timestamp

        # Get base vesting information for special allocations
        overview = {
            "vesting_configured": "true",
            "vesting_contract": vesting_contract.hex(),
        }

        # Special allocation information with custom rules
        staking_percentage = self.project_staking_percentage.get(token_uid, 0)
        if staking_percentage > 0:
            staking_contract = self.project_staking_contract.get(
                token_uid, NULL_CONTRACT_ID
            )
            if staking_contract != NULL_CONTRACT_ID:
                # Staking tokens are distributed via emissions, show as active
                overview["staking_status"] = "active"
                overview["staking_percentage"] = str(staking_percentage)
                overview["staking_contract"] = staking_contract.hex()
            else:
                # Staking allocation exists but contract not created
                overview["staking_status"] = "allocated_not_deployed"
                overview["staking_percentage"] = str(staking_percentage)

        public_sale_percentage = self.project_public_sale_percentage.get(token_uid, 0)
        if public_sale_percentage > 0:
            crowdsale_contract = self.project_crowdsale_contract.get(
                token_uid, NULL_CONTRACT_ID
            )
            if crowdsale_contract != NULL_CONTRACT_ID:
                # Get crowdsale status to determine unlock status
                # In reality, we'd call crowdsale contract view methods
                overview["public_sale_status"] = "deployed"
                overview["public_sale_percentage"] = str(public_sale_percentage)
                overview["crowdsale_contract"] = crowdsale_contract.hex()
            else:
                overview["public_sale_status"] = "allocated_not_deployed"
                overview["public_sale_percentage"] = str(public_sale_percentage)

        dozer_pool_percentage = self.project_dozer_pool_percentage.get(token_uid, 0)
        if dozer_pool_percentage > 0:
            pool_key = self.project_pools.get(token_uid, "")
            if pool_key != "":
                # Pool created, tokens are 100% liquid
                overview["dozer_pool_status"] = "deployed"
                overview["dozer_pool_percentage"] = str(dozer_pool_percentage)
                overview["pool_key"] = pool_key
            else:
                overview["dozer_pool_status"] = "allocated_not_deployed"
                overview["dozer_pool_percentage"] = str(dozer_pool_percentage)

        return overview

    @view
    def get_project_token_distribution(self, token_uid: TokenUid) -> dict[str, str]:
        """Show how tokens are distributed across contracts and vesting.

        Args:
            token_uid: Project token UID

        Returns:
            Dictionary with token distribution information
        """
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        total_supply = self.project_total_supply[token_uid]
        distribution = {
            "total_supply": str(total_supply),
            "vesting_contract": self.project_vesting_contract.get(
                token_uid, NULL_CONTRACT_ID
            ).hex(),
        }

        if not self.project_vesting_configured.get(token_uid, False):
            distribution["status"] = "all_tokens_in_vesting_unconfigured"
            return distribution

        # Calculate token distribution
        staking_percentage = self.project_staking_percentage.get(token_uid, 0)
        public_sale_percentage = self.project_public_sale_percentage.get(token_uid, 0)
        dozer_pool_percentage = self.project_dozer_pool_percentage.get(token_uid, 0)

        regular_percentage = (
            100 - staking_percentage - public_sale_percentage - dozer_pool_percentage
        )

        distribution["staking_allocation_percentage"] = str(staking_percentage)
        distribution["public_sale_allocation_percentage"] = str(public_sale_percentage)
        distribution["dozer_pool_allocation_percentage"] = str(dozer_pool_percentage)
        distribution["regular_vesting_percentage"] = str(regular_percentage)

        # Contract deployment status
        staking_contract = self.project_staking_contract.get(
            token_uid, NULL_CONTRACT_ID
        )
        crowdsale_contract = self.project_crowdsale_contract.get(
            token_uid, NULL_CONTRACT_ID
        )
        pool_key = self.project_pools.get(token_uid, "")

        distribution["staking_deployed"] = (
            "true" if staking_contract != NULL_CONTRACT_ID else "false"
        )
        distribution["crowdsale_deployed"] = (
            "true" if crowdsale_contract != NULL_CONTRACT_ID else "false"
        )
        distribution["pool_deployed"] = "true" if pool_key != "" else "false"

        if staking_contract != NULL_CONTRACT_ID:
            distribution["staking_contract"] = staking_contract.hex()
        if crowdsale_contract != NULL_CONTRACT_ID:
            distribution["crowdsale_contract"] = crowdsale_contract.hex()
        if pool_key != "":
            distribution["pool_key"] = pool_key

        return distribution

    @view
    def get_vesting_allocation_info(
        self, token_uid: TokenUid, allocation_index: int
    ) -> dict[str, str]:
        """Get information about a specific vesting allocation.

        Args:
            token_uid: Project token UID
            allocation_index: Allocation index (0-9)

        Returns:
            Dictionary with allocation information
        """
        if not self.project_exists.get(token_uid, False):
            raise ProjectNotFound("Project does not exist")

        if not self.project_vesting_configured.get(token_uid, False):
            raise VestingNotConfigured("Vesting not configured")

        vesting_contract = self.project_vesting_contract[token_uid]
        current_timestamp = 0  # In real usage, this would be ctx.timestamp

        vesting_info = self.syscall.call_view_method(
            vesting_contract,
            "get_vesting_info",
            allocation_index,
            current_timestamp,
        )

        # Convert to string dict for consistency
        return {
            "name": str(vesting_info.get("name", "")),
            "beneficiary": str(vesting_info.get("beneficiary", "")),
            "amount": str(vesting_info.get("amount", 0)),
            "cliff_months": str(vesting_info.get("cliff_months", 0)),
            "vesting_months": str(vesting_info.get("vesting_months", 0)),
            "withdrawn": str(vesting_info.get("withdrawn", 0)),
            "vested": str(vesting_info.get("vested", 0)),
            "claimable": str(vesting_info.get("claimable", 0)),
        }

    @public
    def configure_project_vesting(
        self,
        ctx: Context,
        token_uid: TokenUid,
        # Special allocation percentages (0-100, 0 means not used)
        staking_percentage: int,
        public_sale_percentage: int,
        dozer_pool_percentage: int,
        # Staking configuration (required if staking_percentage > 0)
        earnings_per_day: int,
        # Regular vesting schedules (comma-separated strings)
        allocation_names: str,
        allocation_percentages: str,
        allocation_beneficiaries: str,
        allocation_cliff_months: str,
        allocation_vesting_months: str,
    ) -> None:
        """Configure project vesting with special allocations and regular schedules.

        Args:
            ctx: Transaction context
            token_uid: Project token UID
            staking_percentage: Percentage for staking (0-100, 0 = not used)
            public_sale_percentage: Percentage for public sale (0-100, 0 = not used)
            dozer_pool_percentage: Percentage for dozer pool (0-100, 0 = not used)
            earnings_per_day: Daily earnings for staking (required if staking_percentage > 0)
            allocation_names: Comma-separated names for regular allocations
            allocation_percentages: Comma-separated percentages for regular allocations
            allocation_beneficiaries: Comma-separated beneficiary addresses for regular allocations
            allocation_cliff_months: Comma-separated cliff periods in months for regular allocations
            allocation_vesting_months: Comma-separated vesting durations in months for regular allocations
        """
        self._only_project_dev(ctx, token_uid)
        self._charge_fee(ctx, token_uid, "configure_project_vesting")

        if self.project_vesting_configured.get(token_uid, False):
            raise InvalidAllocation("Vesting already configured")

        # Parse comma-separated strings into lists
        parsed_allocation_names = allocation_names.split(",") if allocation_names else []
        parsed_allocation_percentages = [int(p) for p in allocation_percentages.split(",")] if allocation_percentages else []
        parsed_allocation_beneficiaries = [Address(decode_address(addr)) for addr in allocation_beneficiaries.split(",")] if allocation_beneficiaries else []
        parsed_allocation_cliff_months = [int(m) for m in allocation_cliff_months.split(",")] if allocation_cliff_months else []
        parsed_allocation_vesting_months = [int(m) for m in allocation_vesting_months.split(",")] if allocation_vesting_months else []

        # Validate percentage totals
        total_percentage = (
            staking_percentage + public_sale_percentage + dozer_pool_percentage
        )
        for percentage in parsed_allocation_percentages:
            total_percentage += percentage

        if total_percentage > 100:
            raise InvalidAllocation("Total allocation exceeds 100%")

        # Validate regular allocation lists have same length
        if not (
            len(parsed_allocation_names)
            == len(parsed_allocation_percentages)
            == len(parsed_allocation_beneficiaries)
            == len(parsed_allocation_cliff_months)
            == len(parsed_allocation_vesting_months)
        ):
            raise InvalidAllocation("All allocation lists must have same length")

        # Configure vesting and start it
        self._configure_vesting(
            ctx,
            token_uid,
            staking_percentage,
            public_sale_percentage,
            dozer_pool_percentage,
            parsed_allocation_names,
            parsed_allocation_percentages,
            parsed_allocation_beneficiaries,
            parsed_allocation_cliff_months,
            parsed_allocation_vesting_months,
        )
        self._start_vesting(ctx, token_uid)

        # Mark as configured
        self.project_vesting_configured[token_uid] = True

        # Auto-create staking contract if staking allocation exists
        if staking_percentage > 0:
            self._create_staking(ctx, token_uid, earnings_per_day)

    def _configure_vesting(
        self,
        ctx: Context,
        token_uid: TokenUid,
        staking_percentage: int,
        public_sale_percentage: int,
        dozer_pool_percentage: int,
        allocation_names: list[str],
        allocation_percentages: list[int],
        allocation_beneficiaries: list[Address],
        allocation_cliff_months: list[int],
        allocation_vesting_months: list[int],
    ) -> None:
        """Configure all vesting allocations (special + regular)."""
        vesting_contract = self.project_vesting_contract[token_uid]
        total_supply = self.project_total_supply[token_uid]

        # Store special allocation percentages
        self.project_staking_percentage[token_uid] = staking_percentage
        self.project_public_sale_percentage[token_uid] = public_sale_percentage
        self.project_dozer_pool_percentage[token_uid] = dozer_pool_percentage

        # Configure special allocations (unlocked: cliff=0, vesting=0)
        if staking_percentage > 0:
            staking_amount = Amount((total_supply * staking_percentage) // 100)
            self.syscall.call_public_method(
                vesting_contract,
                "configure_vesting",
                [],
                STAKING_ALLOCATION_INDEX,
                staking_amount,
                Address(
                    ctx.caller_id
                ),  # DozerTools is beneficiary for special allocations
                0,  # cliff_months = 0 (unlocked)
                0,  # vesting_months = 0 (immediately available)
                "Staking",
            )

        if public_sale_percentage > 0:
            public_sale_amount = Amount((total_supply * public_sale_percentage) // 100)
            self.syscall.call_public_method(
                vesting_contract,
                "configure_vesting",
                [],
                PUBLIC_SALE_ALLOCATION_INDEX,
                public_sale_amount,
                Address(
                    ctx.caller_id
                ),  # DozerTools is beneficiary for special allocations
                0,  # cliff_months = 0 (unlocked)
                0,  # vesting_months = 0 (immediately available)
                "Public Sale",
            )

        if dozer_pool_percentage > 0:
            dozer_pool_amount = Amount((total_supply * dozer_pool_percentage) // 100)
            self.syscall.call_public_method(
                vesting_contract,
                "configure_vesting",
                [],
                DOZER_POOL_ALLOCATION_INDEX,
                dozer_pool_amount,
                Address(
                    ctx.caller_id
                ),  # DozerTools is beneficiary for special allocations
                0,  # cliff_months = 0 (unlocked)
                0,  # vesting_months = 0 (immediately available)
                "Dozer Pool",
            )

        # Configure regular allocations (starting from index 3)
        for i in range(len(allocation_names)):
            if allocation_percentages[i] > 0:
                allocation_amount = Amount(
                    (total_supply * allocation_percentages[i]) // 100
                )
                allocation_index = 3 + i  # Start from index 3

                self.syscall.call_public_method(
                    vesting_contract,
                    "configure_vesting",
                    [],
                    allocation_index,
                    allocation_amount,
                    allocation_beneficiaries[i],
                    allocation_cliff_months[i],
                    allocation_vesting_months[i],
                    allocation_names[i],
                )

    def _start_vesting(self, ctx: Context, token_uid: TokenUid) -> None:
        """Start the vesting schedule."""
        vesting_contract = self.project_vesting_contract[token_uid]
        self.syscall.call_public_method(vesting_contract, "start_vesting", [])

__blueprint__ = DozerTools