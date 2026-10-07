# Nano Shell Implementation Plan

**Goal:** Install a local assistant into the user's existing Linux terminal and publish its source.
**Architecture:** Python stdlib CLI and authenticated local daemon; Chrome document for Nano and optional local Ollama. Read-only commands run through validated argument arrays.
**Tech stack:** Python 3.10+, Bash/Zsh, browser JavaScript, systemd user service.
**Spec:** ../specs/2026-10-07-nano-shell-design.md

## Global constraints

- No root, no Python package dependencies, no arbitrary shell evaluation.
- Preserve user files, opt-in history, exact error status, user confirmation by default.
- Explicit backend selection. Nano requires a live browser document and user activation.

## Review focus

- Malicious or malformed model output must fail before execution.
- Foreign HTTP origins and stale credentials must fail closed.
- Browser disconnects and inference timeouts must produce errors.
- Repeated install, paths with spaces and uninstall must preserve unrelated shell configuration.
- Terminal nonzero exit/cancellation must remain nonzero and noninteractive use must not execute implicitly.

## Task 1: Core and bridge

Owner: implementation worker; files nano_shell/, web/, tests/test_core.py, tests/test_bridge.py.
Interfaces: `python3 -m nano_shell {ask,suggest,start,stop,status,doctor,setup,config}`. Ask positional words form a question; `ask --yes` explicitly executes validated read-only commands. `suggest` prints only command to stdout. Config accepts `--backend nano|ollama --model NAME`; `start --foreground` runs daemon, otherwise starts detached. State/config use standard XDG env; NANO_SHELL_STATE_DIR and NANO_SHELL_CONFIG_DIR overrides for testing. Default port 8765; support NANO_SHELL_PORT. Setup starts bridge and opens Chrome app window with fragment token. All errors go to stderr/nonzero.
- [x] Write adversarial policy, auth, timeout, generation and exit-status tests and observe failure.
- [x] Implement small focused Python modules, Nano document and Ollama adapter.
- [x] Run `python3 -m unittest discover -s tests -v` and Node syntax check. Live socket tests are explicitly skipped only when socket creation is denied; real Handler tests run in memory.

## Task 2: Install and terminal integration

Owner: main agent; files install.sh, uninstall.sh, shell/, tests/test_install.py, README.md, LICENSE, .github/workflows/ci.yml.
- [x] Write installer lifecycle integration test and observe missing installer failure.
- [x] Implement user install/custom prefix, idempotent Bash/Zsh hooks, Ctrl+G and optional service; installer consumes exact Task 1 CLI interfaces.
- [x] Verify install and uninstall in a temporary home, Bash alias/suggestion behavior, and real execution/confirmation with an explicitly mocked inference response. Real model inference and Zsh runtime verification are unavailable in this environment.

## Task 3: Review and publish

- [x] Independent code review; fix substantial findings and rerun relevant checks. Shell-safe insertion, prefix ownership and browser immediate rejection fixes were reviewed again.
- [x] Record verification level and backend limitations in README and docs/verification.md. Worklog accompanies the initial commit.
- [ ] Initialize repository, record monthly worklog, commit authorized work, publish public fiazul/nano-shell, read back repository state.
