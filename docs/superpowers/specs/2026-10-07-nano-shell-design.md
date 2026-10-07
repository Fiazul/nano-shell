# Nano Shell

Build a Linux terminal assistant installable without root or Python dependencies. Users ask `?? question` in Bash/Zsh, preview a read-only command, and confirm execution. Ctrl+G inserts a suggestion into the active prompt without executing. No shell replacement.

Python 3.10+ owns CLI, authenticated loopback daemon, configuration, command validation and execution. Chrome's localhost document owns Gemini Nano inference through LanguageModel; a visible initialization button supplies user activation and reports availability/download/errors. The browser must remain running. Optional local Ollama inference allows operation without a browser. Never silently substitute a backend or present a fixture as real AI.

Default backend is nano; explicit configuration selects ollama and its installed model. State and credentials live in private XDG directories. HTTP binds 127.0.0.1; token authorization, Host and Origin validation protect local requests. Browser bootstrap token is delivered in URL fragment, not query/logs. Generation has bounded timeouts and errors propagate as failures. CLI executes only an independently validated read-only subset, using argument arrays and explicit pipelines without shell evaluation. Reject substitutions, redirects, command chains, interpreters, write-capable options and unknown programs. Confirmation is default; explicit --yes works only within that subset. This is a command policy, not a sandbox or a guarantee of privacy for file reads.

Installer copies versioned source to user data directory, writes launcher and shell hooks idempotently, supports custom prefix, and optionally installs a systemd user service. Uninstaller stops service and removes only managed files/hooks. History sharing is opt-in; cwd and bounded directory names are sent to local inference by default. Commands may read local files only when user confirms.

Verify parser adversarial inputs, authenticated bridge roundtrip, backend errors/timeouts, actual command exit codes, installer repeated runs/custom paths/uninstall, shell syntax, and terminal ask/suggest with a local fake inference server explicitly labeled as test-only. Real Gemini/Ollama inference is separately recorded as verified or unavailable.

Publish public GitHub repository fiazul/nano-shell with MIT license, CI and README. User authorized commit and push; commit identity email fiazulhaque90@gmail.com. Missing archive means this is a new implementation from the supplied behavioral description.
