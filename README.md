# Nano Shell

A headless local AI assistant in your existing Linux terminal. Install once, open a terminal, and ask:

```bash
?? is docker up
?? what was the last downloaded file?
?? what process is listening on port 8000?
?? find the largest file here
?? show python processes
?? what files changed in this git repo?
?? who is Tuhin
```

Nano Shell shows `Running this command "…"` and runs validated read-only commands automatically. It starts its local model server automatically when needed. There is no browser window, account, cloud API key, or per-terminal setup command.

## Install

Install directly from the [public GitHub repository](https://github.com/Fiazul/nano-shell), from any directory:

```bash
npx --yes github:Fiazul/nano-shell install
```

To use the existing local checkout on this machine:

```bash
npx --yes "$HOME/Desktop/cli-assistant/nano-shell" install
```

The public command, **pending npm publication**, will be:

```bash
npx --yes @fiazul/nano-shell install
```

The installer searches for an existing headless Nano Shell Git checkout in the current directory/parents and common home project folders, then uses the bundled package source if present. If neither is available it clones `https://github.com/fiazul/nano-shell.git` at `main`. Search is bounded to 5000 directories and six levels, skips caches and symlinks, and accepts renamed checkout folders with the correct package/runtime structure. Set `NANO_SHELL_SOURCE_DIR` to choose an explicit source, or `NANO_SHELL_SEARCH_ROOTS` to add colon-separated locations such as mounted disks. `NANO_SHELL_REPO` and `NANO_SHELL_REF` can select a different Git source.

The npm package bundles its Python source, shell hooks and installer. It has no npm dependencies or automatic postinstall actions. Explicit `install` handles the runtime/model download, validates an inference response, and installs the terminal hooks. An optional systemd user service and on-demand startup handle subsequent terminal sessions.

Open a new terminal and use `??`. To activate it in the terminal that was already open during installation, run `source ~/.bashrc` once (Zsh: `source ~/.zshrc`). A child installer cannot alter its parent shell's aliases. No browser window or routine setup command is required.

Custom install through the npm package:

```bash
npx --yes @fiazul/nano-shell install --model qwen2.5-coder:3b --prefix "$HOME/tools"
```

Until npm publication, substitute `github:Fiazul/nano-shell` or the absolute local checkout path for `@fiazul/nano-shell` in npx commands. Developers can also run `bash install.sh` inside a checkout.

Requirements: Linux x86_64 or ARM64, Node.js 18+ with npm/npx, Python 3.10+, Bash, enough disk/RAM for the model, and internet for initial downloads. The runtime installer uses curl and zstd, or the system libzstd when the zstd executable is absent. No sudo is used. Automatic downloads use [Ollama's official Linux distribution](https://docs.ollama.com/linux).

The default model is [qwen2.5-coder:1.5b](https://ollama.com/library/qwen2.5-coder:1.5b); its listed model download is approximately 986 MB, plus the Ollama runtime. CPU inference is supported, and latency depends on your hardware. To choose another local model during installation:

```bash
bash install.sh --model qwen2.5-coder:3b
```

Existing local model choices are preserved. Earlier Chrome/Nano configuration migrates to the headless default. This version uses Ollama local inference, rather than Chrome's Gemini Nano.

## Everyday use

Use a space after `??`, and quote questions containing shell punctuation:

```bash
?? 'what files changed today?'
```

Type a question at the prompt and press **Ctrl+G** to insert a shell-safe command suggestion. Review it and press Enter yourself. The shortcut replaces that key's existing binding while its hook is loaded; it sends a request only when invoked.

Normal `??` requests run only commands accepted by the read-only policy, without a confirmation prompt. `--yes` remains accepted for older scripts:

```bash
?? --yes 'show python processes'
```

Questions such as `?? who is Tuhin`, `?? "who's Tuhin?"`, and `?? what do you know about Tuhin` search text files under the current directory with case-insensitive, literal matching. Nano shows file-and-line evidence, then asks the local model to explain those matches. It reports when nothing matches; it does not infer that a person does not exist. Change directory to choose the search scope.

Searches skip binary files, discovered symlinks, `.git`, `node_modules`, `.cache`, and `.venv`. Each search stops after five seconds or 16 KiB of captured output. A partial search, timeout, read error, or failed explanation remains visibly incomplete. Other general questions can return plain-text answers; the model cannot execute those answers.

The runtime starts on demand, including when a service is unavailable or after reboot. Models are downloaded by installation, not silently by ordinary questions. If installation or the model download fails, it reports failure. Run the installer again to resume provisioning.

## Installation options

```bash
bash install.sh --prefix "$HOME/tools" --no-service
```

`--no-shell` skips shell hooks. `--no-start` creates a source-only installation for CI/offline preparation; it skips runtime/model provisioning and service activation, so it does not claim the assistant is ready.

The launcher lives at `<prefix>/bin/nano-shell` (`~/.local/bin/nano-shell` by default); hooks use its absolute path. Administrative commands `status`, `doctor`, `start`, `stop`, and `config --model NAME` are available, but routine terminal use requires none of them. The `setup` subcommand remains as an installer compatibility entrypoint; it never opens a browser.

The dedicated headless endpoint is `127.0.0.1:11435`, separate from an existing system Ollama service. Set `NANO_SHELL_OLLAMA_PORT` consistently during installation and use to change it. `NANO_SHELL_OLLAMA_BIN` can select an existing executable. Nano Shell starts the dedicated process with cloud features disabled and keeps its models in its private XDG state directory.

## Command policy and privacy

Commands run automatically as your own user through validated argument arrays and explicit pipelines. The policy rejects unknown programs, unsupported options, command substitutions, redirects, interpreters and write operations. Docker support is limited to `info`, `version` and `ps`. It cannot run `docker run`, `exec`, `stop`, or Compose mutations. Suggestions are rendered with quoted arguments and trusted absolute executable paths before insertion into a shell.

This policy is not an OS sandbox. Approved reads can show file contents and process details. Model correctness is not guaranteed; inspect the suggested command. Actual command failures remain failures. A model output outside the supported subset is refused.

The model receives your question, current directory and bounded directory names. For local identity searches, it also receives matching file contents and source locations, within the search limits above. History is opt-in through `?? --history 'question'`, sharing at most the last 4096 saved characters from `HISTFILE` or `~/.bash_history`. Local requests bypass proxies and refuse redirects. There is no telemetry. Ollama cloud features are disabled for the managed server.

## Remove

```bash
npx --yes @fiazul/nano-shell uninstall
```

Uninstall stops only verified owned processes, removes its service/files/hooks and preserves models/configuration by default. Add `--purge` to remove Nano Shell's model storage and configuration too. Removing an old installation preserves the current installation's hooks and service. Open a new terminal afterward to unload its aliases and bindings.

## Develop and publish

```bash
python3 -m unittest discover -s tests -v
node --check bin/cli.cjs
node --check bin/source.cjs
python3 -m compileall -q nano_shell
bash -n install.sh uninstall.sh shell/bash.sh scripts/publish.sh
```

See [verification details](docs/verification.md). Fixtures are explicitly mocks; they do not prove a downloaded model ran. Legacy bridge/browser files remain for earlier protocol regression tests, and are not part of the active headless runtime.

To publish as the repository owner, authenticate GitHub CLI with `gh auth login -h github.com`, then run `bash scripts/publish.sh`. It checks account `fiazul`, pushes `main`, and verifies the remote commit and public visibility. The GitHub repository is public; Git-backed npx installation requires no npm registry release. npm publishing is separate: after `npm login` as the scope owner, run `bash scripts/publish-npm.sh`; it publishes the packed artifact and checks the registry version and SHA-512 integrity. Public npm installation remains pending until that readback succeeds.

MIT licensed.
