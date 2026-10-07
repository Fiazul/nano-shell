'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

function usable(directory, checkout = false) {
  try {
    if (!fs.statSync(path.join(directory, 'install.sh')).isFile() ||
        !fs.statSync(path.join(directory, 'nano_shell', 'runtime.py')).isFile()) return false;
    if (checkout && !fs.existsSync(path.join(directory, '.git'))) return false;
    const metadata = JSON.parse(fs.readFileSync(path.join(directory, 'package.json'), 'utf8'));
    return metadata.name === '@fiazul/nano-shell';
  } catch {
    return false;
  }
}

function findLocal({ home = os.homedir(), cwd = process.cwd(), bundled, roots } = {}) {
  if (process.env.NANO_SHELL_SOURCE_DIR) {
    const explicit = path.resolve(process.env.NANO_SHELL_SOURCE_DIR);
    if (!usable(explicit)) throw new Error('NANO_SHELL_SOURCE_DIR is not a usable headless Nano Shell source directory');
    return explicit;
  }
  if (bundled && usable(bundled, true)) return bundled;
  for (let parent = path.resolve(cwd);;) {
    if (usable(parent, true)) return parent;
    const next = path.dirname(parent);
    if (next === parent) break;
    parent = next;
  }
  const locations = roots || [cwd, ...['Desktop', 'Documents', 'Downloads', 'projects', 'src', 'work'].map((name) => path.join(home, name)), home,
    ...(process.env.NANO_SHELL_SEARCH_ROOTS || '').split(path.delimiter).filter(Boolean)];
  const excluded = new Set(['.git', '.cache', '.npm', '.local', '.config', '.codex', '.agents', 'node_modules', 'vendor', '.venv', 'venv', '__pycache__']);
  const visited = new Set();
  const queue = locations.map((directory) => [path.resolve(directory), 0]);
  for (let index = 0; index < queue.length && visited.size < 5000; index += 1) {
    const [directory, depth] = queue[index];
    if (visited.has(directory)) continue;
    visited.add(directory);
    if (usable(directory, true)) return directory;
    if (depth >= 6) continue;
    let entries;
    try { entries = fs.readdirSync(directory, { withFileTypes: true }); } catch { continue; }
    for (const entry of entries) {
      if (entry.isDirectory() && !excluded.has(entry.name) && queue.length < 10000) {
        queue.push([path.join(directory, entry.name), depth + 1]);
      }
    }
  }
  return bundled && usable(bundled) ? bundled : null;
}

function acquire(options = {}) {
  const local = findLocal(options);
  if (local) return { directory: local, cleanup() {} };
  const repository = process.env.NANO_SHELL_REPO || 'fiazul/nano-shell';
  const ref = process.env.NANO_SHELL_REF || 'main';
  if (!/^[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+$/.test(repository) || !/^[a-zA-Z0-9_.-]+$/.test(ref)) {
    throw new Error('Invalid Nano Shell Git repository or reference');
  }
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'nano-shell-source-'));
  const directory = path.join(temporary, 'checkout');
  const cleanup = () => fs.rmSync(temporary, { recursive: true, force: true });
  console.error(`No local source found in searched locations; downloading https://github.com/${repository}.git (${ref})`);
  const cloned = spawnSync('git', ['-c', 'core.hooksPath=/dev/null', 'clone', '--depth', '1', '--single-branch',
    '--branch', ref, '--', `https://github.com/${repository}.git`, directory], {
    stdio: 'inherit', shell: false, timeout: 120000,
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
  });
  if (cloned.error || cloned.status !== 0 || !usable(directory, true)) {
    cleanup();
    throw new Error(`Git source download failed${cloned.error ? ': ' + cloned.error.message : ''}; no successful installation was reported`);
  }
  return { directory, cleanup };
}

module.exports = { findLocal, acquire };
