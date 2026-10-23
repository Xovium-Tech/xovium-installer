from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET

from xovium.core import Error
from xovium import simulation_profiles as sp
from xovium.gazebo_cameras import configure_systems

MODELS = {'x500': 4001, 'rc_cessna': 4003, 'standard_vtol': 4004, 'r1_rover': 4009}
FG_MODELS = sp.FLIGHTGEAR_MODELS
WORLDS = ('default', 'baylands', 'forest', 'windy', 'generated', 'dynamic')


def merge_selection(previous, request):
    result = dict(request)
    groups = [
        (('jetty', 'harmonic'), ('jetty', 'harmonic', 'backend', 'world', 'seed', 'gazebo_model', 'airspeed', 'lidar', 'camera')),
        (('flightgear',), ('flightgear', 'flightgear_model')),
        (('unreal',), ('unreal', 'unreal_archive')),
    ]
    for selected, settings in groups:
        if not any(request[key] for key in selected):
            for key in settings:
                if key in previous:
                    result[key] = previous[key]
    for key in ('sih', 'isaac_sih', 'isaac_physx', 'license', 'qgc', 'router'):
        result[key] = request[key] or previous.get(key, False)
    for checkbox, key in [('qgc', 'qgc_version'), ('router', 'router_ports')]:
        if not request[checkbox] and key in previous: result[key] = deepcopy(previous[key])
    result['simulations'] = {name: sp.get(request if sp.selected(request, name) else previous, name) for name in sp.SIMULATORS}
    for key in ('jetty', 'harmonic'):
        result[key] = request.get(key, False) or previous.get(key, False)
    if 'shared_selection' in request:
        shared = deepcopy(request['shared_selection'])
        result.update(shared_selection=shared, world=shared['world'],
                      gazebo_model=shared['model_base'], flightgear_model=shared['model_base'])
        result['simulations'] = {name: sp.get(result, name) for name in sp.SIMULATORS}
    return result


def xml_text(root):
    ET.indent(root, space='  ')
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding='unicode') + '\n'


def model_profile(models, name, data):
    sdf = ET.Element('sdf', version='1.9')
    model = ET.SubElement(sdf, 'model', name=name)
    include = ET.SubElement(model, 'include', merge='true')
    ET.SubElement(include, 'uri').text = data.get('model_uri', 'model://' + data['gazebo_model'])
    if data['airspeed']:
        include = ET.SubElement(model, 'include', merge='true')
        ET.SubElement(include, 'uri').text = 'model://airspeed'
        ET.SubElement(include, 'pose', relative_to='base_link').text = '0.2 0 0.08 0 0 0'
        joint = ET.SubElement(model, 'joint', name='xovium_airspeed_joint', type='fixed')
        ET.SubElement(joint, 'parent').text = 'base_link'
        ET.SubElement(joint, 'child').text = 'airspeed_link'
    if data['lidar']:
        upstream = ET.parse(models / 'x500_lidar_down/model.sdf').getroot().find('model')
        for element in upstream:
            if element.tag == 'include' and element.findtext('uri') == 'x500':
                continue
            if element.tag in ('link', 'joint', 'include'):
                copy = deepcopy(element)
                for sensor in copy.iter('sensor'):
                    sensor.text = None
                model.append(copy)
    if data['camera']:
        upstream = ET.parse(models / 'x500_mono_cam/model.sdf').getroot().find('model')
        for element in upstream:
            if element.tag == 'include' and element.findtext('uri') == 'x500':
                continue
            if element.tag in ('include', 'joint'):
                model.append(deepcopy(element))
    if data.get('terrain_snippet'):
        plugin = ET.parse(data['terrain_snippet']).getroot()
        if plugin.tag != 'plugin' or plugin.get('name') != 'custom::DynamicTerrainConfig':
            raise Error('Unsupported dynamic terrain model configuration.')
        cameras = plugin.find('camera_names')
        if cameras is not None:
            plugin.remove(cameras)
        if data.get('terrain_settings'):
            settings = data['terrain_settings']
            radius = (settings['radius'] + .5) * 40075016.686 * math.cos(math.radians(settings['origin']['latitude'])) / (2 ** settings['zoom'])
            values = {'imagery_provider': 'custom', 'imagery_url': settings['imagery_url'],
                      'imagery_extension': settings.get('imagery_extension', 'jpg'), 'elevation_url': settings['elevation_url'],
                      'cache_dir': settings['cache_path'], 'static_zoom': settings['zoom'], 'dynamic_zoom': 'false',
                      'radius_tiles': settings['radius'], 'visual_geometry_zoom': settings['zoom'],
                      'visual_elevation_zoom': min(settings['zoom'], 15), 'visual_detail_zoom': settings['zoom'],
                      'visual_radius_m': radius, 'visual_detail_radius_m': radius, 'visual_detail_mode': 'all',
                      'visual_outer_radius_m': settings.get('visual_outer_radius_m', 2000000.0),
                      'visual_activation_timeout_sec': 60.0}
            for key, value in values.items():
                element = plugin.find(key)
                if element is None: element = ET.SubElement(plugin, key)
                element.text = str(value)
        model.append(plugin)
    return xml_text(sdf)


