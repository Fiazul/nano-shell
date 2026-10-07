import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TerminalTests(unittest.TestCase):
    def run_fixture_question(self, home, command, answer):
        script = ('import json,sys\nfrom unittest.mock import patch\n'
                  'from nano_shell import cli\n'
                  'with patch.object(cli, "generate", return_value={"command":json.loads(sys.argv[1])}), '
                  'patch.object(sys.stdin, "isatty", return_value=True):\n'
                  '    raise SystemExit(cli.main(["ask", "last downloaded file"]))\n')
        return subprocess.run([sys.executable, "-c", script, json.dumps(command)],
                              env=dict(os.environ, HOME=str(home), PYTHONPATH=str(ROOT)),
                              cwd=home, input=answer, capture_output=True, text=True, timeout=10)

    def test_original_download_pipeline_runs_without_confirmation(self):
        with tempfile.TemporaryDirectory(prefix="nano terminal ") as directory:
            home = Path(directory)
            downloads = home / "Downloads"
            downloads.mkdir()
            old, new = downloads / "old.txt", downloads / "new file.txt"
            old.write_text("old")
            new.write_text("new")
            os.utime(old, (1000000000, 1000000000))
            os.utime(new, (1700000000, 1700000000))
            command = "find ~/Downloads -type f -printf '%T@ %p\\n' | sort -nr | head -1"
            result = self.run_fixture_question(home, command, "n\n")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(new), result.stdout)
            self.assertNotIn(str(old), result.stdout)
            self.assertNotIn("[y/N]", result.stderr)
            self.assertIn('Running this command "', result.stderr)



if __name__ == "__main__":
    unittest.main()
