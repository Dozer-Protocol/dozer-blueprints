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

from typing import Any

from hathor.conf import settings
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
)

# Blueprint IDs from nano_testnet.yml
VESTING_BLUEPRINT_ID = BlueprintId(VertexId(bytes.fromhex("42e7f272b6b966f26576a5c1d0c9637f456168c85e18a3e86c0c60e909a93275")))
STAKING_BLUEPRINT_ID = BlueprintId(VertexId(bytes.fromhex("ac6bf4f6a89a34e81a21a6e07e24f07739af5c3d6f4c15e16c5ae4e4108aaa48")))
DAO_BLUEPRINT_ID = BlueprintId(VertexId(bytes.fromhex("6cfdd13e8b9c689b8d87bb8100b4e580e0e9d20ee75a8c5aee9e7bef51e0b1a0")))
CROWDSALE_BLUEPRINT_ID = BlueprintId(VertexId(bytes.fromhex("7b3ae18c763b2254baf8b9801bc0dcd3e77db57d7de7fd34cc62b526aa91d9fb")))

# HTR token UID
HTR_UID = settings.HATHOR_TOKEN_UID

# Null contract ID for initialization
NULL_CONTRACT_ID = ContractId(VertexId(b'\x00' * 32))


class TokenManagerError(NCFail):
    """Base error for TokenManager operations."""
    pass


class ContractAlreadyExists(TokenManagerError):
    """Raised when trying to create a contract that already exists."""
    pass


class ContractNotFound(TokenManagerError):
    """Raised when trying to access a contract that doesn't exist."""
    pass


class Unauthorized(TokenManagerError):
    """Raised when unauthorized address tries to perform an action."""
    pass


class InvalidParameters(TokenManagerError):
    """Raised when invalid parameters are provided."""
    pass


