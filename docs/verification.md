# Verification on 2026-10-07

Reached: implemented and unit-tested. Reviewed command and installer ownership fixes independently. No real model inference or hosted publication verified.

- `python3 -m unittest discover -s tests -v`: 37 tests, 31 passed, 6 actual HTTP socket tests explicitly skipped because this sandbox denies socket creation. The same real HTTP Handler/broker paths were exercised in memory.
- Python compilation, Bash script syntax and browser JavaScript syntax checks passed.
- Temporary-home installer checks covered repeated installation, a prefix with spaces, streamed bootstrap with a mocked archive download, uninstall preserving existing shell settings, and obsolete-prefix uninstall preserving the newer hooks and unit.
- A subprocess fixture verified the original newest-download pipeline, including confirmation/cancellation, safe home expansion and a filename with spaces. Real commands ran; the generation response was explicitly mocked.
- An interactive Bash terminal fixture verified Ctrl+G insertion without execution and `??` followed by confirmation and actual `pwd` output. Generation was explicitly mocked; shell job-control support was unavailable in this terminal harness.
- Browser tests with intercepted assets, bridge responses and mocked LanguageModel verified missing API, download/progress/ready, prompt/result submission, session cleanup, token restoration on reload, initialization failure/retry and immediate promise rejection without an unhandled browser error.
- Command tests include rejection of writes/substitutions, unknown flags, failure exit codes, shell-safe insertion against a filename named `-delete`, trusted executable paths and Git flags, token symlink protection, redirect refusal, history opt-in and uncertain shutdown results.

Unverified: real Chrome Gemini Nano download/inference, real Ollama inference, live localhost HTTP, Zsh runtime, systemd user service lifecycle, Docker, other Python versions in CI, internet bootstrap download and public GitHub publication. GitHub CLI reported invalid saved authentication, shell DNS/network access was blocked, and the browser GitHub session was signed out. `scripts/publish.sh` verifies authentication, account identity, pushed commit and public visibility when run in a usable environment.