def write_owned(ctx, relative, text):
    with tempfile.TemporaryDirectory(prefix='xovium-profile-') as folder:
        source = Path(folder) / 'asset'
        source.write_text(text)
        return ctx.copy_tree(source, relative)


def gazebo_settings(ctx, data):
    if not (data['jetty'] or data['harmonic']):
        return {}
    backend = ctx.config['backend'] if ctx else data['backend']
    item = sp.get(data, 'gazebo-' + backend)
    model = item['model']
    airframe = item['model_base'] if model == 'custom' else model
    world = {'airfield': 'dynamic_terrain', 'dynamic': 'dynamic_terrain', 'custom': 'xovium_custom'}.get(item['world'], item['world'])
    if item['dynamic_terrain']:
        world = 'xovium_terrain'
    result = {'PX4_SIM_MODEL': 'gz_xovium_' + model, 'PX4_SYS_AUTOSTART': MODELS[airframe],
              'PX4_GZ_WORLD': world,
              'PX4_PARAM_SENS_EN_ARSPDSIM': 0 if data['airspeed'] or airframe in ('x500', 'r1_rover') else 1}
    if ctx:
        base = ctx.prefix / ('simulation/gazebo-' + backend)
        paths = [str(base / 'models'), str(base / 'worlds')]
        for kind in ('world', 'model'):
            if item[kind] == 'custom' and 'shared_selection' not in data:
                parent = Path(item[kind + '_path']).parent
                paths += [str(parent), str(parent.parent), str(parent / 'models'), str(parent.parent / 'models')]
        result['GZ_SIM_RESOURCE_PATH'] = ':'.join(dict.fromkeys(paths))
        result['PX4_GZ_SERVER_CONFIG'] = base / 'bridge/wizard-server.config'
        result['GZ_SIM_SERVER_CONFIG_PATH'] = result['PX4_GZ_SERVER_CONFIG']
    return result


def custom_world(path):
    root = sp.sdf(path, 'world')
    root.find('world').set('name', 'xovium_custom')
    if root.find('world').findall('plugin'):
        configure_systems(root.find('world'))
    for uri in root.iter():
        if uri.tag not in ('uri', 'albedo_map', 'normal_map', 'roughness_map', 'metalness_map', 'emissive_map', 'light_map', 'diffuse', 'normal'):
            continue
        text = (uri.text or '').strip().removeprefix('file://')
        if text and '://' not in text and not Path(text).is_absolute():
            candidate = path.parent / text
            if candidate.exists():
                uri.text = str(candidate.resolve())
    return xml_text(root)


