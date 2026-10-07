#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
registry=https://registry.npmjs.org/
account="$(npm whoami --registry "$registry" --fetch-retries=0 --fetch-timeout=10000)" || {
  printf '%s\n' 'npm publication is blocked: sign in with npm login, then retry this script.' >&2
  exit 1
}
[[ "$account" == fiazul ]] || {
  printf 'This package uses @fiazul; authenticated npm account is %s. Check scope ownership before publishing.\n' "$account" >&2
  exit 1
}
[[ -z "$(git status --porcelain)" ]] || { printf '%s\n' 'Commit reviewed work before publishing.' >&2; exit 1; }
node --check bin/cli.cjs
package_name="$(node -p 'require("./package.json").name')"
package_version="$(node -p 'require("./package.json").version')"
package_spec="$package_name@$package_version"
release_dir="$(mktemp -d)"
trap 'rm -rf -- "$release_dir"' EXIT
npm pack --ignore-scripts --pack-destination "$release_dir" >/dev/null
artifact="$release_dir/fiazul-nano-shell-$package_version.tgz"
[[ -f "$artifact" ]] || { printf '%s\n' 'Expected release artifact is missing.' >&2; exit 1; }
digest="$(node - "$artifact" <<'JS'
const fs = require('node:fs');
const crypto = require('node:crypto');
console.log('sha512-' + crypto.createHash('sha512').update(fs.readFileSync(process.argv[2])).digest('base64'));
JS
)"
npm publish "$artifact" --access public --registry "$registry"
remote_version="$(npm view "$package_spec" version --registry "$registry" --fetch-retries=0 --fetch-timeout=20000)"
remote_integrity="$(npm view "$package_spec" dist.integrity --registry "$registry" --fetch-retries=0 --fetch-timeout=20000)"
[[ "$remote_version" == "$package_version" && "$remote_integrity" == "$digest" ]] || {
  printf '%s\n' 'Publication was attempted, but registry version/integrity readback did not match.' >&2
  exit 1
}
printf 'Verified npm release: %s\nInstall: npx --yes %s install\n' "$package_spec" "$package_spec"
