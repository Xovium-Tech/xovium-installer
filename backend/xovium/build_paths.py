import hashlib
import os
from pathlib import Path
import re

from xovium.paths import Error


def plain(path):
    return re.fullmatch(r'[A-Za-z0-9_./-]+', str(path)) is not None


def alias_root():
    return (Path.home() / '.local/share').resolve() / 'xovium/build-paths'


def prepare(ctx, plan):
    if 'px4' not in plan or plain(ctx.prefix):
        return
    base = alias_root()
    if not plain(base):
        raise Error('Choose an installation path containing only letters, numbers, /, ., - and _. '
                    'The home directory cannot provide a plain PX4 build alias.')
    token = hashlib.sha256(str(ctx.storage).encode()).hexdigest()[:24]
    alias = base / token
    if alias.is_relative_to(ctx.storage) or ctx.storage.is_relative_to(alias):
        raise Error('The internal build alias must be outside the installation directory.')
    ctx.say(f'Build tools use {alias}; all installed files stay in {ctx.storage}.')
    if ctx.dry:
        return
    for directory in (base.parent, base):
        if directory.is_symlink():
            raise Error(f'Build alias directory must not be a symlink: {directory}')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022:
            raise Error(f'Build alias directory must be owned by you and not writable by others: {directory}')
    if alias.is_symlink():
        if alias.lstat().st_uid != os.getuid() or alias.readlink() != ctx.storage:
            raise Error(f'Build alias points to a different installation: {alias}')
    elif alias.exists():
        raise Error(f'Build alias would overwrite an existing file: {alias}')
    ctx.storage.mkdir(parents=True, exist_ok=True)
    if not alias.is_symlink():
        alias.symlink_to(ctx.storage, target_is_directory=True)
    ctx.state['aliases'] = list(dict.fromkeys([*ctx.state.get('aliases', []), str(alias)]))
    ctx.state['build_prefix'] = str(alias)
    ctx.state['build_alias'] = str(alias)
    ctx.prefix = alias
    ctx.save()


def cleanup(state):
    alias = state.get('build_alias')
    if not alias:
        return
    base = Path(alias).parent
    if base != alias_root():
        return
    for directory in (base, base.parent):
        try:
            directory.rmdir()
        except OSError:
            break
