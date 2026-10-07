"""Terminal entry points; all inference and diagnostics remain local."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from . import backends, policy, storage, runtime, search


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
    runtime.ensure_running()
    runtime.require_model(config['model'])
    raw = backends.ollama_generate(backends.prompt_for(context), config['model'])
    return policy.parse_generation(raw)


def summarize(context):
    config = storage.load_config()
    runtime.ensure_running()
    runtime.require_model(config['model'])
    raw = backends.ollama_generate(backends.grounded_prompt(context), config['model'], schema=backends.ANSWER_SCHEMA)
    response = policy.parse_generation(raw)
    if set(response) != {'answer'}:
        raise policy.PolicyError('The model returned a command instead of a grounded answer; no command was executed.')
    return response


def lookup(question, term):
    pipeline = search.command(term)
    print(f'Running this command "{policy.render_command(pipeline)}"', file=sys.stderr, flush=True)
    result = search.run(term)
    cwd = json.dumps(result['cwd'], ensure_ascii=True)
    label = json.dumps(term, ensure_ascii=True)
    if result['status'] == 'no_matches':
        print(f'No matches found in {cwd} for {label} (case-insensitive local-file search).')
        return 0
    if result['matches']:
        print(f'Local matches in {cwd} for {label}:')
        for record in result['matches']:
            print(json.dumps(record, ensure_ascii=True), flush=True)
    if result['status'] == 'timeout':
        print('Local search timed out; any evidence is partial.', file=sys.stderr)
    elif result['status'] == 'limited':
        print('Local search reached its output limit; evidence is partial.', file=sys.stderr)
    elif result['status'] == 'error':
        print('Local search failed; any evidence is partial.', file=sys.stderr)
    if result.get('discarded_bytes'):
        print('An incomplete evidence record was discarded; it cannot support a citation.', file=sys.stderr)
    if result['errors']:
        print(search.safe_text(result['errors']), file=sys.stderr)
    if not result['matches']:
        return result['exit_code'] or 1
    context = {key: result[key] for key in ('cwd', 'term', 'matches', 'status', 'partial')}
    context['question'] = question
    response = policy.parse_generation(json.dumps(summarize(context), ensure_ascii=False))
    if set(response) != {'answer'}:
        raise policy.PolicyError('The model returned a command instead of a grounded answer; no command was executed.')
    print(response['answer'])
    return result['exit_code']


def start(foreground=False):
    result = runtime.ensure_running(foreground=foreground)
    if result.get('running') is True:
        print('Headless runtime is running.', file=sys.stderr)
        return 0
    return 0 if result.get('stopped') is True else 1


def connection_refused(error):
    import errno
    import urllib.error
    cause = error.__cause__
    return (isinstance(cause, urllib.error.URLError) and isinstance(cause.reason, OSError)
            and cause.reason.errno == errno.ECONNREFUSED)


def _stop_legacy_bridge():
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


def stop():
    _stop_legacy_bridge()
    result = runtime.stop()
    if result.get('stopped') is not True:
        raise CliError('runtime stop outcome is unverified')
    print('Owned headless runtime stopped.' if result.get('changed') else 'No verified owned headless runtime to stop.', file=sys.stderr)
    return 0


def backend_status():
    return runtime.status(storage.load_config()['model'])


def setup():
    result = runtime.provision(storage.load_config()['model'])
    print(json.dumps(result), file=sys.stderr)
    return 0 if result.get('ready') is True else 1


def parser():
    result = argparse.ArgumentParser(prog='nano-shell', description='A local Linux command assistant with a narrow read-only policy')
    commands = result.add_subparsers(dest='action', required=True)
    for name in ('ask', 'suggest'):
        command = commands.add_parser(name)
        command.add_argument('question', nargs='+')
        command.add_argument('--history', action='store_true', help='explicitly share up to 4096 characters from HISTFILE or ~/.bash_history')
        if name == 'ask':
            command.add_argument('--yes', action='store_true', help='compatibility flag; validated read-only commands already run without prompting')
    daemon = commands.add_parser('start')
    daemon.add_argument('--foreground', action='store_true')
    for name in ('stop', 'status', 'doctor', 'setup'):
        commands.add_parser(name)
    config = commands.add_parser('config')
    config.add_argument('--backend', choices=('ollama',))
    config.add_argument('--model')
    return result


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        if args.action in {'ask', 'suggest'}:
            question = ' '.join(args.question)
            term = search.identity_term(question)
            if term is not None:
                if args.action == 'suggest':
                    print(policy.render_command(search.command(term)))
                    return 0
                return lookup(question, term)
            response = generate(question, history=args.history) if args.history else generate(question)
            # Even custom/future adapters cannot bypass the execution policy.
            response = policy.parse_generation(json.dumps(response, ensure_ascii=False))
            if 'answer' in response:
                if args.action == 'suggest':
                    raise CliError('The model answered with text; no command is available to insert. Use ask to read the answer.')
                print(response['answer'])
                return 0
            pipeline = policy.validate_command(response['command'])
            if args.action == 'suggest':
                print(policy.render_command(pipeline))
                return 0
            print(f'Running this command "{response["command"]}"', file=sys.stderr)
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
                print(f'Headless inference endpoint {runtime.endpoint()}.', file=sys.stderr)
            status = backend_status()
            print(json.dumps(status))
            return 0 if status.get('ready') is True else 1
        raise CliError('unknown command')
    except KeyboardInterrupt:
        print('Cancelled.', file=sys.stderr)
        return 130
    except (CliError, policy.PolicyError, storage.StorageError, backends.BackendError, runtime.RuntimeError, OSError) as exc:
        print('nano-shell: ' + str(exc), file=sys.stderr)
        return 1
