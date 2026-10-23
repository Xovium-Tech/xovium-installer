#!/usr/bin/env python3
"""Prepare portable models before a simulator starts, using the native converter.

Only orchestration lives here: .xovmodel parsing and geometry/sensor conversion
are handled by xovium-model-tool. Bundles are the editable source of truth.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from model_adapters import prepare
from camera_streams import configure as configure_camera_streams

SIMULATORS = ('gazebo-jetty', 'gazebo-harmonic', 'isaac', 'sih', 'flightgear', 'unreal-engine')
TARGETS = {name: 'gazebo' if name.startswith('gazebo-') else
           'unreal' if name == 'unreal-engine' else name for name in SIMULATORS}
SCHEMA = 1


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def ordinary(path, root):
    """Reject a symlink anywhere below the managed root, including dangling ones."""
    path = Path(path).absolute()
    root = Path(root).resolve()
    if '..' in path.parts or not path.is_relative_to(root): raise ValueError('Model path escapes shared storage: ' + str(path))
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink(): raise ValueError('Model paths must not contain symlinks: ' + str(current))
    return path


def inventory(folder):
    result = {}
    if not folder.exists(): return result
    for file in sorted(folder.rglob('*')):
        ordinary(file, folder)
        if file.is_file(): result[file.relative_to(folder).as_posix()] = digest(file)
        elif not file.is_dir(): raise ValueError('Unsupported generated asset: ' + str(file))
    return result


def bundles(shared):
    root = shared / 'models'
    entries = {}
    for path in sorted(root.rglob('*.xovmodel')):
        ordinary(path, shared)
        if not path.is_file(): raise ValueError('Expected a model bundle: ' + str(path))
        relative = path.relative_to(root)
        if relative.parts[0] in ('cessna', 'rover', 'custom') and len(relative.parts) == 2 and path.stem in ('model', relative.parts[0]):
            key = {'cessna': 'rc_cessna', 'rover': 'r1_rover', 'custom': 'custom'}[relative.parts[0]]
        else:
            parts = list(relative.parts[:-1])
            if path.stem != 'model' or not parts: parts.append(path.stem)
            key = re.sub('[^A-Za-z0-9_]', '_', '_'.join(parts))
            if not key or key[0].isdigit(): key = 'model_' + key
        if len(key) > 128: key = key[:100] + '_' + hashlib.sha256(key.encode()).hexdigest()[:16]
        if key.casefold() in ('baylands', 'coast_water') or key.casefold().startswith('xovium_'):
            raise ValueError('Model name is reserved for shared world or launch assets; rename the bundle: ' + str(path))
        existing = next((name for name in entries if name.casefold() == key.casefold()), None)
        if existing: raise ValueError(f'Two bundles use model key {key}: {entries[existing]} and {path}')
        entries[key] = path
    return entries


def tool_paths(shared):
    root = shared / 'transform/model-tool'
    tool, helper = root / 'bin/xovium-model-tool', root / 'bin/mesh-import'
    for path in (tool, helper, root / 'backend/sensors.json'):
        ordinary(path, shared)
        if not path.is_file(): raise ValueError('Native model converter is missing; refresh Xovium launchers: ' + str(path))
    return root, tool, helper


def command(shared, *arguments):
    root, tool, _ = tool_paths(shared)
    result = subprocess.run([str(tool), '--root', str(root), *map(str, arguments)], text=True, capture_output=True)
    try: response = json.loads(result.stdout)
    except ValueError: raise RuntimeError('Native model converter returned invalid output: ' + result.stderr[-2000:]) from None
    if result.returncode or not response.get('ok'):
        raise RuntimeError(response.get('error') or result.stderr or 'Model conversion failed')
    return response


def converter_signature(shared, simulator):
    root, tool, helper = tool_paths(shared)
    files = [tool, helper, root / 'backend/sensors.json', Path(__file__).resolve(),
             shared / 'transform/model_adapters.py', shared / 'transform/camera_streams.py']
    if simulator == 'flightgear':
        files.append(shared / 'transform/geometry.py')
    templates = shared / 'transform/templates'
    files += sorted(p for p in templates.rglob('*') if p.is_file())
    if simulator == 'unreal-engine':
        files += [shared / 'transform/unreal_import.py', shared / 'transform/blender_export.py']
    sha = hashlib.sha256((str(SCHEMA) + simulator).encode())
    for file in files:
        ordinary(file, shared)
        sha.update(file.relative_to(shared).as_posix().encode())
        sha.update(digest(file).encode())
    return sha.hexdigest()


def adapter_for(document, bundle, key):
    selection = bundle.parent / 'selection.json'
    if selection.exists():
        if selection.is_symlink(): raise ValueError('Model selection must not be a symlink')
        adapter = json.loads(selection.read_text()).get('model_base')
        if key in ('rc_cessna', 'r1_rover') and adapter != key:
            raise ValueError(f'{bundle}: the {key} library model must retain its matching launch adapter; select a custom model for a different vehicle')
    elif key in ('rc_cessna', 'r1_rover'):
        adapter = key
    else:
        adapter = {'fixed-wing': 'rc_cessna', 'rover': 'r1_rover'}.get(document.get('vehicle'))
    if adapter not in ('rc_cessna', 'r1_rover'):
        raise ValueError(f'{bundle}: choose fixed-wing or rover in the editor, or set model_base in selection.json to rc_cessna or r1_rover')
    family = {'fixed-wing': 'rc_cessna', 'rover': 'r1_rover'}.get(document.get('vehicle'))
    if family and family != adapter:
        raise ValueError(f'{bundle}: vehicle type conflicts with the {adapter} launch adapter; save it as a custom model and select the matching adapter in the installer')
    return adapter


def intact(models, record):
    if not isinstance(record.get('files'), dict) or not record['files']: return False
    for relative, expected in record['files'].items():
        path = ordinary(models / relative, models)
        if not path.is_file() or digest(path) != expected: return False
    return True


def publish(payload, models, record, installed, backup, commit):
    """Replace complete model directories; roll back every replacement on error."""
    expected = dict(installed)
    expected.update(record.get('files', {}))
    generated = inventory(payload)
    roots = list(payload.iterdir())
    for source in roots:
        destination = ordinary(models / source.name, models)
        existing = [destination] if destination.is_file() else list(destination.rglob('*')) if destination.exists() else []
        for file in existing:
            ordinary(file, models)
            if not file.is_file(): continue
            name = file.relative_to(models).as_posix()
            current = digest(file)
            if current not in (expected.get(name), generated.get(name)):
                raise ValueError('Preserve locally edited simulator output before conversion: ' + str(file) + '. Edit the shared .xovmodel as the source model.')
    backup.mkdir()
    changed = []
    try:
        for source in roots:
            destination = models / source.name
            saved = backup / source.name
            if destination.exists(): os.replace(destination, saved)
            changed.append((destination, saved))
            os.replace(source, destination)
        commit(generated)
    except BaseException:
        for destination, saved in reversed(changed):
            if destination.is_dir(): shutil.rmtree(destination)
            elif destination.exists(): destination.unlink()
            if saved.exists(): os.replace(saved, destination)
        raise
    return generated


def prepare_all(shared, simulator, *, say=print):
    shared = Path(shared).resolve()
    if simulator not in SIMULATORS: raise ValueError('Unknown simulator: ' + simulator)
    models = ordinary(shared.parent / 'simulation' / simulator / 'models', shared.parent)
    models.mkdir(parents=True, exist_ok=True)
    cache = ordinary(shared / 'transform/cache/model-exports', shared)
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = ordinary(cache / '.lock', shared)
    with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        sources = bundles(shared)
        if not sources: raise ValueError('No portable shared models are installed; refresh Xovium launchers first.')
        converter = converter_signature(shared, simulator)
        # Installer ownership provides the migration baseline for pre-bundle assets.
        state = json.loads((shared.parent / 'state.json').read_text())
        prefix = f'simulation/{simulator}/models/'
        owned = dict(state.get('copied_assets', {}))
        owned.update(state.get('shared_exports', {}).get(simulator, {}).get('files', {}))
        if simulator == 'unreal-engine': owned.update(state.get('shared_unreal_pending', {}))
        installed = {name[len(prefix):]: value for name, value in owned.items() if name.startswith(prefix)}
        results = []
        for key, bundle in sources.items():
            record_path = ordinary(cache / simulator / (key + '.json'), shared)
            record = json.loads(record_path.read_text()) if record_path.is_file() else {}
            source_hash = digest(bundle)
            selection = bundle.parent / 'selection.json'
            ordinary(selection, shared)
            signature = hashlib.sha256((source_hash + converter + key + (digest(selection) if selection.is_file() else '')).encode()).hexdigest()
            if record.get('schema') == SCHEMA and record.get('signature') == signature and intact(models, record):
                results.append(dict(key=key, changed=False))
                continue
            say(f'[models] Convert {bundle.relative_to(shared)} for {simulator}')
            with tempfile.TemporaryDirectory(prefix='.model-stage-', dir=cache) as temporary:
                stage = Path(temporary)
                workspace, exported, payload = stage / 'workspace', stage / 'export', stage / 'payload'
                _, _, helper = tool_paths(shared)
                command(shared, 'import', bundle, '--workspace', workspace, '--mesh-helper', helper)
                document = json.loads((workspace / 'project.json').read_text())
                adapter = adapter_for(document, bundle, key)
                command(shared, 'export', '--workspace', workspace, '--target', TARGETS[simulator], '--output', exported)
                payload.mkdir()
                prepare(shared, simulator, key, adapter, exported, payload)
                configure_camera_streams(document, simulator, payload / key)
                current = hashlib.sha256((digest(bundle) + converter_signature(shared, simulator) + key +
                    (digest(selection) if selection.is_file() else '')).encode()).hexdigest()
                if current != signature:
                    raise ValueError('The model or converter changed during conversion; start again to use the saved version: ' + str(bundle))
                def commit(files):
                    atomic_json(record_path, dict(schema=SCHEMA, signature=signature,
                                source=bundle.relative_to(shared).as_posix(), source_sha256=source_hash,
                                converter=converter, simulator=simulator, adapter=adapter, files=files))
                publish(payload, models, record, installed, stage / 'backup', commit)
                results.append(dict(key=key, changed=True, adapter=adapter))
        say(f'[models] {simulator}: {len(results)} shared models ready')
        return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--simulator', required=True, choices=SIMULATORS)
    args = parser.parse_args()
    try: prepare_all(args.shared, args.simulator)
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f'Model conversion failed: {error}\n')


if __name__ == '__main__': main()
