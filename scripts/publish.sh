#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v gh >/dev/null || { printf '%s\n' 'Install GitHub CLI, then run gh auth login.' >&2; exit 1; }
gh auth status || { printf '%s\n' 'Run gh auth login -h github.com, then retry this script.' >&2; exit 1; }
account="$(gh api user --jq .login)"
[[ "${account,,}" == fiazul ]] || { printf 'Expected GitHub account fiazul; found %s.\n' "$account" >&2; exit 1; }
git rev-parse --verify HEAD >/dev/null
[[ -z "$(git status --porcelain)" ]] || { printf '%s\n' 'Commit reviewed work before publishing.' >&2; exit 1; }
repo=fiazul/nano-shell
if ! gh repo view "$repo" --json name >/dev/null 2>&1; then
  gh repo create "$repo" --public --description 'Local AI assistant inside your Linux terminal: Gemini Nano or Ollama, command preview and a read-only policy.'
fi
if git remote get-url origin >/dev/null 2>&1; then
  remote="$(git remote get-url origin)"
  case "${remote,,}" in
    https://github.com/fiazul/nano-shell.git|git@github.com:fiazul/nano-shell.git) ;;
    *) printf 'Unexpected origin: %s\n' "$remote" >&2; exit 1 ;;
  esac
else
  git remote add origin https://github.com/fiazul/nano-shell.git
fi
gh auth setup-git
git push -u origin main
local_commit="$(git rev-parse main)"
remote_commit="$(git ls-remote origin refs/heads/main | cut -f1)"
[[ "$local_commit" == "$remote_commit" ]] || { printf '%s\n' 'Remote verification failed.' >&2; exit 1; }
private="$(gh repo view "$repo" --json isPrivate --jq .isPrivate)"
[[ "$private" == false ]] || { printf '%s\n' 'Repository exists but is not public; visibility was left unchanged.' >&2; exit 1; }
printf 'Verified public repository: https://github.com/%s\nCommit: %s\n' "$repo" "$remote_commit"
