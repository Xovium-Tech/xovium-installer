"""Build a Gazebo model wrapper for one launch without editing installed assets."""
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import xml.etree.ElementTree as ET

SIMULATORS = ('gazebo-jetty', 'gazebo-harmonic')
MODEL_KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,127}')
AIRFRAME = re.compile(r'([1-9][0-9]*)_gz_([A-Za-z_][A-Za-z0-9_]*)')
TERRAIN_CONFIG = 'custom::DynamicTerrainConfig'


def _key(value):
    if not isinstance(value, str) or not MODEL_KEY.fullmatch(value):
        raise ValueError('Invalid Gazebo model identifier: ' + str(value))
    return value


def _base(prefix, simulator):
    if simulator not in SIMULATORS: raise ValueError('Unknown Gazebo simulator: ' + str(simulator))
    return Path(prefix).resolve() / 'simulation' / simulator


def _inside(path, root, *, write=False):
    path, root = Path(path).absolute(), Path(root).resolve()
    if '..' in path.parts or not path.is_relative_to(root) or not path.resolve().is_relative_to(root):
        raise ValueError('Gazebo session path escapes its installation: ' + str(path))
    if write:
        current = root
        for part in path.relative_to(root).parts:
            current /= part
            if current.is_symlink(): raise ValueError('Gazebo session paths must not contain symlinks: ' + str(current))
    return path


def _xml(path):
    path = Path(path)
    if not path.is_file(): raise ValueError('Gazebo model/world file is missing: ' + str(path))
    if path.stat().st_size > 16 * 1024**2: raise ValueError('Gazebo XML file is too large: ' + str(path))
    data = path.read_bytes()
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Gazebo model XML must not contain entities or DTDs: ' + str(path))
    try: return ET.fromstring(data)
    except ET.ParseError as error: raise ValueError('Invalid Gazebo XML: ' + str(path) + ': ' + str(error)) from error


def _airframe_directory(prefix):
    return Path(prefix).resolve() / 'px4/source/build/px4_sitl_default/etc/init.d-posix/airframes'


def native_models(prefix, simulator):
    """Offer only models that this PX4 build actually supplies and can start."""
    prefix = Path(prefix).resolve()
    base = _base(prefix, simulator)
    directory = _inside(_airframe_directory(prefix), prefix)
    upstream = prefix / 'px4/source/Tools/simulation/gz/models'
    models = {}
    for file in sorted(directory.glob('*_gz_*')):
        match = AIRFRAME.fullmatch(file.name)
        if not match or not _inside(file, prefix).is_file(): continue
        autostart, name = match.groups()
        if not _inside(upstream / name / 'model.sdf', prefix).is_file(): continue
        if not _inside(base / 'models' / name / 'model.sdf', prefix).is_file(): continue
        if name in models and models[name] != autostart:
            raise ValueError('PX4 has ambiguous Gazebo airframes for ' + name)
        models[name] = autostart
    return models


def _airframe_airspeed(path, directory, visited=None):
    """Read the installed startup defaults without executing PX4 shell scripts."""
    visited = set() if visited is None else visited
    path = _inside(path, directory)
    if path in visited: raise ValueError('Cyclic PX4 airframe include: ' + str(path))
    visited.add(path)
    result = '0'
    for line in path.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        # Native variants inherit their base airframe through ${R}etc/... .
        include = re.fullmatch(r'\.\s+\$\{R\}etc/init\.d-posix/airframes/([A-Za-z0-9_-]+)', line)
        if include:
            result = _airframe_airspeed(directory / include.group(1), directory, visited)
        setting = re.fullmatch(r'param\s+set(?:-default)?\s+SENS_EN_ARSPDSIM\s+([01])', line)
        if setting: result = setting.group(1)
    visited.remove(path)
    return result


def _model_has_airspeed(path, roots, seen=None):
    seen = set() if seen is None else seen
    path = Path(path).resolve()
    if path in seen: return False
    seen.add(path)
    root = _xml(path)
    if any(sensor.get('type') in ('air_speed', 'airspeed') for sensor in root.iter('sensor')):
        return True
    for include in root.iter('include'):
        value = (include.findtext('uri') or '').strip()
        if value.startswith('model://'): value = value[8:]
        if not MODEL_KEY.fullmatch(value): continue
        for folder in roots:
            child = folder / value / 'model.sdf'
            if child.is_file():
                if _model_has_airspeed(child, roots, seen): return True
                break
    return False


