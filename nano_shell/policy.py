"""Validate a narrow read-only argv grammar; never invoke a shell."""
import json
from datetime import date
import os
import re
import shlex
import shutil
import subprocess


class PolicyError(ValueError):
    pass


# Only listed flags are accepted. Flags with values have separate validators.
FLAGS = {
    'pwd': {'-L', '-P'},
    'ls': {'--all', '--almost-all', '--human-readable', '--size', '--reverse', '--recursive', '--directory', '--long'},
    'du': {'--human-readable', '--summarize', '--total'},
    'df': {'--human-readable', '--print-type', '--local'},
    'cat': {'--number', '--number-nonblank', '--show-ends'},
    'wc': {'--lines', '--words', '--bytes', '--chars'},
    'head': set(), 'tail': set(),
    'sort': {'--reverse', '--numeric-sort', '--human-numeric-sort', '--unique', '--ignore-case'},
    'grep': {'--ignore-case', '--line-number', '--invert-match', '--count', '--files-with-matches', '--fixed-strings', '--extended-regexp', '--recursive', '--no-messages', '--null'},
    'ps': {'--everyone', '--no-headers'},
    'ss': {'--all', '--listening', '--numeric', '--tcp', '--udp', '--processes', '--summary'},
}
SHORT = {
    'ls': 'alhstrRdSF1', 'du': 'shack', 'df': 'hTk', 'cat': 'nbET', 'wc': 'lwcmL',
    'head': '', 'tail': '', 'sort': 'rnhuf', 'grep': 'invcFlErsHhIZ', 'ps': 'efaux', 'ss': 'alntupsr', 'pwd': 'LP',
}
VALUE_OPTIONS = {
    'head': {'-n': r'\d{1,6}', '--lines': r'\d{1,6}'},
    'tail': {'-n': r'\d{1,6}', '--lines': r'\d{1,6}'},
    'sort': {'-k': r'\d{1,3}(?:,\d{1,3})?', '--key': r'\d{1,3}(?:,\d{1,3})?'},
    'du': {'--max-depth': r'\d{1,3}'},
    'grep': {'--exclude-dir': r'(?:\.git|node_modules|\.cache|\.venv)'},
}


def _find(args):
    predicates = {'-name', '-iname', '-path', '-ipath', '-type', '-size', '-mtime', '-mmin', '-maxdepth', '-mindepth', '-printf', '-newermt'}
    unary = {'-print', '-empty', '-readable', '-executable', '-a', '-o', '-not', '!'}
    index, predicates_started = 0, False
    while index < len(args):
        arg = args[index]
        if arg in predicates:
            predicates_started = True
            index += 1
            if index >= len(args):
                raise PolicyError('find predicate requires a value')
            value = args[index]
            if arg in {'-maxdepth', '-mindepth'} and not re.fullmatch(r'\d{1,3}', value):
                raise PolicyError('invalid find depth')
            if arg == '-printf' and not re.fullmatch(r'(?:%T@|%s|%p| |\\n){1,100}', value):
                raise PolicyError('unsupported find print format')
            if arg == '-newermt' and value not in {'today', 'yesterday'}:
                try:
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise PolicyError('find date must be today, yesterday or YYYY-MM-DD') from exc
            if arg == '-type' and value not in {'f', 'd', 'l', 'b', 'c', 'p', 's'}:
                raise PolicyError('invalid find type')
            if arg in {'-mtime', '-mmin', '-size'} and not re.fullmatch(r'[+-]?\d{1,8}[ckMGTPwb]?', value):
                raise PolicyError('invalid find numeric predicate')
        elif arg in unary:
            predicates_started = True
        elif arg.startswith('-') or predicates_started:
            raise PolicyError('unsupported find argument: ' + arg)
        index += 1


def _validate_argv(argv):
    program, args = argv[0], argv[1:]
    literal_start = args.index('--') + 1 if program == 'grep' and '--' in args else len(args)
    for index, arg in enumerate(args):
        if '\\' in arg and index < literal_start and not (program == 'find' and index > 0 and args[index - 1] == '-printf'):
            raise PolicyError('backslash escapes are unsupported outside find print formats')
    if program == 'find':
        _find(args)
        return
    if program == 'git':
        if not args or args[0] != 'status' or any(arg not in {'--short', '-s', '--branch', '-b', '--porcelain', '--untracked-files=no', '--untracked-files=normal', '--untracked-files=all'} for arg in args[1:]):
            raise PolicyError('only git status with vetted flags is supported')
        return
    if program == 'docker':
        if not args or args[0] not in {'info', 'version', 'ps'}:
            raise PolicyError('only read-only Docker info, version and ps are supported')
        flags = {'-a', '--all', '-q', '--quiet', '--no-trunc', '--size', '-s'} if args[0] == 'ps' else set()
        if any(arg not in flags for arg in args[1:]):
            raise PolicyError('unsupported Docker inspection option')
        return
    if program not in FLAGS:
        raise PolicyError('unsupported command: ' + program)
    index, positional = 0, False
    while index < len(args):
        arg = args[index]
        if arg == '--':
            positional = True
        elif arg.startswith('-') and arg != '-' and not positional:
            options = VALUE_OPTIONS.get(program, {})
            key, sep, value = arg.partition('=')
            if key in options:
                if not sep:
                    index += 1
                    if index >= len(args):
                        raise PolicyError('missing option value')
                    value = args[index]
                if not re.fullmatch(options[key], value):
                    raise PolicyError('invalid option value')
            elif program in {'head', 'tail'} and re.fullmatch(r'-(?:n)?\d{1,6}', arg):
                pass
            elif arg in FLAGS[program]:
                pass
            elif arg.startswith('--') or not all(letter in SHORT[program] for letter in arg[1:]):
                raise PolicyError('unsupported option: ' + arg)
        elif program == 'pwd':
            raise PolicyError('pwd takes no file operands')
        elif program == 'ps' and arg not in {'aux', 'ax', 'u', 'a', 'x'}:
            raise PolicyError('unsupported ps operand')
        elif program == 'ss':
            raise PolicyError('ss filters are not supported')
        index += 1