class TokenManager(Blueprint):
    """TokenManager blueprint for centralized token and contract management.
    
    This blueprint creates and manages a single token and all its related contracts:
    - Vesting contract for token distribution schedules
    - Staking contract for token staking rewards
    - DAO contract for governance
    - Crowdsale contract for token sales
    - Liquidity pool in DozerPoolManager for trading
    
    Each TokenManager instance manages exactly one token and at most one of each contract type.
    """

    # Token and ownership
    main_token: TokenUid
    owner: Address
    
    # DozerPoolManager reference (for creating liquidity pools)
    dozer_pool_manager_id: ContractId
    
    # Contract references (one each, initialized to NULL_CONTRACT_ID)
    vesting_contract: ContractId
    staking_contract: ContractId  
    dao_contract: ContractId
    crowdsale_contract: ContractId
    
    # Liquidity pool reference (pool key from DozerPoolManager)
    liquidity_pool_key: str

    def _only_owner(self, ctx: Context) -> None:
        """Ensure only the owner can call this method."""
        if Address(ctx.address) != self.owner:
            raise Unauthorized("Only owner can call this method")

    def _generate_salt(self, ctx: Context, contract_type: str) -> bytes:
        """Generate a unique salt for contract creation."""
        return self.main_token + bytes(contract_type, 'utf-8') + bytes(str(ctx.timestamp), 'utf-8')

    @public(allow_deposit=True)
    def initialize(
        self,
        ctx: Context,
        token_name: str,
        token_symbol: str,
        initial_supply: int,
        mint_authority: bool,
        melt_authority: bool,
        dozer_pool_manager_id: ContractId,
    ) -> None:
        """Initialize the TokenManager and create the main token.
        
        Args:
            ctx: Transaction context
            token_name: Human-readable name of the token
            token_symbol: Symbol/ticker of the token
            initial_supply: Initial amount to mint
            mint_authority: Whether contract receives mint authority
            melt_authority: Whether contract receives melt authority
            dozer_pool_manager_id: ContractId of the DozerPoolManager
        """
        # Create the main token
        self.main_token = self.syscall.create_token(
            token_name,
            token_symbol,
            initial_supply,
            mint_authority,
            melt_authority
        )
        
        # Set ownership and DozerPoolManager reference
        self.owner = Address(ctx.address)
        self.dozer_pool_manager_id = dozer_pool_manager_id
        
        # Initialize contract references to null
        self.vesting_contract = NULL_CONTRACT_ID
        self.staking_contract = NULL_CONTRACT_ID
        self.dao_contract = NULL_CONTRACT_ID
        self.crowdsale_contract = NULL_CONTRACT_ID
        self.liquidity_pool_key = ""

    @public(allow_deposit=True)
    def create_vesting_contract(
        self,
        ctx: Context,
        token_amount: Amount,
        allocations: list[dict[str, Any]],
    ) -> ContractId:
        """Create and configure a vesting contract.
        
        Args:
            ctx: Transaction context
            token_amount: Amount of tokens to transfer to vesting contract
            allocations: List of allocation configurations, each containing:
                - name: str - Allocation name
                - amount: int - Token amount for this allocation
                - beneficiary: str - Beneficiary address
                - cliff_months: int - Cliff period in months
                - vesting_months: int - Vesting duration in months
        
        Returns:
            ContractId of the created vesting contract
        """
        self._only_owner(ctx)
        
        if self.vesting_contract != NULL_CONTRACT_ID:
            raise ContractAlreadyExists("Vesting contract already exists")
        
        if len(allocations) == 0 or len(allocations) > 10:
            raise InvalidParameters("Must have 1-10 allocations")
        
        # Generate salt and create vesting contract
        salt = self._generate_salt(ctx, "vesting")
        actions: list[NCAction] = [NCDepositAction(token_uid=self.main_token, amount=token_amount)]
        
        vesting_id, _ = self.syscall.create_contract(
            VESTING_BLUEPRINT_ID,
            salt,
            actions,
            self.main_token
        )
        
        # Configure each allocation
        for i, allocation in enumerate(allocations):
            self.syscall.call_public_method(
                vesting_id,
                "configure_vesting",
                [],
                i,
                Amount(allocation["amount"]),
                Address(allocation["beneficiary"]),
                allocation["cliff_months"],
                allocation["vesting_months"],
                allocation["name"]
            )
        
        # Start vesting
        self.syscall.call_public_method(vesting_id, "start_vesting", [])
        
        self.vesting_contract = vesting_id
        return vesting_id

    @public(allow_deposit=True)
    def create_staking_contract(
        self,
        ctx: Context,
        token_amount: Amount,
        earnings_per_day: int,
    ) -> ContractId:
        """Create a staking contract.
        
        Args:
            ctx: Transaction context
            token_amount: Amount of tokens to transfer to staking contract
            earnings_per_day: Daily earnings rate for staking rewards
        
        Returns:
            ContractId of the created staking contract
        """
        self._only_owner(ctx)
        
        if self.staking_contract != NULL_CONTRACT_ID:
            raise ContractAlreadyExists("Staking contract already exists")
        
        # Generate salt and create staking contract
        salt = self._generate_salt(ctx, "staking")
        actions: list[NCAction] = [NCDepositAction(token_uid=self.main_token, amount=token_amount)]
        
        staking_id, _ = self.syscall.create_contract(
            STAKING_BLUEPRINT_ID,
            salt,
            actions,
            earnings_per_day,
            self.main_token
        )
        
        self.staking_contract = staking_id
        return staking_id

    @public
    def create_dao_contract(
        self,
        ctx: Context,
        name: str,
        description: str,
        voting_period_days: int,
        quorum_percentage: int,
        proposal_threshold: Amount,
    ) -> ContractId:
        """Create a DAO contract.
        
        Args:
            ctx: Transaction context
            name: DAO name
            description: DAO description
            voting_period_days: Voting period in days
            quorum_percentage: Minimum quorum percentage
            proposal_threshold: Minimum tokens needed to create proposals
        
        Returns:
            ContractId of the created DAO contract
        """
        self._only_owner(ctx)
        
        if self.dao_contract != NULL_CONTRACT_ID:
            raise ContractAlreadyExists("DAO contract already exists")
        
        if self.staking_contract == NULL_CONTRACT_ID:
            raise ContractNotFound("Staking contract must be created first")
        
        # Generate salt and create DAO contract
        salt = self._generate_salt(ctx, "dao")
        
        dao_id, _ = self.syscall.create_contract(
            DAO_BLUEPRINT_ID,
            salt,
            [],
            name,
            description,
            self.main_token,
            self.staking_contract,
            voting_period_days,
            quorum_percentage,
            proposal_threshold
        )
        
        self.dao_contract = dao_id
        return dao_id

    @public(allow_deposit=True)
    def create_crowdsale_contract(
        self,
        ctx: Context,
        token_amount: Amount,
        rate: Amount,
        soft_cap: Amount,
        hard_cap: Amount,
        min_deposit: Amount,
        start_time: Timestamp,
        end_time: Timestamp,
        platform_fee: Amount,
    ) -> ContractId:
        """Create a crowdsale contract.
        
        Args:
            ctx: Transaction context
            token_amount: Amount of tokens to transfer to crowdsale contract
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
        self._only_owner(ctx)
        
        if self.crowdsale_contract != NULL_CONTRACT_ID:
            raise ContractAlreadyExists("Crowdsale contract already exists")
        
        # Generate salt and create crowdsale contract
        salt = self._generate_salt(ctx, "crowdsale")
        actions: list[NCAction] = [NCDepositAction(token_uid=self.main_token, amount=token_amount)]
        
        crowdsale_id, _ = self.syscall.create_contract(
            CROWDSALE_BLUEPRINT_ID,
            salt,
            actions,
            self.main_token,
            rate,
            soft_cap,
            hard_cap,
            min_deposit,
            start_time,
            end_time,
            platform_fee
        )
        
        self.crowdsale_contract = crowdsale_id
        return crowdsale_id

    @public(allow_deposit=True)
    def create_liquidity_pool(
        self,
        ctx: Context,
        token_amount: Amount,
        htr_amount: Amount,
        fee: Amount,
    ) -> str:
        """Create a liquidity pool in DozerPoolManager.
        
        Args:
            ctx: Transaction context
            token_amount: Amount of main tokens to add to pool
            htr_amount: Amount of HTR to add to pool
            fee: Pool fee (e.g., 3 for 0.3%)
        
        Returns:
            Pool key from DozerPoolManager
        """
        self._only_owner(ctx)
        
        if self.liquidity_pool_key != "":
            raise ContractAlreadyExists("Liquidity pool already exists")
        
        # Prepare actions for pool creation
        actions: list[NCAction] = [
            NCDepositAction(token_uid=self.main_token, amount=token_amount),
        ]
        actions.append(NCDepositAction(token_uid=TokenUid(HTR_UID), amount=htr_amount))
        
        # Call DozerPoolManager to create pool
        pool_key = self.syscall.call_public_method(
            self.dozer_pool_manager_id,
            "create_pool",
            actions,
            fee
        )
        
        self.liquidity_pool_key = pool_key
        return pool_key

    @public
    def change_owner(self, ctx: Context, new_owner: Address) -> None:
        """Change the owner of the TokenManager.
        
        Args:
            ctx: Transaction context
            new_owner: New owner address
        """
        self._only_owner(ctx)
        self.owner = new_owner

    @view
    def get_main_token(self) -> TokenUid:
        """Get the main token UID.
        
        Returns:
            TokenUid of the main token
        """
        return self.main_token

    @view
    def get_owner(self) -> Address:
        """Get the owner address.
        
        Returns:
            Address of the owner
        """
        return self.owner

    @view
    def get_all_contracts(self) -> dict[str, Any]:
        """Get all created contracts and their IDs.
        
        Returns:
            Dictionary with contract information
        """
        return {
            "main_token": self.main_token.hex(),
            "owner": self.owner,
            "dozer_pool_manager": self.dozer_pool_manager_id.hex(),
            "vesting_contract": self.vesting_contract.hex() if self.vesting_contract != NULL_CONTRACT_ID else None,
            "staking_contract": self.staking_contract.hex() if self.staking_contract != NULL_CONTRACT_ID else None,
            "dao_contract": self.dao_contract.hex() if self.dao_contract != NULL_CONTRACT_ID else None,
            "crowdsale_contract": self.crowdsale_contract.hex() if self.crowdsale_contract != NULL_CONTRACT_ID else None,
            "liquidity_pool_key": self.liquidity_pool_key if self.liquidity_pool_key != "" else None,
        }

    @view
    def get_contract_status(self) -> dict[str, bool]:
        """Get the creation status of all contract types.
        
        Returns:
            Dictionary with boolean status for each contract type
        """
        return {
            "vesting_created": self.vesting_contract != NULL_CONTRACT_ID,
            "staking_created": self.staking_contract != NULL_CONTRACT_ID,
            "dao_created": self.dao_contract != NULL_CONTRACT_ID,
            "crowdsale_created": self.crowdsale_contract != NULL_CONTRACT_ID,
            "liquidity_pool_created": self.liquidity_pool_key != "",
        }

    @view
    def get_vesting_contract(self) -> ContractId:
        """Get the vesting contract ID.
        
        Returns:
            ContractId of the vesting contract
            
        Raises:
            ContractNotFound: If vesting contract doesn't exist
        """
        if self.vesting_contract == NULL_CONTRACT_ID:
            raise ContractNotFound("Vesting contract not created")
        return self.vesting_contract

    @view
    def get_staking_contract(self) -> ContractId:
        """Get the staking contract ID.
        
        Returns:
            ContractId of the staking contract
            
        Raises:
            ContractNotFound: If staking contract doesn't exist
        """
        if self.staking_contract == NULL_CONTRACT_ID:
            raise ContractNotFound("Staking contract not created")
        return self.staking_contract

    @view
    def get_dao_contract(self) -> ContractId:
        """Get the DAO contract ID.
        
        Returns:
            ContractId of the DAO contract
            
        Raises:
            ContractNotFound: If DAO contract doesn't exist
        """
        if self.dao_contract == NULL_CONTRACT_ID:
            raise ContractNotFound("DAO contract not created")
        return self.dao_contract

    @view
    def get_crowdsale_contract(self) -> ContractId:
        """Get the crowdsale contract ID.
        
        Returns:
            ContractId of the crowdsale contract
            
        Raises:
            ContractNotFound: If crowdsale contract doesn't exist
        """
        if self.crowdsale_contract == NULL_CONTRACT_ID:
            raise ContractNotFound("Crowdsale contract not created")
        return self.crowdsale_contract

    @view
    def get_liquidity_pool_key(self) -> str:
        """Get the liquidity pool key.
        
        Returns:
            Pool key from DozerPoolManager
            
        Raises:
            ContractNotFound: If liquidity pool doesn't exist
        """
        if self.liquidity_pool_key == "":
            raise ContractNotFound("Liquidity pool not created")
        return self.liquidity_pool_key

    @view
    def get_project_summary(self) -> dict[str, Any]:
        """Get a comprehensive summary of the project.
        
        Returns:
            Dictionary with project information and statistics
        """
        return {
            "token_info": {
                "token_uid": self.main_token.hex(),
                "current_balance": self.syscall.get_current_balance(self.main_token),
            },
            "owner": self.owner,
            "contracts": self.get_all_contracts(),
            "status": self.get_contract_status(),
            "dozer_pool_manager": self.dozer_pool_manager_id.hex(),
        } 