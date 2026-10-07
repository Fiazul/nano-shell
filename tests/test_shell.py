import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ShellTests(unittest.TestCase):
    def test_question_command_does_not_glob_two_letter_files(self):
        with tempfile.TemporaryDirectory(prefix="nano prompt ") as directory:
            home = Path(directory)
            (home / "ab").touch()
            launcher = home / "fake-launcher"
            launcher.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
            launcher.chmod(0o755)
            env = dict(os.environ, NANO_SHELL_BIN=str(launcher))
            script = 'shopt -s expand_aliases\nsource "' + str(ROOT / "shell/bash.sh") + '"\n?? show python processes\n'
            result = subprocess.run(["bash", "-c", script], env=env, cwd=home,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "ask\nshow\npython\nprocesses\n")

    def test_suggestion_inserts_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            launcher = Path(directory) / "fake-launcher"
            launcher.write_text('#!/usr/bin/env bash\n[[ $1 == suggest ]] || exit 4\nprintf "pwd\\n"\n')
            launcher.chmod(0o755)
            env = dict(os.environ, NANO_SHELL_BIN=str(launcher))
            script = ('source "' + str(ROOT / "shell/bash.sh") + '"\n'
                      'READLINE_LINE="?? show location"\n_nano_shell_insert\n'
                      'printf "%s:%s\\n" "$READLINE_LINE" "$READLINE_POINT"\n')
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "pwd:3\n")


if __name__ == "__main__":
    unittest.main()
