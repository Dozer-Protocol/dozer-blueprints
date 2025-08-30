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

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field, validator

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
class NanoContractStateResource(Resource):
    """ Implements a web server GET API to get a nano contract state.
    You must run with option `--status <PORT>`.
    """
    isLeaf = True

    def __init__(self, manager: 'HathorManager') -> None:
        super().__init__()
        self.manager = manager

    def render_GET(self, request: 'Request') -> bytes:
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'GET')

        params = NCStateParams.from_request(request)
        if isinstance(params, ErrorResponse):
            request.setResponseCode(400)
            return params.json_dumpb()

        if sum(x is not None for x in (params.block_hash, params.block_height, params.timestamp)) > 1:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error='only one of `block_hash`, `block_height`, or `timestamp` must be used',
            )
            return error_response.json_dumpb()

        try:
            nc_id_bytes = ContractId(VertexId(bytes.fromhex(params.id)))
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid id: {params.id}')
            return error_response.json_dumpb()

        nc_storage: NCContractStorage
        block: Block
        block_hash: Optional[bytes]
        try:
            block_hash = bytes.fromhex(params.block_hash) if params.block_hash else None
        except ValueError:
            # This error will be raised in case the block_hash parameter is an invalid hex
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid block_hash parameter: {params.block_hash}')
            return error_response.json_dumpb()

        if params.block_height is not None:
            # Get hash of the block with the height
            if self.manager.tx_storage.indexes is None:
                # No indexes enabled in the storage
                request.setResponseCode(503)
                error_response = ErrorResponse(
                                    success=False,
                                    error='No indexes enabled in the storage, so we can\'t filter by block height.'
                                )
                return error_response.json_dumpb()

            block_hash = self.manager.tx_storage.indexes.height.get(params.block_height)
            if block_hash is None:
                # No block hash was found with this height
                request.setResponseCode(400)
                error_response = ErrorResponse(
                                    success=False,
                                    error=f'No block hash was found with height {params.block_height}.'
                                )
                return error_response.json_dumpb()
        elif params.timestamp is not None:
            if self.manager.tx_storage.indexes is None:
                # No indexes enabled in the storage
                request.setResponseCode(503)
                error_response = ErrorResponse(
                    success=False,
                    error='No indexes enabled in the storage, so we can\'t filter by timestamp.'
                )
                return error_response.json_dumpb()

            block_hashes, has_more = self.manager.tx_storage.indexes.sorted_blocks.get_older(
                timestamp=params.timestamp,
                hash_bytes=None,
                count=1,
            )
            if not block_hashes:
                # No block hash was found before this timestamp
                request.setResponseCode(400)
                error_response = ErrorResponse(
                    success=False,
                    error=f'No block hash was found before timestamp {params.timestamp}.'
                )
                return error_response.json_dumpb()
            assert len(block_hashes) == 1
            block_hash = block_hashes[0]

        if block_hash:
            try:
                block = self.manager.tx_storage.get_block(block_hash)
            except AssertionError:
                # This block hash is not from a block
                request.setResponseCode(400)
                error_response = ErrorResponse(success=False, error=f'Invalid block_hash {params.block_hash}.')
                return error_response.json_dumpb()
        else:
            block = self.manager.tx_storage.get_best_block()

        try:
            runner = self.manager.get_nc_runner(block)
            nc_storage = runner.get_storage(nc_id_bytes)
        except NanoContractDoesNotExist:
            # Nano contract does not exist at this block
            request.setResponseCode(404)
            error_response = ErrorResponse(
                success=False,
                error=f'Nano contract does not exist at block {block.hash_hex}.'
            )
            return error_response.json_dumpb()

        blueprint_id = nc_storage.get_blueprint_id()
        blueprint_class = self.manager.tx_storage.get_blueprint_class(blueprint_id)

        value: Any
        # Get balances.
        balances: dict[str, NCBalanceSuccessResponse | NCValueErrorResponse] = {}
        for token_uid_hex in params.balances:
            if token_uid_hex == '__all__':
                # User wants to get the balance of all tokens in the nano contract
                all_balances = nc_storage.get_all_balances()
                for key_balance, balance in all_balances.items():
                    balances[key_balance.token_uid.hex()] = NCBalanceSuccessResponse(
                        value=str(balance.value),
                        can_mint=balance.can_mint,
                        can_melt=balance.can_melt,
                    )
                break

            try:
                token_uid = bytes.fromhex(token_uid_hex)
            except ValueError:
                balances[token_uid_hex] = NCValueErrorResponse(errmsg='invalid token id')
                continue

            balance = nc_storage.get_balance(token_uid)
            balances[token_uid_hex] = NCBalanceSuccessResponse(
                value=str(balance.value),
                can_mint=balance.can_mint,
                can_melt=balance.can_melt,
            )

        # Get fields.
        fields: dict[str, NCValueSuccessResponse | NCValueErrorResponse] = {}
        param_fields: list[str] = params.fields
        for field in param_fields:
            key_field = self.get_key_for_field(field)
            if key_field is None:
                fields[field] = NCValueErrorResponse(errmsg='invalid format')
                continue

            try:
                field_type = blueprint_class.__annotations__[field]
            except KeyError:
                fields[field] = NCValueErrorResponse(errmsg='not a blueprint field')
                continue

            try:
                field_nc_type = make_nc_type_for_field_type(field_type)
                value = nc_storage.get_obj(key_field.encode(), field_nc_type)
            except KeyError:
                fields[field] = NCValueErrorResponse(errmsg='field not found')
                continue

            json_value = field_nc_type.value_to_json(value)
            fields[field] = NCValueSuccessResponse(value=json_value)

        # Call view methods.
        runner.disable_call_trace()  # call trace is not required for calling view methods.
        calls: dict[str, NCValueSuccessResponse | NCValueErrorResponse] = {}
        for call_info in params.calls:
            try:
                method_name, method_args = parse_nc_method_call(blueprint_class, call_info)
                value = runner.call_view_method(nc_id_bytes, method_name, *method_args)
                if type(value) is bytes:
                    value = value.hex()
            except Exception as e:
                calls[call_info] = NCValueErrorResponse(errmsg=repr(e))
            else:
                calls[call_info] = NCValueSuccessResponse(value=value)

        response = NCStateResponse(
            success=True,
            nc_id=params.id,
            blueprint_id=blueprint_id.hex(),
            blueprint_name=blueprint_class.__name__,
            fields=fields,
            balances=balances,
            calls=calls,
        )
        return response.json_dumpb()

    def get_key_for_field(self, field: str) -> Optional[str]:
        """Return the storage key for a given field."""
        # Queries might have multiple parts separated by '.'
        parts = field.split('.')
        try:
            key_parts = [self.parse_field_name(name) for name in parts]
        except ValueError:
            return None
        return ':'.join(key_parts)

    def parse_field_name(self, field: str) -> str:
        """Parse field names."""
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
            # This will raise ValueError in case it's an invalid hexa
            # and this will be handled in the get_key_for_field method
            return str(bytes.fromhex(hexa))
        return field


