# Nano Shell

A headless local AI assistant in your existing Linux terminal. Install once, open a terminal, and ask:

```bash
?? is docker up
?? what was the last downloaded file?
?? what process is listening on port 8000?
?? find the largest file here
?? show python processes
?? what files changed in this git repo?
```

Nano Shell shows the generated read-only command and asks before running it. It starts its local model server automatically when needed. There is no browser window, account, cloud API key, or per-terminal setup command.

## Install

From this checkout:

```bash
bash nano-shell/install.sh
```

Or, when inside the repository directory, `bash install.sh`.

The installer installs or reuses an Ollama executable, downloads the selected model, and verifies an inference response before reporting ready. It also installs Bash/Zsh hooks and an optional systemd user service. Open a new terminal and use `??`. To activate the shortcut in the terminal that was already open during installation, run `source ~/.bashrc` once (Zsh: `source ~/.zshrc`). A child installer cannot alter its parent shell's aliases.

Once the public GitHub repository is published, the same installation can be run as:

```bash
curl -fsSL https://raw.githubusercontent.com/fiazul/nano-shell/main/install.sh | bash
```

Requirements: Linux x86_64 or ARM64, Python 3.10+, Bash, enough disk/RAM for the model, and internet for initial downloads. The runtime installer uses curl and zstd, or the system libzstd when the zstd executable is absent. No sudo is used. Automatic downloads use [Ollama's official Linux distribution](https://docs.ollama.com/linux).

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

Normal `??` requests show the command and ask `[y/N]`. To deliberately skip confirmation within the same vetted read-only policy:

```bash
?? --yes 'show python processes'
```

The runtime starts on demand, including when a service is unavailable or after reboot. Models are downloaded by installation, not silently by ordinary questions. If installation or the model download fails, it reports failure. Run the installer again to resume provisioning.

## Installation options

```bash
bash install.sh --prefix "$HOME/tools" --no-service
```

`--no-shell` skips shell hooks. `--no-start` creates a source-only installation for CI/offline preparation; it skips runtime/model provisioning and service activation, so it does not claim the assistant is ready.

The launcher lives at `<prefix>/bin/nano-shell` (`~/.local/bin/nano-shell` by default); hooks use its absolute path. Administrative commands `status`, `doctor`, `start`, `stop`, and `config --model NAME` are available, but routine terminal use requires none of them. The `setup` subcommand remains as an installer compatibility entrypoint; it never opens a browser.

The dedicated headless endpoint is `127.0.0.1:11435`, separate from an existing system Ollama service. Set `NANO_SHELL_OLLAMA_PORT` consistently during installation and use to change it. `NANO_SHELL_OLLAMA_BIN` can select an existing executable. Nano Shell starts the dedicated process with cloud features disabled and keeps its models in its private XDG state directory.

## Command policy and privacy

Commands run as your own user through validated argument arrays and explicit pipelines. The policy rejects unknown programs, unsupported options, command substitutions, redirects, interpreters and write operations. Docker support is limited to `info`, `version` and `ps`. It cannot run `docker run`, `exec`, `stop`, or Compose mutations. Suggestions are rendered with quoted arguments and trusted absolute executable paths before insertion into a shell.

This policy is not an OS sandbox. Approved reads can show file contents and process details. Model correctness is not guaranteed; inspect the suggested command. Actual command failures remain failures. A model output outside the supported subset is refused.

The model receives your question, current directory and bounded directory names. History is opt-in through `?? --history 'question'`, sharing at most the last 4096 saved characters from `HISTFILE` or `~/.bash_history`. Local requests bypass proxies and refuse redirects. There is no telemetry. Ollama cloud features are disabled for the managed server.

## Remove

```bash
~/.local/bin/nano-shell uninstall
```

Uninstall stops only verified owned processes, removes its service/files/hooks and preserves models/configuration by default. Add `--purge` to remove Nano Shell's model storage and configuration too. Removing an old installation preserves the current installation's hooks and service. Open a new terminal afterward to unload its aliases and bindings.

## Develop and publish

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nano_shell
bash -n install.sh uninstall.sh shell/bash.sh scripts/publish.sh
```

See [verification details](docs/verification.md). Fixtures are explicitly mocks; they do not prove a downloaded model ran. Legacy bridge/browser files remain for earlier protocol regression tests, and are not part of the active headless runtime.

To publish as the repository owner, authenticate GitHub CLI with `gh auth login -h github.com`, then run `bash scripts/publish.sh`. It checks account `fiazul`, pushes `main`, and verifies the remote commit and public visibility. The hosted install URL is pending until publication succeeds.

MIT licensed.
