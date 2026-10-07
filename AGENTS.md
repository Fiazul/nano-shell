# Nano Shell contributor instructions

```text
Bash/Zsh ?? / Ctrl+G → Python CLI → automatic headless runtime → local Ollama on 127.0.0.1:11435
validated model JSON → argv policy → preview/confirm → subprocess pipeline → actual exit status
```

- Keep the HTTP bridge inference-only; never add an HTTP command execution endpoint.
- Validate generated commands in both generation and execution paths. Never invoke a shell or use eval.
- Allow only explicitly reviewed programs, subcommands and options; preserve the unknown/failed/cancelled outcome.
- Require confirmation by default. Keep `--yes` limited to the same reviewed subset.
- Preserve private XDG state, locks, process ownership and bounded requests/timeouts. Preserve token/Host/Origin checks in legacy protocol code.
- Disable environment proxy use and HTTP redirects for local inference requests.
- Share history only with explicit `--history`. Never log questions, context, model output, or credentials.
- Keep the normal product flow fully headless. Provision runtime/model during install; start automatically on demand. Never open a browser or require routine setup.
- Disable Ollama cloud features, preserve process identity and locks, and stop only verified owned processes. Never kill an arbitrary stored PID.
- Keep legacy bridge/browser files isolated from the active runtime; retain their regression tests while those files exist.
- Keep the Python runtime dependency-free on Python 3.10+.
- Preserve unrelated shell configuration and test streamed installs, paths with spaces and repeated install/uninstall.
- Add tests for destructive option bypasses, auth regressions and incorrect success reporting.
- Run `python3 -m unittest discover -s tests -v`, `python3 -m compileall -q nano_shell`, `bash -n install.sh uninstall.sh shell/bash.sh scripts/publish.sh` and `node --check web/worker.js` before reporting completion.
- Distinguish in-memory/fixture verification from a real bridge or model roundtrip. Record skips and external blockers.
- Keep comments minimal; explain non-obvious constraints rather than restating code.
- Keep README focused on installation and use.
- Keep the npm installer dependency-free, bundle all installation source, and resolve source paths from the package location. Never require the terminal's current directory to contain the repo.
- Require explicit install invocation; do not add npm install lifecycle hooks that provision models automatically when the package is fetched.
- Run `node --check bin/cli.cjs`, `node --check bin/source.cjs` and npm packaging/npx integration tests before changing the npm entrypoint. Mark public npm commands as pending until publication/readback succeeds.
- Use the global monthly-worklog skill before every commit, include the worklog in that commit, and obtain explicit user authorization before committing or pushing.
