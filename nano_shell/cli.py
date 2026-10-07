"""Terminal entry points; all inference and diagnostics remain local."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

from . import backends, bridge, policy, storage


class CliError(RuntimeError):
    pass


def port():
    try:
        value = int(os.environ.get('NANO_SHELL_PORT', '8765'))
    except ValueError as exc:
        raise CliError('NANO_SHELL_PORT must be an integer') from exc
    if not 1024 <= value <= 65535:
        raise CliError('NANO_SHELL_PORT must be between 1024 and 65535')
    return value


def request(path, payload=None, timeout=35):
    return backends.local_request(f'http://127.0.0.1:{port()}{path}', payload, timeout,
                                  {'Authorization': 'Bearer ' + storage.get_token()})


def context_for(question, history=False):
    if not question.strip() or len(question) > 4096:
        raise CliError('question must contain 1 to 4096 characters')
    cwd = os.getcwd()
    try:
        with os.scandir(cwd) as directory:
            names = []
            for entry in directory:
                names.append(entry.name[:256])
                if len(names) >= 40:
                    break
        names.sort()
    except OSError:
        names = []
    context = {'question': question, 'cwd': cwd, 'entries': names}
    if history:
        path = Path(os.environ.get('HISTFILE') or str(Path.home() / '.bash_history')).expanduser()
        try:
            with path.open('rb') as handle:
                handle.seek(0, os.SEEK_END)
                handle.seek(max(0, handle.tell() - 4096))
                context['history'] = handle.read(4096).decode('utf-8', errors='replace')[-4096:]
        except OSError as exc:
            raise CliError('cannot read explicitly requested history: ' + str(exc)) from exc
    return context


def generate(question, history=False):
    context = context_for(question, history)
    config = storage.load_config()
    if config['backend'] == 'ollama':
        raw = backends.ollama_generate(backends.prompt_for(context), config['model'])
        return policy.parse_generation(raw)
    try:
        return request('/generate', context)
    except backends.BackendError as exc:
        raise CliError(str(exc) + '; run nano-shell setup and initialize Chrome') from exc


def _secure_log():
    path = storage.state_dir() / 'daemon.log'
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    import stat
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(descriptor)
        raise CliError('daemon log is not a private regular file')
    os.fchmod(descriptor, 0o600)
    return os.fdopen(descriptor, 'a')


def start(foreground=False):
    if foreground:
        server = bridge.make_server(port=port(), token=storage.get_token(), config_provider=storage.load_config)
        def stop_signal(_signum, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()
        old = {sig: signal.signal(sig, stop_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            server.serve_forever()
        finally:
            server.server_close()
            for sig, handler in old.items():
                signal.signal(sig, handler)
        return 0
    try:
        request('/status', timeout=0.5)
        print('Nano Shell daemon is already running.', file=sys.stderr)
        return 0
    except backends.BackendError:
        pass
    source = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env['PYTHONPATH'] = source + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    with _secure_log() as log:
        process = subprocess.Popen([sys.executable, '-m', 'nano_shell', 'start', '--foreground'],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, env=env, cwd=source)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise CliError(f'daemon failed to start (exit {process.returncode}); see {storage.state_dir() / "daemon.log"}')
        try:
            request('/status', timeout=0.2)
            print('Nano Shell daemon started.', file=sys.stderr)
            return 0
        except backends.BackendError:
            time.sleep(0.05)
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise CliError('daemon did not become available; inspect daemon.log')


def connection_refused(error):
    import errno
    import urllib.error
    cause = error.__cause__
    return (isinstance(cause, urllib.error.URLError) and isinstance(cause.reason, OSError)
            and cause.reason.errno == errno.ECONNREFUSED)


def stop():
    # An uninitialized installation is already stopped, including offline uninstall.
    if not storage.token_path().exists():
        return 0
    try:
        request('/status', timeout=0.5)
    except backends.BackendError as exc:
        # Connection refusal proves there is no listening daemon. Other failures do not.
        if connection_refused(exc):
            return 0
        raise
    request('/shutdown', {}, timeout=2)
    for _ in range(40):
        try:
            request('/status', timeout=0.2)
        except backends.BackendError as exc:
            if not connection_refused(exc):
                raise
            print('Nano Shell daemon stopped.', file=sys.stderr)
            return 0
        time.sleep(0.05)
    raise CliError('shutdown requested but daemon is still reachable')


def backend_status():
    config = storage.load_config()
    if config['backend'] == 'nano':
        return request('/status', timeout=2)
    response = backends.local_request('http://127.0.0.1:11434/api/tags', timeout=2)
    models = response.get('models')
    if not isinstance(models, list):
        raise CliError('invalid Ollama model inventory')
    names = {model.get('name') for model in models if isinstance(model, dict)}
    model = config['model']
    ready = model in names or (':' not in model and model + ':latest' in names)
    return {'backend': 'ollama', 'model': model, 'ready': ready,
            'detail': 'Configured model is installed; inference has not been verified' if ready else 'Configured model is not installed'}


def setup():
    if storage.load_config()['backend'] != 'nano':
        status = backend_status()
        print(json.dumps(status), file=sys.stderr)
        return 0 if status['ready'] else 1
    executable = next((shutil.which(name) for name in ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser') if shutil.which(name)), None)
    if not executable:
        raise CliError('Chrome/Chromium is not installed; Gemini Nano requires a compatible Chrome browser')
    start()
    url = f'http://127.0.0.1:{port()}/#token={storage.get_token()}'
    subprocess.Popen([executable, '--app=' + url], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print('Chrome initialization window requested. Click Initialize Gemini Nano; keep the window open.', file=sys.stderr)
    return 0


def parser():
    result = argparse.ArgumentParser(prog='nano-shell', description='A local Linux command assistant with a narrow read-only policy')
    commands = result.add_subparsers(dest='action', required=True)
    for name in ('ask', 'suggest'):
        command = commands.add_parser(name)
        command.add_argument('question', nargs='+')
        command.add_argument('--history', action='store_true', help='explicitly share up to 4096 characters from HISTFILE or ~/.bash_history')
        if name == 'ask':
            command.add_argument('--yes', action='store_true', help='execute only validated read-only commands without prompting')
    daemon = commands.add_parser('start')
    daemon.add_argument('--foreground', action='store_true')
    for name in ('stop', 'status', 'doctor', 'setup'):
        commands.add_parser(name)
    config = commands.add_parser('config')
    config.add_argument('--backend', choices=('nano', 'ollama'))
    config.add_argument('--model')
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.action in {'ask', 'suggest'}:
            question = ' '.join(args.question)
            response = generate(question, history=args.history) if args.history else generate(question)
            # Even custom/future adapters cannot bypass the execution policy.
            response = policy.parse_generation(json.dumps(response))
            pipeline = policy.validate_command(response['command'])
            if args.action == 'suggest':
                print(policy.render_command(pipeline))
                return 0
            print('$ ' + response['command'], file=sys.stderr)
            if response.get('explanation'):
                print(response['explanation'], file=sys.stderr)
            if not args.yes:
                if not sys.stdin.isatty():
                    raise CliError('execution requires an interactive confirmation or explicit --yes')
                print('Run this read-only command? [y/N] ', end='', file=sys.stderr, flush=True)
                answer = sys.stdin.readline().strip().lower()
                if answer not in {'y', 'yes'}:
                    print('Cancelled; command was not executed.', file=sys.stderr)
                    return 1
            return policy.execute(pipeline)
        if args.action == 'start':
            return start(args.foreground)
        if args.action == 'stop':
            return stop()
        if args.action == 'setup':
            return setup()
        if args.action == 'config':
            config = storage.load_config()
            if args.backend is not None:
                config['backend'] = args.backend
            if args.model is not None:
                config['model'] = args.model
            if args.backend is not None or args.model is not None:
                storage.save_config(config)
            print(json.dumps(config))
            return 0
        if args.action in {'status', 'doctor'}:
            if args.action == 'doctor':
                print(f'Python {sys.version.split()[0]}; state {storage.state_dir()}; config {storage.config_dir()}', file=sys.stderr)
                print(f'Inference endpoint http://127.0.0.1:{port()}; Chrome worker requires user initialization.', file=sys.stderr)
            status = backend_status()
            print(json.dumps(status))
            return 0 if status.get('ready') is True else 1
        raise CliError('unknown command')
    except KeyboardInterrupt:
        print('Cancelled.', file=sys.stderr)
        return 130
    except (CliError, policy.PolicyError, storage.StorageError, backends.BackendError, OSError) as exc:
        print('nano-shell: ' + str(exc), file=sys.stderr)
        return 1
