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

from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field

from hathor.api_util import Resource, set_cors
from hathor.cli.openapi_files.register import register_resource
from hathor.nanocontracts.exception import NCContractCreationNotFound
from hathor.nanocontracts.utils import get_nano_contract_creation
from hathor.utils.api import ErrorResponse, QueryParams, Response

if TYPE_CHECKING:
    from twisted.web.http import Request

    from hathor.manager import HathorManager


@register_resource
class NanoContractHistoryResource(Resource):
    """ Implements a web server GET API to get a nano contract history.
    You must run with option `--status <PORT>`.
    """
    isLeaf = True

    def __init__(self, manager: 'HathorManager'):
        self.manager = manager

    def render_GET(self, request: 'Request') -> bytes:
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'GET')

        tx_storage = self.manager.tx_storage
        assert tx_storage.indexes is not None
        if tx_storage.indexes.nc_history is None:
            request.setResponseCode(503)
            error_response = ErrorResponse(success=False, error='Nano contract history index not initialized')
            return error_response.json_dumpb()

        params = NCHistoryParams.from_request(request)
        if isinstance(params, ErrorResponse):
            request.setResponseCode(400)
            return params.json_dumpb()

        try:
            nc_id_bytes = bytes.fromhex(params.id)
        except ValueError:
            request.setResponseCode(400)
            error_response = ErrorResponse(success=False, error=f'Invalid id: {params.id}')
            return error_response.json_dumpb()

        # Check if the contract exists.
        try:
            get_nano_contract_creation(self.manager.tx_storage, nc_id_bytes)
        except NCContractCreationNotFound:
            request.setResponseCode(404)
            error_response = ErrorResponse(success=False, error=f'Nano contract not found: {params.id}')
            return error_response.json_dumpb()

        iter_history = tx_storage.indexes.nc_history.get_sorted_from_contract_id(nc_id_bytes)

        after_bytes: Optional[bytes] = None
        if params.after:
            after_bytes = bytes.fromhex(params.after)
            for tx_id in iter_history:
                if tx_id == after_bytes:
                    break

        count = params.count
        history_list = []
        for idx, tx_id in enumerate(iter_history):
            history_list.append(tx_storage.get_transaction(tx_id).to_json())
            if idx >= count - 1:
                break

        response = NCHistoryResponse(
            success=True,
            count=count,
            after=params.after,
            history=history_list,
        )
        return response.json_dumpb()


class NCHistoryParams(QueryParams):
    id: str
    after: Optional[str]
    count: int = Field(default=100, lt=500)


class NCHistoryResponse(Response):
    success: bool
    count: int
    after: Optional[str]
    history: list[dict[str, Any]]


openapi_history_response = {
    'hash': '5c02adea056d7b43e83171a0e2d226d564c791d583b32e9a404ef53a2e1b363a',
    'nonce': 0,
    'timestamp': 1572636346,
    'version': 4,
    'weight': 1,
    'signal_bits': 0,
    'parents': ['1234', '5678'],
    'inputs': [],
    'outputs': [],
    'metadata': {
        'hash': '5c02adea056d7b43e83171a0e2d226d564c791d583b32e9a404ef53a2e1b363a',
        'spent_outputs': [],
        'received_by': [],
        'children': [],
        'conflict_with': [],
        'voided_by': [],
        'twins': [],
        'accumulated_weight': 1,
        'score': 0,
        'height': 0,
        'min_height': 0,
        'feature_activation_bit_counts': None,
        'first_block': None,
        'validation': 'full'
    },
    'tokens': [],
    'nc_id': '5c02adea056d7b43e83171a0e2d226d564c791d583b32e9a404ef53a2e1b363a',
    'nc_method': 'initialize',
    'nc_args': '0004313233340001000004654d8749',
    'nc_pubkey': '033f5d238afaa9e2218d05dd7fa50eb6f9e55431e6359e04b861cd991ae24dc655'
}


NanoContractHistoryResource.openapi = {
    '/nano_contract/history': {
        'x-visibility': 'public',
        'x-rate-limit': {
            'global': [
                {
                    'rate': '3r/s',
                    'burst': 10,
                    'delay': 3
                }
            ],
            'per-ip': [
                {
                    'rate': '1r/s',
                    'burst': 4,
                    'delay': 2
                }
            ]
        },
        'get': {
            'tags': ['nano_contracts'],
            'operationId': 'nano_contracts_history',
            'summary': 'Get history of a nano contract',
            'description': 'Returns the history of a nano contract.',
            'parameters': [
                {
                    'name': 'id',
                    'in': 'query',
                    'description': 'ID of the nano contract to get the history from.',
                    'required': True,
                    'schema': {
                        'type': 'string'
                    }
                }, {
                    'name': 'count',
                    'in': 'query',
                    'description': 'Maximum number of items to be returned. Default is 100.',
                    'required': False,
                    'schema': {
                        'type': 'int',
                    }
                }, {
                    'name': 'after',
                    'in': 'query',
                    'description': 'Hash of transaction to offset the result.',
                    'required': False,
                    'schema': {
                        'type': 'string',
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
                                    'summary': 'History of a nano contract',
                                    'value': {
                                        'success': True,
                                        'count': 100,
                                        'history': [openapi_history_response],
                                    }
                                },
                                'error': {
                                    'summary': 'Nano contract history index not initialized.',
                                    'value': {
                                        'success': False,
                                        'message': 'Nano contract history index not initialized.'
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