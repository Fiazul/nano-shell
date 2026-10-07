#!/usr/bin/env bash
set -euo pipefail
prefix="${NANO_SHELL_PREFIX:-$HOME/.local}"
purge=0
while (($#)); do
  case "$1" in
    --prefix) prefix="${2:?--prefix needs a path}"; shift 2 ;;
    --purge) purge=1; shift ;;
    --help) printf '%s\n' 'Usage: nano-shell uninstall [--purge] [--prefix PATH]'; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
done
prefix="$(python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "$prefix")"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
ownership="$(python3 - "$prefix" "$unit_dir/nano-shell.service" <<'PY'
from pathlib import Path
import json
import os
import shlex
import sys
prefix, unit = Path(sys.argv[1]), Path(sys.argv[2])
configuration = Path(os.environ.get("NANO_SHELL_CONFIG_DIR") or
    str(Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "nano-shell")).expanduser()
marker = configuration / "installation.json"
if marker.is_symlink():
    raise SystemExit("Refusing a symlink installation marker.")
def escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
expected = 'ExecStart="' + escape(prefix / "bin/nano-shell") + '" start --foreground'
owns_unit = unit.exists() and expected in unit.read_text().splitlines()
if marker.exists():
    owns_install = json.loads(marker.read_text()).get("prefix") == str(prefix)
else:
    ownerline = "export NANO_SHELL_BIN=" + shlex.quote(str(prefix / "bin/nano-shell"))
    owns_install = owns_unit or any((Path.home() / name).exists() and
        ownerline in (Path.home() / name).read_text().splitlines() for name in (".bashrc", ".zshrc"))
print(int(owns_install), int(owns_unit))
PY
)"
read -r owns_install owns_unit <<< "$ownership"
if ((owns_unit)) && command -v systemctl >/dev/null; then
  systemctl --user disable --now nano-shell.service >/dev/null 2>&1 || true
fi
if ((owns_install)) && [[ -x "$prefix/bin/nano-shell" ]]; then
  "$prefix/bin/nano-shell" stop || { printf '%s\n' 'Daemon could not be stopped; aborting uninstall.' >&2; exit 1; }
fi
python3 - "$prefix" "$purge" "$owns_install" "$owns_unit" <<'PY'
from pathlib import Path
import os
import shutil
import sys
import shlex
prefix = Path(sys.argv[1])
ownerline = "export NANO_SHELL_BIN=" + shlex.quote(str(prefix / "bin/nano-shell"))
for filename in (".bashrc", ".zshrc"):
    rc = Path.home() / filename
    if not rc.exists():
        continue
    output, block = [], None
    for line in rc.read_text().splitlines(keepends=True):
        if line.rstrip("\r\n") == "# >>> nano-shell >>>":
            if block is not None:
                raise SystemExit(f"Nested Nano Shell block in {rc}; repair it before uninstalling.")
            block = [line]
        elif line.rstrip("\r\n") == "# <<< nano-shell <<<" and block is not None:
            block.append(line)
            if ownerline not in [part.rstrip("\r\n") for part in block]:
                output.extend(block)
            block = None
        elif block is None:
            output.append(line)
        else:
            block.append(line)
    if block is not None:
        raise SystemExit(f"Unterminated Nano Shell block in {rc}; repair it before uninstalling.")
    rc.write_text("".join(output))
(prefix / "bin/nano-shell").unlink(missing_ok=True)
target = prefix / "share/nano-shell"
if target.exists():
    shutil.rmtree(target)
config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
if sys.argv[4] == "1":
    (config / "systemd/user/nano-shell.service").unlink(missing_ok=True)
configuration = Path(os.environ.get("NANO_SHELL_CONFIG_DIR", str(config / "nano-shell"))).expanduser()
if sys.argv[3] == "1":
    (configuration / "installation.json").unlink(missing_ok=True)
if sys.argv[2] == "1" and sys.argv[3] == "1":
    directories = [Path(os.environ.get("NANO_SHELL_CONFIG_DIR", str(config / "nano-shell"))),
                   Path(os.environ.get("NANO_SHELL_STATE_DIR",
                        str(Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "nano-shell")))]
    for directory in directories:
        directory = directory.resolve()
        protected = {Path.home().resolve(), prefix.resolve(), config.resolve(), Path("/")}
        if directory in protected or len(directory.parts) < 3:
            raise SystemExit(f"Refusing to purge unsafe directory: {directory}")
        if directory.exists():
            shutil.rmtree(directory)
PY
if ((owns_unit)) && command -v systemctl >/dev/null; then systemctl --user daemon-reload >/dev/null 2>&1 || true; fi
if ((!owns_install)); then printf '%s\n' 'Preserved the newer installation, its daemon, and shared configuration.'; fi
printf '%s\n' 'This Nano Shell installation was removed. Open a new terminal to unload its shell functions.'
