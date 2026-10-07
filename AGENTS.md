# Nano Shell contributor instructions

```text
Bash/Zsh ?? / Ctrl+G → Python CLI → authenticated localhost bridge → Chrome LanguageModel
                              └→ explicitly configured localhost Ollama
validated model JSON → argv policy → preview/confirm → subprocess pipeline → actual exit status
```

- Keep the HTTP bridge inference-only; never add an HTTP command execution endpoint.
- Validate generated commands in both generation and execution paths. Never invoke a shell or use eval.
- Allow only explicitly reviewed programs, subcommands and options; preserve the unknown/failed/cancelled outcome.
- Require confirmation by default. Keep `--yes` limited to the same reviewed subset.
- Preserve private XDG state, token permissions, strict Host/Origin checks, bounded requests and timeouts.
- Disable environment proxy use and HTTP redirects for local inference requests.
- Share history only with explicit `--history`. Never log questions, context, model output, or credentials.
- Report Chrome model availability, download and errors honestly. Keep a user activation button; do not claim headless Nano support.
- Keep the Python runtime dependency-free on Python 3.10+.
- Preserve unrelated shell configuration and test streamed installs, paths with spaces and repeated install/uninstall.
- Add tests for destructive option bypasses, auth regressions and incorrect success reporting.
- Run `python3 -m unittest discover -s tests -v`, `python3 -m compileall -q nano_shell`, `bash -n install.sh uninstall.sh shell/bash.sh scripts/publish.sh` and `node --check web/worker.js` before reporting completion.
- Distinguish in-memory/fixture verification from a real bridge or model roundtrip. Record skips and external blockers.
- Keep comments minimal; explain non-obvious constraints rather than restating code.
- Keep README focused on installation and use.
- Use the global monthly-worklog skill before every commit, include the worklog in that commit, and obtain explicit user authorization before committing or pushing.
