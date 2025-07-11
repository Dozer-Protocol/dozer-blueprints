#  Copyright 2023 Hathor Labs
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

from __future__ import annotations

from typing import Any, Optional, TypeAlias, Union, cast

from pydantic import Extra, validator
from typing_extensions import Self

from hathor.pubsub import EventArguments
from hathor.utils.pydantic import BaseModel


class DecodedTxOutput(BaseModel, extra=Extra.ignore):
    type: str
    address: str
    timelock: Optional[int]


class TxOutput(BaseModel, extra=Extra.ignore):
    value: int
    token_data: int
    script: str
    # Instead of None, an empty dict represents an unknown script, as requested by our wallet-service use case.
    decoded: DecodedTxOutput | dict[Any, Any]


class TxInput(BaseModel):
    tx_id: str
    index: int
    spent_output: TxOutput


class SpentOutput(BaseModel):
    index: int
    tx_ids: list[str]


class TxMetadata(BaseModel, extra=Extra.ignore):
    hash: str
    spent_outputs: list[SpentOutput]
    conflict_with: list[str]
    voided_by: list[str]
    received_by: list[int]
    children: list[str]
    twins: list[str]
    accumulated_weight: float
    score: float
    accumulated_weight_raw: str
    score_raw: str
    first_block: Optional[str]
    height: int
    validation: str
    nc_execution: str | None

    @validator('spent_outputs', pre=True, each_item=True)
    def _parse_spent_outputs(cls, spent_output: Union[SpentOutput, list[Union[int, list[str]]]]) -> SpentOutput:
        """
        This validator method is called by pydantic when parsing models, and is not supposed to be called directly.
        It either returns a SpentOutput if it receives one, or tries to parse it as a list (as returned from
        metadata.to_json() method). Examples:

        >>> TxMetadata._parse_spent_outputs(SpentOutput(index=0, tx_ids=['tx1', 'tx2']))
        SpentOutput(index=0, tx_ids=['tx1', 'tx2'])
        >>> TxMetadata._parse_spent_outputs([0, ['tx1', 'tx2']])
        SpentOutput(index=0, tx_ids=['tx1', 'tx2'])
        """
        if isinstance(spent_output, list):
            index, tx_ids = spent_output

            return SpentOutput(
                index=cast(int, index),
                tx_ids=cast(list[str], tx_ids)
            )

        return spent_output


class BaseEventData(BaseModel):
    """Base class for event data polymorphism."""
    @classmethod
    def from_event_arguments(cls, args: EventArguments) -> 'EventData':
        """Returns an instance of this class by processing PubSub's EventArguments."""
        raise NotImplementedError()


class EmptyData(BaseEventData):
    """Class that represents empty data on an event."""
    @classmethod
    def from_event_arguments(cls, args: EventArguments) -> 'EmptyData':
        return cls()


class TxDataWithoutMeta(BaseEventData, extra=Extra.ignore):
    """Class that represents transaction data on an event."""
    hash: str
    nonce: Optional[int] = None
    timestamp: int
    signal_bits: int | None
    version: int
    weight: float
    inputs: list['TxInput']
    outputs: list['TxOutput']
    parents: list[str]
    tokens: list[str]
    # TODO: Token name and symbol could be in a different class because they're only used by TokenCreationTransaction
    token_name: Optional[str]
    token_symbol: Optional[str]
    aux_pow: Optional[str] = None

    @classmethod
    def from_event_arguments(cls, args: EventArguments) -> Self:
        from hathor.transaction.resources.transaction import get_tx_extra_data
        tx_extra_data_json = get_tx_extra_data(args.tx, detail_tokens=False, force_reload_metadata=False)
        tx_json = tx_extra_data_json['tx']
        meta_json = tx_extra_data_json['meta']
        tx_json['metadata'] = meta_json

        inputs = []
        for tx_input in tx_json['inputs']:
            inputs.append(
                dict(
                    tx_id=tx_input['tx_id'],
                    index=tx_input['index'],
                    spent_output=tx_input,
                )
            )

        tx_json['inputs'] = inputs
        return cls(**tx_json)


class TxData(TxDataWithoutMeta):
    metadata: 'TxMetadata'


class ReorgData(BaseEventData):
    """Class that represents reorg data on an event."""
    reorg_size: int
    previous_best_block: str
    new_best_block: str
    common_block: str

    @classmethod
    def from_event_arguments(cls, args: EventArguments) -> 'ReorgData':
        return cls(
            reorg_size=args.reorg_size,
            previous_best_block=args.old_best_block.hash_hex,
            new_best_block=args.new_best_block.hash_hex,
            common_block=args.common_block.hash_hex,
        )


class NCEventData(BaseEventData):
    """Class that represents data for a custom nano contract event."""

    # The ID of the transaction that executed a nano contract.
    vertex_id: str

    # The ID of the nano contract that was executed.
    nc_id: str

    # The nano contract execution state.
    nc_execution: str

    # The block that confirmed this transaction, executing the nano contract.
    first_block: str

    # Custom data provided by the blueprint.
    data_hex: str

    @classmethod
    def from_event_arguments(cls, args: EventArguments) -> NCEventData:
        meta = args.tx.get_metadata()
        assert meta.nc_execution is not None
        assert meta.first_block is not None

        return cls(
            vertex_id=args.tx.hash_hex,
            nc_id=args.nc_event.nc_id.hex(),
            nc_execution=meta.nc_execution,
            first_block=meta.first_block.hex(),
            data_hex=args.nc_event.data.hex(),
        )


# Union type to encompass BaseEventData polymorphism
EventData: TypeAlias = EmptyData | TxData | TxDataWithoutMeta | ReorgData | NCEventData
