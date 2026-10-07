import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class InstallTests(unittest.TestCase):
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
