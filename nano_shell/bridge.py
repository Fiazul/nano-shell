"""Authenticated loopback inference broker. HTTP never executes commands."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

from . import backends, policy

MAX_BODY = 65536
STATES = {'unavailable', 'downloadable', 'downloading', 'available', 'initializing', 'ready', 'error'}


class BridgeError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class Broker:
    def __init__(self, timeout):
        self.timeout = timeout
        self.condition = threading.Condition()
        self.jobs = {}
        self.queue = deque()
        self.state = 'unavailable'
        self.detail = 'Open setup and initialize the Chrome worker'
        self.last_seen = 0.0

    def status(self):
        with self.condition:
            connected = time.monotonic() - self.last_seen < 15
            return {'worker': self.state if connected else 'disconnected',
                    'ready': connected and self.state == 'ready', 'detail': self.detail if connected else 'Chrome worker is disconnected'}

    def update(self, state, detail):
        if state not in STATES or not isinstance(detail, str) or len(detail) > 2048:
            raise BridgeError('invalid worker status', 400)
        with self.condition:
            self.state, self.detail, self.last_seen = state, detail, time.monotonic()
            if state != 'ready':
                for job in self.jobs.values():
                    job['error'] = detail or 'Chrome worker is not ready'
                self.condition.notify_all()

    def generate(self, prompt):
        with self.condition:
            if not self.status()['ready']:
                raise BridgeError(self.status()['detail'])
            if len(self.jobs) >= 8:
                raise BridgeError('inference queue is full', 429)
            identifier = secrets.token_urlsafe(18)
            job = {'id': identifier, 'prompt': prompt, 'delivered': False}
            self.jobs[identifier] = job
            self.queue.append(identifier)
            self.condition.notify_all()
            deadline = time.monotonic() + self.timeout
            try:
                while 'text' not in job and 'error' not in job:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BridgeError('Nano inference timed out', 504)
                    self.condition.wait(remaining)
                if 'error' in job:
                    raise BridgeError(job['error'], 502)
                return job['text']
            finally:
                self.jobs.pop(identifier, None)
                try:
                    self.queue.remove(identifier)
                except ValueError:
                    pass

    def poll(self):
        with self.condition:
            self.last_seen = time.monotonic()
            deadline = time.monotonic() + 1
            while not self.queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.condition.wait(remaining)
            identifier = self.queue.popleft()
            job = self.jobs.get(identifier)
            if job is None:
                return None
            job['delivered'] = True
            return {'id': identifier, 'prompt': job['prompt']}

    def complete(self, data):
        identifier = data.get('id')
        if not isinstance(identifier, str):
            raise BridgeError('invalid result identifier', 400)
        with self.condition:
            job = self.jobs.get(identifier)
            if not job or not job['delivered'] or 'text' in job or 'error' in job:
                raise BridgeError('unknown, expired or completed inference job', 409)
            if set(data) == {'id', 'text'} and isinstance(data['text'], str) and len(data['text']) <= 8192:
                job['text'] = data['text']
            elif set(data) == {'id', 'error'} and isinstance(data['error'], str) and 0 < len(data['error']) <= 2048:
                job['error'] = data['error']
            else:
                raise BridgeError('invalid inference result', 400)
            self.last_seen = time.monotonic()
            self.condition.notify_all()


def validate_context(data):
    if not isinstance(data, dict) or set(data) - {'question', 'cwd', 'entries', 'history'}:
        raise BridgeError('invalid question context', 400)
    question = data.get('question')
    if not isinstance(question, str) or not question.strip() or len(question) > 4096:
        raise BridgeError('question must contain 1 to 4096 characters', 400)
    cwd = data.get('cwd', '')
    entries = data.get('entries', [])
    if not isinstance(cwd, str) or len(cwd) > 4096 or not isinstance(entries, list) or len(entries) > 40 or any(not isinstance(entry, str) or len(entry) > 256 for entry in entries):
        raise BridgeError('invalid directory context', 400)
    if 'history' in data and (not isinstance(data['history'], str) or len(data['history']) > 4096):
        raise BridgeError('invalid opt-in history', 400)
    return data


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'NanoShell'

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *_args):
        pass  # Tokens, questions and context must not appear in HTTP logs.

    def reply(self, status, data, content_type='application/json'):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def check_request(self, authentication=True):
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if len(self.headers.get_all('Host', [])) != 1 or self.headers.get('Host') not in hosts:
            raise BridgeError('invalid Host', 403)
        origins = self.headers.get_all('Origin', [])
        if len(origins) > 1 or (origins and origins[0] != 'http://' + self.headers['Host']):
            raise BridgeError('foreign Origin is forbidden', 403)
        if self.headers.get('Sec-Fetch-Site') in {'cross-site', 'same-site'}:
            raise BridgeError('foreign site is forbidden', 403)
        if authentication:
            authorization = self.headers.get('Authorization', '')
            if len(self.headers.get_all('Authorization', [])) != 1 or not hmac.compare_digest(authorization.encode(), ('Bearer ' + self.server.token).encode()):
                raise BridgeError('invalid bridge credentials', 401)

    def body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or self.headers.get('Transfer-Encoding') is not None:
            raise BridgeError('Content-Length is required; streaming requests are unsupported', 400)
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise BridgeError('invalid Content-Length', 400) from exc
        if length < 0 or length > MAX_BODY:
            raise BridgeError('request body exceeds 65536 bytes', 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BridgeError('incomplete request body', 400)
        try:
            result = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise BridgeError('invalid JSON request', 400) from exc
        if not isinstance(result, dict):
            raise BridgeError('request must be an object', 400)
        return result

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if self.path != path:
                raise BridgeError('query parameters are unsupported', 400)
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/worker.js': ('worker.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
            self.check_request(authentication=path not in assets)
            if path in assets:
                name, content_type = assets[path]
                raw = (Path(__file__).resolve().parent.parent / 'web' / name).read_bytes()
                self.reply(200, raw, content_type)
            elif path == '/status':
                config = self.server.config_provider()
                status = self.server.broker.status()
                status.update({'daemon': 'running', 'backend': config['backend'], 'model': config['model']})
                self.reply(200, status)
            elif path == '/worker/poll':
                job = self.server.broker.poll()
                self.reply(200 if job else 204, job if job else b'')
            else:
                self.reply(404, {'error': 'unknown inference route'})
        except BridgeError as exc:
            self.reply(exc.status, {'error': str(exc)})
        except (OSError, ValueError) as exc:
            self.reply(500, {'error': str(exc)})

    def do_POST(self):
        try:
            self.check_request()
            data = self.body()
            if self.path == '/generate':
                context = validate_context(data)
                config = self.server.config_provider()
                prompt = backends.prompt_for(context)
                if config['backend'] == 'nano':
                    raw = self.server.broker.generate(prompt)
                else:
                    raw = backends.ollama_generate(prompt, config['model'], self.server.broker.timeout)
                self.reply(200, policy.parse_generation(raw))
            elif self.path == '/worker/status':
                if set(data) - {'state', 'detail'}:
                    raise BridgeError('invalid worker status fields', 400)
                self.server.broker.update(data.get('state'), data.get('detail', ''))
                self.reply(200, {'accepted': True})
            elif self.path == '/worker/result':
                self.server.broker.complete(data)
                self.reply(200, {'accepted': True})
            elif self.path == '/shutdown':
                if data:
                    raise BridgeError('shutdown body must be empty', 400)
                self.reply(200, {'stopping': True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            else:
                self.reply(404, {'error': 'unknown inference route'})
        except BridgeError as exc:
            self.reply(exc.status, {'error': str(exc)})
        except (backends.BackendError, policy.PolicyError) as exc:
            self.reply(502, {'error': str(exc)})
        except (OSError, ValueError) as exc:
            self.reply(500, {'error': str(exc)})


def make_server(port=8765, token='', timeout=30, config_provider=None):
    if not token or len(token) < 40:
        raise ValueError('a strong bridge token is required')
    if not 0 < timeout <= 60:
        raise ValueError('inference timeout must be between 0 and 60 seconds')
    server = LocalServer(('127.0.0.1', port), Handler)
    server.token = token
    server.broker = Broker(timeout)
    server.config_provider = config_provider or (lambda: {'backend': 'nano', 'model': ''})
    return server
