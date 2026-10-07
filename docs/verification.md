# Headless revision verification on 2026-10-07

Reached: implemented and unit-tested. This revision replaces the active Chrome flow with local Ollama and automatic startup. No real model download/inference or installed host upgrade has been verified from this sandbox.

- Full suite: 76 tests, 70 passed, 6 legacy live-HTTP socket tests explicitly skipped because socket creation is denied.
- Headless lifecycle tests run real lightweight fixture subprocesses with mocked HTTP. They cover automatic startup, four concurrent requests sharing one PID, private environment/model directory, owned shutdown, forged PID preservation, startup timeout/failed process cleanup, foreground signal shutdown and explicit administrative stop avoiding supervisor restart.
- Model tests cover saved Nano configuration migration, preserved local choices, cold-load timeout, routine requests never pulling models, download failure and false success, inference smoke validation without command execution, and status ownership races.
- Runtime installation fixtures cover official ./bin and ./lib archive paths, executable staging, repeated binary reuse, zstd and libzstd decompression, truncated input, traversal/special-file rejection and chained-symlink/hardlink escape prevention and final tree/binary confinement against late symlinks.
- Installer fixtures cover automatic bootstrap/provisioning/status, provisioning failure without readiness text, isolated trusted installed imports despite hostile package and stdlib files in the terminal cwd, preserving a foreign service before any file mutation, repeated installs, source-only installation, custom paths and ownership-aware uninstall.
- Docker policy tests allow only info/version/ps inspection and reject mutation commands, global endpoint flags and formatting templates. No actual Docker daemon was tested.
- Python compilation, Bash syntax, legacy JavaScript syntax and Git diff whitespace checks pass.

Remaining external checks: actual official Ollama runtime download, selected model download, live local inference and real HTTP, installed host migration, systemd login/reboot lifecycle, Zsh runtime, ARM64, other Python versions and public GitHub publication. This sandbox forbids network sockets and writing the user's installed ~/.local tree, so the new source must be installed once in the user's terminal. No routine setup command or browser exists in the active workflow.

## Earlier Chrome build verification (historical)

Reached: implemented and unit-tested. Reviewed command and installer ownership fixes independently. No real model inference or hosted publication verified.

- `python3 -m unittest discover -s tests -v`: 37 tests, 31 passed, 6 actual HTTP socket tests explicitly skipped because this sandbox denies socket creation. The same real HTTP Handler/broker paths were exercised in memory.
- Python compilation, Bash script syntax and browser JavaScript syntax checks passed.
- Temporary-home installer checks covered repeated installation, a prefix with spaces, streamed bootstrap with a mocked archive download, uninstall preserving existing shell settings, and obsolete-prefix uninstall preserving the newer hooks and unit.
- A subprocess fixture verified the original newest-download pipeline, including confirmation/cancellation, safe home expansion and a filename with spaces. Real commands ran; the generation response was explicitly mocked.
- An interactive Bash terminal fixture verified Ctrl+G insertion without execution and `??` followed by confirmation and actual `pwd` output. Generation was explicitly mocked; shell job-control support was unavailable in this terminal harness.
- Browser tests with intercepted assets, bridge responses and mocked LanguageModel verified missing API, download/progress/ready, prompt/result submission, session cleanup, token restoration on reload, initialization failure/retry and immediate promise rejection without an unhandled browser error.
- Command tests include rejection of writes/substitutions, unknown flags, failure exit codes, shell-safe insertion against a filename named `-delete`, trusted executable paths and Git flags, token symlink protection, redirect refusal, history opt-in and uncertain shutdown results.

Unverified: real Chrome Gemini Nano download/inference, real Ollama inference, live localhost HTTP, Zsh runtime, systemd user service lifecycle, Docker, other Python versions in CI, internet bootstrap download and public GitHub publication. GitHub CLI reported invalid saved authentication, shell DNS/network access was blocked, and the browser GitHub session was signed out. `scripts/publish.sh` verifies authentication, account identity, pushed commit and public visibility when run in a usable environment.
