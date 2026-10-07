import contextlib
import io
import json
import os
import shlex
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

try:
    from nano_shell import policy, cli, storage, backends
except ImportError:
    policy = cli = storage = backends = None


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(policy, 'core implementation is missing')

    def test_read_only_examples_and_literal_patterns(self):
        for command in ['find . -type f -name "*.log" | sort | head -n 10', 'ps aux',
                        'ss -tuln', 'git status --short', 'ls -lah', 'pwd', 'du -sh .',
                        'grep -n "error" README.md', 'head -n 5 "file with spaces"']:
            with self.subTest(command=command):
                self.assertTrue(policy.validate_command(command))

    def test_rejects_command_execution_and_write_options(self):
        for command in ['rm -rf .', 'ls; pwd', 'ls && pwd', 'ls > out', 'ls $(pwd)',
                        'ls `pwd`', 'find . -delete', 'find . -exec sh -c id ;',
                        'sort -o output input', 'sort --output=out input', 'git clean -fd',
                        'git -c alias.status="!id" status', 'grep --include x f',
                        'python -c print(1)', 'env ls', '/bin/ls', 'ls\npwd', 'ls |',
                        'head --bytes=10 /etc/passwd', 'ss -K', 'ls\x00']:
            with self.subTest(command=command):
                with self.assertRaises(policy.PolicyError):
                    policy.validate_command(command)

    def test_download_examples_and_safe_home_expansion(self):
        commands = ["find ~/Downloads -type f -printf '%T@ %p\\n' | sort -nr | head -1",
                    'find ~/Downloads -type f -newermt today | head -n 10']
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(policy.validate_command(command))
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {'HOME': home}):
            Path(home, 'present').touch()
            result = subprocess.run([sys.executable, '-c',
                "from nano_shell.policy import *; raise SystemExit(execute(validate_command('ls ~')))"],
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('present', result.stdout)
        for command in ["find . -printf '%T@ %p\\q'", 'find . -newermt invalid',
                        "cat 'file\\name'", "ls \\abc"]:
            with self.subTest(command=command), self.assertRaises(policy.PolicyError):
                policy.validate_command(command)

    def test_local_transport_rejects_redirects(self):
        opener = backends.local_opener()
        from urllib.request import Request
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            opener.error('http', Request('http://127.0.0.1:8765/status'), None, 302,
                         'redirect', {'Location': 'https://remote.example/'})

    def test_generation_requires_bounded_json_command(self):
        result = policy.parse_generation('{"command":"pwd","explanation":"Location"}')
        self.assertEqual(result['command'], 'pwd')
        for value in ['pwd', '```json\n{"command":"pwd"}\n```', '{}',
                      '{"command":"rm a"}', '{"command":["pwd"]}',
                      '{"command":"pwd","explanation":3}', json.dumps({'command': 'pwd', 'explanation': '\x1b]52;c;secret\x07'}), '{"command":"pwd","x":1}']:
            with self.subTest(value=value):
                with self.assertRaises(policy.PolicyError):
                    policy.parse_generation(value)

    def test_execution_preserves_nonzero_and_pipeline_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / 'absent')
            self.assertNotEqual(policy.execute(policy.validate_command('cat ' + missing)), 0)
            self.assertNotEqual(policy.execute(policy.validate_command('cat ' + missing + ' | head')), 0)
            self.assertEqual(policy.execute(policy.validate_command('pwd')), 0)

    def test_private_token_and_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'NANO_SHELL_STATE_DIR': directory + '/state',
                'NANO_SHELL_CONFIG_DIR': directory + '/config'}):
            token = storage.get_token()
            self.assertGreaterEqual(len(token), 40)
            self.assertEqual(token, storage.get_token())
            self.assertEqual(storage.token_path().stat().st_mode & 0o777, 0o600)
            self.assertEqual(storage.state_dir().stat().st_mode & 0o777, 0o700)
            storage.save_config({'backend': 'ollama', 'model': 'small:latest'})
            self.assertEqual(storage.load_config()['model'], 'small:latest')

    def test_token_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'NANO_SHELL_STATE_DIR': directory + '/state'}):
            storage.state_dir()
            target = Path(directory) / 'other'
            target.write_text('a' * 64)
            storage.token_path().symlink_to(target)
            with self.assertRaises(storage.StorageError):
                storage.get_token()

    def test_suggest_stdout_contains_only_validated_command(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli, 'generate', return_value={'command': 'pwd', 'explanation': 'where'}), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(['suggest', 'show', 'location']), 0)
        self.assertEqual(out.getvalue(), shutil.which('pwd', path='/usr/bin:/bin') + '\n')

    def test_inserted_suggestion_cannot_expand_into_find_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory, '-delete')
            marker.write_text('must survive')
            out = io.StringIO()
            with patch.object(cli, 'generate', return_value={'command': 'find *'}), \
                 contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(['suggest', 'list']), 0)
            subprocess.run(['bash', '-c', out.getvalue()], cwd=directory,
                           capture_output=True, text=True, check=False)
            self.assertTrue(marker.exists(), 'shell-expanded model text invoked find -delete')
            self.assertEqual(shlex.split(out.getvalue()), [shutil.which('find', path='/usr/bin:/bin'), '*'])

    def test_suggest_uses_trusted_git_flags_and_quotes_home_path(self):
        with tempfile.TemporaryDirectory(prefix='nano home ') as home, patch.dict(os.environ, {'HOME': home}):
            for raw, expected in [
                ('git status --short', [shutil.which('git', path='/usr/bin:/bin'), '-c',
                                      'core.fsmonitor=false', '--no-optional-locks', 'status', '--short']),
                ('ls ~/Downloads', [shutil.which('ls', path='/usr/bin:/bin'), home + '/Downloads']),
            ]:
                out = io.StringIO()
                with patch.object(cli, 'generate', return_value={'command': raw}), contextlib.redirect_stdout(out):
                    self.assertEqual(cli.main(['suggest', 'question']), 0)
                self.assertEqual(shlex.split(out.getvalue()), expected)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for browser-worker regression')
    def test_browser_immediate_creation_rejection_is_handled(self):
        worker = Path(__file__).resolve().parents[1] / 'web' / 'worker.js'
        script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let click;
