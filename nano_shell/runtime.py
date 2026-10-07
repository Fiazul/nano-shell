"""Private, serialized Linux Ollama lifecycle; no question or response logs."""
from contextlib import contextmanager
import errno
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from urllib.error import URLError

from . import backends, policy, storage


class RuntimeError(Exception):
    pass


START_TIMEOUT = 20
STOP_TIMEOUT = 5
LOCK_TIMEOUT = 30
_children = {}


def endpoint():
    try:
        port = int(os.environ.get('NANO_SHELL_OLLAMA_PORT', '11435'))
    except ValueError as exc:
        raise RuntimeError('NANO_SHELL_OLLAMA_PORT must be an integer') from exc
    if not 1024 <= port <= 65535:
        raise RuntimeError('NANO_SHELL_OLLAMA_PORT must be between 1024 and 65535')
    return f'http://127.0.0.1:{port}'


def binary():
    override = os.environ.get('NANO_SHELL_OLLAMA_BIN')
    if override:
        candidate = Path(override).expanduser().resolve()
    else:
        installed = Path(__file__).resolve().parent.parent / 'runtime' / 'bin' / 'ollama'
        candidate = installed if installed.is_file() and os.access(installed, os.X_OK) else Path(shutil.which('ollama') or '/nonexistent/ollama')
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise RuntimeError('installation is incomplete: Ollama runtime is missing; reinstall Nano Shell')
    return str(candidate.resolve())


def environment():
    models = storage.state_dir() / 'models'
    if models.is_symlink():
        raise storage.StorageError('model directory must not be a symbolic link')
    models.mkdir(mode=0o700, exist_ok=True)
    if models.stat().st_uid != os.getuid():
        raise storage.StorageError('model directory is owned by another user')
    models.chmod(0o700)
    env = os.environ.copy()
    for key in tuple(env):
        if key.lower() in {'http_proxy', 'https_proxy', 'all_proxy'} or key.startswith('OLLAMA_'):
            env.pop(key)
    env.update(OLLAMA_HOST=endpoint().removeprefix('http://'), OLLAMA_MODELS=str(models),
               OLLAMA_NO_CLOUD='1', OLLAMA_DEBUG='0', NO_PROXY='127.0.0.1,localhost')
    return env


@contextmanager
def _lock():
    path = storage.state_dir() / 'runtime.lock'
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise storage.StorageError('runtime lock must be a private regular file')
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('runtime is busy; startup lock timed out')
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def _record_path():
    return storage.state_dir() / 'runtime.json'