def gazebo_profile(ctx, data, distro):
    relative = 'simulation/gazebo-' + distro
    base = ctx.prefix / relative
    item = sp.get(data, 'gazebo-' + distro)
    if not (base / 'models/x500/model.sdf').is_file():
        return
    profile = dict(data, gazebo_model=item['model'])
    if item['model'] == 'custom':
        profile['model_uri'] = 'model://custom' if 'shared_selection' in data else item['model_path']
    if item['dynamic_terrain']:
        profile['terrain_settings'] = sp.shared_terrain(ctx)
        profile['terrain_snippet'] = ctx.assets / 'gazebo/terrain-plugin.sdf'
    name = 'xovium_' + item['model']
    write_owned(ctx, f'{relative}/models/{name}/model.sdf', model_profile(base / 'models', name, profile))
    write_owned(ctx, f'{relative}/models/{name}/model.config',
                f'<?xml version="1.0"?><model><name>{name}</name><version>1.0</version><sdf version="1.9">model.sdf</sdf><description>Xovium wizard profile</description></model>\n')
    if item['world'] == 'custom':
        write_owned(ctx, f'{relative}/worlds/xovium_custom.sdf', custom_world(Path(item['world_path'])))
    root = ET.parse(base / 'bridge/server.config').getroot()
    plugins = root.find('plugins')
    if plugins is None:
        raise Error('Gazebo server configuration has no plugins section.')
    configure_systems(plugins, server_config=True)
    for plugin in list(plugins):
        if 'dynamic-terrain' in plugin.get('filename', ''):
            plugins.remove(plugin)
    if item['dynamic_terrain']:
        ET.SubElement(plugins, 'plugin', entity_name='*', entity_type='world',
                      filename='libgz-dynamic-terrain-system.so', name='custom::DynamicTerrainSystem')
    write_owned(ctx, relative + '/bridge/wizard-server.config', xml_text(root))
    if item['dynamic_terrain']:
        world_name = {'airfield': 'dynamic_terrain', 'dynamic': 'dynamic_terrain', 'custom': 'xovium_custom'}.get(item['world'], item['world'])
        world_root = ET.parse(base / ('worlds/' + world_name + '.sdf')).getroot()
        world = world_root.find('world')
        world.set('name', 'xovium_terrain')
        coordinates = world.find('spherical_coordinates')
        if coordinates is None: coordinates = ET.SubElement(world, 'spherical_coordinates')
        origin = profile['terrain_settings']['origin']
        for key, value in {'surface_model': 'EARTH_WGS84', 'latitude_deg': origin['latitude'], 'longitude_deg': origin['longitude'], 'elevation': origin['altitude'], 'heading_deg': 0}.items():
            element = coordinates.find(key)
            if element is None: element = ET.SubElement(coordinates, key)
            element.text = str(value)
        names = {p.get('name') for p in world.findall('plugin')}
        for plugin in plugins:
            if plugin.get('entity_type') == 'world' and plugin.get('name') not in names:
                copy = deepcopy(plugin)
                copy.attrib.pop('entity_name', None)
                copy.attrib.pop('entity_type', None)
                world.append(copy)
        configure_systems(world)
        write_owned(ctx, relative + '/worlds/xovium_terrain.sdf', xml_text(world_root))



def apply(ctx, data):
    for distro in ('jetty', 'harmonic'):
        if data[distro]:
            gazebo_profile(ctx, data, distro)
    if data['jetty'] or data['harmonic']:
        from installers.gazebo import px4_launcher
        px4_launcher(ctx)
    if data['flightgear']:
        from installers.px4 import flightgear_launcher
        flightgear_launcher(ctx)
    from installers import apps
    if data['isaac_physx'] and (ctx.prefix / 'simulation/isaac/bridge/run_isaac.sh').is_file():
        apps.isaac_launcher(ctx)
        _, extra = sp.launch_options(ctx, 'isaac')
        ctx.launcher('isaac-px4', ['bash', ctx.prefix / 'simulation/isaac/bridge/run_px4.sh'],
                     {**apps.isaac_environment(ctx), **extra})
    if (data['sih'] or data['isaac_sih']) and (ctx.prefix / 'simulation/sih/bridge/run_px4.sh').is_file():
        from installers.px4 import environment
        env = environment(ctx)
        env.pop('DONT_RUN', None)
        _, extra = sp.launch_options(ctx, 'sih')
        ctx.launcher('sih-px4', ['bash', ctx.prefix / 'simulation/sih/bridge/run_px4.sh'], {**env, **extra})
    if data['isaac_sih'] and (ctx.prefix / 'simulation/sih/bridge/run_view.sh').is_file():
        apps.sih_view_launcher(ctx)
    if data['unreal']:
        if 'shared_selection' not in data:
            sp.import_unreal(ctx, sp.get(data, 'unreal-engine'))
        apps.unreal_launchers(ctx)
    write_owned(ctx, 'config/installer-profile.json', json.dumps(data, indent=2, sort_keys=True) + '\n')
    defaults = '# Generated by the installation wizard; override in scripts/local.env.\n'
    if data['license']:
        defaults += 'export OMNI_KIT_ACCEPT_EULA=YES\n'
    write_owned(ctx, 'scripts/wizard.env', defaults)
