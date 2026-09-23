from copy import deepcopy
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xovium.core import Error

SIMULATORS = ('gazebo-jetty', 'gazebo-harmonic', 'flightgear', 'isaac', 'sih', 'unreal-engine')
GAZEBO_MODELS = ('x500', 'rc_cessna', 'standard_vtol', 'r1_rover')
FLIGHTGEAR_MODELS = ('rascal-electric', 'rascal', 'tf-g1', 'tf-g2', 'tf-r1')
GAZEBO_WORLDS = ('default', 'baylands', 'forest', 'windy', 'generated', 'airfield', 'dynamic')
FIELDS = {'world', 'model', 'world_path', 'model_path', 'model_base', 'seed', 'dynamic_terrain'}


def selected(data, name):
    keys = {'gazebo-jetty': ('jetty',), 'gazebo-harmonic': ('harmonic',),
            'flightgear': ('flightgear',), 'isaac': ('isaac_physx',),
            'sih': ('sih', 'isaac_sih'), 'unreal-engine': ('unreal',)}
    return any(data.get(key, False) for key in keys[name])


def get(data, name):
    if name in data.get('simulations', {}):
        item = deepcopy(data['simulations'][name])
        if isinstance(item, dict) and item.get('world') == 'dynamic' and item.get('dynamic_terrain') is False:
            item['dynamic_terrain'] = True
        return item
    gazebo = name.startswith('gazebo-')
    world = data.get('world', 'default') if gazebo else 'default'
    model = data.get('gazebo_model', 'rc_cessna') if gazebo else data.get('flightgear_model', 'rascal-electric') if name == 'flightgear' else 'default'
    return dict(world='airfield' if world == 'dynamic' else world, model=model,
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
        models = GAZEBO_MODELS if gazebo else FLIGHTGEAR_MODELS if name == 'flightgear' else ('default',)
        worlds = GAZEBO_WORLDS if gazebo else ('default',)
        if item['world'] not in (*worlds, 'custom') or item['model'] not in (*models, 'custom') or item['model_base'] not in models:
            raise Error(f'Unsupported world/model selection for {name}.')
        if type(item['dynamic_terrain']) is not bool or (item['dynamic_terrain'] and not gazebo):
            raise Error('The Gazebo terrain plugin is available for Gazebo profiles only.')
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


def launch_options(ctx, name):
    item = get(ctx.config.get('wizard', {}), name)
    base = ctx.prefix / 'simulation' / name
    args, env = [], {}
    if name in ('isaac', 'sih'):
        if item['world'] == 'custom':
            args += ['--world' if name == 'isaac' else '--world-usd', item['world_path']]
        if item['model'] == 'custom':
            args += ['--model' if name == 'isaac' else '--aircraft-usd', item['model_path']]
        elif name == 'isaac':
            args += ['--model', str(base / 'models/airplane.usda')]
    elif name == 'unreal-engine':
        for kind in ('world', 'model'):
            if item[kind] == 'custom':
                _, package = unreal_content(Path(item[kind + '_path']))
                env['XOVIUM_UE_' + kind.upper()] = package + ('.' + package.rsplit('/', 1)[-1] if kind == 'model' else '')
    elif name == 'flightgear':
        if item['world'] == 'custom':
            env['FG_SCENERY'] = f"{item['world_path']}:{base}/worlds:/usr/share/games/flightgear/Scenery"
        if item['model'] == 'custom':
            env['XOVIUM_FG_AIRCRAFT'] = item['model_path']
    return args, env