def _current_terrain(base, env):
    """Carry the configured terrain provider/cache options, never vehicle plugins."""
    world = str(env.get('PX4_GZ_WORLD', ''))
    dynamic = world in ('xovium_terrain', 'dynamic_terrain', 'dynamic')
    if not world: dynamic = env.get('XOVIUM_WORLD') == 'dynamic'
    if MODEL_KEY.fullmatch(world):
        world_file = Path(env.get('PX4_GZ_WORLDS') or base / 'worlds') / (world + '.sdf')
        if world_file.is_file():
            world_root = _xml(world_file)
            dynamic |= any(plugin.get('name') == 'custom::DynamicTerrainSystem'
                           for plugin in world_root.iter('plugin'))
    if not dynamic: return None
    current = str(env.get('PX4_SIM_MODEL', ''))
    if current.startswith('gz_'): current = current[3:]
    if not MODEL_KEY.fullmatch(current):
        raise ValueError('The dynamic terrain world needs the installed model profile. Refresh launchers before selecting a session model.')
    model_file = Path(env.get('PX4_GZ_MODELS') or base / 'models') / current / 'model.sdf'
    document = _xml(model_file)
    model = document.find('model') if document.tag == 'sdf' else document
    plugins = [] if model is None else [plugin for plugin in model.findall('plugin')
                                        if plugin.get('name') == TERRAIN_CONFIG]
    if len(plugins) != 1:
        raise ValueError('The dynamic terrain model profile has no unique terrain configuration: ' + str(model_file) + '. Refresh launchers before starting this model.')
    return deepcopy(plugins[0])


def prepare(prefix, simulator, key, adapter, env):
    """Return launch overrides after creating a wrapper in this session's log folder.

    ``env`` must contain the original launcher defaults and local overrides. Its
    current model supplies the terrain configuration; the selected model supplies
    all vehicle links, dynamics and sensors. Caller applies the returned mapping.
    """
    prefix = Path(prefix).resolve()
    base, key, adapter = _base(prefix, simulator), _key(key), _key(adapter)
    models = native_models(prefix, simulator)
    if adapter not in models:
        raise ValueError('PX4 has no built Gazebo airframe for ' + adapter + '. Repair PX4 or select a model supported by this PX4 release.')
    source = _inside(base / 'models' / key / 'model.sdf', prefix)
    document = _xml(source)
    if document.tag != 'sdf' or document.find('model') is None:
        raise ValueError('Selected Gazebo asset is not an SDF model: ' + str(source))
    terrain = _current_terrain(base, env)
    log_value = env.get('XOVIUM_SESSION_LOG_DIR')
    if not log_value: raise ValueError('A session log directory is required before preparing a Gazebo model.')
    sessions = prefix / 'runtime/sessions'
    log = _inside(Path(log_value), sessions, write=True)
    if not log.is_dir(): raise ValueError('The session log directory does not exist: ' + str(log))
    model_root = _inside(log / 'models', sessions, write=True)
    wrapper = 'xovium_session_' + key
    target = _inside(model_root / wrapper, sessions, write=True)
    airframe = _airframe_directory(prefix) / (models[adapter] + '_gz_' + adapter)
    airspeed = _airframe_airspeed(airframe, _airframe_directory(prefix))
    resource_roots = [base / 'models', prefix / 'px4/source/Tools/simulation/gz/models']
    if _model_has_airspeed(source, resource_roots): airspeed = '0'
    sdf = ET.Element('sdf', version='1.9')
    model = ET.SubElement(sdf, 'model', name=wrapper)
    include = ET.SubElement(model, 'include', merge='true')
    ET.SubElement(include, 'uri').text = 'model://' + key
    if terrain is not None: model.append(terrain)
    config = ET.Element('model')
    ET.SubElement(config, 'name').text = wrapper
    ET.SubElement(config, 'version').text = '1.0'
    ET.SubElement(config, 'sdf', version='1.9').text = 'model.sdf'
    ET.SubElement(config, 'description').text = 'Xovium session model; installed defaults remain unchanged.'
    target.mkdir(parents=True, exist_ok=True)
    for filename, tree in (('model.sdf', sdf), ('model.config', config)):
        destination = _inside(target / filename, sessions, write=True)
        ET.indent(tree, space='  ')
        with tempfile.NamedTemporaryFile(prefix='.model-', dir=target, delete=False) as temporary:
            temporary = Path(temporary.name)
        try:
            ET.ElementTree(tree).write(temporary, encoding='utf-8', xml_declaration=True)
            temporary.replace(destination)
        finally: temporary.unlink(missing_ok=True)
    resources = [str(model_root), str(base / 'models'), str(base / 'worlds')]
    resources.extend(part for part in str(env.get('GZ_SIM_RESOURCE_PATH', '')).split(':') if part)
    return {'PX4_SIM_MODEL': 'gz_' + wrapper, 'PX4_SYS_AUTOSTART': models[adapter],
            'PX4_PARAM_SENS_EN_ARSPDSIM': airspeed, 'PX4_GZ_MODEL_NAME': '',
            'PX4_GZ_MODELS': str(model_root), 'GZ_SIM_RESOURCE_PATH': ':'.join(dict.fromkeys(resources))}
