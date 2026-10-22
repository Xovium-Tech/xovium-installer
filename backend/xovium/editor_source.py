"""Resolve the editor submodule, including ordinary clones and source archives."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xovium.core import Error


def metadata(root):
    data = json.loads((Path(root) / 'backend/editor-source.json').read_text())
    if (data.get('schema') != 1 or data.get('path') != 'tools/xovium-model-editor'
            or data.get('repository') != 'https://github.com/Xovium-Tech/xovium-model-editor.git'
            or not re.fullmatch('[0-9a-f]{40}', data.get('revision', ''))):
        raise Error('Invalid pinned model editor source metadata.')
    return data


def revision(root):
    return metadata(root)['revision']


def _probe(*args):
    result = subprocess.run(list(map(str, args)), text=True, capture_output=True)
    return result.returncode, result.stdout.strip()


def _run(*args):
    print('+ ' + ' '.join(map(str, args)), file=sys.stderr, flush=True)
    result = subprocess.run(list(map(str, args)), stdout=sys.stderr)
    if result.returncode:
        raise Error('Unable to prepare model editor sources; check the command output above.')


def _check_sources(source):
    paths = [source / name for name in ('CMakeLists.txt', 'run.sh', 'build.sh', 'LICENSE', 'THIRD_PARTY.md')]
    for folder in ('app', 'backend', 'schema', 'tools'):
        base = source / folder
        if base.is_symlink():
            raise Error('Model editor sources must not contain symlinks: ' + str(base))
        paths.extend(path for path in base.rglob('*')
                     if not set(path.relative_to(source).parts) & {'.git', '.build', '__pycache__'})
    for path in paths:
        if path.is_symlink():
            raise Error('Model editor sources must not contain symlinks: ' + str(path))


def resolve(root, run=None):
    """Return pinned sources without resetting or replacing a local checkout."""
    root = Path(root).resolve()
    data = metadata(root)
    source = root / data['path']
    runner = run or _run
    for path in (source.parent, source):
        if path.is_symlink():
            raise Error('The model editor source directory must not be a symbolic link: ' + str(path))
    _check_sources(source)
    git_checkout = (root / '.git').exists()
    if git_checkout:
        status, entry = _probe('git', '-C', root, 'ls-files', '--stage', '--', data['path'])
        if status or not entry.startswith('160000 ' + data['revision'] + ' 0\t'):
            raise Error('The editor submodule and backend/editor-source.json must pin the same commit.')
    if (source / '.git').exists():
        status, head = _probe('git', '-C', source, 'rev-parse', '--verify', 'HEAD')
        if not status and head != data['revision']:
            raise Error('The model editor checkout differs from the pinned version. Preserve local edits, '
                        'then run git submodule update --init --recursive from the installer folder.')
        if not status and all((source / name).is_file() for name in
                              ('CMakeLists.txt', 'run.sh', 'build.sh', 'backend/main.cpp')):
            return source
    elif source.exists() and any(source.iterdir()):
        raise Error('Untracked files occupy the model editor source directory: ' + str(source))
    if git_checkout:
        status, url = _probe('git', 'config', '--file', root / '.gitmodules', '--get',
                             'submodule.tools/xovium-model-editor.url')
        if status or url != data['repository']:
            raise Error('The model editor submodule URL does not match its pinned source metadata.')
        runner('git', '-C', root, 'submodule', 'update', '--init', '--recursive', '--', data['path'])
    else:
        # GitHub source ZIPs omit gitlink contents. Fetch the same exact commit.
        source.parent.mkdir(parents=True, exist_ok=True)
        if not (source / '.git').exists():
            runner('git', 'init', source)
            runner('git', '-C', source, 'remote', 'add', 'origin', data['repository'])
        status, url = _probe('git', '-C', source, 'remote', 'get-url', 'origin')
        if status or url != data['repository']:
            raise Error('Existing editor source remote differs from the pinned repository.')
        runner('git', '-C', source, 'fetch', '--depth', '1', 'origin', data['revision'])
        runner('git', '-C', source, 'checkout', '--detach', data['revision'])
    status, head = _probe('git', '-C', source, 'rev-parse', '--verify', 'HEAD')
    if status or head != data['revision'] or not (source / 'backend/main.cpp').is_file():
        raise Error('Model editor source preparation did not produce the pinned native backend.')
    _check_sources(source)
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(resolve(args.root))
    except (Error, OSError, ValueError) as error:
        parser.exit(1, 'Model editor setup failed: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
