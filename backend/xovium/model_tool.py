"""Install the desktop model editor and native converter; seed editable bundles."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from xovium.core import Error, digest
from xovium.model_defaults import add_plane_cameras, plane_defaults_signature, upgrade_pristine_plane_cameras

EDITOR_LAUNCHER = '''#!/usr/bin/env bash
set -euo pipefail
EDITOR_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export XOVIUM_MESH_IMPORT="$EDITOR_ROOT/bin/mesh-import"
export XOVIUM_MODEL_TOOL="$EDITOR_ROOT/bin/xovium-model-tool"
if [[ ${1:-} == --cli ]]; then
    shift
    exec "$XOVIUM_MODEL_TOOL" --root "$EDITOR_ROOT" "$@"
fi
exec "$EDITOR_ROOT/bin/xovium-model-editor" --root "$EDITOR_ROOT" "$@"
'''


def runtime_module(shared):
    path = shared / 'transform/model_cache.py'
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location('xovium_model_cache', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def install(ctx):
    from xovium.editor_source import resolve, revision
    override = os.environ.get('XOVIUM_MODEL_EDITOR_SOURCE')
    if override:
        source = Path(override).expanduser().resolve()
        if not all((source / name).is_file() for name in ('backend/main.cpp', 'app/main.cpp', 'tools/build-state.sh')):
            raise Error('XOVIUM_MODEL_EDITOR_SOURCE must contain the full native model editor source.')
    else:
        source = resolve(ctx.root, run=ctx.run)
    files = [source / name for name in ('CMakeLists.txt', 'build.sh', 'run.sh', 'LICENSE', 'THIRD_PARTY.md')]
    for folder in ('backend', 'app', 'schema', 'tools'):
        files += sorted(p for p in (source / folder).rglob('*')
                        if p.is_file() and not set(p.relative_to(source).parts) & {'.git', '__pycache__', '.build'})
    fingerprint = hashlib.sha256(EDITOR_LAUNCHER.encode())
    for file in files:
        if file.is_symlink(): raise Error('Model editor sources must not contain symlinks: ' + str(file))
        fingerprint.update(file.relative_to(source).as_posix().encode())
        fingerprint.update(digest(file).encode())
    signature = fingerprint.hexdigest()
    destination = ctx.managed('shared/transform/model-tool')
    previous = ctx.state.get('model_tool', {})
    if (previous.get('source') == signature and previous.get('files') and
            all(ctx.managed(name).is_file() and digest(ctx.managed(name)) == value
                and (not (Path(name).parent.name == 'bin' or Path(name).name == 'run.sh')
                     or os.access(ctx.managed(name), os.X_OK))
                for name, value in previous['files'].items())):
        return destination
    build = ctx.managed('shared/transform/build/model-tool')
    dependencies = ctx.managed('shared/transform/build/model-tool-deps')
    # The wizard may already have built the editor before installation.
    for candidate in (source / '.build/cmake', build):
        checked = subprocess.run(['bash', str(source / 'tools/build-state.sh'), 'check',
                                  str(source), str(candidate), 'gui'], capture_output=True)
        if checked.returncode == 0:
            build = candidate
            ctx.say('[models] Reuse desktop editor build: ' + str(build))
            break
    else:
        ctx.run('bash', source / 'build.sh', env={
            'XOVIUM_BUILD_DIR': build, 'XOVIUM_DEPS_DIR': dependencies,
            'XOVIUM_SOURCE_CACHE': source / '.build/deps',
            'XOVIUM_BUILD_JOBS': str(ctx.options.jobs)})
    copies = [(build / 'xovium-model-editor', 'bin/xovium-model-editor'),
              (build / 'xovium-model-tool', 'bin/xovium-model-tool'),
              (build / 'mesh-import', 'bin/mesh-import'),
              (source / 'backend/sensors.json', 'backend/sensors.json'),
              (source / 'schema/model.schema.json', 'schema/model.schema.json'),
              (source / 'LICENSE', 'LICENSE'), (source / 'THIRD_PARTY.md', 'THIRD_PARTY.md')]
    recorded = {}
    for original, relative in copies:
        targets = ['tools/xovium-model-editor/' + relative]
        if relative != 'bin/xovium-model-editor':
            targets.append('shared/transform/model-tool/' + relative)
        for name in targets:
            ctx.copy_tree(original, name)
            recorded[name] = digest(original)
    # Stage a relocatable launcher, without the source checkout's build logic.
    with tempfile.TemporaryDirectory(prefix='editor-launcher-') as temporary:
        launcher = Path(temporary) / 'run.sh'
        launcher.write_text(EDITOR_LAUNCHER)
        launcher.chmod(0o755)
        name = 'tools/xovium-model-editor/run.sh'
        ctx.copy_tree(launcher, name)
        recorded[name] = digest(launcher)
    ctx.state['model_tool'] = dict(source=signature, revision=revision(ctx.root), files=recorded,
                                  launcher='tools/xovium-model-editor/run.sh')
    ctx.save()
    return destination


def source_signature(folder):
    value = hashlib.sha256()
    for path in sorted(p for p in folder.rglob('*') if p.is_file() and '.git' not in p.parts):
        if path.is_symlink(): raise Error('Shared model sources must not contain symlinks: ' + str(path))
        value.update(path.relative_to(folder).as_posix().encode())
        value.update(digest(path).encode())
    return value.hexdigest()


def seed_bundles(ctx):
    shared = ctx.managed('shared')
    cache = runtime_module(shared)
    tracked = ctx.state.setdefault('shared_portable_seeds', {})
    for folder, vehicle in (('cessna', 'fixed-wing'), ('rover', 'rover'), ('custom', 'custom')):
        base = shared / 'models' / folder
        source, bundle = base / 'source/model.sdf', base / 'model.xovmodel'
        if not source.is_file(): continue
        source_base = source_signature(source.parent)
        signature = source_base
        if folder == 'cessna':
            signature = hashlib.sha256((signature + plane_defaults_signature()).encode()).hexdigest()
        relative = bundle.relative_to(ctx.prefix).as_posix()
        previous = tracked.get(relative, {})
        if bundle.exists():
            if digest(bundle) != previous.get('bundle'):
                ctx.say('[models] Preserve edited bundle: ' + str(bundle))
                continue
            if previous.get('source') == signature: continue
        ctx.say('[models] Create portable shared model: ' + str(bundle))
        with tempfile.TemporaryDirectory(prefix='portable-', dir=shared / 'cache') as temporary:
            workspace = Path(temporary) / 'workspace'
            # A defaults-only upgrade starts from the pristine portable bundle
            # so its existing sensor IDs, mesh assets and document stay intact.
            unchanged_source = previous.get('source_base', previous.get('source')) == source_base
            original = bundle if bundle.is_file() and unchanged_source else source
            cache.command(shared, 'import', original, '--workspace', workspace,
                          '--mesh-helper', shared / 'transform/model-tool/bin/mesh-import')
            document_file = workspace / 'project.json'
            document = json.loads(document_file.read_text())
            if vehicle != 'custom': document['vehicle'] = vehicle
            if folder == 'cessna':
                # Existing bundles reach here only after exact ownership checks.
                if original == bundle: upgrade_pristine_plane_cameras(document)
                add_plane_cameras(document)
            document_file.write_text(json.dumps(document) + '\n')
            cache.command(shared, 'save', '--workspace', workspace, '--output', bundle)
        tracked[relative] = dict(source=signature, source_base=source_base, bundle=digest(bundle))
        ctx.save()
    ctx.save()


def import_bundle(ctx, source, destination):
    shared = ctx.managed('shared')
    cache = runtime_module(shared)
    with tempfile.TemporaryDirectory(prefix='portable-', dir=shared / 'cache') as temporary:
        workspace = Path(temporary) / 'workspace'
        cache.command(shared, 'import', source, '--workspace', workspace,
                      '--mesh-helper', shared / 'transform/model-tool/bin/mesh-import')
        if source.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            cache.command(shared, 'save', '--workspace', workspace, '--output', destination)


def prepare_models(ctx, simulator):
    cache = runtime_module(ctx.managed('shared'))
    cache.prepare_all(ctx.prefix / 'shared', simulator, say=ctx.say)
    adopt(ctx)


def adopt(ctx):
    """Recognize runtime-generated outputs when the installer later repairs assets."""
    from xovium.layout import SIMULATORS

    def ordinary(relative):
        path = Path(relative)
        if (not isinstance(relative, str) or not relative or path.is_absolute()
                or '..' in path.parts or path.as_posix() != relative or '\\' in relative):
            raise Error('Invalid cached model path: ' + str(relative))
        file = ctx.managed(relative)
        current = ctx.prefix
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise Error('Cached model paths must not contain symlinks: ' + str(current))
        return file

    folder = ordinary('shared/transform/cache/model-exports')
    adopted = {}
    for path in sorted(folder.glob('*/*.json')):
        ordinary(path.relative_to(ctx.prefix).as_posix())
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or not isinstance(record.get('files'), dict):
            raise Error('Invalid cached model record: ' + str(path))
        simulator = record.get('simulator')
        if simulator not in SIMULATORS or simulator != path.parent.name:
            raise Error('Invalid cached model simulator: ' + str(path))
        for relative, expected in record['files'].items():
            if (not isinstance(relative, str) or not relative or Path(relative).is_absolute()
                    or '..' in Path(relative).parts or Path(relative).as_posix() != relative
                    or '\\' in relative):
                raise Error('Invalid cached model output path: ' + str(relative))
            name = f'simulation/{simulator}/models/{relative}'
            file = ordinary(name)
            if file.is_file() and digest(file) == expected:
                adopted[name] = expected
    ctx.state.setdefault('copied_assets', {}).update(adopted)
    ctx.save()