def _identity(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    process = _children.get(pid)
    if process is not None and process.poll() is not None:
        _children.pop(pid, None)
        return None
    path = Path('/proc') / str(pid)
    try:
        if path.stat().st_uid != os.getuid():
            return None
        fields = (path / 'stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return {'start': fields[19], 'exe': os.readlink(path / 'exe'),
                'argv': (path / 'cmdline').read_bytes().split(b'\0')[:-1],
                'env': (path / 'environ').read_bytes().split(b'\0')}
    except (OSError, ValueError, IndexError):
        return None


def _matches(record):
    if not isinstance(record, dict):
        return False
    identity = _identity(record.get('pid'))
    if identity is None or identity['start'] != record.get('start') or identity['exe'] != record.get('exe'):
        return False
    executable = record.get('binary')
    address = record.get('endpoint')
    if not isinstance(executable, str) or address != endpoint():
        return False
    if executable.encode() not in identity['argv'] or b'serve' not in identity['argv']:
        return False
    required = [b'OLLAMA_NO_CLOUD=1', ('OLLAMA_HOST=' + address.removeprefix('http://')).encode(),
                ('OLLAMA_MODELS=' + str(storage.state_dir() / 'models')).encode()]
    return all(value in identity['env'] for value in required)


def _owned_record():
    path = _record_path()
    if not path.exists() and not path.is_symlink():
        return None
    try:
        record = json.loads(storage._read_private(path))
    except (ValueError, RecursionError) as exc:
        raise RuntimeError('invalid runtime ownership record') from exc
    return record if _matches(record) else None


def _refused(error):
    cause = error.__cause__
    return (isinstance(cause, URLError) and isinstance(cause.reason, OSError)
            and cause.reason.errno == errno.ECONNREFUSED)


def _inventory(timeout=2):
    response = backends.local_request(endpoint() + '/api/tags', timeout=timeout)
    models = response.get('models')
    if not isinstance(models, list) or any(not isinstance(model, dict) or not isinstance(model.get('name'), str) for model in models):
        raise RuntimeError('invalid Ollama model inventory')
    return {model['name'] for model in models}


def _installed(model, names):
    return model in names or (':' not in model and model + ':latest' in names)


def _signal_owned(record, signum):
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        raise RuntimeError('safe owned shutdown requires Linux pidfd support')
    try:
        descriptor = os.pidfd_open(record['pid'])
    except ProcessLookupError:
        return
    try:
        if _matches(record):
            signal.pidfd_send_signal(descriptor, signum)
    finally:
        os.close(descriptor)


def _terminate(record):
    _signal_owned(record, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while _matches(record) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _matches(record):
        _signal_owned(record, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while _matches(record) and time.monotonic() < deadline:
            time.sleep(0.05)
    if _matches(record):
        raise RuntimeError('owned runtime did not stop')


def _ready(record):
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if not _matches(record):
            process = _children.get(record['pid'])
            code = process.poll() if process is not None else None
            raise RuntimeError(f'Ollama runtime exited before readiness (exit {code})')
        try:
            _inventory(timeout=0.5)
            if not _matches(record):
                raise RuntimeError('owned runtime exited during readiness')
            return {'running': True, 'pid': record['pid'], 'endpoint': endpoint()}
        except backends.BackendError as exc:
            if not _refused(exc):
                raise
        time.sleep(0.05)
    raise RuntimeError('Ollama runtime startup timed out; installation is incomplete')


def _start_locked():
    record = _owned_record()
    if record is not None:
        return record, _ready(record)
    executable = binary()
    try:
        _inventory(timeout=0.5)
    except backends.BackendError as exc:
        if not _refused(exc):
            raise
    else:
        raise RuntimeError('dedicated endpoint is occupied by a service not owned by Nano Shell')
    process = subprocess.Popen([executable, 'serve'], env=environment(), cwd=storage.state_dir(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    _children[process.pid] = process
    identity = _identity(process.pid)
    if identity is None:
        process.wait(timeout=2)
        raise RuntimeError(f'Ollama runtime exited at startup (exit {process.returncode})')
    record = {'pid': process.pid, 'start': identity['start'], 'exe': identity['exe'],
              'binary': executable, 'endpoint': endpoint()}
    try:
        storage.write_private_json(_record_path(), record)
        return record, _ready(record)
    except BaseException:
        _terminate(record)
        _record_path().unlink(missing_ok=True)
        raise


def ensure_running(foreground=False):
    if not foreground:
        with _lock():
            return _start_locked()[1]
    stopping = threading.Event()
    old = {sig: signal.signal(sig, lambda *_: stopping.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with _lock():
            record, result = _start_locked()
        while _matches(record) and not stopping.wait(0.2):
            pass
        if stopping.is_set():
            with _lock():
                current = _owned_record()
                if current is not None and current['pid'] == record['pid'] and current['start'] == record['start']:
                    _stop_locked()
            return {'running': False, 'stopped': True}
        intent = storage.state_dir() / 'runtime-stop.json'
        with _lock():
            if intent.exists() or intent.is_symlink():
                try:
                    requested = json.loads(storage._read_private(intent))
                except (ValueError, RecursionError) as exc:
                    raise RuntimeError('invalid shutdown intent record') from exc
                if isinstance(requested, dict) and requested.get('pid') == record['pid'] and requested.get('start') == record['start']:
                    return {'running': False, 'stopped': True}
        raise RuntimeError('owned Ollama runtime exited unexpectedly')
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def _stop_locked():
    record = _owned_record()
    changed = record is not None
    if record is None:
        _record_path().unlink(missing_ok=True)
        return {'stopped': True, 'changed': False, 'owned': False,
                'detail': 'No verified owned runtime to stop; unmanaged endpoint state was not queried'}
    storage.write_private_json(storage.state_dir() / 'runtime-stop.json',
                               {'pid': record['pid'], 'start': record['start']})
    _terminate(record)
    try:
        _inventory(timeout=0.5)
    except backends.BackendError as exc:
        if not _refused(exc):
            raise
    else:
        raise RuntimeError('runtime shutdown unverified: dedicated endpoint is still reachable')
    _record_path().unlink(missing_ok=True)
    return {'stopped': True, 'changed': changed}


def stop():
    with _lock():
        return _stop_locked()


def require_model(model):
    if not _installed(model, _inventory()):
        raise RuntimeError('installation is incomplete: configured model is missing; reinstall Nano Shell')


def status(model):
    record = _owned_record()
    if record is None:
        return {'backend': 'ollama', 'model': model, 'running': False, 'ready': False,
                'detail': 'Owned headless runtime is stopped or its ownership cannot be verified'}
    installed = _installed(model, _inventory())
    if not _matches(record):
        raise RuntimeError('runtime ownership changed during status readback; readiness is unverified')
    verified = installed and record.get('verified_model') == model
    return {'backend': 'ollama', 'model': model, 'endpoint': endpoint(), 'running': True,
            'model_installed': installed, 'inference_verified': verified, 'ready': verified,
            'detail': 'Model inference verified' if verified else 'Model inference has not been verified' if installed else 'Configured model is missing; installation is incomplete'}


def provision(model):
    storage.validate_config({'backend': 'ollama', 'model': model})
    ensure_running()
    if not _installed(model, _inventory()):
        print(f'Downloading local model {model}...', file=sys.stderr, flush=True)
        try:
            result = subprocess.run([binary(), 'pull', model], env=environment(),
                                    stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr, timeout=1800)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError('model download timed out; installation is incomplete') from exc
        if result.returncode != 0:
            raise RuntimeError(f'model download failed (exit {result.returncode}); installation is incomplete')
        if not _installed(model, _inventory()):
            raise RuntimeError('model download reported success but inventory does not contain the model')
    prompt = ('Return exactly {"command":"pwd","explanation":"Print current directory"}. '
              'This is an installation check. Do not return any other command.')
    verified_record = _owned_record()
    if verified_record is None:
        raise RuntimeError('runtime exited before model verification')
    generated = policy.parse_generation(backends.ollama_generate(prompt, model))
    if generated.get('command') != 'pwd':
        raise RuntimeError('model installation check did not return pwd; inference is unverified')
    with _lock():
        record = _owned_record()
        if record is None or record['pid'] != verified_record['pid'] or record['start'] != verified_record['start']:
            raise RuntimeError('runtime ownership changed during model verification')
        record['verified_model'] = model
        storage.write_private_json(_record_path(), record)
    return status(model)
