import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
    def test_foreign_service_is_preserved_before_any_install_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            unit = home / 'config/systemd/user/nano-shell.service'
            unit.parent.mkdir(parents=True)
            original = '[Service]\nExecStart=/usr/bin/some-other-program\n'
            unit.write_text(original)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                       XDG_STATE_HOME=str(home / 'state'))
            result = subprocess.run(['bash', str(ROOT / 'install.sh')], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('another installation', result.stderr)
            self.assertEqual(unit.read_text(), original)
            self.assertFalse((home / '.local/bin/nano-shell').exists())
            self.assertFalse((home / '.bashrc').exists())

    def test_default_install_provisions_without_user_setup_instruction(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            fakebin = home / 'fakebin'
            fakebin.mkdir()
            calls = home / 'calls'
            python = fakebin / 'python3'
            python.write_text('#!/usr/bin/env bash\n'
                              'if [[ ${1:-} == -I && ${2:-} == -c && ${3:-} == *"nano_shell.bootstrap"* ]]; then\n'
                              '  echo "bootstrap:$PWD" >> "$TEST_CALLS"; echo /fake/ollama; exit 0\nfi\n'
                              'if [[ ${1:-} == -I && ${2:-} == -c && ${3:-} == *"from nano_shell.cli import main"* && ${4:-} != --help ]]; then\n'
                              '  echo "$4" >> "$TEST_CALLS"\n'
                              '  if [[ ${4:-} == setup && ${TEST_PROVISION_FAIL:-0} == 1 ]]; then exit 9; fi\n'
                              '  exit 0\nfi\n'
                              'exec ' + __import__('shlex').quote(__import__('sys').executable) + ' "$@"\n')
            python.chmod(0o755)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                       XDG_STATE_HOME=str(home / 'state'), TEST_CALLS=str(calls),
                       PATH=str(fakebin) + os.pathsep + os.environ['PATH'])
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), '--no-service'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('bootstrap', calls.read_text())
            self.assertIn('bootstrap:' + str(home / '.local/share/nano-shell'), calls.read_text())
            self.assertIn('setup', calls.read_text())
            self.assertIn('status', calls.read_text())
            self.assertNotIn('Chrome', result.stdout)
            self.assertNotIn('nano-shell setup', result.stdout)
            self.assertIn('??', result.stdout)
            env['TEST_PROVISION_FAIL'] = '1'
            failed = subprocess.run(['bash', str(ROOT / 'install.sh'), '--no-service'],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(failed.returncode, 9)
            self.assertNotIn('Headless runtime and local model verified', failed.stdout)

    def test_uninstall_old_prefix_preserves_current_hooks_and_service(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            a, b = home / "prefix-a", home / "prefix-b"
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                       XDG_STATE_HOME=str(home / "state"))
            for prefix in (a, b):
                result = subprocess.run(["bash", str(ROOT / "install.sh"), "--prefix", str(prefix),
                                         "--no-service", "--no-start"], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            bashrc = (home / ".bashrc").read_text()
            zshrc = (home / ".zshrc").read_text()
            unit = home / "config/systemd/user/nano-shell.service"
            unit.parent.mkdir(parents=True)
            content = f'[Service]\nExecStart="{b}/bin/nano-shell" start --foreground\n'
            unit.write_text(content)
            result = subprocess.run([str(a / "bin/nano-shell"), "uninstall"], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((home / ".bashrc").read_text(), bashrc)
            self.assertEqual((home / ".zshrc").read_text(), zshrc)
            self.assertEqual(unit.read_text(), content)
            self.assertTrue((b / "bin/nano-shell").exists())

    def test_streamed_installer_downloads_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            archive = home / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                for name in ("nano_shell", "web", "shell", "uninstall.sh", "LICENSE"):
                    tar.add(ROOT / name, arcname="nano-shell-main/" + name)
            fakebin = home / "fakebin"
            fakebin.mkdir()
            curl = fakebin / "curl"
            curl.write_text('#!/usr/bin/env bash\nwhile [[ $1 != -o ]]; do shift; done\ncp "$TEST_ARCHIVE" "$2"\n')
            curl.chmod(0o755)
            env = dict(os.environ, HOME=str(home), TEST_ARCHIVE=str(archive),
                       PATH=str(fakebin) + os.pathsep + os.environ["PATH"])
            result = subprocess.run(["bash", "-s", "--", "--no-service", "--no-start", "--no-shell"],
                                    input=(ROOT / "install.sh").read_text(), env=env,
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / ".local/bin/nano-shell").exists())

    def test_install_twice_and_uninstall_preserves_shell_files(self):
        with tempfile.TemporaryDirectory(prefix="nano home ") as directory:
            home = Path(directory)
            prefix = home / "local prefix"
            bashrc = home / ".bashrc"
            zshrc = home / ".zshrc"
            bashrc.write_text("# existing bash config\nexport MY_SETTING=kept\n")
            zshrc.write_text("# existing zsh config\n")
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                       XDG_STATE_HOME=str(home / "state"))
            command = ["bash", str(ROOT / "install.sh"), "--prefix", str(prefix),
                       "--no-service", "--no-start"]
            for _ in range(2):
                result = subprocess.run(command, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(bashrc.read_text().count("# >>> nano-shell >>>"), 1)
            self.assertEqual(zshrc.read_text().count("# >>> nano-shell >>>"), 1)
            launcher = prefix / "bin/nano-shell"
            result = subprocess.run([str(launcher), "--help"], env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ask", result.stdout)
            shadow = home / 'nano_shell'
            shadow.mkdir()
            (shadow / '__init__.py').write_text('raise RuntimeError("must not import from terminal cwd")\n')
            (home / 'argparse.py').write_text('raise RuntimeError("must not import stdlib from terminal cwd")\n')
            result = subprocess.run([str(launcher), '--help'], env=env, cwd=home,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            check = subprocess.run(["bash", "-c", 'shopt -s expand_aliases; source "$HOME/.bashrc"; type "??"'],
                                   env=env, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            result = subprocess.run([str(launcher), "uninstall", "--prefix", str(prefix)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(bashrc.read_text(), "# existing bash config\nexport MY_SETTING=kept\n")
            self.assertEqual(zshrc.read_text(), "# existing zsh config\n")
            self.assertFalse(launcher.exists())
            self.assertFalse((prefix / "share/nano-shell").exists())


if __name__ == "__main__":
    unittest.main()
