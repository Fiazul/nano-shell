"""Bounded, literal local-file evidence for narrowly recognized identity questions."""
import os
import re
import selectors
import signal
import subprocess
import time

from . import policy

EXCLUDED_DIRS = ('.git', 'node_modules', '.cache', '.venv')


def identity_term(question):
    if not isinstance(question, str) or len(question) > 180:
        return None
    match = re.fullmatch(r"(?:who\s+is|who['’]s|what\s+do\s+you\s+know\s+about)\s+(.+?)\s*\??", question.strip(), re.IGNORECASE)
    if not match:
        return None
    term = match.group(1).strip()
    if not term or len(term) > 120 or any(not char.isprintable() for char in term):
        return None
    return term


def command(term):
    if not isinstance(term, str) or not term.strip() or len(term) > 120 or any(not char.isprintable() for char in term):
        raise policy.PolicyError('local search requires a printable name of at most 120 characters')
    # -- makes the fixed-string term an operand even when it resembles an option.
    return [['grep', '-r', '-i', '-n', '-F', '-I', '-H', '-Z',
             *('--exclude-dir=' + name for name in EXCLUDED_DIRS), '--', term, '.']]


def safe_text(text):
    # Retain line breaks for evidence, escape terminal controls and Unicode controls.
    return ''.join(char if char.isprintable() else ascii(char)[1:-1] for char in text)


def records_from(raw):
    records, offset = [], 0
    while offset < len(raw):
        # grep -H -n -Z: filename NUL decimal-line-number colon content newline.
        # A filename may itself contain newlines or colons; find NUL first.
        separator = raw.find(b'\0', offset)
        if separator < 0:
            break
        colon = raw.find(b':', separator + 1)
        if colon < 0:
            break
        number = raw[separator + 1:colon]
        if not re.fullmatch(rb'[1-9][0-9]{0,19}', number):
            break
        end = raw.find(b'\n', colon + 1)
        if end < 0:
            break
        records.append({'file': os.fsdecode(raw[offset:separator]), 'line': int(number),
                        'content': raw[colon + 1:end].decode('utf-8', errors='replace')})
        offset = end + 1
    # An unfinished filename/header/content never becomes a cited match.
    return records, len(raw) - offset


def _terminate(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass
    # The leader may exit on TERM while an owned descendant ignores it.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run(term, cwd=None, timeout=5.0, max_output=16384):
    if not 0 < timeout <= 30 or not 1 <= max_output <= 65536:
        raise ValueError('invalid local search limits')
    cwd = cwd or os.getcwd()
    argv = policy.trusted_pipeline(command(term))[0]
    result = {'term': term, 'cwd': cwd, 'matches': [], 'discarded_bytes': 0, 'errors': '',
              'status': 'error', 'partial': False, 'exit_code': 2}
    env = os.environ.copy()
    env['LC_ALL'] = 'C'
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, env=env)
    except OSError as exc:
        result['errors'] = str(exc)
        return result
    buffers = {'matches': bytearray(), 'errors': bytearray()}
    count, cause = 0, None
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            for name, stream in [('matches', process.stdout), ('errors', process.stderr)]:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cause = 'timeout'
                    break
                for key, _events in selector.select(remaining):
                    # Read at most the remaining allowance plus one detection byte.
                    chunk = os.read(key.fileobj.fileno(), min(4096, max_output - count + 1))
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    accepted = chunk[:max_output - count]
                    buffers[key.data].extend(accepted)
                    count += len(accepted)
                    if len(chunk) > len(accepted):
                        cause = 'limited'
                        break
                if cause:
                    break
            if cause:
                _terminate(process)
            else:
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    cause = 'timeout'
                    _terminate(process)
    except BaseException:
        _terminate(process)
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    result['matches'], result['discarded_bytes'] = records_from(bytes(buffers['matches']))
    result['errors'] = buffers['errors'].decode('utf-8', errors='replace')
    if not cause and result['discarded_bytes']:
        result['errors'] += ' Local search returned an incomplete or invalid evidence record.'
        result['partial'] = True
    if cause:
        result.update(status=cause, partial=True, exit_code=124 if cause == 'timeout' else 125)
    elif process.returncode == 0 and result['matches'] and not result['discarded_bytes']:
        result.update(status='matches', exit_code=0)
    elif process.returncode == 1 and not result['matches'] and not result['errors'] and not result['discarded_bytes']:
        result.update(status='no_matches', exit_code=1)
    else:
        code = process.returncode
        result.update(status='error', partial=bool(result['matches'] or result['discarded_bytes']),
                      exit_code=code if code and code > 0 else 128 - code if code and code < 0 else 2)
        if not result['errors']:
            result['errors'] = 'Local search exited without a verified complete result.'
    return result