def validate_command(command):
    if not isinstance(command, str) or not command.strip() or len(command) > 4096:
        raise PolicyError('command must be a nonempty string of at most 4096 characters')
    if any(ord(char) < 32 or ord(char) == 127 for char in command) or any(char in command for char in '$`;&<>()'):
        raise PolicyError('shell operators, escapes and substitutions are not supported')
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars='|')
        lexer.whitespace_split = True
        lexer.commenters = ''
        lexer.escape = ''
        tokens = list(lexer)
    except ValueError as exc:
        raise PolicyError(str(exc)) from exc
    pipeline, argv = [], []
    for token in tokens:
        if token == '|':
            if not argv:
                raise PolicyError('empty pipeline stage')
            _validate_argv(argv)
            pipeline.append(argv)
            argv = []
        elif '|' in token:
            raise PolicyError('unsupported pipe syntax')
        else:
            argv.append(token)
    if not argv or len(pipeline) >= 5 or len(tokens) > 128:
        raise PolicyError('empty or oversized pipeline')
    _validate_argv(argv)
    return pipeline + [argv]


def _response_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate response field')
        result[key] = value
    return result


def parse_generation(raw):
    if not isinstance(raw, str) or len(raw) > 8192:
        raise PolicyError('The model response is too long or is not text. Please try a shorter question.')
    try:
        result = json.loads(raw, object_pairs_hook=_response_object)
    except (ValueError, RecursionError) as exc:
        raise PolicyError('The model returned an invalid response. Please try again; expected one JSON answer or command.') from exc
    if not isinstance(result, dict):
        raise PolicyError('The model returned an invalid response. Please try again; expected an answer or command.')
    if set(result) == {'answer'}:
        answer = result['answer']
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 4096 or any(not char.isprintable() for char in answer):
            raise PolicyError('The model answer must be nonempty printable text of at most 4096 characters. Please try again.')
        return result
    if 'command' not in result or set(result) - {'command', 'explanation'}:
        raise PolicyError('The model returned an invalid response. Please try again; expected either an answer or a command, without extra fields.')
    if not isinstance(result.get('explanation', ''), str) or len(result.get('explanation', '')) > 2048:
        raise PolicyError('The model command explanation is invalid. Please try again.')
    if any(not char.isprintable() for char in result.get('explanation', '')):
        raise PolicyError('The model command explanation must contain only printable text. Please try again.')
    # Structured output never replaces the independent execution policy.
    validate_command(result['command'])
    return result


def trusted_pipeline(pipeline):
    # Both insertion into a shell and direct execution use the same vetted argv.
    if not isinstance(pipeline, list) or not pipeline or len(pipeline) > 5:
        raise PolicyError('invalid pipeline')
    trusted = []
    for argv in pipeline:
        if not argv or any(not isinstance(arg, str) for arg in argv):
            raise PolicyError('invalid argument array')
        _validate_argv(argv)
        executable = shutil.which(argv[0], path='/usr/bin:/bin')
        if not executable:
            raise PolicyError('program is not installed: ' + argv[0])
        literal_pattern = argv.index('--') + 1 if argv[0] == 'grep' and '--' in argv else -1
        operands = [os.path.expanduser(arg) if index != literal_pattern and (arg == '~' or arg.startswith('~/')) else arg
                    for index, arg in enumerate(argv[1:], start=1)]
        actual = [executable] + operands
        if argv[0] == 'git':
            actual = [executable, '-c', 'core.fsmonitor=false', '--no-optional-locks'] + operands
        trusted.append(actual)
    return trusted


def render_command(pipeline):
    return ' | '.join(' '.join(shlex.quote(arg) for arg in argv) for argv in trusted_pipeline(pipeline))


def execute(pipeline):
    pipeline = trusted_pipeline(pipeline)
    processes, previous = [], None
    env = os.environ.copy()
    env.update({'LC_ALL': 'C', 'GIT_PAGER': 'cat', 'GIT_TERMINAL_PROMPT': '0'})
    # Avoid user git config launching fsmonitor, and avoid trusted-name PATH spoofing.
    try:
        for index, argv in enumerate(pipeline):
            process = subprocess.Popen(argv, stdin=previous.stdout if previous else None,
                                       stdout=subprocess.PIPE if index < len(pipeline) - 1 else None,
                                       env=env)
            if previous:
                previous.stdout.close()
            processes.append(process)
            previous = process
        statuses = [process.wait() for process in processes]
        # SIGPIPE is expected when a downstream head ends early.
        failures = [code for code in statuses if code not in (0, -13)]
        code = failures[-1] if failures else statuses[-1]
        return code if code >= 0 else 128 - code
    except BaseException:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait()
        raise
