from typing import Optional

from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.context import Context
from hathor.nanocontracts.exception import NCFail
from hathor.nanocontracts.types import ContractId, public


class Oasis(Blueprint):
    """Oasis contract that interacts with Dozer Pool contract."""

    dozer_pool: Optional[ContractId]

    @public
    def initialize(self, ctx: Context) -> None:
        """Initialize the contract with no dozer pool set."""
        self.dozer_pool = None

    @public
    def set_dozer_pool(self, ctx: Context, dozer_pool: ContractId) -> None:
        """Set the dozer pool contract address."""
        self.dozer_pool = dozer_pool

    @public
    def check_pool_liquidity(self, ctx: Context, token_uid: bytes, amount: int) -> dict:
        """Check liquidity for adding tokens to the pool.

        Args:
            ctx: The execution context
            token_uid: The token to check liquidity for
            amount: The amount to check

        Returns:
            The liquidity quote from the pool

        Raises:
            NCFail: If dozer pool is not set
        """
        if self.dozer_pool is None:
            raise NCFail("Dozer pool contract not set")

        # Call the private method on the dozer pool contract
        return ctx.call_private_method(
            self.dozer_pool, "front_quote_add_liquidity_in", amount, token_uid
        )

    @public
    def return_ctx(self, ctx: Context) -> Context:
        """Return the execution context."""
        if self.dozer_pool is None:
            raise NCFail("Dozer pool contract not set")
        return ctx.call_public_method(self.dozer_pool, "return_ctx", [])
