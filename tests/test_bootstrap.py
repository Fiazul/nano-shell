import io
import os
from pathlib import Path
import tarfile
import tempfile
import subprocess
import shutil
import unittest
from unittest.mock import patch

try:
    from nano_shell import bootstrap
except ImportError:
    bootstrap = None


class BootstrapTests(unittest.TestCase):
    def test_later_link_cannot_redirect_binary_outside_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / 'target'
            target.mkdir()
            outside_binary = parent / 'pwn'
            outside_binary.write_text('#!/bin/sh\nexit 0\n')
            outside_binary.chmod(0o755)
            archive = parent / 'runtime.tar'
            with tarfile.open(archive, 'w') as handle:
                for name, linkname in [('lib/a', 'b/../..'),
                                       ('bin/ollama', '../lib/a/pwn'),
                                       ('lib/b', '../bin')]:
                    member = tarfile.TarInfo(name)
                    member.type, member.linkname = tarfile.SYMTYPE, linkname
                    handle.addfile(member)
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.extract_archive(archive, target)

    def test_chained_symlinks_cannot_write_outside_with_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / 'target'
            target.mkdir()
            (target / 'bin').mkdir()
            outside = parent / 'outside'
            outside.mkdir()
            victim = outside / 'victim'
            victim.write_bytes(b'original')
            archive = parent / 'runtime.tar'
            with tarfile.open(archive, 'w') as handle:
                for name, linkname, kind in [('lib/a', '../bin', tarfile.SYMTYPE),
                                            ('lib/b', 'a/../../outside', tarfile.SYMTYPE),
                                            ('lib/c', 'lib/b/victim', tarfile.LNKTYPE)]:
                    member = tarfile.TarInfo(name)
                    member.type, member.linkname = kind, linkname
                    handle.addfile(member)
                payload = tarfile.TarInfo('lib/c')
                payload.size = 7
                handle.addfile(payload, io.BytesIO(b'changed'))
            with self.assertRaises(bootstrap.BootstrapError):
                bootstrap.extract_archive(archive, target)
            self.assertEqual(victim.read_bytes(), b'original')

    @unittest.skipUnless(shutil.which('zstd'), 'zstd unavailable to construct runtime bundle fixture')
    def test_runtime_download_extracts_into_installed_root(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            archive = home / 'bundle.tar'
            packed = home / 'bundle.tar.zst'
            with tarfile.open(archive, 'w') as handle:
                binary = tarfile.TarInfo('./bin/ollama')
                binary.mode = 0o755
                data = b'#!/bin/sh\nexit 0\n'
                binary.size = len(data)
                handle.addfile(binary, io.BytesIO(data))
            subprocess.run(['zstd', '-q', str(archive), '-o', str(packed)], check=True)
            root = home / 'installed'
            (root / 'nano_shell').mkdir(parents=True)
            fakebin = home / 'fakebin'
            fakebin.mkdir()
            curl = fakebin / 'curl'
            curl.write_text('#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done\ncp "$TEST_PACKED" "$2"\n')
            curl.chmod(0o755)
            original_which = shutil.which
            def locate(name):
                return None if name == 'ollama' else original_which(name)
            with patch.dict(os.environ, {'PATH': str(fakebin) + os.pathsep + os.environ['PATH'],
                                         'TEST_PACKED': str(packed)}), \
                 patch.object(bootstrap, '__file__', str(root / 'nano_shell/bootstrap.py')), \
                 patch('shutil.which', side_effect=locate), \
                 patch('platform.system', return_value='Linux'), \
                 patch('platform.machine', return_value='x86_64'):
                binary = bootstrap.ensure_binary()
                self.assertEqual(binary, root / 'runtime/bin/ollama')
                self.assertTrue(os.access(binary, os.X_OK))
                self.assertEqual(bootstrap.ensure_binary(), binary)

    @unittest.skipUnless(shutil.which('zstd'), 'zstd unavailable to construct compression fixture')
    def test_libzstd_fallback_decompresses_and_rejects_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            source, packed, output = [Path(directory) / name for name in ('source', 'packed', 'output')]
            data = b'local runtime fixture' * 10000
            source.write_bytes(data)
            subprocess.run(['zstd', '-q', str(source), '-o', str(packed)], check=True)
            with patch('shutil.which', return_value=None):
                bootstrap.decompress(packed, output)
                self.assertEqual(output.read_bytes(), data)
                packed.write_bytes(packed.read_bytes()[:-3])
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.decompress(packed, output)

    def test_existing_binary_requires_no_download(self):
        self.assertIsNotNone(bootstrap)
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'ollama'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            with patch('shutil.which', return_value=str(binary)), patch('subprocess.run') as run:
                self.assertEqual(bootstrap.ensure_binary(), binary)
                run.assert_not_called()

    def test_archive_extraction_accepts_runtime_and_internal_links(self):
        self.assertIsNotNone(bootstrap)
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'runtime.tar'
            with tarfile.open(archive, 'w') as handle:
                root = tarfile.TarInfo('.')
                root.type = tarfile.DIRTYPE
                handle.addfile(root)
                binary = tarfile.TarInfo('./bin/ollama')
                binary.mode = 0o755
                binary.size = 4
                handle.addfile(binary, io.BytesIO(b'test'))
                link = tarfile.TarInfo('lib/ollama/libfoo.so')
                link.type = tarfile.SYMTYPE
                link.linkname = 'libfoo.so.1'
                handle.addfile(link)
            target = Path(directory) / 'target'
            target.mkdir()
            bootstrap.extract_archive(archive, target)
            self.assertEqual((target / 'bin/ollama').read_bytes(), b'test')
            self.assertTrue((target / 'lib/ollama/libfoo.so').is_symlink())

    def test_archive_escape_and_special_files_rejected(self):
        self.assertIsNotNone(bootstrap)
        for name, kind, destination in [('../escape', tarfile.REGTYPE, ''),
                                        ('/tmp/escape', tarfile.REGTYPE, ''),
                                        ('bin/link', tarfile.SYMTYPE, '../../outside'),
                                        ('bin/device', tarfile.CHRTYPE, '')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / 'runtime.tar'
                with tarfile.open(archive, 'w') as handle:
                    member = tarfile.TarInfo(name)
                    member.type = kind
                    member.linkname = destination
                    handle.addfile(member)
                target = Path(directory) / 'target'
                target.mkdir()
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.extract_archive(archive, target)


if __name__ == '__main__':
    unittest.main()
