from typing import Optional
from hathor.nanocontracts.blueprint import Blueprint
from hathor.nanocontracts.types import (
    Context,
    ContractId,
    NCAction,
    NCActionType,
    public,
)


class OasisBlueprint(Blueprint):
    """Blueprint for the Oasis contract."""

    lp_contract: Optional[ContractId]

    @public
    def initialize(self, ctx: Context) -> None:
        self.lp_contract = None

    @public
    def set_lp_contract(self, ctx: Context, lp_contract: ContractId) -> None:
        self.lp_contract = lp_contract

    @public
    def exec_swap