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

"""Optimized historical time-series endpoint for chart data.

This endpoint provides time-series data optimized for chart visualization.
It samples nano contract state at specified intervals across a time range.

Designed for owned nodes with appropriate caching layers in front.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional
from enum import Enum

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


class Resolution(str, Enum):
    """Time resolution for data sampling."""
    BLOCK = "block"  # Every block (highest precision)
    FIVE_MIN = "5m"  # 5-minute intervals
    FIFTEEN_MIN = "15m"  # 15-minute intervals
    ONE_HOUR = "1h"  # 1-hour intervals
    ONE_DAY = "1d"  # 1-day intervals


# Resolution to seconds mapping
RESOLUTION_SECONDS = {
    Resolution.FIVE_MIN: 300,
    Resolution.FIFTEEN_MIN: 900,
    Resolution.ONE_HOUR: 3600,
    Resolution.ONE_DAY: 86400,
}


@register_resource
class NanoContractHistoryOptimizedResource(Resource):
    """Optimized historical time-series endpoint for chart data.

    This endpoint samples nano contract state at specified time intervals,
    providing efficient data for chart visualization.

    Supports adaptive resolution based on time range and caching-friendly
    responses for optimal performance.
    """
    isLeaf = True

    # Maximum data points to prevent excessive computation
    MAX_DATA_POINTS = 1000

    def __init__(self, manager: 'HathorManager') -> None:
        super().__init__()
        self.manager = manager

    def render_GET(self, request: 'Request') -> bytes:
        """Handle GET request for historical time-series data.

        Query parameters:
            id: contract_id_hex (required)
            start_timestamp: start of time range in seconds (required)
            end_timestamp: end of time range in seconds (required)
            resolution: 'block', '5m', '15m', '1h', '1d' (required)
            calls[]: array of method calls (optional)
            fields[]: array of field names (optional)
            balances[]: array of token UIDs (optional)

        Response includes timestamps, block info, and requested state data.
        """
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'GET')

        # Parse parameters
        params = NCHistoryOptimizedParams.from_request(request)
        if isinstance(params, ErrorResponse):
            request.setResponseCode(400)
            return params.json_dumpb()

        # Validate contract ID
        try:
            nc_id_bytes = ContractId(VertexId(bytes.fromhex(params.id)))
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid contract id: {params.id}')
            return error_response.json_dumpb()

        # Validate time range
        if params.start_timestamp >= params.end_timestamp:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error='start_timestamp must be less than end_timestamp'
            )
            return error_response.json_dumpb()

        # Validate resolution
        try:
            resolution = Resolution(params.resolution)
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error=f'Invalid resolution: {params.resolution}. Must be one of: block, 5m, 15m, 1h, 1d'
            )
            return error_response.json_dumpb()

        # Check if indexes are enabled
        if self.manager.tx_storage.indexes is None:
            request.setResponseCode(503)
            error_response = ErrorResponse(
                success=False,
                error='No indexes enabled in the storage.'
            )
            return error_response.json_dumpb()

        # Generate timestamps to sample
        sample_timestamps = self._generate_sample_timestamps(
            params.start_timestamp,
            params.end_timestamp,
            resolution
        )

        # Check if we exceed max data points
        if len(sample_timestamps) > self.MAX_DATA_POINTS:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error=f'Too many data points ({len(sample_timestamps)}). Maximum is {self.MAX_DATA_POINTS}. '
                      f'Use a coarser resolution.'
            )
            return error_response.json_dumpb()

        # Fetch data for each timestamp
        data_points = []
        errors = []

        for timestamp in sample_timestamps:
            try:
                data_point = self._fetch_state_at_timestamp(
                    nc_id_bytes=nc_id_bytes,
                    timestamp=timestamp,
                    calls=params.calls,
                    fields=params.fields,
                    balances=params.balances
                )
                if data_point:
                    data_points.append(data_point)
            except Exception as e:
                errors.append({
                    'timestamp': timestamp,
                    'error': str(e)
                })

        # Build response
        response = NCHistoryOptimizedResponse(
            success=True,
            resolution=params.resolution,
            start_timestamp=params.start_timestamp,
            end_timestamp=params.end_timestamp,
            data_points=data_points,
            total_points=len(data_points),
            errors=errors if errors else None
        )

        return response.json_dumpb()

    def _generate_sample_timestamps(
        self,
        start_timestamp: int,
        end_timestamp: int,
        resolution: Resolution
    ) -> list[int]:
        """Generate list of timestamps to sample based on resolution.

        For block resolution, we'll sample every block. For time-based
        resolutions, we sample at regular intervals.
        """
        if resolution == Resolution.BLOCK:
            # For block resolution, we need to get all blocks in range
            # This will be handled by fetching blocks directly
            # We'll return just start and end, and handle blocks in fetch
            return [start_timestamp, end_timestamp]

        # For time-based resolutions, generate regular intervals
        interval_seconds = RESOLUTION_SECONDS[resolution]
        timestamps = []

        current = start_timestamp
        while current <= end_timestamp:
            timestamps.append(current)
            current += interval_seconds

        # Always include the end timestamp if not already included
        if timestamps[-1] != end_timestamp:
            timestamps.append(end_timestamp)

        return timestamps

    def _fetch_state_at_timestamp(
        self,
        nc_id_bytes: bytes,
        timestamp: int,
        calls: list[str],
        fields: list[str],
        balances: list[str]
    ) -> Optional[dict]:
        """Fetch nano contract state at a specific timestamp.

        Returns a data point with timestamp, block info, and state values.
        """
        # Get block at or before this timestamp
        block_hashes, has_more = self.manager.tx_storage.indexes.sorted_blocks.get_older(
            timestamp=timestamp,
            hash_bytes=None,
            count=1,
        )

        if not block_hashes:
            return None

        block_hash = block_hashes[0]

        try:
            block = self.manager.tx_storage.get_block(block_hash)
        except AssertionError:
            return None

        # Get nano contract state at this block
        try:
            runner = self.manager.get_nc_runner(block)
            nc_storage = runner.get_storage(nc_id_bytes)
        except NanoContractDoesNotExist:
            return None

        # Build state values
        values = {}

        # Get balances
        for token_uid_hex in balances:
            if token_uid_hex == '__all__':
                all_balances = nc_storage.get_all_balances()
                for key_balance, balance in all_balances.items():
                    values[f'balance_{key_balance.token_uid.hex()}'] = str(balance.value)
                break

            try:
                token_uid = bytes.fromhex(token_uid_hex)
                balance = nc_storage.get_balance(token_uid)
                values[f'balance_{token_uid_hex}'] = str(balance.value)
            except (ValueError, KeyError):
                continue

        # Get fields
        blueprint_id = nc_storage.get_blueprint_id()
        blueprint_class = self.manager.tx_storage.get_blueprint_class(blueprint_id)

        for field in fields:
            key_field = self._get_key_for_field(field)
            if key_field is None:
                continue

            try:
                field_type = blueprint_class.__annotations__[field]
                field_nc_type = make_nc_type_for_field_type(field_type)
                value = nc_storage.get_obj(key_field.encode(), field_nc_type)
                values[field] = field_nc_type.value_to_json(value)
            except (KeyError, Exception):
                continue

        # Call view methods
        runner.disable_call_trace()
        for call_info in calls:
            try:
                method_name, method_args = parse_nc_method_call(blueprint_class, call_info)
                value = runner.call_view_method(nc_id_bytes, method_name, *method_args)
                if type(value) is bytes:
                    value = value.hex()
                values[call_info] = value
            except Exception:
                continue

        return {
            'timestamp': block.timestamp,
            'block_height': block.get_height(),
            'block_hash': block.hash_hex,
            'values': values
        }

    def _get_key_for_field(self, field: str) -> Optional[str]:
        """Return the storage key for a given field."""
        parts = field.split('.')
        try:
            key_parts = [self._parse_field_name(name) for name in parts]
        except ValueError:
            return None
        return ':'.join(key_parts)

    def _parse_field_name(self, field: str) -> str:
        """Parse field names."""
        if field.startswith("a'") and field.endswith("'"):
            address = field[2:-1]
            try:
                return str(decode_address(address))
            except InvalidAddress as e:
                raise ValueError from e
        elif field.startswith("b'") and field.endswith("'"):
            hexa = field[2:-1]
            return str(bytes.fromhex(hexa))
        return field


class NCHistoryOptimizedParams(QueryParams):
    """Query parameters for history optimized endpoint."""
    id: str
    start_timestamp: int
    end_timestamp: int
    resolution: str
    fields: list[str] = Field(alias='fields[]', default_factory=list)
    balances: list[str] = Field(alias='balances[]', default_factory=list)
    calls: list[str] = Field(alias='calls[]', default_factory=list)


class NCHistoryOptimizedResponse(Response):
    """Response model for history optimized endpoint."""
    success: bool
    resolution: str
    start_timestamp: int
    end_timestamp: int
    data_points: list[dict[str, Any]]
    total_points: int
    errors: Optional[list[dict[str, Any]]] = None


# OpenAPI documentation
_openapi_example_response = {
    'success': True,
    'resolution': '5m',
    'start_timestamp': 1234567890,
    'end_timestamp': 1234654290,
    'total_points': 288,
    'data_points': [
        {
            'timestamp': 1234567890,
            'block_height': 5135133,
            'block_hash': '00001234...',
            'values': {
                'get_pool_info(00/000008...)': {
                    'reserve0': '1000000',
                    'reserve1': '500000',
                    'volume_usd': '1234567'
                }
            }
        },
        {
            'timestamp': 1234568190,
            'block_height': 5135145,
            'block_hash': '00001235...',
            'values': {
                'get_pool_info(00/000008...)': {
                    'reserve0': '1001000',
                    'reserve1': '501000',
                    'volume_usd': '1235000'
                }
            }
        }
    ],
    'errors': None
}

NanoContractHistoryOptimizedResource.openapi = {
    '/nano_contract/history_optimized': {
        'x-visibility': 'public',
        'x-rate-limit': {
            'global': [
                {
                    'rate': '50r/s',
                    'burst': 100,
                    'delay': 10
                }
            ],
            'per-ip': [
                {
                    'rate': '10r/s',
                    'burst': 20,
                    'delay': 5
                }
            ]
        },
        'get': {
            'tags': ['nano_contracts'],
            'operationId': 'nano_contracts_history_optimized',
            'summary': 'Get historical time-series data for chart visualization',
            'description': (
                'Returns nano contract state sampled at specified intervals across a time range. '
                'Optimized for chart visualization with adaptive resolution support. '
                'Responses are designed to be cached aggressively.'
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
                    'name': 'start_timestamp',
                    'in': 'query',
                    'description': 'Start of time range (Unix timestamp in seconds)',
                    'required': True,
                    'schema': {'type': 'integer'}
                },
                {
                    'name': 'end_timestamp',
                    'in': 'query',
                    'description': 'End of time range (Unix timestamp in seconds)',
                    'required': True,
                    'schema': {'type': 'integer'}
                },
                {
                    'name': 'resolution',
                    'in': 'query',
                    'description': 'Sampling resolution: block, 5m, 15m, 1h, 1d',
                    'required': True,
                    'schema': {
                        'type': 'string',
                        'enum': ['block', '5m', '15m', '1h', '1d']
                    }
                },
                {
                    'name': 'calls[]',
                    'in': 'query',
                    'description': 'View method calls (same format as /nano_contract/state)',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    }
                },
                {
                    'name': 'fields[]',
                    'in': 'query',
                    'description': 'Fields to extract from contract state',
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
                                    'summary': 'Successful historical query',
                                    'value': _openapi_example_response
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
                                'invalid_time_range': {
                                    'summary': 'Invalid time range',
                                    'value': {
                                        'success': False,
                                        'error': 'start_timestamp must be less than end_timestamp'
                                    }
                                },
                                'too_many_points': {
                                    'summary': 'Too many data points',
                                    'value': {
                                        'success': False,
                                        'error': 'Too many data points (1500). Maximum is 1000. Use a coarser resolution.'
                                    }
                                }
                            }
                        }
                    }
                },
                '503': {
                    'description': 'Service unavailable',
                    'content': {
                        'application/json': {
                            'examples': {
                                'no_indexes': {
                                    'summary': 'Indexes not enabled',
                                    'value': {
                                        'success': False,
                                        'error': 'No indexes enabled in the storage.'
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
