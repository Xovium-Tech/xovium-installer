from copy import deepcopy
from pathlib import Path
import json
import zipfile
import math
import re
import xml.etree.ElementTree as ET
from xovium.core import Error

SIMULATORS = ('gazebo-jetty', 'gazebo-harmonic', 'flightgear', 'isaac', 'sih', 'unreal-engine')
GAZEBO_MODELS = ('x500', 'rc_cessna', 'standard_vtol', 'r1_rover')
SHARED_MODELS = ('rc_cessna', 'r1_rover')
FLIGHTGEAR_MODELS = ('rascal-electric', 'rascal', 'tf-g1', 'tf-g2', 'tf-r1', *SHARED_MODELS)
SHARED_WORLDS = ('default', 'baylands', 'dynamic')
GAZEBO_WORLDS = ('default', 'baylands', 'forest', 'windy', 'generated', 'airfield', 'dynamic')
FIELDS = {'world', 'model', 'world_path', 'model_path', 'model_base', 'seed', 'dynamic_terrain'}
SHARED_FIELDS = {'world', 'model', 'model_path', 'model_base'}


def defaults(config):
    """Command-line installations use the same shared presets as the wizard."""
    requested = config.get('requested', [])
    data = {key: component in requested for key, component in (
        ('jetty', 'gazebo-jetty'), ('harmonic', 'gazebo-harmonic'), ('flightgear', 'flightgear'),
        ('sih', 'sih-core'), ('isaac_sih', 'sih'), ('isaac_physx', 'isaac'), ('unreal', 'unreal-engine'),
        ('qgc', 'qgc'), ('router', 'mavlink-router'))}
    world = 'dynamic' if config.get('plugins') else 'baylands'
    data.update(backend=config.get('backend', 'harmonic'), px4_version=config.get('px4_version', '1.16.0'),
                airspeed=False, lidar=False, camera=False, license=False, seed=42,
                world=world, gazebo_model='rc_cessna', flightgear_model='rc_cessna',
                shared_selection=dict(world=world, model='rc_cessna', model_path='', model_base='rc_cessna'))
    return data


def configure_shared(config):
    from xovium.catalog import resolve
    plan = resolve(config['requested'], config['backend'])
    data = config.get('wizard', {})
    if 'shared_selection' not in data:
        return plan
    for name, key in [('gazebo-jetty', 'jetty'), ('gazebo-harmonic', 'harmonic'),
                      ('flightgear', 'flightgear'), ('isaac', 'isaac_physx'),
                      ('sih-core', 'sih'), ('sih', 'isaac_sih'), ('unreal-engine', 'unreal')]:
        if name in plan: data[key] = True
    data['backend'] = config['backend']
    if data['shared_selection']['world'] == 'dynamic' and (data['jetty'] or data['harmonic']):
        if 'gazebo-plugins' not in config['requested']: config['requested'].append('gazebo-plugins')
        config['plugins'] = True
        plan = resolve(config['requested'], config['backend'])
    return plan


def migrate_shared(ctx):
    """Move saved per-simulator presets to one common selection before refresh."""
    config = ctx.config
    previous = config.get('wizard', {})
    if 'shared_selection' in previous:
        return False
    baseline = defaults(config)
    data = deepcopy(previous)
    names = [name for name in SIMULATORS if selected(data, name) or selected(baseline, name)]
    for name in names:
        item = get(data, name)
        if item['world'] == 'custom' or item['model'] == 'custom':
            raise Error(f'{name} has a saved simulator-specific custom asset. Reopen the installer and '
                        'choose Baylands or dynamic terrain and one shared .xovmodel or model.sdf before refreshing; '
                        'the old custom selection has not been replaced.')
    active = 'gazebo-' + config.get('backend', 'harmonic')
    primary = active if active in names else names[0] if names else None
    item = get(data, primary) if primary and previous else get(baseline, primary)
    world = 'dynamic' if item['dynamic_terrain'] else item['world']
    if world not in ('baylands', 'dynamic'):
        world = 'baylands'
    model = item['model'] if item['model'] in SHARED_MODELS else item['model_base']
    if model not in SHARED_MODELS:
        model = 'rc_cessna'
    for key, value in baseline.items():
        data.setdefault(key, value)
    data.update(world=world, gazebo_model=model, flightgear_model=model,
                shared_selection=dict(world=world, model=model, model_path='', model_base=model))
    data['simulations'] = {name: get(data, name) for name in SIMULATORS}
    config['wizard'] = data
    configure_shared(config)
    ctx.say(f'[shared] Migrate saved presets to {model} in {world} for all installed simulators.')
    return True


