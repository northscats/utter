"""Server-only MAX credentials and desired state. Never serialize this to a response."""
import contextlib
import fcntl
import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / 'private' / 'max-bot.json'
_lock = threading.RLock()
_local = threading.local()


def _directory():
    parent = CONFIG.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise ValueError('Unsafe MAX configuration directory')
    os.chmod(parent, 0o700)


@contextlib.contextmanager
def locked():
    """Serialize file changes and outgoing operations across threads and workers."""
    with _lock:
        if getattr(_local, 'depth', 0):
            _local.depth += 1
            try:
                yield
            finally:
                _local.depth -= 1
            return
        _directory()
        path = CONFIG.parent / 'max-bot.lock'
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            _local.depth = 1
            try:
                yield
            finally:
                _local.depth = 0
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def _read():
    try:
        fd = os.open(CONFIG, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {'token': '', 'group_id': None, 'enabled': False, 'generation': 0}
    try:
        mode = os.fstat(fd).st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError('Invalid MAX configuration file')
        with os.fdopen(fd, 'r', encoding='utf-8') as source:
            data = json.load(source)
    except Exception:
        # No accidental recovery from corrupt credentials by falling back to env.
        raise ValueError('MAX configuration cannot be read') from None
    if not isinstance(data, dict) or not isinstance(data.get('token'), str):
        raise ValueError('MAX configuration cannot be read')
    return data


def read():
    with locked():
        return _read()


def _write(data):
    _directory()
    fd, temporary = tempfile.mkstemp(prefix='.max-bot-', dir=CONFIG.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(data, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, CONFIG)
        directory_fd = os.open(CONFIG.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(token, group):
    with locked():
        old = _read()
        token = token.strip() or old['token']
        if not re.fullmatch(r'[A-Za-z0-9_-]{30,256}', token):
            raise ValueError('Укажите действительный токен бота MAX.')
        if not re.fullmatch(r'-?[0-9]{1,19}', group.strip()) or not int(group):
            raise ValueError('Укажите числовой ID группы.')
        group_id = int(group)
        if not -(2**63) <= group_id < 2**63:
            raise ValueError('Укажите корректный ID группы.')
        # Saving never activates the bot; changing either credential stops it.
        changed = token != old.get('token') or group_id != old.get('group_id')
        data = {'token': token, 'group_id': group_id,
                'enabled': bool(old.get('enabled')) and not changed,
                'generation': int(old.get('generation', 0)) + 1}
        _write(data)
        return changed, old.get('token', '')


def set_enabled(enabled):
    with locked():
        data = _read()
        if enabled and (not data['token'] or not data['group_id']):
            raise ValueError('Сначала сохраните токен и ID группы.')
        if data.get('enabled') != enabled:
            data['enabled'] = enabled
            data['generation'] = int(data.get('generation', 0)) + 1
            _write(data)
        return data


def active(token):
    data = _read()
    return bool(data.get('enabled') and data.get('token') == token)