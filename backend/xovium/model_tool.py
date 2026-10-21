"""Install the native portable-model converter and seed editable shared bundles."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

from xovium.core import Error, digest

MODEL_EDITOR_REPOSITORY = 'https://github.com/Xovium-Tech/xovium-model-editor.git'
# Pin the tested native backend so installer refreshes remain reproducible.
MODEL_EDITOR_COMMIT = 'd4af8258f186be9cdf4d8893f9ab9b213d66f2cf'


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
    override = os.environ.get('XOVIUM_MODEL_EDITOR_SOURCE')
    if override:
        source = Path(override).expanduser().resolve()
        if not (source / 'backend/main.cpp').is_file():
            raise Error('XOVIUM_MODEL_EDITOR_SOURCE must contain the native model editor backend.')
    else:
        source = ctx.clone(MODEL_EDITOR_REPOSITORY, MODEL_EDITOR_COMMIT,
                           'shared/transform/model-tool-source/' + MODEL_EDITOR_COMMIT)
    files = [source / 'CMakeLists.txt', source / 'tools/mesh_import.cpp', source / 'app/dependencies.json']
    files += sorted(p for p in (source / 'backend').glob('*') if p.is_file())
    signature = hashlib.sha256(''.join(digest(file) for file in files).encode()).hexdigest()
    destination = ctx.managed('shared/transform/model-tool')
    previous = ctx.state.get('model_tool', {})
    if (previous.get('source') == signature and previous.get('files') and
            all(ctx.managed(name).is_file() and digest(ctx.managed(name)) == value
                for name, value in previous['files'].items())):
        return destination
    build = ctx.managed('shared/transform/build/model-tool')
    dependencies = ctx.managed('shared/transform/build/model-tool-deps')
    ctx.run('bash', source / 'build.sh', '--backend-only', env={
        'XOVIUM_BUILD_DIR': build, 'XOVIUM_DEPS_DIR': dependencies,
        'XOVIUM_BUILD_JOBS': str(ctx.options.jobs)})
    copies = [(build / 'xovium-model-tool', 'bin/xovium-model-tool'),
              (build / 'mesh-import', 'bin/mesh-import'),
              (source / 'backend/sensors.json', 'backend/sensors.json'),
              (source / 'schema/model.schema.json', 'schema/model.schema.json'),
              (source / 'LICENSE', 'LICENSE'), (source / 'THIRD_PARTY.md', 'THIRD_PARTY.md')]
    recorded = {}
    for original, relative in copies:
        name = 'shared/transform/model-tool/' + relative
        ctx.copy_tree(original, name)
        recorded[name] = digest(original)
    ctx.state['model_tool'] = dict(source=signature, revision=MODEL_EDITOR_COMMIT, files=recorded)
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
        signature = source_signature(source.parent)
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
            cache.command(shared, 'import', source, '--workspace', workspace,
                          '--mesh-helper', shared / 'transform/model-tool/bin/mesh-import')
            document_file = workspace / 'project.json'
            document = json.loads(document_file.read_text())
            if vehicle != 'custom': document['vehicle'] = vehicle
            document_file.write_text(json.dumps(document) + '\n')
            cache.command(shared, 'save', '--workspace', workspace, '--output', bundle)
        tracked[relative] = dict(source=signature, bundle=digest(bundle))
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
