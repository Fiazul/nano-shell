import ctypes
import ctypes.util
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile


class BootstrapError(RuntimeError):
    pass


def decompress(source, destination):
    executable = shutil.which('zstd')
    if executable:
        with destination.open('wb') as output:
            subprocess.run([executable, '-dc', str(source)], stdout=output, check=True)
        return
    library = ctypes.util.find_library('zstd')
    if not library:
        raise BootstrapError('Runtime extraction needs zstd or libzstd. No files were installed.')
    lib = ctypes.CDLL(library)
    class Buffer(ctypes.Structure):
        _fields_ = [('data', ctypes.c_void_p), ('size', ctypes.c_size_t), ('pos', ctypes.c_size_t)]
    lib.ZSTD_createDStream.restype = ctypes.c_void_p
    lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    lib.ZSTD_initDStream.restype = ctypes.c_size_t
    lib.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(Buffer), ctypes.POINTER(Buffer)]
    lib.ZSTD_decompressStream.restype = ctypes.c_size_t
    lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
    lib.ZSTD_isError.restype = ctypes.c_uint
    lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    stream = lib.ZSTD_createDStream()
    if not stream:
        raise BootstrapError('Could not allocate runtime decompressor')
    try:
        if lib.ZSTD_isError(lib.ZSTD_initDStream(stream)):
            raise BootstrapError('Could not initialize runtime decompressor')
        remaining = 1
        with source.open('rb') as input_file, destination.open('wb') as output_file:
            while chunk := input_file.read(131072):
                data = ctypes.create_string_buffer(chunk)
                input_buffer = Buffer(ctypes.cast(data, ctypes.c_void_p), len(chunk), 0)
                while input_buffer.pos < input_buffer.size:
                    output = ctypes.create_string_buffer(131072)
                    output_buffer = Buffer(ctypes.cast(output, ctypes.c_void_p), len(output), 0)
                    remaining = lib.ZSTD_decompressStream(stream, ctypes.byref(output_buffer), ctypes.byref(input_buffer))
                    if lib.ZSTD_isError(remaining):
                        raise BootstrapError('Invalid compressed runtime download')
                    output_file.write(output.raw[:output_buffer.pos])
            if remaining:
                raise BootstrapError('Incomplete compressed runtime download')
    finally:
        lib.ZSTD_freeDStream(stream)


def extract_archive(archive, target):
    root = target.resolve()
    with tarfile.open(archive, 'r:') as handle:
        members = handle.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if not parts and member.isdir():
                continue
            if not parts or parts[0] not in {'bin', 'lib'} or '..' in parts or Path(member.name).is_absolute():
                raise BootstrapError('Unexpected path in runtime archive: ' + member.name)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise BootstrapError('Special files are forbidden in runtime archive')
            if member.issym() or member.islnk():
                link = (root / member.name).parent / member.linkname if member.issym() else root / member.linkname
                if Path(member.linkname).is_absolute() or not link.resolve().is_relative_to(root):
                    raise BootstrapError('Runtime archive link escapes install directory')
        for member in members:
            if not Path(member.name).parts:
                continue
            destination = root / member.name
            if not destination.resolve().is_relative_to(root):
                raise BootstrapError('Runtime archive path escapes install directory')
            if member.issym() or member.islnk():
                link = destination.parent / member.linkname if member.issym() else root / member.linkname
                if not link.resolve().is_relative_to(root):
                    raise BootstrapError('Runtime archive link escapes install directory')
                if member.islnk() and (link.is_symlink() or not link.is_file()):
                    raise BootstrapError('Runtime hardlink must target an extracted regular file')
            member.mode &= 0o755
            handle.extract(member, root, set_attrs=False)
            if member.isfile():
                destination.chmod(member.mode)
        try:
            if any(not path.resolve().is_relative_to(root) for path in root.rglob('*')):
                raise BootstrapError('Completed runtime archive tree escapes install directory')
        except (OSError, RuntimeError) as error:
            raise BootstrapError('Completed runtime archive contains invalid links') from error


def ensure_binary():
    override = os.environ.get('NANO_SHELL_OLLAMA_BIN')
    target = Path(__file__).resolve().parent.parent / 'runtime'
    for candidate in (override, str(target / 'bin/ollama'), shutil.which('ollama')):
        if candidate:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return path.resolve()
    if override:
        raise BootstrapError('NANO_SHELL_OLLAMA_BIN does not name an executable')
    architecture = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if platform.system() != 'Linux' or not architecture:
        raise BootstrapError('Automatic runtime installation supports Linux x86_64 and ARM64')
    if not shutil.which('curl'):
        raise BootstrapError('curl is required to download the headless runtime')
    print('Downloading local Ollama runtime (one-time install)…', file=sys.stderr)
    with tempfile.TemporaryDirectory(prefix='.runtime-', dir=target.parent) as directory:
        temporary = Path(directory)
        compressed, archive = temporary / 'runtime.tar.zst', temporary / 'runtime.tar'
        subprocess.run(['curl', '-fL', '--retry', '2', '--connect-timeout', '15', '--max-time', '1800',
                        '--proto', '=https', '--proto-redir', '=https',
                        f'https://ollama.com/download/ollama-linux-{architecture}.tar.zst',
                        '-o', str(compressed)], check=True)
        decompress(compressed, archive)
        staging = temporary / 'extracted'
        staging.mkdir()
        extract_archive(archive, staging)
        binary = staging / 'bin/ollama'
        if binary.is_symlink() or not binary.resolve().is_relative_to(staging.resolve()) or not binary.is_file() or not os.access(binary, os.X_OK):
            raise BootstrapError('Downloaded runtime does not contain an executable bin/ollama')
        if target.exists():
            raise BootstrapError('Incomplete existing runtime directory; remove it before retrying installation')
        staging.rename(target)
    return target / 'bin/ollama'


if __name__ == '__main__':
    try:
        print(ensure_binary())
    except (BootstrapError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print('nano-shell install: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
