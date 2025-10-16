import os
from hathor.conf.get_settings import HathorSettings
from hathor.crypto.util import decode_address
from hathor.nanocontracts.blueprints.stake import Stake
from hathor.nanocontracts.blueprints.dao import DAO
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import (
    NCDepositAction,
    NCWithdrawalAction,
    Address,
    Amount,
    TokenUid,
    Timestamp,
)
from hathor.wallet.keypair import KeyPair
from hathor.util import not_none
from tests.nanocontracts.blueprints.unittest import BlueprintTestCase

settings = HathorSettings()
HTR_UID = settings.HATHOR_TOKEN_UID


class DAOTestCase(BlueprintTestCase):
    def setUp(self):
        super().setUp()

        # Set up DAO contract
        self.dao_contract_id = self.gen_random_contract_id()
        self.dao_blueprint_id = self.gen_random_blueprint_id()
        self.register_blueprint_class(self.dao_blueprint_id, DAO)

        # Set up Stake contract
        self.stake_contract_id = self.gen_random_contract_id()
        self.stake_blueprint_id = self.gen_random_blueprint_id()
        self.register_blueprint_class(self.stake_blueprint_id, Stake)

        # Generate test tokens and addresses
        self.token_uid = self.gen_random_token_uid()
        self.admin_address, self.admin_key = self._get_any_address()

        # Test parameters
        self.voting_period_days = 7
        self.quorum_percentage = 30
        self.proposal_threshold = 1000_00
        self.earnings_per_day = 100_00

        # Initialize base tx
        self.tx = self.get_genesis_tx()

    def _get_any_address(self) -> tuple[Address, KeyPair]:
        password = os.urandom(12)
        key = KeyPair.create(password)
        address_b58 = key.address
        address_bytes = decode_address(not_none(address_b58))
        return Address(address_bytes), key

    def initialize_contracts(self) -> None:
        """Initialize both stake and DAO contracts"""
        # Initialize stake contract
        stake_ctx = self.create_context(
            actions=[NCDepositAction(token_uid=self.token_uid, amount=10000_00)],
            address=self.admin_address,
            timestamp=self.now,
        )
        self.runner.create_contract(
            self.stake_contract_id,
            self.stake_blueprint_id,
            stake_ctx,
            self.earnings_per_day,
            self.token_uid,
        )

        # Initialize DAO contract
        dao_ctx = self.create_context(address=self.admin_address, timestamp=self.now)
        self.runner.create_contract(
            self.dao_contract_id,
            self.dao_blueprint_id,
            dao_ctx,
            "Test DAO",
            "A test DAO",
            self.token_uid,
            self.stake_contract_id,
            self.voting_period_days,
            self.quorum_percentage,
            self.proposal_threshold,
        )

    def test_initialize(self) -> None:
        """Test basic initialization"""
        self.initialize_contracts()

        # Verify DAO state using contract instance
        dao_contract = self.get_readonly_contract(self.dao_contract_id)
        assert isinstance(dao_contract, DAO)
        self.assertEqual(dao_contract.name, "Test DAO")
        self.assertEqual(dao_contract.governance_token, self.token_uid)
        self.assertEqual(dao_contract.staking_contract, self.stake_contract_id)
        self.assertEqual(dao_contract.proposal_count, 0)

    def test_create_proposal(self) -> None:
        """Test proposal creation with staking requirement"""
        self.initialize_contracts()

        # Stake tokens first
        user_addr = self._get_any_address()[0]
        stake_amount = self.proposal_threshold
        stake_ctx = self.create_context(
            actions=[NCDepositAction(token_uid=self.token_uid, amount=stake_amount)],
            address=user_addr,
            timestamp=self.now,
        )
        self.runner.call_public_method(self.stake_contract_id, "stake", stake_ctx)

        # Create proposal
        proposal_ctx = self.create_context(address=user_addr, timestamp=self.now)
        proposal_id = self.runner.call_public_method(
            self.dao_contract_id,
            "create_proposal",
            proposal_ctx,
            "Test Proposal",
            "Description",
        )

        # Verify proposal
        dao_contract = self.get_readonly_contract(self.dao_contract_id)
        assert isinstance(dao_contract, DAO)
        self.assertEqual(dao_contract.proposal_count, 1)
        proposal = self.runner.call_view_method(
            self.dao_contract_id, "get_proposal", proposal_id
        )
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.title, "Test Proposal")
        self.assertEqual(proposal.creator, user_addr)

    def test_voting(self) -> None:
        """Test voting mechanics"""
        self.initialize_contracts()

        # Setup multiple stakers
        stakers = []
        stake_amount = 1000_00

        for _ in range(3):
            addr = self._get_any_address()[0]
            stakers.append(addr)
            ctx = self.create_context(
                actions=[
                    NCDepositAction(token_uid=self.token_uid, amount=stake_amount)
                ],
                address=addr,
                timestamp=self.now,
            )
            self.runner.call_public_method(self.stake_contract_id, "stake", ctx)

        # Create proposal
        proposal_ctx = self.create_context(address=stakers[0], timestamp=self.now)
        proposal_id = self.runner.call_public_method(
            self.dao_contract_id,
            "create_proposal",
            proposal_ctx,
            "Test Proposal",
            "Description",
        )

        # Cast votes
        for i, staker in enumerate(stakers):
            vote_ctx = self.create_context(address=staker, timestamp=self.now)
            self.runner.call_public_method(
                self.dao_contract_id,
                "cast_vote",
                vote_ctx,
                proposal_id,
                i < 2,  # First two vote yes, last one no
            )

        # Verify vote counts
        proposal = self.runner.call_view_method(
            self.dao_contract_id, "get_proposal", proposal_id
        )
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.for_votes, stake_amount * 2)
        self.assertEqual(proposal.against_votes, stake_amount)
        self.assertEqual(proposal.total_voters, 3)

    def test_quorum_calculation(self) -> None:
        """Test quorum calculation"""
        self.initialize_contracts()

        # Setup stakers to meet quorum
        total_stake = 10000_00
        num_stakers = 5
        stake_per_user = total_stake // num_stakers

        stakers = []
        for _ in range(num_stakers):
            addr = self._get_any_address()[0]
            stakers.append(addr)
            ctx = self.create_context(
                actions=[
                    NCDepositAction(token_uid=self.token_uid, amount=stake_per_user)
                ],
                address=addr,
                timestamp=self.now,
            )
            self.runner.call_public_method(self.stake_contract_id, "stake", ctx)

        # Create and vote on proposal
        proposal_id = self.runner.call_public_method(
            self.dao_contract_id,
            "create_proposal",
            self.create_context(address=stakers[0], timestamp=self.now),
            "Quorum Test",
            "Testing quorum calculation",
        )

        # Have enough voters to reach quorum
        quorum_voters = int((num_stakers * self.quorum_percentage) / 100) + 1

        for i in range(quorum_voters):
            self.runner.call_public_method(
                self.dao_contract_id,
                "cast_vote",
                self.create_context(address=stakers[i], timestamp=self.now),
                proposal_id,
                True,
            )

        proposal = self.runner.call_view_method(
            self.dao_contract_id, "get_proposal", proposal_id
        )
        self.assertIsNotNone(proposal)
        self.assertTrue(proposal.quorum_reached)
