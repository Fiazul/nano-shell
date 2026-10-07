#!/usr/bin/env bash
set -euo pipefail

prefix="${NANO_SHELL_PREFIX:-$HOME/.local}"
shell_hooks=1
service=1
start=1
model=""
while (($#)); do
  case "$1" in
    --prefix) prefix="${2:?--prefix needs a path}"; shift 2 ;;
    --no-shell) shell_hooks=0; shift ;;
    --no-service) service=0; shift ;;
    --no-start) start=0; shift ;;
    --model) model="${2:?--model needs a local model name}"; shift 2 ;;
    --help) printf '%s\n' 'Usage: bash install.sh [--prefix PATH] [--model NAME] [--no-shell] [--no-service] [--no-start]'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
command -v python3 >/dev/null || { printf '%s\n' 'Python 3.10+ is required.' >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  printf '%s\n' 'Python 3.10+ is required.' >&2; exit 1;
}
prefix="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$prefix")"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if ((start && service)) && [[ -e "$unit_dir/nano-shell.service" || -L "$unit_dir/nano-shell.service" ]]; then
  if ! python3 - "$unit_dir/nano-shell.service" "$prefix/bin/nano-shell" <<'PY'
from pathlib import Path
import sys
unit = Path(sys.argv[1])
escaped = sys.argv[2].replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
expected = 'ExecStart="' + escaped + '" start --foreground'
raise SystemExit(0 if not unit.is_symlink() and expected in unit.read_text().splitlines() else 1)
PY
  then
    printf '%s\n' 'nano-shell.service belongs to another installation; it was left unchanged. Use --no-service for on-demand startup.' >&2
    exit 1
  fi
fi
source_dir=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
fi
download_dir=""
trap 'if [[ -n "$download_dir" ]]; then rm -rf -- "$download_dir"; fi' EXIT
if [[ -z "$source_dir" || ! -f "$source_dir/nano_shell/__main__.py" ]]; then
  command -v curl >/dev/null || { printf '%s\n' 'curl is required to download Nano Shell.' >&2; exit 1; }
  command -v tar >/dev/null || { printf '%s\n' 'tar is required to download Nano Shell.' >&2; exit 1; }
  repo="${NANO_SHELL_REPO:-fiazul/nano-shell}"
  ref="${NANO_SHELL_REF:-main}"
  [[ "$repo" =~ ^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$ && "$ref" =~ ^[a-zA-Z0-9_.-]+$ ]] || {
    printf '%s\n' 'Invalid repository or release reference.' >&2; exit 2;
  }
  download_dir="$(mktemp -d)"
  printf 'Downloading %s (%s)…\n' "$repo" "$ref"
  curl -fSL --retry 2 --connect-timeout 15 --max-time 120 \
    "https://github.com/$repo/archive/$ref.tar.gz" -o "$download_dir/source.tar.gz"
  mkdir "$download_dir/source"
  tar -xzf "$download_dir/source.tar.gz" --strip-components=1 -C "$download_dir/source"
  source_dir="$download_dir/source"
fi
[[ -f "$source_dir/nano_shell/__main__.py" ]] || { printf '%s\n' 'Incomplete source archive.' >&2; exit 1; }
target="$prefix/share/nano-shell"
mkdir -p -- "$target" "$prefix/bin"
python3 - "$source_dir" "$target" "$prefix" "$shell_hooks" <<'PY'
from pathlib import Path
import os
import shlex
import shutil
import sys
import json
import tempfile