def selected(data, name):
    keys = {'gazebo-jetty': ('jetty',), 'gazebo-harmonic': ('harmonic',),
            'flightgear': ('flightgear',), 'isaac': ('isaac_physx',),
            'sih': ('sih', 'isaac_sih'), 'unreal-engine': ('unreal',)}
    return any(data.get(key, False) for key in keys[name])


def get(data, name):
    if not data:
        data = defaults({})
    if 'shared_selection' in data:
        shared = data['shared_selection']
        return dict(world=shared['world'], model=shared['model'], world_path='',
                    model_path=shared['model_path'], model_base=shared['model_base'],
                    seed=42, dynamic_terrain=shared['world'] == 'dynamic')
    if name in data.get('simulations', {}):
        item = deepcopy(data['simulations'][name])
        if isinstance(item, dict) and item.get('world') in ('generated', 'airfield'):
            item['world'] = 'dynamic' if item.get('dynamic_terrain') else 'baylands'
        if isinstance(item, dict) and item.get('world') == 'dynamic' and item.get('dynamic_terrain') is False:
            item['dynamic_terrain'] = True
        return item
    gazebo = name.startswith('gazebo-')
    world = data.get('world', 'default') if gazebo else 'default'
    model = data.get('gazebo_model', 'rc_cessna') if gazebo else data.get('flightgear_model', 'rascal-electric') if name == 'flightgear' else 'default'
    return dict(world='baylands' if world == 'generated' else world, model=model,
                world_path='', model_path='', model_base=model,
                seed=data.get('seed', 42), dynamic_terrain=gazebo and world == 'dynamic')


def sdf(path, kind):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise Error(f'Cannot read {kind} SDF {path}: {exc}') from exc
    nodes = root.findall(kind)
    if root.tag != 'sdf' or len(nodes) != 1:
        raise Error(f'Select an SDF containing exactly one top-level {kind}: {path}')
    return root


def custom_source(path):
    """Validate a portable model folder and make its SDF resources relative."""
    path = Path(path).resolve()
    if path.name != 'model.sdf' or not path.is_file():
        raise Error('Choose model.sdf inside a self-contained custom model folder.')
    root = sdf(path, 'model')
    model = root.find('model')
    if model.findall('include') or model.findall('model'):
        raise Error('The shared custom model must contain its links directly; expand nested models and includes first.')
    if not model.findall('link/visual/geometry'):
        raise Error('The custom model needs visible geometry to export to every simulator.')
    aliases = {path.parent.name, model.get('name', ''), 'custom'}
    resource_tags = {'uri', 'albedo_map', 'normal_map', 'roughness_map', 'metalness_map', 'emissive_map', 'light_map'}
    for element in root.iter():
        if element.tag not in resource_tags:
            continue
        value = (element.text or '').strip()
        if not value:
            continue
        if value.startswith('model://'):
            name, _, relative = value[8:].partition('/')
            if name not in aliases:
                raise Error(f'Custom resources must be inside the selected model folder: {value}')
            resource = path.parent / relative
        elif '://' in value and not value.startswith('file://'):
            raise Error(f'Custom resources must be local files inside the model folder: {value}')
        else:
            resource = path.parent / value.removeprefix('file://')
        resource = resource.resolve()
        if not resource.is_relative_to(path.parent) or not resource.exists():
            raise Error(f'Missing or external custom model resource: {value}')
        element.text = resource.relative_to(path.parent).as_posix()
    return root


def portable_source(path):
    """Read bundle metadata for the wizard; native import validates every asset."""
    if not path.is_file() or path.is_symlink(): raise Error('Choose an existing .xovmodel file.')
    try:
        with zipfile.ZipFile(path) as archive:
            names = set()
            total = 0
            for entry in archive.infolist():
                relative = Path(entry.filename)
                total += entry.file_size
                if (relative.is_absolute() or '..' in relative.parts or '\\' in entry.filename or
                        entry.filename in names or (entry.external_attr >> 16) & 0o170000 == 0o120000 or
                        entry.file_size > 512 * 1024**2 or total > 2 * 1024**3 or len(names) >= 10000):
                    raise Error('Unsafe or oversized model bundle: ' + str(path))
                names.add(entry.filename)
            info = archive.getinfo('project.json')
            if info.file_size > 16 * 1024**2: raise Error('Model document is too large.')
            document = json.loads(archive.read(info))
            if (not isinstance(document, dict) or document.get('format') != 'xovium-model' or
                    type(document.get('version')) is not int or document['version'] != 1):
                raise Error('Unsupported .xovmodel format or version.')
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        raise Error('Cannot read portable model: ' + str(error)) from error
    return document