@register_resource
class NanoContractPriceHistoryResource(Resource):
    """ Implements a web server GET API to get nano contract price history with OHLC data.
    You must run with option `--status <PORT>`.
    """
    isLeaf = True
    
    # Supported intervals in seconds and their sampling frequency (in seconds)
    INTERVALS = {
        '1m': {'seconds': 60, 'sample_interval': 60},      # 1 sample per minute
        '5m': {'seconds': 300, 'sample_interval': 60},     # 5 samples per candle
        '15m': {'seconds': 900, 'sample_interval': 60},    # 15 samples per candle
        '30m': {'seconds': 1800, 'sample_interval': 60},   # 30 samples per candle
        '1h': {'seconds': 3600, 'sample_interval': 60},    # 60 samples per candle
        '4h': {'seconds': 14400, 'sample_interval': 300},  # 48 samples per candle (every 5 minutes)
        '1d': {'seconds': 86400, 'sample_interval': 1800}, # 48 samples per candle (every 30 minutes)
        '1w': {'seconds': 604800, 'sample_interval': 7200}, # 84 samples per candle (every 2 hours)
    }

    # Valid price method calls
    VALID_PRICE_METHODS = {
        'get_token_price_in_usd',
        'get_token_price_in_htr'
    }

    def __init__(self, manager: 'HathorManager') -> None:
        super().__init__()
        self.manager = manager

    def render_GET(self, request: 'Request') -> bytes:
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'GET')

        params = NCPriceHistoryParams.from_request(request)
        if isinstance(params, ErrorResponse):
            request.setResponseCode(400)
            return params.json_dumpb()

        # Validate interval
        if params.interval not in self.INTERVALS:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error=f'Invalid interval: {params.interval}. Supported: {", ".join(self.INTERVALS.keys())}'
            )
            return error_response.json_dumpb()

        # Validate time range
        if params.start_time >= params.end_time:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error='start_time must be less than end_time'
            )
            return error_response.json_dumpb()

        # Validate calls are for price methods only
        for call_info in params.calls:
            method_name = call_info.split('(')[0] if '(' in call_info else call_info
            if method_name not in self.VALID_PRICE_METHODS:
                request.setResponseCode(400)
                error_response = ErrorResponse(
                    success=False,
                    error=f'Only price methods allowed: {", ".join(self.VALID_PRICE_METHODS)}'
                )
                return error_response.json_dumpb()

        if not params.calls:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error='At least one price method call is required'
            )
            return error_response.json_dumpb()

        # Validate contract ID
        try:
            nc_id_bytes = ContractId(VertexId(bytes.fromhex(params.id)))
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid id: {params.id}')
            return error_response.json_dumpb()

        # Check if indexes are available
        if self.manager.tx_storage.indexes is None:
            request.setResponseCode(503)
            error_response = ErrorResponse(
                success=False,
                error='No indexes enabled in the storage.'
            )
            return error_response.json_dumpb()

        # Generate timestamps at intervals
        interval_config = self.INTERVALS[params.interval]
        interval_seconds = interval_config['seconds']
        timestamps = self._generate_timestamps(params.start_time, params.end_time, interval_seconds)
        
        # Limit the number of data points to prevent abuse
        if len(timestamps) > params.limit:
            timestamps = timestamps[:params.limit]

        # Get OHLC data for each timestamp interval
        candles = []
        for timestamp in timestamps:
            try:
                ohlc_data = self._get_ohlc_at_interval(
                    nc_id_bytes=nc_id_bytes,
                    interval_start=timestamp,
                    interval_seconds=interval_seconds,
                    sample_interval=interval_config['sample_interval'],
                    calls=params.calls
                )
                if ohlc_data:
                    ohlc_data['timestamp'] = timestamp
                    candles.append(ohlc_data)
            except Exception:
                # Log error but continue with other timestamps
                continue

        response = NCPriceHistoryResponse(
            success=True,
            nc_id=params.id,
            interval=params.interval,
            start_time=params.start_time,
            end_time=params.end_time,
            candles=candles,
        )
        return response.json_dumpb()

    def _generate_timestamps(self, start_time: int, end_time: int, interval_seconds: int) -> list[int]:
        """Generate timestamps at specified intervals between start and end time."""
        timestamps = []
        current_time = start_time
        
        # Align to interval boundary
        current_time = (current_time // interval_seconds) * interval_seconds
        
        while current_time <= end_time:
            timestamps.append(current_time)
            current_time += interval_seconds
            
        return timestamps

    def _get_ohlc_at_interval(
        self, 
        nc_id_bytes: bytes,
        interval_start: int,
        interval_seconds: int,
        sample_interval: int,
        calls: list[str]
    ) -> Optional[dict]:
        """Calculate OHLC data for a specific time interval by sampling prices."""
        
        # Generate sample timestamps within the interval
        sample_timestamps = []
        current_sample = interval_start
        interval_end = interval_start + interval_seconds
        
        while current_sample < interval_end:
            sample_timestamps.append(current_sample)
            current_sample += sample_interval
        
        # If no samples would be taken, add at least the start timestamp
        if not sample_timestamps:
            sample_timestamps = [interval_start]
        
        # Collect price samples for all calls
        call_samples = {call_info: [] for call_info in calls}
        
        for sample_timestamp in sample_timestamps:
            # Get state at this sample timestamp
            state_data = self._get_state_at_timestamp(
                nc_id_bytes=nc_id_bytes,
                timestamp=sample_timestamp,
                fields=[],
                balances=[],
                calls=calls
            )
            
            if state_data and 'calls' in state_data:
                for call_info in calls:
                    if call_info in state_data['calls']:
                        call_result = state_data['calls'][call_info]
                        if hasattr(call_result, 'value') and isinstance(call_result.value, (int, float)):
                            call_samples[call_info].append(float(call_result.value))
        
        # Calculate OHLC for each call
        ohlc_result = {}
        for call_info, samples in call_samples.items():
            if samples:
                ohlc_result[call_info] = {
                    'open': samples[0],
                    'high': max(samples),
                    'low': min(samples),
                    'close': samples[-1]
                }
            else:
                ohlc_result[call_info] = {
                    'open': 0,
                    'high': 0,
                    'low': 0,
                    'close': 0
                }
        
        return ohlc_result if ohlc_result else None

    def _get_state_at_timestamp(
        self, 
        nc_id_bytes: bytes,
        timestamp: int,
        fields: list[str],
        balances: list[str],
        calls: list[str]
    ) -> Optional[dict]:
        """Get nano contract state at a specific timestamp. Reuses logic from NanoContractStateHistoryResource."""
        
        # Find block at or before the timestamp
        block_hashes, _ = self.manager.tx_storage.indexes.sorted_blocks.get_older(
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

        try:
            runner = self.manager.get_nc_runner(block)
            nc_storage = runner.get_storage(ContractId(nc_id_bytes))
        except NanoContractDoesNotExist:
            return None

        blueprint_id = nc_storage.get_blueprint_id()
        blueprint_class = self.manager.tx_storage.get_blueprint_class(blueprint_id)

        # Call view methods (only what we need for OHLC)
        runner.disable_call_trace()
        state_calls: dict[str, NCValueSuccessResponse | NCValueErrorResponse] = {}
        for call_info in calls:
            try:
                method_name, method_args = parse_nc_method_call(blueprint_class, call_info)
                value = runner.call_view_method(ContractId(nc_id_bytes), method_name, *method_args)
                if type(value) is bytes:
                    value = value.hex()
            except Exception as e:
                state_calls[call_info] = NCValueErrorResponse(errmsg=repr(e))
            else:
                state_calls[call_info] = NCValueSuccessResponse(value=value)

        return {
            'success': True,
            'calls': state_calls,
        }

    def _get_key_for_field(self, field: str) -> Optional[str]:
        """Return the storage key for a given field. Copied from NanoContractStateResource."""
        # Queries might have multiple parts separated by '.'
        parts = field.split('.')
        try:
            key_parts = [self._parse_field_name(name) for name in parts]
        except ValueError:
            return None
        return ':'.join(key_parts)

    def _parse_field_name(self, field: str) -> str:
        """Parse field names. Copied from NanoContractStateResource."""
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
            # This will raise ValueError in case it's an invalid hexa
            # and this will be handled in the get_key_for_field method
            return str(bytes.fromhex(hexa))
        return field


@register_resource
class NanoContractStateHistoryResource(Resource):
    """ Implements a web server GET API to get nano contract state history at intervals.
    You must run with option `--status <PORT>`.
    """
    isLeaf = True
    
    # Supported intervals in seconds
    INTERVALS = {
        '1m': 60,
        '5m': 300,
        '15m': 900,
        '30m': 1800,
        '1h': 3600,
        '4h': 14400,
        '1d': 86400,
        '1w': 604800,
    }

    def __init__(self, manager: 'HathorManager') -> None:
        super().__init__()
        self.manager = manager

    def render_GET(self, request: 'Request') -> bytes:
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'GET')

        params = NCStateHistoryParams.from_request(request)
        if isinstance(params, ErrorResponse):
            request.setResponseCode(400)
            return params.json_dumpb()

        # Validate interval
        if params.interval not in self.INTERVALS:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error=f'Invalid interval: {params.interval}. Supported: {", ".join(self.INTERVALS.keys())}'
            )
            return error_response.json_dumpb()

        # Validate time range
        if params.start_time >= params.end_time:
            request.setResponseCode(400)
            error_response = ErrorResponse(
                success=False,
                error='start_time must be less than end_time'
            )
            return error_response.json_dumpb()

        # Validate contract ID
        try:
            nc_id_bytes = ContractId(VertexId(bytes.fromhex(params.id)))
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid id: {params.id}')
            return error_response.json_dumpb()

        # Check if indexes are available
        if self.manager.tx_storage.indexes is None:
            request.setResponseCode(503)
            error_response = ErrorResponse(
                success=False,
                error='No indexes enabled in the storage.'
            )
            return error_response.json_dumpb()

        # Generate timestamps at intervals
        interval_seconds = self.INTERVALS[params.interval]
        timestamps = self._generate_timestamps(params.start_time, params.end_time, interval_seconds)
        
        # Limit the number of data points to prevent abuse
        if len(timestamps) > params.limit:
            timestamps = timestamps[:params.limit]

        # Get state for each timestamp
        states = []
        for timestamp in timestamps:
            try:
                state_data = self._get_state_at_timestamp(
                    nc_id_bytes=nc_id_bytes,
                    timestamp=timestamp,
                    fields=params.fields,
                    balances=params.balances,
                    calls=params.calls
                )
                if state_data:
                    state_data['timestamp'] = timestamp
                    states.append(state_data)
            except Exception as e:
                # Log error but continue with other timestamps
                continue

        response = NCStateHistoryResponse(
            success=True,
            nc_id=params.id,
            interval=params.interval,
            start_time=params.start_time,
            end_time=params.end_time,
            states=states,
        )
        return response.json_dumpb()

    def _generate_timestamps(self, start_time: int, end_time: int, interval_seconds: int) -> list[int]:
        """Generate timestamps at specified intervals between start and end time."""
        timestamps = []
        current_time = start_time
        
        # Align to interval boundary
        current_time = (current_time // interval_seconds) * interval_seconds
        
        while current_time <= end_time:
            timestamps.append(current_time)
            current_time += interval_seconds
            
        return timestamps

    def _get_state_at_timestamp(
        self, 
        nc_id_bytes: bytes,
        timestamp: int,
        fields: list[str],
        balances: list[str],
        calls: list[str]
    ) -> Optional[dict]:
        """Get nano contract state at a specific timestamp. Reuses logic from NanoContractStateResource."""
        
        # Find block at or before the timestamp
        block_hashes, _ = self.manager.tx_storage.indexes.sorted_blocks.get_older(
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

        try:
            runner = self.manager.get_nc_runner(block)
            nc_storage = runner.get_storage(ContractId(nc_id_bytes))
        except NanoContractDoesNotExist:
            return None

        blueprint_id = nc_storage.get_blueprint_id()
        blueprint_class = self.manager.tx_storage.get_blueprint_class(blueprint_id)

        # Get balances (reuse logic from original endpoint)
        state_balances: dict[str, NCBalanceSuccessResponse | NCValueErrorResponse] = {}
        for token_uid_hex in balances:
            if token_uid_hex == '__all__':
                all_balances = nc_storage.get_all_balances()
                for key_balance, balance in all_balances.items():
                    state_balances[key_balance.token_uid.hex()] = NCBalanceSuccessResponse(
                        value=str(balance.value),
                        can_mint=balance.can_mint,
                        can_melt=balance.can_melt,
                    )
                break

            try:
                token_uid = bytes.fromhex(token_uid_hex)
            except ValueError:
                state_balances[token_uid_hex] = NCValueErrorResponse(errmsg='invalid token id')
                continue

            balance = nc_storage.get_balance(token_uid)
            state_balances[token_uid_hex] = NCBalanceSuccessResponse(
                value=str(balance.value),
                can_mint=balance.can_mint,
                can_melt=balance.can_melt,
            )

        # Get fields (reuse logic from original endpoint)
        state_fields: dict[str, NCValueSuccessResponse | NCValueErrorResponse] = {}
        for field in fields:
            key_field = self._get_key_for_field(field)
            if key_field is None:
                state_fields[field] = NCValueErrorResponse(errmsg='invalid format')
                continue

            try:
                field_type = blueprint_class.__annotations__[field]
            except KeyError:
                state_fields[field] = NCValueErrorResponse(errmsg='not a blueprint field')
                continue

            try:
                field_nc_type = make_nc_type_for_field_type(field_type)
                value = nc_storage.get_obj(key_field.encode(), field_nc_type)
            except KeyError:
                state_fields[field] = NCValueErrorResponse(errmsg='field not found')
                continue

            json_value = field_nc_type.value_to_json(value)
            state_fields[field] = NCValueSuccessResponse(value=json_value)

        # Call view methods (reuse logic from original endpoint)
        runner.disable_call_trace()
        state_calls: dict[str, NCValueSuccessResponse | NCValueErrorResponse] = {}
        for call_info in calls:
            try:
                method_name, method_args = parse_nc_method_call(blueprint_class, call_info)
                value = runner.call_view_method(ContractId(nc_id_bytes), method_name, *method_args)
                if type(value) is bytes:
                    value = value.hex()
            except Exception as e:
                state_calls[call_info] = NCValueErrorResponse(errmsg=repr(e))
            else:
                state_calls[call_info] = NCValueSuccessResponse(value=value)

        return {
            'success': True,
            'nc_id': nc_id_bytes.hex(),
            'blueprint_id': blueprint_id.hex(),
            'blueprint_name': blueprint_class.__name__,
            'fields': state_fields,
            'balances': state_balances,
            'calls': state_calls,
        }

    def _get_key_for_field(self, field: str) -> Optional[str]:
        """Return the storage key for a given field. Copied from NanoContractStateResource."""
        # Queries might have multiple parts separated by '.'
        parts = field.split('.')
        try:
            key_parts = [self._parse_field_name(name) for name in parts]
        except ValueError:
            return None
        return ':'.join(key_parts)

    def _parse_field_name(self, field: str) -> str:
        """Parse field names. Copied from NanoContractStateResource."""
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
            # This will raise ValueError in case it's an invalid hexa
            # and this will be handled in the get_key_for_field method
            return str(bytes.fromhex(hexa))
        return field


class NCStateParams(QueryParams):
    id: str
    fields: list[str] = Field(alias='fields[]', default_factory=list)
    balances: list[str] = Field(alias='balances[]', default_factory=list)
    calls: list[str] = Field(alias='calls[]', default_factory=list)
    block_hash: Optional[str]
    block_height: Optional[int]
    timestamp: Optional[int]


class NCStateHistoryParams(QueryParams):
    id: str
    start_time: int
    end_time: int
    interval: str
    fields: list[str] = Field(alias='fields[]', default_factory=list)
    balances: list[str] = Field(alias='balances[]', default_factory=list)
    calls: list[str] = Field(alias='calls[]', default_factory=list)
    limit: int = Field(default=1000, le=1000)  # Prevent abuse


class NCValueSuccessResponse(Response):
    value: Any


class NCBalanceSuccessResponse(Response):
    value: str
    can_mint: bool
    can_melt: bool


class NCValueErrorResponse(Response):
    errmsg: str


class NCStateResponse(Response):
    success: bool
    nc_id: str
    blueprint_id: str
    blueprint_name: str
    fields: dict[str, NCValueSuccessResponse | NCValueErrorResponse]
    balances: dict[str, NCBalanceSuccessResponse | NCValueErrorResponse]
    calls: dict[str, NCValueSuccessResponse | NCValueErrorResponse]


class NCStateHistoryResponse(Response):
    success: bool
    nc_id: str
    interval: str
    start_time: int
    end_time: int
    states: list[dict[str, Any]]


class NCPriceHistoryParams(QueryParams):
    id: str
    start_time: int
    end_time: int
    interval: str
    calls: list[str] = Field(alias='calls[]', default_factory=list)
    limit: int = Field(default=100, le=500)  # More conservative limit for OHLC processing


class NCPriceHistoryResponse(Response):
    success: bool
    nc_id: str
    interval: str
    start_time: int
    end_time: int
    candles: list[dict[str, Any]]


_openapi_success_value = {
    'success': True,
    'nc_id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
    'blueprint_id': '3cb032600bdf7db784800e4ea911b10676fa2f67591f82bb62628c234e771595',
    'blueprint_name': 'Bet',
    'fields': {
        'token_uid': {'value': '00'},
        'total': {'value': 300},
        'final_result': {'value': '1x0'},
        'oracle_script': {'value': '76a91441c431ff7ad5d6ce5565991e3dcd5d9106cfd1e288ac'},
        'withdrawals.a\'Wi8zvxdXHjaUVAoCJf52t3WovTZYcU9aX6\'': {'value': 300},
        'address_details.a\'Wi8zvxdXHjaUVAoCJf52t3WovTZYcU9aX6\'': {'value': {'1x0': 100}},
    }
}


NanoContractStateResource.openapi = {
    '/nano_contract/state': {
        'x-visibility': 'public',
        'x-rate-limit': {
            'global': [
                {
                    'rate': '30r/s',
                    'burst': 20,
                    'delay': 10
                }
            ],
            'per-ip': [
                {
                    'rate': '5r/s',
                    'burst': 6,
                    'delay': 3
                }
            ]
        },
        'get': {
            'tags': ['nano_contracts'],
            'operationId': 'nano_contracts_state',
            'summary': 'Get state of a nano contract',
            'description': 'Returns the state requested of a nano contract.',
            'parameters': [
                {
                    'name': 'id',
                    'in': 'query',
                    'description': 'ID of the nano contract to get the state from',
                    'required': True,
                    'schema': {
                        'type': 'string'
                    }
                },
                {
                    'name': 'balances[]',
                    'in': 'query',
                    'description': 'List of token ids in hex to get the contract balance. '
                                   'If you want to get the balance for all tokens in the contract, just use __all__.',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    },
                    'examples': {
                        'balances': {
                            'summary': 'Example of balances',
                            'value': ['00', '000008f2ee2059a189322ae7cb1d7e7773dcb4fdc8c4de8767f63022b3731845']
                        },
                    }
                },
                {
                    'name': 'calls[]',
                    'in': 'query',
                    'description': 'List of private method calls to be executed. '
                                   'The format must be "method_name(arg1, arg2, arg3, ...)". '
                                   'Bytes arguments must be sent in hex, address arguments in bytes '
                                   'must be sent as hex itself, or in base58 with the address tag, e.g. '
                                   'a\'Wi8zvxdXHjaUVAoCJf52t3WovTZYcU9aX6\', and tuple arguments must be '
                                   'sent as an array, e.g., (a, b, c) must be sent as [a, b, c]. '
                                   'For SignedData field we expect a list with two elements, where the '
                                   'first one is the data to be signed and the second is the signature in hex.',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    },
                    'examples': {
                        'calls': {
                            'summary': 'Example of calls',
                            'value': ['view_method_1(arg1, arg2)', 'view_method_2()']
                        },
                    }
                },
                {
                    'name': 'fields[]',
                    'in': 'query',
                    'description': 'Fields to get the data from the nano contract state',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    },
                    'examples': {
                        'simple fields': {
                            'summary': 'Only direct fields',
                            'value': ['token_uid', 'total', 'final_result', 'oracle_script']
                        },
                        'With dict fields': {
                            'summary': ('Simple and dict fields (dict fields where the keys are addresses). '
                                        'For an address you must encapsulate the b58 with a\'\''),
                            'value': [
                                'token_uid',
                                'total',
                                'final_result',
                                'oracle_script',
                                'withdrawals.a\'Wi8zvxdXHjaUVAoCJf52t3WovTZYcU9aX6\'',
                                'address_details.a\'Wi8zvxdXHjaUVAoCJf52t3WovTZYcU9aX6\''
                            ]
                        },
                    }
                },
                {
                    'name': 'block_height',
                    'in': 'query',
                    'description': 'Height of the block to get the nano contract state from.'
                                   'Can\'t be used together with block_hash or timestamp parameter.',
                    'required': False,
                    'schema': {
                        'type': 'int'
                    }
                },
                {
                    'name': 'block_hash',
                    'in': 'query',
                    'description': 'Hash of the block to get the nano contract state from.'
                                   'Can\'t be used together with block_height or timestamp parameter.',
                    'required': False,
                    'schema': {
                        'type': 'string'
                    }
                },
                {
                    'name': 'timestamp',
                    'in': 'query',
                    'description': 'Timestamp to get the nano contract state from.'
                                   'Can\'t be used together with block_hash or block_height parameter.',
                    'required': False,
                    'schema': {
                        'type': 'int'
                    }
                },
            ],
            'responses': {
                '200': {
                    'description': 'Success',
                    'content': {
                        'application/json': {
                            'examples': {
                                'success': {
                                    'summary': 'Success to get state from nano',
                                    'value': _openapi_success_value,
                                },
                                'error': {
                                    'summary': 'Invalid nano contract ID',
                                    'value': {
                                        'success': False,
                                        'message': 'Invalid nano contract ID.'
                                    }
                                },
                            }
                        }
                    }
                }
            }
        }
    }
}


