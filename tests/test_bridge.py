import concurrent.futures
import http.client
import json
import threading
import unittest

try:
    from nano_shell import bridge
except ImportError:
    bridge = None


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(bridge, 'bridge implementation is missing')
        try:
            self.server = bridge.make_server(port=0, token='t' * 64, timeout=0.25)
        except PermissionError:
            self.skipTest('sandbox denies socket creation; in-memory Handler tests cover protocol')
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()

    def request(self, path, body=None, headers=None, method=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        defaults = {'Authorization': 'Bearer ' + 't' * 64}
        defaults.update(headers or {})
        data = json.dumps(body) if body is not None else None
        conn.request(method or ('POST' if body is not None else 'GET'), path, data, defaults)
        response = conn.getresponse()
        result = response.status, response.read()
        conn.close()
        return result

    def test_auth_host_origin_and_no_execution_route(self):
        self.assertEqual(self.request('/status')[0], 200)
        self.assertEqual(self.request('/status', headers={'Authorization': 'Bearer stale'})[0], 401)
        self.assertEqual(self.request('/status', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/status', headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/status', headers={'Origin': 'null'})[0], 403)
        self.assertEqual(self.request('/execute', {'command': 'pwd'})[0], 404)

    def test_root_assets_require_loopback_host_and_no_remote_origin(self):
        self.assertEqual(self.request('/')[0], 200)
        self.assertEqual(self.request('/worker.js')[0], 200)
        self.assertEqual(self.request('/', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/', headers={'Origin': 'https://evil.example'})[0], 403)

    def test_invalid_and_oversized_generation_rejected(self):
        self.assertEqual(self.request('/generate', {'question': 7})[0], 400)
        self.assertEqual(self.request('/generate', {'question': 'x' * 70000})[0], 413)
        self.assertEqual(self.request('/worker/status', {'state': 'fake-success'})[0], 400)

    def test_browser_unavailable_and_timeout_are_errors(self):
        self.assertEqual(self.request('/generate', {'question': 'where'})[0], 503)
        self.assertEqual(self.request('/worker/status', {'state': 'ready'})[0], 200)
        self.assertEqual(self.request('/generate', {'question': 'where'})[0], 504)
        self.assertEqual(self.request('/worker/result', {'id': 'stale', 'text': '{}'})[0], 409)

    def test_authenticated_browser_generation_roundtrip(self):
        self.request('/worker/status', {'state': 'ready'})
        with concurrent.futures.ThreadPoolExecutor() as pool:
            waiting = pool.submit(self.request, '/generate', {'question': 'location', 'cwd': '/tmp', 'entries': []})
            status, body = self.request('/worker/poll')
            self.assertEqual(status, 200)
            job = json.loads(body)
            self.assertIn('location', job['prompt'])
            self.assertEqual(self.request('/worker/result', {'id': job['id'], 'text': '{"command":"pwd"}'})[0], 200)
            result_status, result_body = waiting.result()
        self.assertEqual(result_status, 200)
        self.assertEqual(json.loads(result_body)['command'], 'pwd')

    def test_model_error_and_malicious_output_propagate(self):
        for response in [{'error': 'model failed'}, {'text': '{"command":"rm -rf ."}'}]:
            self.request('/worker/status', {'state': 'ready'})
            with concurrent.futures.ThreadPoolExecutor() as pool:
                waiting = pool.submit(self.request, '/generate', {'question': 'anything'})
                _, body = self.request('/worker/poll')
                self.request('/worker/result', dict(response, id=json.loads(body)['id']))
                self.assertEqual(waiting.result()[0], 502)


if __name__ == '__main__':
    unittest.main()


class InMemoryBridgeTests(BridgeTests):
    """Run the real HTTP handler and broker without permission to create sockets."""
    def setUp(self):
        self.assertIsNotNone(bridge, 'bridge implementation is missing')
        from types import SimpleNamespace
        self.port = 8765
        self.server = SimpleNamespace(server_address=('127.0.0.1', self.port), token='t' * 64,
            broker=bridge.Broker(0.25), config_provider=lambda: {'backend': 'nano', 'model': ''})

    def tearDown(self):
        pass

    def request(self, path, body=None, headers=None, method=None):
        import io
        class Socket:
            def __init__(self, raw):
                self.input = io.BytesIO(raw)
                self.output = bytearray()
            def makefile(self, *_args):
                return self.input
            def settimeout(self, _timeout):
                pass
            def sendall(self, raw):
                self.output.extend(raw)
        defaults = {'Host': '127.0.0.1:' + str(self.port), 'Authorization': 'Bearer ' + 't' * 64}
        defaults.update(headers or {})
        data = json.dumps(body).encode() if body is not None else b''
        if body is not None:
            defaults['Content-Length'] = str(len(data))
        verb = method or ('POST' if body is not None else 'GET')
        raw = f'{verb} {path} HTTP/1.1\r\n'.encode()
        raw += ''.join(f'{key}: {value}\r\n' for key, value in defaults.items()).encode() + b'\r\n' + data
        socket = Socket(raw)
        bridge.Handler(socket, ('127.0.0.1', 1234), self.server)
        head, result = bytes(socket.output).split(b'\r\n\r\n', 1)
        return int(head.split(b' ')[1]), result

    def test_disconnect_freshness_is_honest(self):
        import time
        self.server.broker.update('ready', 'ready')
        self.server.broker.last_seen = time.monotonic() - 16
        status, raw = self.request('/status')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['worker'], 'disconnected')
        self.assertFalse(json.loads(raw)['ready'])

    def test_result_before_delivery_is_rejected(self):
        self.server.broker.jobs['id'] = {'id': 'id', 'delivered': False}
        self.assertEqual(self.request('/worker/result', {'id': 'id', 'text': '{}'})[0], 409)
