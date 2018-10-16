from twisted.internet.defer import succeed
from twisted.web import server
from twisted.web.test.requesthelper import DummyRequest
from twisted.internet.task import Clock

from hathor.p2p.peer_id import PeerId
from hathor.manager import HathorManager

from tests import unittest
import json
import time


class _BaseResourceTest:
    class _ResourceTest(unittest.TestCase):
        def setUp(self):
            super().setUp()

            peer_id = PeerId()
            wallet = self.create_test_wallet()
            self.reactor = Clock()
            self.reactor.advance(time.time())
            network = 'testnet'
            self.manager = HathorManager(self.reactor, peer_id=peer_id, network=network, wallet=wallet)
            self.manager.start()


class RequestBody(object):
    """
    Dummy request body object to represent content
    """
    def __init__(self):
        self.content = None

    def setvalue(self, value):
        self.content = value

    def read(self):
        return self.content


class TestDummyRequest(DummyRequest):
    def __init__(self, method, url, args=None, headers=None):
        DummyRequest.__init__(self, url.split('/'))
        self.method = method
        self.headers = headers or {}
        self.content = RequestBody()

        # Set request args
        args = args or {}
        for k, v in args.items():
            self.addArg(k, v)

    def json_value(self):
        return json.loads(self.written[0].decode('utf-8'))


class TestSite(server.Site):
    def get(self, url, args=None, headers=None):
        return self._request('GET', url, args, headers)

    def post(self, url, args=None, headers=None):
        return self._request('POST', url, args, headers)

    def _request(self, method, url, args, headers):
        request = TestDummyRequest(method, url, args, headers)
        if method == 'POST' and args:
            # Creating post content exactly the same as twisted resource
            request.content.setvalue(bytes(json.dumps(args), 'utf-8'))
        resource = self.getResourceFor(request)
        result = resource.render(request)
        return self._resolveResult(request, result)

    def _resolveResult(self, request, result):
        if isinstance(result, bytes):
            request.write(result)
            request.finish()
            return succeed(request)
        elif result is server.NOT_DONE_YET:
            if request.finished:
                return succeed(request)
            else:
                return request.notifyFinish().addCallback(lambda _: request)
        else:
            raise ValueError('Unexpected return value: %r' % (result,))
