# Copyright 2021 Hathor Labs
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

"""Batch state query endpoint for efficient historical data retrieval.

This endpoint is designed for owned nodes to enable fast historical backfilling
without rate limits. It processes multiple block heights in a single request.

IMPORTANT: This endpoint should ONLY be enabled on owned/private nodes.
Do NOT enable this on public nodes as it can be resource-intensive.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field

from hathor.api_util import Resource, set_cors
from hathor.cli.openapi_files.register import register_resource
from hathor.crypto.util import decode_address
from hathor.nanocontracts.api_arguments_parser import parse_nc_method_call
from hathor.nanocontracts.exception import NanoContractDoesNotExist
from hathor.nanocontracts.nc_types import make_nc_type_for_field_type
from hathor.nanocontracts.types import ContractId, VertexId
from hathor.utils.api import ErrorResponse, QueryParams, Response
from hathor.wallet.exceptions import InvalidAddress

if TYPE_CHECKING:
    from twisted.web.http import Request

    from hathor.manager import HathorManager
    from hathor.nanocontracts.storage import NCContractStorage
    from hathor.transaction import Block


@register_resource
class NanoContractStateBatchResource(Resource):
    """Implements a batch state query API for efficient historical data retrieval.

    This endpoint allows querying multiple block heights in a single request,
    significantly improving performance for historical data backfilling.

    IMPORTANT: This endpoint has NO rate limiting by design. It should ONLY
    be enabled on owned/private nodes. Do NOT enable on public nodes.

    You must run with option `--status <PORT>`.
    """
    isLeaf = True  # This is a leaf endpoint - no children allowed

    # Maximum blocks per request to prevent memory issues
    MAX_BLOCKS_PER_REQUEST = 100

    def __init__(self, manager: 'HathorManager') -> None:
        super().__init__()
        self.manager = manager

    def render_GET(self, request: 'Request') -> bytes:
        """Handle GET request for batch state queries.

        Query parameters:
            id: contract_id_hex (required)
            block_heights[]: array of block heights (required, up to 100)
            calls[]: array of method calls (optional)
            fields[]: array of field names (optional)
            balances[]: array of token UIDs (optional)

        Response (JSON):
        {
            "success": true,
            "snapshots": [
                {
                    "block_height": 123,
                    "block_hash": "...",
                    "timestamp": 1234567890,
                    "nc_id": "...",
                    "blueprint_id": "...",
                    "blueprint_name": "...",
                    "fields": {...},
                    "balances": {...},
                    "calls": {...}
                },
                // ... more snapshots
            ]
        }
        """
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'GET')

        # Parse query parameters
        params = NCStateBatchParams.from_request(request)
        if isinstance(params, ErrorResponse):
            request.setResponseCode(400)
            return params.json_dumpb()

        # Parse contract ID
        try:
            nc_id_bytes = ContractId(VertexId(bytes.fromhex(params.id)))
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid contract id: {params.id}')
            return error_response.json_dumpb()

        # Validate block_heights
        block_heights = params.block_heights
        if not block_heights:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error='Missing required parameter: block_heights[]')
            return error_response.json_dumpb()

        # Enforce maximum batch size
        if len(block_heights) > self.MAX_BLOCKS_PER_REQUEST:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error=f'Too many blocks requested. Maximum is {self.MAX_BLOCKS_PER_REQUEST}'
            )
            return error_response.json_dumpb()

        # Get optional parameters
        calls = params.calls
        fields = params.fields
        balances = params.balances

        # Check if indexes are enabled (needed for block_height lookup)
        if self.manager.tx_storage.indexes is None:
            request.setResponseCode(503)
            error_response = ErrorResponse(
                success=False,
                error='No indexes enabled in the storage, so we can\'t filter by block height.'
            )
            return error_response.json_dumpb()

        # Process each block height
        snapshots = []
        errors = []

        for block_height in block_heights:
            try:
                # Get block hash at this height
                block_hash = self.manager.tx_storage.indexes.height.get(block_height)
                if block_hash is None:
                    errors.append({
                        'block_height': block_height,
                        'error': f'No block found at height {block_height}'
                    })
                    continue

                # Get the block
                try:
                    block = self.manager.tx_storage.get_block(block_hash)
                except AssertionError:
                    errors.append({
                        'block_height': block_height,
                        'error': f'Invalid block at height {block_height}'
                    })
                    continue

                # Get nano contract state at this block
                try:
                    runner = self.manager.get_nc_runner(block)
                    nc_storage = runner.get_storage(nc_id_bytes)
                except NanoContractDoesNotExist:
                    errors.append({
                        'block_height': block_height,
                        'error': f'Nano contract does not exist at block {block.hash_hex}'
                    })
                    continue

                # Build snapshot (reuse logic from state.py)
                snapshot = self._build_snapshot(
                    nc_storage=nc_storage,
                    runner=runner,
                    block=block,
                    block_height=block_height,
                    nc_id_bytes=nc_id_bytes,
                    calls=calls,
                    fields=fields,
                    balances=balances
                )

                snapshots.append(snapshot)

            except Exception as e:
                errors.append({
                    'block_height': block_height,
                    'error': f'Unexpected error: {str(e)}'
                })

        # Build response
        response = NCStateBatchResponse(
            success=True,
            snapshots=snapshots,
            errors=errors if errors else None,
            total_requested=len(block_heights),
            total_succeeded=len(snapshots),
            total_failed=len(errors)
        )

        return response.json_dumpb()

    def _build_snapshot(
        self,
        nc_storage: 'NCContractStorage',
        runner: Any,
        block: 'Block',
        block_height: int,
        nc_id_bytes: bytes,
        calls: list[str],
        fields: list[str],
        balances: list[str]
    ) -> dict:
        """Build a state snapshot for a single block.

        This reuses the logic from NanoContractStateResource.render_GET()
        """
        blueprint_id = nc_storage.get_blueprint_id()
        blueprint_class = self.manager.tx_storage.get_blueprint_class(blueprint_id)

        # Get balances
        balances_result: dict[str, dict] = {}
        for token_uid_hex in balances:
            if token_uid_hex == '__all__':
                # Get all balances
                all_balances = nc_storage.get_all_balances()
                for key_balance, balance in all_balances.items():
                    balances_result[key_balance.token_uid.hex()] = {
                        'value': str(balance.value),
                        'can_mint': balance.can_mint,
                        'can_melt': balance.can_melt,
                    }
                break

            try:
                token_uid = bytes.fromhex(token_uid_hex)
            except ValueError:
                balances_result[token_uid_hex] = {'errmsg': 'invalid token id'}
                continue

            balance = nc_storage.get_balance(token_uid)
            balances_result[token_uid_hex] = {
                'value': str(balance.value),
                'can_mint': balance.can_mint,
                'can_melt': balance.can_melt,
            }

        # Get fields
        fields_result: dict[str, dict] = {}
        for field in fields:
            key_field = self._get_key_for_field(field)
            if key_field is None:
                fields_result[field] = {'errmsg': 'invalid format'}
                continue

            try:
                field_type = blueprint_class.__annotations__[field]
            except KeyError:
                fields_result[field] = {'errmsg': 'not a blueprint field'}
                continue

            try:
                field_nc_type = make_nc_type_for_field_type(field_type)
                value = nc_storage.get_obj(key_field.encode(), field_nc_type)
            except KeyError:
                fields_result[field] = {'errmsg': 'field not found'}
                continue

            json_value = field_nc_type.value_to_json(value)
            fields_result[field] = {'value': json_value}

        # Call view methods
        runner.disable_call_trace()
        calls_result: dict[str, dict] = {}
        for call_info in calls:
            try:
                method_name, method_args = parse_nc_method_call(blueprint_class, call_info)
                value = runner.call_view_method(nc_id_bytes, method_name, *method_args)
                if type(value) is bytes:
                    value = value.hex()
            except Exception as e:
                calls_result[call_info] = {'errmsg': repr(e)}
            else:
                calls_result[call_info] = {'value': value}

        # Build snapshot
        return {
            'block_height': block_height,
            'block_hash': block.hash_hex,
            'timestamp': block.timestamp,
            'nc_id': nc_id_bytes.hex(),
            'blueprint_id': blueprint_id.hex(),
            'blueprint_name': blueprint_class.__name__,
            'fields': fields_result,
            'balances': balances_result,
            'calls': calls_result,
        }

    def _get_key_for_field(self, field: str) -> Optional[str]:
        """Return the storage key for a given field.

        Copied from NanoContractStateResource.get_key_for_field()
        """
        parts = field.split('.')
        try:
            key_parts = [self._parse_field_name(name) for name in parts]
        except ValueError:
            return None
        return ':'.join(key_parts)

    def _parse_field_name(self, field: str) -> str:
        """Parse field names.

        Copied from NanoContractStateResource.parse_field_name()
        """
        if field.startswith("a'") and field.endswith("'"):
            # Addresses are decoded to bytes
            address = field[2:-1]
            try:
                return str(decode_address(address))
            except InvalidAddress as e:
                raise ValueError from e
        elif field.startswith("b'") and field.endswith("'"):
            # This field is bytes and we receive this in hexa
            hexa = field[2:-1]
            return str(bytes.fromhex(hexa))
        return field


class NCStateBatchParams(QueryParams):
    """Query parameters for batch state queries."""
    id: str
    block_heights: list[int] = Field(alias='block_heights[]', default_factory=list)
    fields: list[str] = Field(alias='fields[]', default_factory=list)
    balances: list[str] = Field(alias='balances[]', default_factory=list)
    calls: list[str] = Field(alias='calls[]', default_factory=list)


class NCStateBatchResponse(Response):
    """Response model for batch state queries."""
    success: bool
    snapshots: list[dict[str, Any]]
    errors: Optional[list[dict[str, Any]]] = None
    total_requested: int
    total_succeeded: int
    total_failed: int


# OpenAPI documentation
_openapi_example_request = {
    'id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
    'block_heights': [5135133, 5135134, 5135135],
    'calls': ['getAllPricesUsd()', 'getAllPricesHtr()'],
    'fields': ['token_uid'],
    'balances': ['00']
}

_openapi_example_response = {
    'success': True,
    'snapshots': [
        {
            'block_height': 5135133,
            'block_hash': '00001234...',
            'timestamp': 1234567890,
            'nc_id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
            'blueprint_id': '3cb032600bdf7db784800e4ea911b10676fa2f67591f82bb62628c234e771595',
            'blueprint_name': 'DozerPoolManager',
            'fields': {
                'token_uid': {'value': '00'}
            },
            'balances': {
                '00': {'value': '100000', 'can_mint': False, 'can_melt': False}
            },
            'calls': {
                'getAllPricesUsd()': {'value': [['00', 100000], ['token2', 50000]]},
                'getAllPricesHtr()': {'value': [['00', 100000], ['token2', 50000]]}
            }
        },
        # ... more snapshots
    ],
    'errors': None,
    'total_requested': 3,
    'total_succeeded': 3,
    'total_failed': 0
}

NanoContractStateBatchResource.openapi = {
    '/nano_contract/state_batch': {
        'x-visibility': 'private',  # Mark as private - for owned nodes only
        'x-rate-limit': {
            'global': [
                {
                    'rate': '100r/s',  # Very high limit for owned nodes
                    'burst': 200,
                    'delay': 0
                }
            ],
            'per-ip': [
                {
                    'rate': '100r/s',
                    'burst': 200,
                    'delay': 0
                }
            ]
        },
        'get': {
            'tags': ['nano_contracts'],
            'operationId': 'nano_contracts_state_batch',
            'summary': 'Batch query nano contract state across multiple blocks',
            'description': (
                'Returns the state of a nano contract at multiple block heights in a single request. '
                'This endpoint is designed for owned/private nodes to enable efficient historical '
                'data backfilling. IMPORTANT: This endpoint should NOT be enabled on public nodes '
                'as it can be resource-intensive.'
            ),
            'parameters': [
                {
                    'name': 'id',
                    'in': 'query',
                    'description': 'ID of the nano contract',
                    'required': True,
                    'schema': {'type': 'string'}
                },
                {
                    'name': 'block_heights[]',
                    'in': 'query',
                    'description': f'Array of block heights to query (max {NanoContractStateBatchResource.MAX_BLOCKS_PER_REQUEST})',
                    'required': True,
                    'schema': {
                        'type': 'array',
                        'items': {'type': 'integer'},
                        'maxItems': NanoContractStateBatchResource.MAX_BLOCKS_PER_REQUEST
                    }
                },
                {
                    'name': 'calls[]',
                    'in': 'query',
                    'description': 'List of view method calls (same format as /nano_contract/state)',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    }
                },
                {
                    'name': 'fields[]',
                    'in': 'query',
                    'description': 'Fields to get from nano contract state',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    }
                },
                {
                    'name': 'balances[]',
                    'in': 'query',
                    'description': 'Token UIDs to get balances for (or "__all__")',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    }
                }
            ],
            'responses': {
                '200': {
                    'description': 'Success',
                    'content': {
                        'application/json': {
                            'examples': {
                                'success': {
                                    'summary': 'Successful batch query',
                                    'value': _openapi_example_response
                                },
                                'partial_success': {
                                    'summary': 'Partial success (some blocks failed)',
                                    'value': {
                                        'success': True,
                                        'snapshots': [{'block_height': 5135133, '...': '...'}],
                                        'errors': [
                                            {
                                                'block_height': 5135134,
                                                'error': 'Nano contract does not exist at block...'
                                            }
                                        ],
                                        'total_requested': 3,
                                        'total_succeeded': 2,
                                        'total_failed': 1
                                    }
                                }
                            }
                        }
                    }
                },
                '400': {
                    'description': 'Bad request',
                    'content': {
                        'application/json': {
                            'examples': {
                                'too_many_blocks': {
                                    'summary': 'Too many blocks requested',
                                    'value': {
                                        'success': False,
                                        'error': f'Too many blocks requested. Maximum is {NanoContractStateBatchResource.MAX_BLOCKS_PER_REQUEST}'
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