# OpenAPI documentation for state history endpoint
_openapi_success_history_value = {
    'success': True,
    'nc_id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
    'interval': '1h',
    'start_time': 1640995200,
    'end_time': 1640998800,
    'states': [
        {
            'timestamp': 1640995200,
            'success': True,
            'nc_id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
            'blueprint_id': '3cb032600bdf7db784800e4ea911b10676fa2f67591f82bb62628c234e771595',
            'blueprint_name': 'Bet',
            'fields': {
                'token_uid': {'value': '00'},
                'total': {'value': 100},
            },
            'balances': {},
            'calls': {}
        },
        {
            'timestamp': 1640998800,
            'success': True,
            'nc_id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
            'blueprint_id': '3cb032600bdf7db784800e4ea911b10676fa2f67591f82bb62628c234e771595',
            'blueprint_name': 'Bet',
            'fields': {
                'token_uid': {'value': '00'},
                'total': {'value': 300},
            },
            'balances': {},
            'calls': {}
        }
    ]
}


NanoContractStateHistoryResource.openapi = {
    '/nano_contract/state_history': {
        'x-visibility': 'public',
        'x-rate-limit': {
            'global': [
                {
                    'rate': '10r/s',
                    'burst': 20,
                    'delay': 10
                }
            ],
            'per-ip': [
                {
                    'rate': '2r/s',
                    'burst': 5,
                    'delay': 3
                }
            ]
        },
        'get': {
            'tags': ['nano_contracts'],
            'operationId': 'nano_contracts_state_history',
            'summary': 'Get historical state of a nano contract at intervals',
            'description': 'Returns the historical state of a nano contract at specified time intervals, similar to k-lines data.',
            'parameters': [
                {
                    'name': 'id',
                    'in': 'query',
                    'description': 'ID of the nano contract to get the state history from',
                    'required': True,
                    'schema': {
                        'type': 'string'
                    }
                },
                {
                    'name': 'start_time',
                    'in': 'query',
                    'description': 'Start timestamp (Unix timestamp in seconds)',
                    'required': True,
                    'schema': {
                        'type': 'integer'
                    }
                },
                {
                    'name': 'end_time',
                    'in': 'query',
                    'description': 'End timestamp (Unix timestamp in seconds)',
                    'required': True,
                    'schema': {
                        'type': 'integer'
                    }
                },
                {
                    'name': 'interval',
                    'in': 'query',
                    'description': 'Time interval between data points. Supported: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w',
                    'required': True,
                    'schema': {
                        'type': 'string',
                        'enum': ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w']
                    }
                },
                {
                    'name': 'fields[]',
                    'in': 'query',
                    'description': 'Fields to get the data from the nano contract state',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    }
                },
                {
                    'name': 'balances[]',
                    'in': 'query',
                    'description': 'List of token ids in hex to get the contract balance. Use __all__ for all tokens.',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    }
                },
                {
                    'name': 'calls[]',
                    'in': 'query',
                    'description': 'List of view method calls to be executed',
                    'required': False,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    }
                },
                {
                    'name': 'limit',
                    'in': 'query',
                    'description': 'Maximum number of data points to return (default: 1000, max: 1000)',
                    'required': False,
                    'schema': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': 1000
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
                                    'summary': 'Success to get historical state from nano contract',
                                    'value': _openapi_success_history_value,
                                },
                                'error': {
                                    'summary': 'Invalid interval',
                                    'value': {
                                        'success': False,
                                        'message': 'Invalid interval: 2h. Supported: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w'
                                    }
                                },
                            }
                        }
                    }
                }
            }
        }
    }
}