def validate_shared(data):
    result = deepcopy(data)
    shared = result['shared_selection']
    if not isinstance(shared, dict) or set(shared) != SHARED_FIELDS:
        raise Error('Choose one shared world and model for all simulators.')
    if shared['world'] not in ('baylands', 'dynamic'):
        raise Error('The shared world must be Baylands or dynamic_terrain.')
    if shared['model'] not in (*SHARED_MODELS, 'custom') or shared['model_base'] not in SHARED_MODELS:
        raise Error('Choose Cessna, rover, or a custom model with a Cessna/rover physics adapter.')
    value = shared['model_path']
    if not isinstance(value, str) or len(value) > 4095 or any(c in value for c in '\r\n\0'):
        raise Error('Invalid shared model path.')
    if shared['model'] == 'custom':
        path = Path(value).expanduser().resolve()
        if path.suffix.lower() == '.xovmodel': portable_source(path)
        else: custom_source(path)
        shared['model_path'] = str(path)
    else:
        shared['model_base'] = shared['model']
        shared['model_path'] = ''
    result.update(world=shared['world'], gazebo_model=shared['model_base'], flightgear_model=shared['model_base'])
    result['simulations'] = {name: get(result, name) for name in SIMULATORS}
    return result


def unreal_content(path):
    for parent in path.parents:
        if parent.name == 'Content':
            relative = path.relative_to(parent).with_suffix('')
            if not re.fullmatch(r'[A-Za-z0-9_/]+', relative.as_posix()):
                raise Error('Unreal package names must contain letters, numbers, underscores and folders only.')
            return parent, '/Game/' + relative.as_posix()
    for parent in path.parents:
        if parent.name in ('models', 'worlds') and parent.parent.name == 'unreal-engine':
            relative = path.relative_to(parent).with_suffix('')
            if not re.fullmatch(r'[A-Za-z0-9_/]+', relative.as_posix()):
                raise Error('Invalid Unreal asset package name.')
            content = parent.parent / 'bridge/Unreal/Content'
            folder = 'Models' if parent.name == 'models' else 'Worlds'
            if (content / folder).resolve() == parent.resolve():
                return content, '/Game/' + folder + '/' + relative.as_posix()
    raise Error('Select an Unreal asset inside its project Content folder (or a migrated Content folder).')


def validate(data):
    if 'shared_selection' in data:
        return validate_shared(data)
    result = deepcopy(data)
    entries = data.get('simulations')
    if entries is not None and (not isinstance(entries, dict) or set(entries) != set(SIMULATORS)):
        raise Error('A world/model profile is required for every simulator.')
    result['simulations'] = {}
    for name in SIMULATORS:
        item = get(data, name)
        if not isinstance(item, dict) or set(item) != FIELDS:
            raise Error(f'Invalid world/model fields for {name}.')
        gazebo = name.startswith('gazebo-')
        models = GAZEBO_MODELS if gazebo else FLIGHTGEAR_MODELS if name == 'flightgear' else ('default', *SHARED_MODELS)
        worlds = GAZEBO_WORLDS if gazebo else SHARED_WORLDS
        if item['world'] not in (*worlds, 'custom') or item['model'] not in (*models, 'custom') or item['model_base'] not in models:
            raise Error(f'Unsupported world/model selection for {name}.')
        if type(item['dynamic_terrain']) is not bool or (not gazebo and item['dynamic_terrain'] != (item['world'] == 'dynamic')):
            raise Error('Dynamic terrain must be selected as the world for this simulator.')
        if type(item['seed']) is not int or not 0 <= item['seed'] <= 1000000:
            raise Error(f'{name}: world seed must be 0..1000000.')
        for kind in ('world', 'model'):
            key = kind + '_path'
            value = item[key]
            if not isinstance(value, str) or len(value) > 4095 or any(c in value for c in '\r\n\0'):
                raise Error(f'Invalid {name} {kind} path.')
            if not selected(data, name) or item[kind] != 'custom':
                continue
            if not value:
                raise Error(f'Choose a custom {kind} path for {name}.')
            path = Path(value).expanduser().resolve()
            if name == 'flightgear' and kind == 'world':
                if not path.is_dir() or not any((path / p).is_dir() for p in ('Terrain', 'Objects')):
                    raise Error('FlightGear scenery must be a folder with Terrain/ or Objects/.')
            else:
                extensions = ('.sdf',) if gazebo else ('.xml',) if name == 'flightgear' else ('.umap',) if name == 'unreal-engine' and kind == 'world' else ('.uasset',) if name == 'unreal-engine' else ('.usd', '.usda', '.usdc')
                if not path.is_file() or path.suffix.lower() not in extensions:
                    raise Error(f'{name}: choose an existing {kind} ({", ".join(extensions)}).')
                if gazebo:
                    sdf(path, kind)
                elif name == 'flightgear':
                    try:
                        if not path.name.endswith('-set.xml') or ET.parse(path).getroot().tag != 'PropertyList':
                            raise Error('Select a FlightGear aircraft -set.xml file.')
                    except ET.ParseError as exc:
                        raise Error(f'Invalid FlightGear aircraft XML: {path}') from exc
                elif name == 'unreal-engine':
                    unreal_content(path)
            if name == 'sih' and not data.get('isaac_sih'):
                raise Error('Custom SIH worlds/models need the Isaac / SIH viewer; enable it on the Simulators page.')
            item[key] = str(path)
        result['simulations'][name] = item
    return result


