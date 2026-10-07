#!/usr/bin/env node
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { acquire } = require('./source.cjs');

const root = path.resolve(__dirname, '..');
const argv = process.argv.slice(2);
const command = argv[0] || 'install';
const args = argv.length ? argv.slice(1) : [];

function run(executable, arguments_, cwd = process.cwd()) {
  const child = spawnSync(executable, arguments_, { stdio: 'inherit', cwd, shell: false });
  if (child.error) {
    console.error(`nano-shell: ${child.error.message}`);
    return 1;
  }
  return child.status === null ? 128 + (os.constants.signals[child.signal] || 1) : child.status;
}

function installedPrefix() {
  if (process.env.NANO_SHELL_PREFIX) return resolvePrefix(process.env.NANO_SHELL_PREFIX);
  const configuration = process.env.NANO_SHELL_CONFIG_DIR ||
    path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'nano-shell');
  const marker = path.join(configuration, 'installation.json');
  if (fs.existsSync(marker)) {
    if (fs.lstatSync(marker).isSymbolicLink()) throw new Error('installation marker must not be a symlink');
    const value = JSON.parse(fs.readFileSync(marker, 'utf8'));
    if (typeof value.prefix !== 'string' || !path.isAbsolute(value.prefix)) {
      throw new Error('invalid installation marker');
    }
    return value.prefix;
  }
  return path.join(os.homedir(), '.local');
}

function resolvePrefix(value) {
  if (value === '~') return os.homedir();
  if (value.startsWith('~/')) return path.join(os.homedir(), value.slice(2));
  return path.resolve(value);
}

try {
  for (const variable of ['NANO_SHELL_PREFIX', 'NANO_SHELL_CONFIG_DIR', 'NANO_SHELL_STATE_DIR',
    'NANO_SHELL_OLLAMA_BIN', 'XDG_CONFIG_HOME', 'XDG_STATE_HOME']) {
    if (process.env[variable]) process.env[variable] = resolvePrefix(process.env[variable]);
  }
  if (['--help', '-h', 'help'].includes(command)) {
    console.log('Usage: npx --yes @fiazul/nano-shell install [--model NAME] [--prefix PATH]\n' +
      '       npx --yes @fiazul/nano-shell uninstall [--purge]\n' +
      'Install once, then open a terminal and use: ?? is docker up');
  } else if (process.platform !== 'linux') {
    throw new Error('Nano Shell currently supports Linux only');
  } else if (command === 'install') {
    const installArgs = args.slice();
    for (let index = 0; index < installArgs.length; index += 1) {
      if (installArgs[index] === '--prefix' && installArgs[index + 1]) {
        installArgs[index + 1] = resolvePrefix(installArgs[index + 1]);
        index += 1;
      }
    }
    const source = acquire({ bundled: root });
    console.error(`Using Nano Shell source: ${source.directory}`);
    try {
      process.exitCode = run('bash', [path.join(source.directory, 'install.sh'), ...installArgs], source.directory);
    } finally {
      source.cleanup();
    }
  } else if (['ask', 'suggest', 'status', 'doctor', 'start', 'stop', 'config', 'uninstall'].includes(command)) {
    const launcher = path.join(installedPrefix(), 'bin', 'nano-shell');
    if (!fs.existsSync(launcher)) throw new Error('Nano Shell is not installed; run npx --yes @fiazul/nano-shell install');
    process.exitCode = run(launcher, [command, ...args]);
  } else {
    throw new Error(`unknown command: ${command}; use install, uninstall, or --help`);
  }
} catch (error) {
  console.error(`nano-shell: ${error.message}`);
  process.exitCode = 1;
}
