import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('node') and shutil.which('npm') and shutil.which('npx'), 'Node/npm/npx unavailable')
class NpxTests(unittest.TestCase):
    def test_tilde_and_relative_configuration_follow_invoking_home(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            home, package = parent / 'home', parent / 'package'
            home.mkdir()
            shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
            for configuration in ('~/special-config', 'relative-config'):
                with self.subTest(configuration=configuration):
                    env = dict(os.environ, HOME=str(home), NANO_SHELL_CONFIG_DIR=configuration,
                               XDG_STATE_HOME=str(home / 'state'))
                    prefix = home / 'custom-prefix'
                    result = subprocess.run(['node', str(package / 'bin/cli.cjs'), 'install', '--prefix', str(prefix),
                                             '--no-start', '--no-service'], cwd=home, env=env,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    result = subprocess.run(['node', str(package / 'bin/cli.cjs'), 'uninstall'],
                                            cwd=home, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse((prefix / 'bin/nano-shell').exists())

    def test_relative_prefix_is_resolved_from_invoking_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            home, package = parent / 'home', parent / 'package'
            home.mkdir()
            shutil.copytree(ROOT, package, ignore=shutil.ignore_patterns('.git', '__pycache__', '*.pyc'))
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                       XDG_STATE_HOME=str(home / 'state'))
            result = subprocess.run(['node', str(package / 'bin/cli.cjs'), 'install', '--prefix', 'relative tools',
                                     '--no-start', '--no-service'], cwd=home, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / 'relative tools/bin/nano-shell').exists())
            self.assertFalse((package / 'relative tools').exists())
            env['NANO_SHELL_PREFIX'] = 'env tools'
            result = subprocess.run(['node', str(package / 'bin/cli.cjs'), 'install', '--no-start', '--no-service'],
                                    cwd=home, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((home / 'env tools/bin/nano-shell').exists())

    def source_fixture(self, directory):
        (directory / 'nano_shell').mkdir(parents=True)
        (directory / '.git').mkdir()
        (directory / 'install.sh').write_text('#!/bin/bash\nexit 0\n')
        (directory / 'nano_shell/runtime.py').write_text('')
        (directory / 'package.json').write_text('{"name":"@fiazul/nano-shell"}')

    def test_finds_existing_checkout_before_download(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            checkout = home / 'Desktop/other folder/renamed checkout'
            self.source_fixture(checkout)
            code = 'const s=require(process.argv[1]); console.log(s.findLocal({home:process.argv[2],cwd:process.argv[2],roots:[process.argv[2]]}));'
            result = subprocess.run(['node', '-e', code, str(ROOT / 'bin/source.cjs'), str(home)],
                                    cwd=home, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(checkout))

    def test_missing_checkout_downloads_git_and_cleans_temporary_source(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            home, seed, fakebin = [parent / name for name in ('home', 'seed', 'fakebin')]
            home.mkdir()
            fakebin.mkdir()
            self.source_fixture(seed)
            git = fakebin / 'git'
            git.write_text('#!/usr/bin/env bash\n'
                           'printf "%s\\n" "$@" > "$TEST_GIT_CALLS"\n'
                           'for destination in "$@"; do :; done\n'
                           'mkdir -p "$destination"\ncp -R "$TEST_SEED/." "$destination/"\n')
            git.chmod(0o755)
            env = dict(os.environ, HOME=str(home), TMPDIR=str(home), TEST_SEED=str(seed),
                       TEST_GIT_CALLS=str(parent / 'git-calls'),
                       PATH=str(fakebin) + os.pathsep + os.environ['PATH'])
            code = ('const s=require(process.argv[1]); const source=s.acquire({home:process.argv[2],cwd:process.argv[2],roots:[process.argv[2]]}); '
                    'console.log(source.directory); source.cleanup();')
            result = subprocess.run(['node', '-e', code, str(ROOT / 'bin/source.cjs'), str(home)],
                                    cwd=home, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('https://github.com/fiazul/nano-shell.git', (parent / 'git-calls').read_text())
            self.assertFalse(Path(result.stdout.strip()).exists())
            git.write_text('#!/bin/bash\nexit 7\n')
            failed = subprocess.run(['node', '-e', code, str(ROOT / 'bin/source.cjs'), str(home)],
                                    cwd=home, env=env, capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('Git source download failed', failed.stderr)
            self.assertFalse(list(home.glob('nano-shell-source-*')))

    def test_install_from_unrelated_directory_and_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='npx home ') as directory:
            home = Path(directory)
            env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                       XDG_STATE_HOME=str(home / 'state'))
            result = subprocess.run(['node', str(ROOT / 'bin/cli.cjs'), 'install', '--no-start', '--no-service'],
                                    cwd=home, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            launcher = home / '.local/bin/nano-shell'
            self.assertTrue(launcher.exists())
            self.assertIn('Source-only install', result.stdout)
            self.assertNotIn('Headless runtime and local model verified', result.stdout)

    def test_bundle_contains_all_runtime_sources_and_no_local_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, npm_config_cache=directory + '/cache')
            packed = subprocess.run(['npm', 'pack', '--json', '--ignore-scripts', '--pack-destination', directory],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(packed.returncode, 0, packed.stderr)
            metadata = json.loads(packed.stdout)[0]
            archive = Path(directory) / metadata['filename']
            with tarfile.open(archive) as handle:
                names = set(handle.getnames())
                for name in ('bin/cli.cjs', 'bin/source.cjs', 'install.sh', 'uninstall.sh', 'nano_shell/runtime.py',
                             'nano_shell/bootstrap.py', 'nano_shell/__main__.py', 'shell/bash.sh',
                             'shell/zsh.sh', 'web/index.html', 'LICENSE'):
                    self.assertIn('package/' + name, names)
                self.assertFalse(any('__pycache__' in name or name.endswith('.pyc') or '/.git/' in name
                                     or '/.codex/' in name or '/runtime/' in name for name in names))
            home = Path(directory) / 'fresh home'
            home.mkdir()
            env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / 'config'),
                       XDG_STATE_HOME=str(home / 'state'), npm_config_offline='true')
            result = subprocess.run(['npx', '--yes', '--offline', '--package=' + str(archive), 'nano-shell',
                                     'install', '--no-start', '--no-service'],
                                    cwd=home, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            launcher = home / '.local/bin/nano-shell'
            self.assertTrue(launcher.exists())
            help_result = subprocess.run([str(launcher), '--help'], cwd=home, env=env, capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            result = subprocess.run(['npx', '--yes', '--offline', '--package=' + str(archive), 'nano-shell', 'uninstall'],
                                    cwd=home, env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(launcher.exists())


if __name__ == '__main__':
    unittest.main()