def import_unreal(ctx, item):
    roots = set()
    for kind in ('world', 'model'):
        if item[kind] == 'custom':
            root, _ = unreal_content(Path(item[kind + '_path']))
            roots.add(root)
    content = ctx.prefix / 'simulation/unreal-engine/bridge/Unreal/Content'
    copies = []
    for root in sorted(roots):
        if root.resolve() == content.resolve():
            continue
        files = sorted(p for p in root.rglob('*') if p.is_file())
        for file in files:
            relative = file.relative_to(root)
            for other in roots - {root}:
                candidate = other / relative
                if candidate.is_file() and candidate.read_bytes() != file.read_bytes():
                    raise Error(f'Custom Unreal Content trees contain conflicting package: {relative}')
            destination = content / relative
            if destination.resolve() == file.resolve():
                continue
            copies.append((file, str(destination.relative_to(ctx.prefix))))
    for file, destination in copies:
        ctx.copy_tree(file, destination)


def shared_terrain(ctx):
    path = ctx.prefix / 'shared/worlds/dynamic_terrain/terrain.json'
    source = path if path.is_file() else Path(__file__).resolve().parents[1] / 'assets/shared/worlds/dynamic_terrain/terrain.json'
    data = json.loads(source.read_text())
    if data.get('schema') != 1:
        raise Error('Unsupported shared terrain configuration schema.')
    origin = data.get('origin', {})
    if not all(type(origin.get(k)) in (int, float) and math.isfinite(origin[k]) for k in ('latitude', 'longitude', 'altitude')):
        raise Error('Shared terrain origin must contain finite latitude, longitude and altitude.')
    if not -85 <= origin['latitude'] <= 85 or not -180 <= origin['longitude'] <= 180:
        raise Error('Shared terrain origin is outside supported geographic bounds.')
    if type(data.get('zoom')) is not int or not 10 <= data['zoom'] <= 17 or type(data.get('radius')) is not int or not 1 <= data['radius'] <= 3:
        raise Error('Shared terrain zoom must be 10..17 and radius must be 1..3.')
    outer_radius = data.get('visual_outer_radius_m', 2000000.0)
    if type(outer_radius) not in (int, float) or not math.isfinite(outer_radius) or not 0 <= outer_radius <= 2000000:
        raise Error('Shared visual outer terrain radius must be finite and between 0 and 2000000 metres.')
    # Resolve the optional default in memory; preserve the user's manifest.
    data['visual_outer_radius_m'] = outer_radius
    for key in ('imagery_url', 'elevation_url'):
        value = data.get(key)
        if not isinstance(value, str) or not value.startswith('https://') or not all('{' + axis + '}' in value for axis in ('x', 'y', 'z')) or any(c in value for c in '\r\n\0'):
            raise Error('Shared terrain URLs need HTTPS and {x}/{y}/{z} tile placeholders.')
    if data['elevation_url'].startswith('https://s3.amazonaws.com/elevation-tiles-prod/terrarium/') and data['zoom'] > 15:
        raise Error('The default AWS Terrarium provider supports shared terrain zoom up to 15.')
    cache = data.get('cache')
    if not isinstance(cache, str) or not cache or any(c in cache for c in '\r\n\0'):
        raise Error('Shared terrain cache must be a valid path.')
    data['cache_path'] = str(path.parent / cache)
    return data


