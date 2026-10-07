"""Private XDG state and configuration."""
import json
import os
from pathlib import Path
import secrets
import stat


class StorageError(ValueError):
    pass


def _private_dir(override, xdg, fallback):
    path = Path(os.environ.get(override) or str(Path(os.environ.get(xdg) or str(Path.home() / fallback)) / 'nano-shell')).expanduser()
    if path.is_symlink():
        raise StorageError('private directory must not be a symbolic link')
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise StorageError('private directory is owned by another user')
    path.chmod(0o700)
    return path


def state_dir():
    return _private_dir('NANO_SHELL_STATE_DIR', 'XDG_STATE_HOME', '.local/state')


def config_dir():
    return _private_dir('NANO_SHELL_CONFIG_DIR', 'XDG_CONFIG_HOME', '.config')


def token_path():
    return state_dir() / 'token'


def _read_private(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise StorageError('cannot safely read ' + str(path)) from exc
    with os.fdopen(descriptor) as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise StorageError('private file must be a regular file owned by this user')
        os.fchmod(handle.fileno(), 0o600)
        value = handle.read(65537)
        if len(value) > 65536:
            raise StorageError('private file is too large')
        return value


def get_token():
    path = token_path()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        token = _read_private(path).strip()
    else:
        token = secrets.token_urlsafe(48)
        with os.fdopen(descriptor, 'w') as handle:
            handle.write(token + '\n')
    if len(token) < 40 or len(token) > 128 or any(not (char.isalnum() or char in '-_') for char in token):
        raise StorageError('invalid bridge token; remove the token file while daemon is stopped')
    return token


def validate_config(config):
    if not isinstance(config, dict) or set(config) - {'backend', 'model'} or config.get('backend') not in {'nano', 'ollama'}:
        raise StorageError('invalid configuration')
    model = config.get('model', '')
    if not isinstance(model, str) or len(model) > 200 or any(ord(char) < 32 for char in model):
        raise StorageError('invalid model name')
    if config['backend'] == 'ollama' and not model.strip():
        raise StorageError('Ollama requires an explicitly configured model')
    return {'backend': config['backend'], 'model': model}


def load_config():
    path = config_dir() / 'config.json'
    if not path.exists() and not path.is_symlink():
        return {'backend': 'nano', 'model': ''}
    try:
        return validate_config(json.loads(_read_private(path)))
    except (ValueError, RecursionError) as exc:
        raise StorageError('invalid configuration: ' + str(exc)) from exc


def save_config(config):
    config = validate_config(config)
    path = config_dir() / 'config.json'
    if path.is_symlink():
        raise StorageError('configuration must not be a symbolic link')
    temp = path.with_name('config-' + secrets.token_hex(8) + '.tmp')
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(config, handle)
            handle.write('\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
