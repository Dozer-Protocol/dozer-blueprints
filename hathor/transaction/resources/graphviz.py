from twisted.internet import threads
from twisted.web import resource
from twisted.web.http import Request

from hathor import graphviz
from hathor.api_util import set_cors
from hathor.cli.openapi_files.register import register_resource


@register_resource
class GraphvizResource(resource.Resource):
    """ Implements a web server API that returns a visualization of the DAG using Graphviz.

    You must run with option `--status <PORT>`.
    """
    isLeaf = True

    def __init__(self, manager):
        # Important to have the manager so we can know the tx_storage
        self.manager = manager

    def _render_GET_thread(self, request: Request) -> bytes:
        """ GET request /graphviz/
            Expects 'format' parameter in request to set the content-type of the graph
            Format options are 'pdf', 'png' and 'jpg'. Default format is 'pdf'
            Returns the file
        """
        set_cors(request, 'GET')

        contenttype = {
            'pdf': b'application/pdf',
            'png': b'image/png',
            'jpg': b'image/jpeg',
            'dot': b'application/dot',
        }

        dotformat = 'pdf'
        if b'format' in request.args:
            dotformat = request.args[b'format'][0].decode('utf-8')

        weight = False
        if b'weight' in request.args:
            weight = self.parseBoolArg(request.args[b'weight'][0].decode('utf-8'))

        acc_weight = False
        if b'acc_weight' in request.args:
            acc_weight = self.parseBoolArg(request.args[b'acc_weight'][0].decode('utf-8'))

        funds = False
        if b'funds' in request.args:
            funds = self.parseBoolArg(request.args[b'funds'][0].decode('utf-8'))

        tx_storage = self.manager.tx_storage
        if not funds:
            dot = graphviz.verifications(tx_storage, format=dotformat, weight=weight, acc_weight=acc_weight)
        else:
            dot = graphviz.funds(tx_storage, format=dotformat, weight=weight, acc_weight=acc_weight)

        if dotformat == 'dot':
            request.setHeader(b'content-type', contenttype[dotformat])
            return str(dot).encode('utf-8')

        request.setHeader(b'content-type', contenttype[dotformat])
        return dot.pipe()

    def render_GET(self, request):
        deferred = threads.deferToThread(self._render_GET_thread, request)
        deferred.addCallback(self._cb_tx_resolve, request)
        deferred.addErrback(self._err_tx_resolve, request)

        from twisted.web.server import NOT_DONE_YET
        return NOT_DONE_YET

    def _cb_tx_resolve(self, result, request):
        """ Called when `_render_GET_thread` finishes
        """
        request.write(result)
        request.finish()

    def _err_tx_resolve(self, reason, request):
        """ Called when an error occur in `_render_GET_thread`
        """
        request.processingFailed(reason)

    def parseBoolArg(self, arg: str) -> bool:
        """Returns a boolean object for the given parameter

        :rtype: bool
        """
        if not arg:
            return False
        if arg in ['false', 'False', '0']:
            return False

        return True


GraphvizResource.openapi = {
    '/graphviz': {
        'get': {
            'tags': ['transaction'],
            'operationId': 'graphviz',
            'summary': 'Dashboard of transactions',
            'description': 'Returns the generated file with the graph in the format requested',
            'parameters': [
                {
                    'name': 'format',
                    'in': 'query',
                    'description': 'Format of the returned file',
                    'required': True,
                    'schema': {
                        'type': 'string'
                    }
                },
                {
                    'name': 'weight',
                    'in': 'query',
                    'description': 'If we will show the weight',
                    'required': False,
                    'schema': {
                        'type': 'boolean'
                    }
                },
                {
                    'name': 'acc_weight',
                    'in': 'query',
                    'description': 'If we will show the accumulated weight',
                    'required': False,
                    'schema': {
                        'type': 'boolean'
                    }
                },
                {
                    'name': 'funds',
                    'in': 'query',
                    'description': 'If we will generate the network graph or the funds graph',
                    'required': False,
                    'schema': {
                        'type': 'boolean'
                    }
                }
            ],
            'responses': {
                '200': {
                    'description': 'Success'
                }
            }
        }
    }
}