def world_origin(ctx, world):
    if world == 'dynamic':
        return shared_terrain(ctx)['origin']
    if world == 'baylands':
        path = ctx.prefix / 'shared/catalog.json'
        if not path.is_file(): path = Path(__file__).resolve().parents[1] / 'assets/shared/catalog.json'
        return json.loads(path.read_text())['worlds']['baylands']['origin']
    return None


def launch_options(ctx, name):
    data = ctx.config.get('wizard', {})
    item = get(data, name)
    shared_custom = 'shared_selection' in data and item['model'] == 'custom'
    base = ctx.prefix / 'simulation' / name
    args, env = [], {}
    world = 'baylands' if item['world'] == 'default' else item['world']
    vehicle = item['model_base'] if item['model'] == 'custom' else item['model']
    vehicle = vehicle if vehicle in SHARED_MODELS else 'rc_cessna'
    env.update(XOVIUM_VEHICLE=vehicle, XOVIUM_WORLD=world if world in SHARED_WORLDS else 'baylands')
    if vehicle == 'r1_rover' and name in ('isaac', 'sih', 'flightgear'):
        env['XOVIUM_ROVER_CONFIG'] = str(ctx.prefix / 'shared/models/rover/physics.json')
        if name == 'isaac': args += ['--config', env['XOVIUM_ROVER_CONFIG']]
    if world == 'dynamic':
        env['XOVIUM_TERRAIN_CONFIG'] = str(ctx.prefix / 'shared/worlds/dynamic_terrain/terrain.json')
    if world == 'baylands' and name in ('sih', 'flightgear'):
        env['XOVIUM_HEIGHTFIELD'] = str(base / 'worlds/baylands-heightfield.json')
    origin = world_origin(ctx, world)
    if origin:
        env.update({key: str(origin[field]) for key, field in (('PX4_HOME_LAT', 'latitude'), ('PX4_HOME_LON', 'longitude'), ('PX4_HOME_ALT', 'altitude'), ('SIM_LAT', 'latitude'), ('SIM_LON', 'longitude'), ('SIM_ALT', 'altitude'))})
    if name in ('isaac', 'sih'):
        world_flag = '--world' if name == 'isaac' else '--world-usd'
        model_flag = '--model' if name == 'isaac' else '--aircraft-usd'
        if world == 'custom':
            args += [world_flag, item['world_path']]
        elif world in ('baylands', 'dynamic'):
            args += [world_flag, str(base / 'worlds' / (world + '.usda'))]
        if item['model'] == 'custom':
            args += [model_flag, str(base / 'models/custom.usda') if shared_custom else item['model_path']]
        elif item['model'] in SHARED_MODELS:
            args += [model_flag, str(base / 'models' / (vehicle + '.usda'))]
        elif name == 'isaac':
            args += ['--model', str(base / 'models/rc_cessna.usda')]
    elif name == 'unreal-engine':
        if world == 'baylands': env['XOVIUM_UE_WORLD'] = '/Game/Worlds/baylands'
        if item['model'] in SHARED_MODELS or shared_custom:
            model = 'custom' if shared_custom else vehicle
            env['XOVIUM_UE_MODEL'] = f'/Game/Models/{model}/{model}.{model}'
            env['XOVIUM_UE_CAMERA_SCENE'] = str(base / 'models' / model / 'scene.json')
        for kind in ('world', 'model'):
            if item[kind] == 'custom' and not (kind == 'model' and shared_custom):
                _, package = unreal_content(Path(item[kind + '_path']))
                env['XOVIUM_UE_' + kind.upper()] = package + ('.' + package.rsplit('/', 1)[-1] if kind == 'model' else '')
    elif name == 'flightgear':
        if world == 'custom':
            env['FG_SCENERY'] = f"{item['world_path']}:{base}/worlds:/usr/share/games/flightgear/Scenery"
        if item['model'] == 'custom':
            env['XOVIUM_FG_AIRCRAFT'] = str(base / 'models/custom' / ('R1-set.xml' if vehicle == 'r1_rover' else 'Cessna-set.xml')) if shared_custom else item['model_path']
        elif item['model'] in SHARED_MODELS:
            env['XOVIUM_FG_AIRCRAFT'] = str(base / 'models' / vehicle / ('R1-set.xml' if vehicle == 'r1_rover' else 'Cessna-set.xml'))
    return args, env
