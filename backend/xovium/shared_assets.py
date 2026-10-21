"""Install editable common sources and regenerate owned simulator exports."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET

from xovium.core import Error, digest


def seed(ctx, source, relative):
    """Update an unedited seed; the shared library belongs to the user."""
    source = Path(source)
    files = sorted(p for p in source.rglob('*') if p.is_file() and '__pycache__' not in p.parts) if source.is_dir() else [source]
    previous = ctx.state.setdefault('shared_seeds', {})
    for original in files:
        name = str(Path(relative) / original.relative_to(source)) if source.is_dir() else relative
        destination = ctx.managed(name)
        expected = digest(original)
        if destination.exists() and digest(destination) not in (expected, previous.get(name)):
            ctx.say(f'[shared] Preserve edited source: {name}')
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists() or digest(destination) != expected:
            shutil.copy2(original, destination)
        previous[name] = expected


def ensure(ctx):
    for folder in ('models/cessna', 'models/rover', 'worlds/baylands', 'worlds/dynamic_terrain', 'transform', 'cache/terrain'):
        ctx.managed('shared/' + folder).mkdir(parents=True, exist_ok=True)
    seed(ctx, ctx.assets / 'shared', 'shared')


def unpack(archive, destination):
    """Fuel bundles are data: reject links and paths outside the staging area."""
    with zipfile.ZipFile(archive) as zipped:
        for info in zipped.infolist():
            path = Path(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if path.is_absolute() or '..' in path.parts or '\\' in info.filename or mode == 0o120000:
                raise Error(f'Unsafe shared asset ZIP entry: {info.filename}')
        zipped.extractall(destination)


def populate(ctx):
    ensure(ctx)
    source = ctx.prefix / 'px4/source/Tools/simulation/gz'
    if not source.is_dir():
        return False
    for original, folder in (('rc_cessna', 'cessna'), ('r1_rover', 'rover')):
        model = source / 'models' / original
        if not (model / 'model.sdf').is_file():
            raise Error(f'PX4 is missing the shared model source: {model}')
        seed(ctx, model, f'shared/models/{folder}/source')
        if not (model / 'LICENSE').is_file() and (source / 'LICENSE').is_file():
            seed(ctx, source / 'LICENSE', f'shared/models/{folder}/source/LICENSE')
    seed(ctx, source / 'worlds/baylands.sdf', 'shared/worlds/baylands/source/world.sdf')
    seed(ctx, ctx.assets / 'isaac/Isaac sim/rover.json', 'shared/models/rover/physics.json')
    for model, template in (('rc_cessna', 'Cessna-set.xml'), ('r1_rover', 'R1-set.xml')):
        seed(ctx, ctx.assets / 'flightgear' / template, f'shared/transform/templates/flightgear/{model}/{template}')
    dynamics = ctx.prefix / 'px4/source/Tools/simulation/flightgear/flightgear_bridge/models/Rascal/Rascal110-Electric-YASim.xml'
    if dynamics.is_file():
        seed(ctx, dynamics, 'shared/transform/templates/flightgear/rc_cessna/Cessna.xml')
        seed(ctx, dynamics.parent / 'LICENSE', 'shared/transform/templates/flightgear/rc_cessna/LICENSE')
    metadata = json.loads((ctx.assets / 'shared/worlds/baylands/sources.json').read_text())
    sources = ctx.state.setdefault('shared_asset_sources', {})
    for asset in metadata['assets']:
        destination = f"shared/worlds/baylands/source/models/{asset['name']}"
        seeded = [name for name in ctx.state.get('shared_seeds', {}) if name.startswith(destination + '/')]
        if (sources.get(asset['name']) == asset['sha256'] and seeded
                and all(ctx.managed(name).is_file() for name in seeded)):
            continue
        archive = ctx.download(asset['url'], f"shared/cache/downloads/{asset['name']}-v{asset['version']}.zip", sha256=asset['sha256'])
        with tempfile.TemporaryDirectory(prefix='unpack-', dir=ctx.managed('shared/cache')) as temp:
            unpack(archive, temp)
            seed(ctx, Path(temp), destination)
        sources[asset['name']] = asset['sha256']
    import_custom(ctx)
    ctx.save()
    return True


def import_custom(ctx):
    from xovium.simulation_profiles import custom_source
    selection = ctx.config.get('wizard', {}).get('shared_selection', {})
    if selection.get('model') != 'custom':
        return
    destination = ctx.managed('shared/models/custom/source')
    source = Path(selection['model_path']).expanduser().resolve()
    original = ctx.state.get('shared_custom_input')
    if source.suffix.lower() == '.xovmodel':
        from xovium.model_tool import import_bundle
        from xovium.profiles import write_owned
        bundle = ctx.managed('shared/models/custom/model.xovmodel')
        if source == bundle.resolve() or original != str(source) or not bundle.is_file():
            if bundle.is_file() and source != bundle.resolve() and original != str(source):
                raise Error(f'Preserve your existing shared custom model before replacing it: {bundle}')
            import_bundle(ctx, source, bundle)
        write_owned(ctx, 'shared/models/custom/selection.json', json.dumps({'model_base': selection['model_base']}) + '\n')
        ctx.state['shared_custom_input'] = str(source)
        selection['model_path'] = str(bundle)
        return
    bundle = ctx.managed('shared/models/custom/model.xovmodel')
    if bundle.exists() and original != str(source) and source != (destination / 'model.sdf').resolve():
        raise Error(f'Preserve the current shared custom bundle before selecting a different SDF source: {bundle}')
    if source == (destination / 'model.sdf').resolve():
        custom_source(source)
    elif original == str(source) and (destination / 'model.sdf').is_file():
        # The imported shared folder is authoritative after the first import.
        # Refreshing must work even after the original download is removed.
        custom_source(destination / 'model.sdf')
    else:
        root = custom_source(source)
        if ctx.prefix.resolve().is_relative_to(source.parent):
            raise Error('Choose a dedicated custom model folder outside the installation directory tree.')
        if destination.exists():
            for file in destination.rglob('*'):
                if file.is_file():
                    relative = str(file.relative_to(ctx.prefix))
                    if digest(file) != ctx.state.get('shared_seeds', {}).get(relative):
                        raise Error(f'Preserve your edited custom source before replacing it: {file}')
        for file in source.parent.rglob('*'):
            if file.is_symlink():
                raise Error(f'Use ordinary files inside the custom model folder, not symlinks: {file}')
        with tempfile.TemporaryDirectory(prefix='custom-', dir=ctx.managed('shared/cache')) as temp:
            staged = Path(temp) / 'source'
            shutil.copytree(source.parent, staged, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            ET.indent(root, space='  ')
            ET.ElementTree(root).write(staged / 'model.sdf', encoding='utf-8', xml_declaration=True)
            if destination.exists():
                for file in sorted(destination.rglob('*'), reverse=True):
                    if file.is_file() and not (staged / file.relative_to(destination)).is_file():
                        relative = str(file.relative_to(ctx.prefix))
                        ctx.managed(relative).unlink()
                        ctx.state.get('shared_seeds', {}).pop(relative, None)
                    elif file.is_dir() and not any(file.iterdir()):
                        file.rmdir()
            seed(ctx, staged, 'shared/models/custom/source')
        ctx.state['shared_custom_input'] = str(source)
    from xovium.profiles import write_owned
    write_owned(ctx, 'shared/models/custom/selection.json', json.dumps({'model_base': selection['model_base']}) + '\n')
    selection['model_path'] = str(destination / 'model.sdf')


def fingerprint(shared):
    sha = hashlib.sha256()
    for name in ('catalog.json', 'worlds', 'transform'):
        path = shared / name
        files = sorted(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else [path]
        for file in files:
            relative = file.relative_to(shared)
            if set(relative.parts) & {'cache', 'build', '__pycache__', 'model-tool', 'model-tool-source', '.git'}:
                continue
            sha.update(str(relative).encode())
            sha.update(digest(file).encode())
    return sha.hexdigest()


def compile_converter(ctx):
    shared = ctx.managed('shared')
    source = shared / 'transform/mesh_export.cpp'
    build = ctx.managed('shared/transform/build')
    build.mkdir(exist_ok=True)
    executable = build / 'mesh-export'
    stamp = build / 'source.sha256'
    expected = digest(source)
    if not executable.is_file() or not stamp.is_file() or stamp.read_text() != expected:
        ctx.run('g++', '-std=c++17', '-O2', source, '-lassimp', '-o', executable)
        stamp.write_text(expected)


def installed_simulators(ctx):
    from xovium.layout import SIMULATORS
    components = ctx.state.get('components', {})
    return [name for name in SIMULATORS if components.get(name, {}).get('status') == 'installed'
            or name == 'sih' and components.get('sih-core', {}).get('status') == 'installed']


def px4_supplied(ctx, relative):
    parts = Path(relative).parts
    return (len(parts) > 3 and parts[0] == 'simulation' and parts[1].startswith('gazebo-')
            and parts[2] in ('models', 'worlds')
            and (ctx.prefix / 'px4/source/Tools/simulation/gz' / Path(*parts[2:])).is_file())


def retire_presets(ctx):
    """Remove unchanged installer presets, retaining PX4 files and local edits."""
    retired = ['shared/worlds/dynamic_terrain/source/models/uneven_ground',
               'simulation/isaac/models/airplane.usda', 'simulation/isaac/worlds/runway.usda',
               'simulation/isaac/worlds/rover_runway.usda',
               'simulation/sih/models/airplane.usda', 'simulation/sih/worlds/runway.usda']
    for distro in ('jetty', 'harmonic'):
        retired += [f'simulation/gazebo-{distro}/{name}' for name in (
            'models/advanced_plane', 'models/uneven_ground', 'models/xovium_x500', 'models/xovium_standard_vtol',
            'worlds/mcmillan_airfield_gz.sdf', 'worlds/xovium_training.sdf')]
    for relative in retired:
        path = ctx.managed(relative)
        files = list(path.rglob('*')) if path.is_dir() else [path]
        for file in files:
            if not file.is_file(): continue
            name = str(file.relative_to(ctx.prefix))
            if px4_supplied(ctx, name): continue
            expected = ctx.state.get('copied_assets', {}).get(name) or ctx.state.get('shared_seeds', {}).get(name)
            if expected and digest(file) == expected:
                ctx.managed(name).unlink()
                for key in ('copied_assets', 'shared_seeds'):
                    ctx.state.get(key, {}).pop(name, None)
                ctx.say(f'[shared] Remove retired preset: {name}')
            else:
                ctx.say(f'[shared] Preserve local asset: {name}')
        if path.is_dir():
            for directory in sorted((p for p in path.rglob('*') if p.is_dir()), reverse=True):
                if not any(directory.iterdir()): directory.rmdir()
            if not any(path.iterdir()): path.rmdir()


def refresh(ctx):
    if ctx.dry:
        ctx.say('Shared library: seed Cessna, rover, Baylands and dynamic terrain; regenerate installed simulator formats.')
        return
    if not (ctx.prefix / 'px4/source/Tools/simulation/gz').is_dir():
        return
    from xovium import model_tool
    ensure(ctx)
    model_tool.install(ctx)
    if not populate(ctx): return
    model_tool.seed_bundles(ctx)
    model_tool.adopt(ctx)
    retire_presets(ctx)
    shared = ctx.managed('shared')
    names = installed_simulators(ctx)
    compile_converter(ctx)
    signature = fingerprint(shared)
    exports = ctx.state.setdefault('shared_exports', {})
    bundle_keys = set(model_tool.runtime_module(shared).bundles(shared))
    for name in names:
        model_tool.prepare_models(ctx, name)
        previous = exports.get(name, {})
        # Reuse only if both the common sources and every exported file match.
        intact = previous.get('source') == signature and all(
            ctx.managed(path).is_file() and digest(ctx.managed(path)) == value
            for path, value in previous.get('files', {}).items())
        if intact and previous.get('files'):
            ctx.say(f'[shared] Reuse {name} export')
            continue
        ctx.say(f'[shared] Convert worlds for {name}')
        with tempfile.TemporaryDirectory(prefix='export-', dir=ctx.managed('shared/cache')) as temp:
            output = Path(temp)
            ctx.run('python3', shared / 'transform/convert.py', '--shared', shared,
                    '--simulator', name, '--output', output, '--worlds-only')
            files = {}
            for file in sorted(p for p in output.rglob('*') if p.is_file()):
                relative = f'simulation/{name}/' + str(file.relative_to(output))
                destination = ctx.managed(relative)
                if destination.exists() and digest(destination) not in (digest(file), ctx.state.get('copied_assets', {}).get(relative)):
                    raise Error(f'Locally modified simulator export: {destination}. Preserve it before regenerating shared assets.')
                files[relative] = digest(file)
            obsolete = {path: value for path, value in previous.get('files', {}).items()
                        if path not in files and Path(path).suffix not in ('.umap', '.uasset')
                        and not px4_supplied(ctx, path)
                        and not (len(Path(path).parts) > 3 and Path(path).parts[2] == 'models'
                                 and (Path(path).parts[3] in bundle_keys or Path(path).parts[3].removesuffix('.usda') in bundle_keys))}
            for path, value in obsolete.items():
                destination = ctx.managed(path)
                if destination.exists() and digest(destination) != value:
                    raise Error(f'Preserve locally modified obsolete export before regenerating: {destination}')
            ctx.copy_tree(output, f'simulation/{name}')
            for path in obsolete:
                destination = ctx.managed(path)
                if destination.is_file(): destination.unlink()
                ctx.state.get('copied_assets', {}).pop(path, None)
        if name == 'unreal-engine':
            files.update(import_unreal(ctx))
        exports[name] = {'source': signature, 'files': files}
        ctx.save()
    ctx.say(f'Shared sources: {shared}/models and {shared}/worlds; converters: {shared}/transform')


def import_unreal(ctx):
    base = ctx.managed('simulation/unreal-engine')
    # Check generated native packages before the editor is allowed to replace them.
    tracked = ctx.state.get('shared_exports', {}).get('unreal-engine', {}).get('files', {})
    pending = ctx.state.get('shared_unreal_pending', {})
    for relative, current in unreal_files(ctx).items():
        if current not in (tracked.get(relative), pending.get(relative)):
            raise Error(f'Preserve locally modified Unreal package before shared import: {ctx.prefix / relative}')
    editor = base / 'engine/Engine/Binaries/Linux/UnrealEditor-Cmd'
    script = ctx.managed('shared/transform/unreal_import.py')
    try:
        # Older installations put this cheat CVar in Engine.ini. Remove that
        # legacy entry for the commandlet without changing the user's config.
        # Tracing is unnecessary for imports and its daemon inherits the terminal.
        ctx.run(editor, base / 'bridge/Unreal/PX4Unreal.uproject', '-unattended', '-nullrhi', '-nosound',
                '-nop4', '-NoSplash', '-notraceserver', '-traceautostart=0',
                '-ini:Engine:[SystemSettings]:-t.IdleWhenNotForeground=0',
                '-run=pythonscript', f'-script={script}',
                env={'XOVIUM_SHARED_UE_EXPORT': base, 'XOVIUM_SHARED_UE_SKIP_MODELS': '1'})
    finally:
        # A failed editor import can leave saved packages. Record only files in
        # our reserved export namespaces so a repair can resume, but subsequent
        # local edits are still detected before the editor runs again.
        ctx.state['shared_unreal_pending'] = unreal_files(ctx)
        ctx.save()
    required = [base / 'worlds/baylands.umap', base / 'worlds/M_Terrain.uasset',
                 base / 'worlds/BaylandsAssets/BaylandsCollision.uasset']
    if not all(file.is_file() and file.stat().st_size for file in required):
        raise Error('Unreal shared import did not produce all native packages.')
    result = ctx.state.pop('shared_unreal_pending')
    return result


def unreal_files(ctx):
    base = ctx.managed('simulation/unreal-engine')
    result = {}
    for name in ('worlds/BaylandsAssets',
                 'worlds/baylands.umap', 'worlds/M_Terrain.uasset'):
        path = base / name
        files = path.rglob('*') if path.is_dir() else [path]
        for file in files:
            if file.is_file() and file.suffix in ('.uasset', '.umap'):
                ctx.managed(str(file.relative_to(ctx.prefix)))
                result[str(file.relative_to(ctx.prefix))] = digest(file)
    return result