source, target, prefix = map(Path, sys.argv[1:4])
for name in ("nano_shell", "web", "shell"):
    if (source / name).resolve() != (target / name).resolve():
        shutil.copytree(source / name, target / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
for name in ("uninstall.sh", "LICENSE"):
    if (source / name).resolve() != (target / name).resolve():
        shutil.copy2(source / name, target / name)
launcher = prefix / "bin/nano-shell"
launcher.write_text("#!/usr/bin/env bash\nset -e\n"
    + "if [[ ${1:-} == uninstall ]]; then\n  shift\n  exec bash "
    + shlex.quote(str(target / "uninstall.sh")) + " --prefix "
    + shlex.quote(str(prefix)) + ' "$@"\nfi\n'
    + "exec python3 -I -c " + shlex.quote("import sys; sys.path.insert(0, " + repr(str(target))
    + "); from nano_shell.cli import main; raise SystemExit(main())") + ' "$@"\n')
launcher.chmod(0o755)
if sys.argv[4] == "1":
    for filename, script in ((".bashrc", "bash.sh"), (".zshrc", "zsh.sh")):
        rc = Path.home() / filename
        text = rc.read_text() if rc.exists() else ""
        begin, end = "# >>> nano-shell >>>", "# <<< nano-shell <<<"
        lines = text.splitlines(keepends=True)
        output, managed = [], False
        for line in lines:
            if line.rstrip("\r\n") == begin:
                managed = True
            elif line.rstrip("\r\n") == end and managed:
                managed = False
            elif not managed:
                output.append(line)
        if managed:
            raise SystemExit(f"Unterminated Nano Shell block in {rc}; repair it before installing.")
        text = "".join(output)
        if text and not text.endswith("\n"):
            text += "\n"
        block = (begin + "\nexport NANO_SHELL_BIN=" + shlex.quote(str(launcher))
                 + "\nsource " + shlex.quote(str(target / "shell" / script))
                 + "\n" + end + "\n")
        rc.write_text(text + block)
configuration = Path(os.environ.get("NANO_SHELL_CONFIG_DIR") or
    str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "nano-shell")).expanduser()
if configuration.is_symlink():
    raise SystemExit("Nano Shell configuration directory must not be a symlink.")
configuration.mkdir(mode=0o700, parents=True, exist_ok=True)
configuration.chmod(0o700)
marker = configuration / "installation.json"
if marker.is_symlink():
    raise SystemExit("Nano Shell installation marker must not be a symlink.")
with tempfile.NamedTemporaryFile(mode="w", dir=configuration, delete=False) as temporary:
    json.dump({"prefix": str(prefix)}, temporary)
    temporary_path = Path(temporary.name)
os.replace(temporary_path, marker)
PY
printf 'Installed Nano Shell files: %s\n' "$prefix/bin/nano-shell"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
if ((start)); then
  if [[ -f "$unit_dir/nano-shell.service" ]] && command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
    if python3 - "$unit_dir/nano-shell.service" "$prefix/bin/nano-shell" <<'PY'
from pathlib import Path
import sys
escaped = sys.argv[2].replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
raise SystemExit(0 if 'ExecStart="' + escaped + '" start --foreground' in Path(sys.argv[1]).read_text().splitlines() else 1)
PY
    then systemctl --user disable --now nano-shell.service; fi
  fi
  "$prefix/bin/nano-shell" stop
  (cd -- "$target"; python3 -I -c 'import sys,runpy; sys.path.insert(0,sys.argv[1]); runpy.run_module("nano_shell.bootstrap",run_name="__main__")' "$target" >/dev/null)
  if [[ -n "$model" ]]; then "$prefix/bin/nano-shell" config --model "$model" >/dev/null; fi
fi
if ((start && service)) && command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  python3 - "$prefix" <<'PY'
from pathlib import Path
import os
import sys
prefix = Path(sys.argv[1])
directory = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "systemd/user"
directory.mkdir(parents=True, exist_ok=True)
def escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
unit = ("[Unit]\nDescription=Nano Shell headless local model runtime\nAfter=network.target\n\n"
        "[Service]\nType=simple\nExecStart=\"" + escape(prefix / "bin/nano-shell")
        + "\" start --foreground\nRestart=on-failure\nRestartSec=3\n")
for variable in ('NANO_SHELL_OLLAMA_PORT', 'NANO_SHELL_OLLAMA_BIN', 'NANO_SHELL_STATE_DIR',
                 'NANO_SHELL_CONFIG_DIR', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME'):
    if variable in os.environ:
        unit += 'Environment="' + variable + '=' + escape(os.environ[variable]) + '"\n'
unit += ("\n"
        "[Install]\nWantedBy=default.target\n")
(directory / "nano-shell.service").write_text(unit)
PY
  systemctl --user daemon-reload
  systemctl --user enable --now nano-shell.service
  printf '%s\n' 'Enabled nano-shell.service for this user.'
fi
if ((start)); then
  "$prefix/bin/nano-shell" setup
  "$prefix/bin/nano-shell" status >/dev/null
  printf '%s\n' 'Headless runtime and local model verified. Open a new terminal and use:'
  printf '%s\n' '?? is docker up'
  printf '%s\n' 'No browser or per-terminal setup is needed.'
else
  printf '%s\n' 'Source-only install: runtime/model provisioning skipped (--no-start).'
fi
printf '%s\n' 'For this existing terminal only: source ~/.bashrc (or ~/.zshrc).'
