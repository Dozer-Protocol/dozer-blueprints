from twisted.web import resource
from hathor.api_util import set_cors, render_options

import json


class LockWalletResource(resource.Resource):
    """ Implements a web server API with POST to lock the wallet

    You must run with option `--status <PORT>`.
    """
    isLeaf = True

    def __init__(self, wallet):
        self.wallet = wallet

    def render_POST(self, request):
        """ Lock the wallet

            :return: Boolean if the user locked the wallet with success
            :rtype: string (json) Dict['success', bool]
        """
        request.setHeader(b'content-type', b'application/json; charset=utf-8')
        set_cors(request, 'POST')

        self.wallet.lock()

        ret = {'success': True}
        return json.dumps(ret, indent=4).encode('utf-8')

    def render_OPTIONS(self, request):
        return render_options(request)
