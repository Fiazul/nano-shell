# Nano Shell

Ask your Linux terminal a question. Get a command, inspect it, then run it.

```text
$ ?? what was the last downloaded file?
→ find ~/Downloads -type f -printf '%T@ %p\n' | sort -nr | head -1
Run it? [y/N]
```

Works inside your existing Bash or Zsh terminal. Uses Gemini Nano through Chrome, or an explicitly selected local Ollama model. No cloud API key, root access, or Python dependencies.

## Install

Requires Linux, Python 3.10+, Bash, and curl/tar for the download. Chrome with the Prompt API and a downloaded model is required for the default Nano backend.

Once this repository is published:

```bash
curl -fsSL https://raw.githubusercontent.com/fiazul/nano-shell/main/install.sh | bash
```

From a downloaded or cloned checkout:

```bash
bash install.sh
```

Open a new terminal, or run `source ~/.bashrc` (Zsh: `source ~/.zshrc`). Initialize Nano:

```bash
~/.local/bin/nano-shell setup
```

Click the initialization button in the Chrome window. It checks availability, downloads the model if Chrome permits it, and connects to the local bridge. Keep that window running while using Nano. An existing compatible Chrome profile may be necessary to access its downloaded model.

Chrome's Prompt API runs in a browser document. This project does not claim that a Python daemon can run Gemini Nano on its own. Hardware requirements, download eligibility, and API availability come from [Chrome's Prompt API documentation](https://developer.chrome.com/docs/ai/prompt-api). If the API is unavailable, the setup page reports it instead of inventing a response.

The installer adds a launcher under `~/.local/bin`, Bash/Zsh hooks, and a systemd user service when a user systemd session is available. Otherwise it starts a detached bridge. It does not use sudo. Configuration survives upgrades and uninstall by default.

Custom install:

```bash
bash install.sh --prefix "$HOME/tools" --no-service
```

Use `--no-start` to install without starting the bridge and `--no-shell` to skip shell configuration. Add the custom `bin` directory to PATH for direct `nano-shell` use; installed shell hooks use its absolute path.

## Use

```bash
?? what was the last downloaded file?
?? what process is listening on port 8000?
?? find the largest file here
?? show python processes
?? what files changed in this git repo?
?? find all json files modified today
```

Quote questions containing shell punctuation, for example `?? 'what changed today?'`.

The model may produce a command outside the supported read-only subset; Nano Shell refuses it and explains why. A model suggestion is not proof that a command answers the question correctly.

Type a question at the prompt and press **Ctrl+G** to replace the current input with a suggested command. Review it and press Enter yourself. This is an explicit suggestion shortcut; it does not send every keystroke to a model. Ctrl+G replaces that key's existing Bash/Zsh binding while the hook is loaded.

```bash
~/.local/bin/nano-shell suggest 'show python processes'
~/.local/bin/nano-shell ask --yes 'what files changed in this git repo?'
~/.local/bin/nano-shell status
~/.local/bin/nano-shell doctor
~/.local/bin/nano-shell start
~/.local/bin/nano-shell stop
```

`--yes` skips confirmation only for the validated read-only subset. Normal questions always ask first. Generated commands never execute inside the HTTP bridge or browser. Actual command errors retain a nonzero exit status.

## Without a browser

Install and run [Ollama](https://github.com/ollama/ollama), and download a model suitable for your machine. Then select its exact installed model name:

```bash
~/.local/bin/nano-shell config --backend ollama --model YOUR_INSTALLED_MODEL
~/.local/bin/nano-shell stop
~/.local/bin/nano-shell start
```

Ollama's local service must be running. There is no silent backend fallback. Switching back:

```bash
~/.local/bin/nano-shell config --backend nano
~/.local/bin/nano-shell stop
~/.local/bin/nano-shell setup
```

## Command policy and privacy

Commands run as your own user, through argument arrays and explicit pipelines. Shell evaluation is disabled. The validator rejects redirects, substitutions, command chains, interpreters, unknown executables, and unsupported options. It checks options as well as program names, including write-capable `find` actions and Git subcommands. This deliberately limits what the assistant can execute.

This is a command policy, not an operating-system sandbox. Approved commands can display your files, process details, and filenames. Avoid asking for sensitive data in a shared terminal. The bridge listens only on `127.0.0.1`, requires a private local token, validates HTTP Host/Origin, and exposes inference rather than execution. It cannot protect against other programs running as your own OS user.

The model receives your question, current directory, and bounded directory context. Shell history is not shared by default. Nano inference runs locally after Chrome's model download; Ollama requests stay on its configured loopback endpoint. No telemetry is included.

Use `?? --history 'explain what I was doing'` to explicitly share the last 4096 characters from `HISTFILE` (or `~/.bash_history`). This reads saved history; it may not include commands from the current session that your shell has not flushed.

## Remove

```bash
~/.local/bin/nano-shell uninstall
```

Stops the bridge, removes its service, launcher, installed source, and managed shell blocks. Opens no browser and preserves unrelated shell configuration. Add `--purge` to also remove Nano Shell configuration and state. Open a new terminal afterward to unload functions and key bindings.

## Develop

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q nano_shell
bash -n install.sh uninstall.sh shell/bash.sh
node --check web/worker.js
```

Tests exercise the command policy, bridge authorization/timeouts, and installer lifecycle. Test inference fixtures are explicitly local fakes; they do not establish Gemini Nano or Ollama model availability. Live model verification depends on a downloaded compatible model and is reported separately.

See [verification and remaining limitations](docs/verification.md) for the exact checks reached in the initial build.

MIT licensed. Contributions welcome.

To publish the prepared repository as its owner, authenticate GitHub CLI with `gh auth login -h github.com`, then run `bash scripts/publish.sh`. The script checks account `fiazul`, pushes `main`, and verifies the remote commit and public visibility.