const unhandled = [];
process.on('unhandledRejection', (error) => unhandled.push(error));
const nodes = { status: { textContent: '' }, initialize: {
  disabled: true, addEventListener(name, callback) { click = callback; }
}, download: {} };
const LanguageModel = {
  availability: async () => 'available',
  create: () => Promise.reject(new Error('unsupported model')),
};
const context = {
  location: { hash: '#token=test', pathname: '/' }, history: { replaceState() {} },
  sessionStorage: { setItem() {}, getItem() { return null; } },
  document: { getElementById(id) { return nodes[id]; } },
  LanguageModel, self: { LanguageModel }, URLSearchParams, AbortSignal,
  setInterval() {}, setTimeout() {},
  async fetch() {
    await new Promise(resolve => setTimeout(resolve, 10));
    return { ok: true, status: 200, async json() { return {}; } };
  },
};
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), context);
(async () => {
  await new Promise(resolve => setTimeout(resolve, 30));
  await click();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(unhandled.length, 0, 'create rejection escaped before report finished');
  assert.match(nodes.status.textContent, /Initialization failed: unsupported model/);
  assert.equal(nodes.initialize.disabled, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run(['node', '-e', script, str(worker)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_untrusted_suggestion_and_noninteractive_ask_never_execute(self):
        for response in [{'command': 'rm .'}, {'command': 'pwd'}]:
            with patch.object(cli, 'generate', return_value=response), \
                 patch.object(policy, 'execute') as execute, patch.object(sys.stdin, 'isatty', return_value=False), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                self.assertNotEqual(cli.main(['ask', 'question']), 0)
                execute.assert_not_called()

    def test_yes_is_still_vetted_and_exit_code_preserved(self):
        with patch.object(cli, 'generate', return_value={'command': 'pwd'}), \
             patch.object(policy, 'execute', return_value=7) as execute, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['ask', '--yes', 'where']), 7)
            execute.assert_called_once()
        with patch.object(cli, 'generate', return_value={'command': 'rm .'}), \
             patch.object(policy, 'execute') as execute, contextlib.redirect_stderr(io.StringIO()):
            self.assertNotEqual(cli.main(['ask', '--yes', 'remove']), 0)
            execute.assert_not_called()

    def test_stop_readback_error_is_not_success(self):
        from urllib.error import URLError
        import errno
        transient = backends.BackendError('credentials rejected')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'NANO_SHELL_STATE_DIR': directory}):
            storage.get_token()
            with patch.object(cli, 'request', side_effect=[{'daemon': 'running'}, {'stopping': True}, transient]), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertNotEqual(cli.main(['stop']), 0)

    def test_context_bounded_and_history_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            for index in range(80):
                Path(directory, str(index)).touch()
            with patch('os.getcwd', return_value=directory):
                context = cli.context_for('what')
            self.assertEqual(context['cwd'], directory)
            self.assertLessEqual(len(context['entries']), 40)
            self.assertNotIn('history', context)

    def test_ollama_never_substitutes_model_or_swallows_failure(self):
        with self.assertRaises(backends.BackendError):
            backends.ollama_generate('prompt', '', timeout=0.1)
        with patch('urllib.request.OpenerDirector.open', side_effect=TimeoutError('timeout')):
            with self.assertRaises(backends.BackendError):
                backends.ollama_generate('prompt', 'explicit-model', timeout=0.1)


if __name__ == '__main__':
    unittest.main()
