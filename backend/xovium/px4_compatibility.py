import json
from pathlib import Path
from xovium.paths import Error

CATALOG = json.loads(Path(__file__).with_name('px4-releases.json').read_text())
RELEASES = {item['version']: item for item in CATALOG['releases']}
DEFAULT = CATALOG['default']


def release(version):
    if not isinstance(version, str) or version not in RELEASES:
        raise Error('Unsupported PX4 version. Select one of: ' + ', '.join(RELEASES))
    return RELEASES[version]


def validate(version, components):
    info = release(version)
    for name in components:
        if name.startswith('gazebo-') and name != 'gazebo-plugins':
            backend = name.removeprefix('gazebo-')
            if info.get(backend, 'unsupported') == 'unsupported':
                raise Error(f'Gazebo {backend.title()} is not supported with PX4 {version}. Harmonic supports 1.15.x, 1.16.x and 1.17.0; Jetty needs 1.16.x with the kit adapter or 1.17.0 natively.')
        if name in ('flightgear', 'isaac', 'sih', 'unreal-engine') and not info['native_bridges']:
            raise Error(f'This kit does not support the {name} bridge with PX4 {version}.')
    return info


def notice(version, backend):
    info = release(version)
    if info.get(backend) == 'adapter':
        return f'Gazebo {backend.title()} + PX4 {version}: PX4 source will be modified to support this simulator (kit compatibility adapter).'
    return f'Gazebo {backend.title()} + PX4 {version}: native upstream Gazebo support.'


def validate_profile(data):
    version = data.get('px4_version', DEFAULT)
    components = [name for key, name in [('jetty','gazebo-jetty'),('harmonic','gazebo-harmonic'),
                  ('flightgear','flightgear'),('unreal','unreal-engine'),('isaac_physx','isaac'),('isaac_sih','sih')] if data.get(key)]
    info = validate(version, components)
    if info['legacy_models'] and (data.get('jetty') or data.get('harmonic')):
        if data.get('airspeed') or data.get('lidar'):
            raise Error('The supplied extra airspeed/lidar payload models require PX4 1.16.x or 1.17.0; the 1.15.x model set does not contain them.')
        from xovium.simulation_profiles import get, selected
        for name in ('gazebo-jetty','gazebo-harmonic'):
            if selected(data,name) and get(data,name)['world'] == 'forest':
                raise Error('The Forest world is not supplied by PX4 1.15.x. Choose another world or a custom SDF.')