# OpenAPI documentation for price history endpoint
_openapi_success_price_history_value = {
    'success': True,
    'nc_id': '00007f246f6d645ef3174f2eddf53f4b6bd41e8be0c0b7fbea9827cf53e12d9e',
    'interval': '1h',
    'start_time': 1640995200,
    'end_time': 1640998800,
    'candles': [
        {
            'timestamp': 1640995200,
            'get_token_price_in_usd(0x123456)': {
                'open': 1.2345,
                'high': 1.2580,
                'low': 1.2100,
                'close': 1.2450
            },
            'get_token_price_in_htr(0x123456)': {
                'open': 0.0125,
                'high': 0.0128,
                'low': 0.0121,
                'close': 0.0126
            }
        },
        {
            'timestamp': 1640998800,
            'get_token_price_in_usd(0x123456)': {
                'open': 1.2450,
                'high': 1.2600,
                'low': 1.2200,
                'close': 1.2380
            },
            'get_token_price_in_htr(0x123456)': {
                'open': 0.0126,
                'high': 0.0130,
                'low': 0.0122,
                'close': 0.0124
            }
        }
    ]
}


NanoContractPriceHistoryResource.openapi = {
    '/nano_contract/price_history': {
        'x-visibility': 'public',
        'x-rate-limit': {
            'global': [
                {
                    'rate': '5r/s',
                    'burst': 10,
                    'delay': 10
                }
            ],
            'per-ip': [
                {
                    'rate': '1r/s',
                    'burst': 3,
                    'delay': 5
                }
            ]
        },
        'get': {
            'tags': ['nano_contracts'],
            'operationId': 'nano_contracts_price_history',
            'summary': 'Get OHLC price history of a nano contract',
            'description': 'Returns OHLC (Open, High, Low, Close) price data for DozerPoolManager price methods at specified time intervals.',
            'parameters': [
                {
                    'name': 'id',
                    'in': 'query',
                    'description': 'ID of the DozerPoolManager nano contract',
                    'required': True,
                    'schema': {
                        'type': 'string'
                    }
                },
                {
                    'name': 'start_time',
                    'in': 'query',
                    'description': 'Start timestamp (Unix timestamp in seconds)',
                    'required': True,
                    'schema': {
                        'type': 'integer'
                    }
                },
                {
                    'name': 'end_time',
                    'in': 'query',
                    'description': 'End timestamp (Unix timestamp in seconds)',
                    'required': True,
                    'schema': {
                        'type': 'integer'
                    }
                },
                {
                    'name': 'interval',
                    'in': 'query',
                    'description': 'Time interval for OHLC candles. Supported: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w',
                    'required': True,
                    'schema': {
                        'type': 'string',
                        'enum': ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w']
                    }
                },
                {
                    'name': 'calls[]',
                    'in': 'query',
                    'description': 'Price method calls to calculate OHLC for. Only get_token_price_in_usd and get_token_price_in_htr are supported.',
                    'required': True,
                    'schema': {
                        'type': 'array',
                        'items': {
                            'type': 'string'
                        }
                    },
                    'examples': {
                        'price_methods': {
                            'summary': 'Example price method calls',
                            'value': ['get_token_price_in_usd(0x123456)', 'get_token_price_in_htr(0x123456)']
                        },
                    }
                },
                {
                    'name': 'limit',
                    'in': 'query',
                    'description': 'Maximum number of candles to return (default: 100, max: 500)',
                    'required': False,
                    'schema': {
                        'type': 'integer',
                        'minimum': 1,
                        'maximum': 500
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
                                    'summary': 'Success to get OHLC price history',
                                    'value': _openapi_success_price_history_value,
                                },
                                'error': {
                                    'summary': 'Invalid price method',
                                    'value': {
                                        'success': False,
                                        'message': 'Only price methods allowed: get_token_price_in_usd, get_token_price_in_htr'
                                    }
                                },
                            }
                        }
                    }
                }
            }
        }
    }
}