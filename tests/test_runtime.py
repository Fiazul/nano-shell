"""Socket-free coverage of the managed headless service."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from nano_shell import backends, cli, storage
try:
    from nano_shell import runtime
except ImportError:
    runtime = None


def refused():
    error = backends.BackendError('unavailable')
    error.__cause__ = URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))
    return error


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(runtime, 'headless runtime is missing')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            'NANO_SHELL_STATE_DIR': str(self.directory / 'state'),
            'NANO_SHELL_CONFIG_DIR': str(self.directory / 'config'),
            'NANO_SHELL_OLLAMA_PORT': '11435',
            'NANO_SHELL_OLLAMA_BIN': str(self.directory / 'ollama'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.binary = self.directory / 'ollama'
        self.binary.write_text('#!/usr/bin/python3\nimport time\ntime.sleep(60)\n')
        self.binary.chmod(0o700)

    def fake_api(self, url, payload=None, timeout=35, headers=None):
        if runtime._owned_record() is None:
            raise refused()
        if url.endswith('/api/tags'):
            return {'models': [{'name': 'qwen2.5-coder:1.5b'}]}
        return {'done': True, 'response': '{"command":"pwd","explanation":"Location"}'}

    def cleanup_service(self):
        with patch.object(backends, 'local_request', side_effect=refused()):
            runtime.stop()

    def start_service(self):
        self.addCleanup(self.cleanup_service)
        with patch.object(backends, 'local_request', side_effect=self.fake_api):
            return runtime.ensure_running()

    def test_empty_config_uses_headless_default(self):
        self.assertEqual(storage.load_config(), {'backend': 'ollama', 'model': 'qwen2.5-coder:1.5b'})

    def test_saved_nano_migrates_but_ollama_model_is_preserved(self):
        path = storage.config_dir() / 'config.json'
        path.write_text('{"backend":"nano","model":""}')
        self.assertEqual(storage.load_config()['backend'], 'ollama')
        self.assertEqual(json.loads(path.read_text())['model'], 'qwen2.5-coder:1.5b')
        storage.save_config({'backend': 'ollama', 'model': 'mine:custom'})
        self.assertEqual(storage.load_config()['model'], 'mine:custom')

    def test_model_names_cannot_be_cli_options_or_paths(self):
        for name in ['', '--help', 'other model', '../model', 'x\nsecret']:
            with self.subTest(name=name), self.assertRaises(storage.StorageError):
                storage.save_config({'backend': 'ollama', 'model': name})

    def test_backend_uses_dedicated_endpoint_and_cold_load_timeout(self):
        seen = []
        def api(url, payload=None, timeout=35, headers=None):
            seen.append((url, timeout))
            return {'done': True, 'response': '{"command":"pwd"}'}
        with patch.object(backends, 'local_request', side_effect=api):
            backends.ollama_generate('context', 'mine:custom')
        self.assertEqual(seen, [('http://127.0.0.1:11435/api/generate', 120)])

    def test_port_rejects_invalid_or_privileged_values(self):
        for value in ['word', '0', '80', '65536']:
            with patch.dict(os.environ, {'NANO_SHELL_OLLAMA_PORT': value}), self.assertRaises(runtime.RuntimeError):
                runtime.endpoint()

    def test_missing_binary_reports_incomplete_installation(self):
        self.binary.unlink()
        with patch.object(backends, 'local_request', side_effect=refused()), self.assertRaisesRegex(runtime.RuntimeError, 'installation'):
            runtime.ensure_running()

    def test_start_reuses_owned_process_and_private_env(self):
        result = self.start_service()
        self.assertTrue(result['running'])
        record = runtime._owned_record()
        with patch.object(backends, 'local_request', side_effect=self.fake_api):
            runtime.ensure_running()
        self.assertEqual(runtime._owned_record()['pid'], record['pid'])
        env = Path('/proc', str(record['pid']), 'environ').read_bytes()
        self.assertIn(b'OLLAMA_HOST=127.0.0.1:11435\0', env)
        self.assertIn(b'OLLAMA_NO_CLOUD=1\0', env)
        self.assertIn(('OLLAMA_MODELS=' + str(storage.state_dir() / 'models')).encode() + b'\0', env)
        self.assertFalse((storage.state_dir() / 'daemon.log').exists())
        self.assertEqual((storage.state_dir() / 'models').stat().st_mode & 0o777, 0o700)

    def test_concurrent_starts_share_one_owned_pid(self):
        self.addCleanup(self.cleanup_service)
        results, errors = [], []
        def start():
            try:
                results.append(runtime.ensure_running()['pid'])
            except Exception as exc:
                errors.append(exc)
        with patch.object(backends, 'local_request', side_effect=self.fake_api):
            threads = [threading.Thread(target=start) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 4)
        self.assertEqual(len(set(results)), 1)

    def test_existing_unowned_service_is_never_adopted(self):
        with patch.object(backends, 'local_request', return_value={'models': []}), self.assertRaisesRegex(runtime.RuntimeError, 'owned'):
            runtime.ensure_running()
        self.assertIsNone(runtime._owned_record())

    def test_start_failure_removes_owned_record(self):
        self.binary.write_text('#!/usr/bin/python3\nraise SystemExit(7)\n')
        with patch.object(backends, 'local_request', side_effect=refused()), self.assertRaisesRegex(runtime.RuntimeError, 'exit'):
            runtime.ensure_running()
        self.assertIsNone(runtime._owned_record())

    def test_symlink_lock_is_rejected(self):
        target = self.directory / 'target'
        target.touch()
        (storage.state_dir() / 'runtime.lock').symlink_to(target)
        with self.assertRaises((storage.StorageError, OSError, runtime.RuntimeError)):
            runtime.ensure_running()

    def test_stop_verifies_actual_owned_exit(self):
        self.start_service()
        with patch.object(backends, 'local_request', side_effect=refused()):
            result = runtime.stop()
        self.assertEqual(result['stopped'], True)
        self.assertEqual(result['changed'], True)
        self.assertIsNone(runtime._owned_record())

    def test_forged_pid_does_not_kill_unrelated_process(self):
        process = subprocess.Popen(['/bin/sleep', '60'])
        self.addCleanup(process.wait)
        self.addCleanup(process.terminate)
        (storage.state_dir() / 'runtime.json').write_text(json.dumps({
            'pid': process.pid, 'start': 'fake', 'endpoint': runtime.endpoint(), 'binary': str(self.binary)}))
        with patch.object(backends, 'local_request', side_effect=refused()):
            result = runtime.stop()
        self.assertTrue(result['stopped'])
        self.assertIsNone(process.poll())

    def test_stop_readback_auth_or_network_error_is_not_success(self):
        self.start_service()
        with patch.object(backends, 'local_request', side_effect=backends.BackendError('unknown')), self.assertRaises(backends.BackendError):
            runtime.stop()

    def test_generate_auto_starts_without_pulling(self):
        seen = []
        def api(url, payload=None, timeout=35, headers=None):
            seen.append(url)
            return self.fake_api(url, payload, timeout, headers)
        self.addCleanup(self.cleanup_service)
        with patch.object(backends, 'local_request', side_effect=api):
            result = cli.generate('where am I')
        self.assertEqual(result['command'], 'pwd')
        self.assertTrue(seen[-1].endswith('/api/generate'))

    def test_routine_missing_model_fails_without_pull(self):
        self.start_service()
        with patch.object(backends, 'local_request', return_value={'models': []}), self.assertRaisesRegex(runtime.RuntimeError, 'installation'):
            cli.generate('where am I')

    def test_provision_validates_smoke_without_execution(self):
        self.addCleanup(self.cleanup_service)
        with patch.object(backends, 'local_request', side_effect=self.fake_api), patch.object(cli.policy, 'execute') as execute:
            result = runtime.provision('qwen2.5-coder:1.5b')
        self.assertTrue(result['ready'])
        self.assertTrue(result['inference_verified'])
        execute.assert_not_called()

    def test_provision_missing_model_requires_pull_and_readback(self):
        self.start_service()
        def api(url, payload=None, timeout=35, headers=None):
            return {'models': []}
        with patch.object(backends, 'local_request', side_effect=api), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), self.assertRaisesRegex(runtime.RuntimeError, 'inventory'):
            runtime.provision('qwen2.5-coder:1.5b')

    def test_setup_failure_never_reports_ready(self):
        with patch.object(backends, 'local_request', side_effect=refused()), contextlib.redirect_stderr(io.StringIO()):
            self.binary.unlink()
            self.assertEqual(cli.main(['setup']), 1)

    def test_inventory_status_does_not_claim_verified_ready(self):
        self.start_service()
        with patch.object(backends, 'local_request', side_effect=self.fake_api):
            status = cli.backend_status()
        self.assertTrue(status['model_installed'])
        self.assertFalse(status['ready'])

    def test_stop_uninitialized_installation_requires_no_socket(self):
        with patch.object(backends, 'local_request', side_effect=AssertionError('no socket for no owned process')):
            result = runtime.stop()
        self.assertTrue(result['stopped'])
        self.assertFalse(result['changed'])
        self.assertFalse(result['owned'])

    def test_start_timeout_terminates_owned_process(self):
        with patch.object(runtime, 'START_TIMEOUT', 0.05), patch.object(backends, 'local_request', side_effect=refused()), self.assertRaisesRegex(runtime.RuntimeError, 'timed out'):
            runtime.ensure_running()
        self.assertIsNone(runtime._owned_record())

    def test_failed_pull_is_not_installed_success(self):
        self.start_service()
        with patch.object(backends, 'local_request', return_value={'models': []}), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 9)), self.assertRaisesRegex(runtime.RuntimeError, 'download failed'):
            runtime.provision('qwen2.5-coder:1.5b')

    def test_provision_rejects_unsafe_or_non_pwd_smoke(self):
        self.start_service()
        for command in ['rm .', 'ls', None]:
            def api(url, payload=None, timeout=35, headers=None):
                if url.endswith('/api/tags'):
                    return {'models': [{'name': 'qwen2.5-coder:1.5b'}]}
                response = {'command': command} if command is not None else {'answer': 'This is an answer, not the requested smoke check.'}
                return {'done': True, 'response': json.dumps(response)}
            with self.subTest(command=command), patch.object(backends, 'local_request', side_effect=api), self.assertRaises((runtime.RuntimeError, cli.policy.PolicyError)):
                runtime.provision('qwen2.5-coder:1.5b')
        self.assertNotIn('verified_model', runtime._owned_record())

    def test_foreground_supervisor_handles_signal_and_verifies_shutdown(self):
        supervisor = self.foreground_supervisor()
        supervisor.send_signal(signal.SIGTERM)
        out, err = supervisor.communicate(timeout=5)
        self.assertEqual(supervisor.returncode, 0, err.decode())
        self.assertIsNone(runtime._owned_record())

    def foreground_supervisor(self):
        helper = self.directory / 'foreground.py'
        helper.write_text('''from nano_shell import runtime, backends
from unittest.mock import patch
from urllib.error import URLError
import errno
import os
from pathlib import Path
def api(url, payload=None, timeout=35, headers=None):
    if runtime._owned_record() is not None:
        Path(os.environ['TEST_SUPERVISOR_READY']).touch()
        return {'models': []}
    error = backends.BackendError('refused')
    error.__cause__ = URLError(ConnectionRefusedError(errno.ECONNREFUSED, 'refused'))
    raise error
with patch.object(backends, 'local_request', side_effect=api):
    runtime.ensure_running(foreground=True)
''')
        import time
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        marker = self.directory / 'supervisor-ready'
        env['TEST_SUPERVISOR_READY'] = str(marker)
        supervisor = subprocess.Popen([os.sys.executable, str(helper)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(supervisor.wait)
        self.addCleanup(lambda: supervisor.kill() if supervisor.poll() is None else None)
        deadline = time.monotonic() + 5
        while not marker.exists() and supervisor.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(marker.exists())
        self.assertIsNotNone(runtime._owned_record())
        return supervisor

    def test_administrative_stop_exits_supervisor_successfully(self):
        self.start_service()
        original = runtime._owned_record()['pid']
        supervisor = self.foreground_supervisor()
        self.assertEqual(runtime._owned_record()['pid'], original)
        with patch.object(backends, 'local_request', side_effect=refused()):
            runtime.stop()
        out, err = supervisor.communicate(timeout=5)
        self.assertEqual(supervisor.returncode, 0, err.decode())

    def test_status_ownership_change_during_inventory_cannot_report_ready(self):
        self.start_service()
        with patch.object(backends, 'local_request', side_effect=self.fake_api):
            runtime.provision('qwen2.5-coder:1.5b')
        record = runtime._owned_record()
        def api(url, payload=None, timeout=35, headers=None):
            runtime._terminate(record)
            return {'models': [{'name': 'qwen2.5-coder:1.5b'}]}
        with patch.object(backends, 'local_request', side_effect=api), self.assertRaises(runtime.RuntimeError):
            runtime.status('qwen2.5-coder:1.5b')

    def test_daemon_environment_strips_user_cloud_and_proxy_overrides(self):
        with patch.dict(os.environ, {'OLLAMA_HOST': 'remote.example', 'OLLAMA_MODELS': '/shared', 'OLLAMA_NO_CLOUD': '0', 'HTTPS_PROXY': 'https://remote.example', 'http_proxy': 'http://remote.example'}):
            env = runtime.environment()
        self.assertEqual(env['OLLAMA_HOST'], '127.0.0.1:11435')
        self.assertEqual(env['OLLAMA_NO_CLOUD'], '1')
        self.assertNotIn('HTTPS_PROXY', env)
        self.assertNotIn('http_proxy', env)


if __name__ == '__main__':
    unittest.main()
