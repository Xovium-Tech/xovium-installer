from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import xml.etree.ElementTree as ET

from xovium.core import Error
from xovium import simulation_profiles as sp

MODELS = {'x500': 4001, 'rc_cessna': 4003, 'standard_vtol': 4004, 'r1_rover': 4009}
FG_MODELS = ('rascal-electric', 'rascal', 'tf-g1', 'tf-g2', 'tf-r1')
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
        model.append(plugin)
    return xml_text(sdf)


def generated_world(default_world, seed):
    sdf = ET.parse(default_world).getroot()
    world = sdf.find('world')
    world.set('name', 'xovium_training')
    rng = random.Random(seed)
    for i in range(36):
        x = rng.uniform(-180, 180)
        y = rng.choice([-1, 1]) * rng.uniform(28, 180)
        height = rng.uniform(2, 18)
        model = ET.SubElement(world, 'model', name=f'xovium_obstacle_{i:02d}')
        ET.SubElement(model, 'static').text = 'true'
        ET.SubElement(model, 'pose').text = f'{x:.3f} {y:.3f} {height / 2:.3f} 0 0 0'
        link = ET.SubElement(model, 'link', name='body')
        for kind in ('collision', 'visual'):
            child = ET.SubElement(link, kind, name=kind)
            geometry = ET.SubElement(child, 'geometry')
            ET.SubElement(ET.SubElement(geometry, 'box'), 'size').text = f'6 6 {height:.3f}'
            if kind == 'visual':
                material = ET.SubElement(child, 'material')
                ET.SubElement(material, 'ambient').text = '0.23 0.38 0.43 1'
                ET.SubElement(material, 'diffuse').text = '0.23 0.38 0.43 1'
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
    world = {'airfield': 'mcmillan_airfield_gz', 'dynamic': 'mcmillan_airfield_gz', 'generated': 'xovium_training', 'custom': 'xovium_custom'}.get(item['world'], item['world'])
    if item['dynamic_terrain']:
        world = 'xovium_terrain'
    result = {'PX4_SIM_MODEL': 'gz_xovium_' + model, 'PX4_SYS_AUTOSTART': MODELS[airframe],
              'PX4_GZ_WORLD': world,
              'PX4_PARAM_SENS_EN_ARSPDSIM': 0 if data['airspeed'] or airframe in ('x500', 'r1_rover') else 1}
    if ctx:
        base = ctx.prefix / ('simulation/gazebo-' + backend)
        paths = [str(base / 'models'), str(base / 'worlds')]
        for kind in ('world', 'model'):
            if item[kind] == 'custom':
                parent = Path(item[kind + '_path']).parent
                paths += [str(parent), str(parent.parent), str(parent / 'models'), str(parent.parent / 'models')]
        result['GZ_SIM_RESOURCE_PATH'] = ':'.join(dict.fromkeys(paths))
        result['PX4_GZ_SERVER_CONFIG'] = base / 'bridge/wizard-server.config'
        result['GZ_SIM_SERVER_CONFIG_PATH'] = result['PX4_GZ_SERVER_CONFIG']
    return result


def custom_world(path):
    root = sp.sdf(path, 'world')
    root.find('world').set('name', 'xovium_custom')
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
        profile['model_uri'] = item['model_path']
    if item['dynamic_terrain']:
        profile['terrain_snippet'] = base / f'plugins/terrain/source/examples/{distro}/plugin_snippet.sdf'
        if not profile['terrain_snippet'].is_file():
            raise Error(f'Dynamic terrain was selected for {distro}, but its plugin was not installed.')
    name = 'xovium_' + item['model']
    write_owned(ctx, f'{relative}/models/{name}/model.sdf', model_profile(base / 'models', name, profile))
    write_owned(ctx, f'{relative}/models/{name}/model.config',
                f'<?xml version="1.0"?><model><name>{name}</name><version>1.0</version><sdf version="1.9">model.sdf</sdf><description>Xovium wizard profile</description></model>\n')
    if item['world'] == 'generated':
        write_owned(ctx, f'{relative}/worlds/xovium_training.sdf', generated_world(base / 'worlds/default.sdf', item['seed']))
    elif item['world'] == 'custom':
        write_owned(ctx, f'{relative}/worlds/xovium_custom.sdf', custom_world(Path(item['world_path'])))
    elif item['world'] in ('airfield', 'dynamic'):
        ctx.copy_asset('gazebo/models/uneven_ground', relative + '/models/uneven_ground')
        root = ET.parse(ctx.assets / 'gazebo/worlds/mcmillan_airfield_gz.sdf').getroot()
        if item['dynamic_terrain']:
            for include in list(root.find('world').findall('include')):
                if include.findtext('uri') == 'model://uneven_ground': root.find('world').remove(include)
        write_owned(ctx, relative + '/worlds/mcmillan_airfield_gz.sdf', xml_text(root))
    root = ET.parse(base / 'bridge/server.config').getroot()
    plugins = root.find('plugins')
    if plugins is None:
        raise Error('Gazebo server configuration has no plugins section.')
    for plugin in list(plugins):
        if 'dynamic-terrain' in plugin.get('filename', ''):
            plugins.remove(plugin)
    if item['dynamic_terrain']:
        ET.SubElement(plugins, 'plugin', entity_name='*', entity_type='world',
                      filename='libgz-dynamic-terrain-system.so', name='custom::DynamicTerrainSystem')
    write_owned(ctx, relative + '/bridge/wizard-server.config', xml_text(root))
    if item['dynamic_terrain']:
        world_name = {'airfield': 'mcmillan_airfield_gz', 'dynamic': 'mcmillan_airfield_gz', 'generated': 'xovium_training', 'custom': 'xovium_custom'}.get(item['world'], item['world'])
        world_root = ET.parse(base / ('worlds/' + world_name + '.sdf')).getroot()
        world = world_root.find('world')
        world.set('name', 'xovium_terrain')
        names = {p.get('name') for p in world.findall('plugin')}
        for plugin in plugins:
            if plugin.get('entity_type') == 'world' and plugin.get('name') not in names:
                copy = deepcopy(plugin)
                copy.attrib.pop('entity_name', None)
                copy.attrib.pop('entity_type', None)
                world.append(copy)
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
    if data['isaac_sih'] and (ctx.prefix / 'simulation/sih/bridge/run_view.sh').is_file():
        apps.sih_view_launcher(ctx)
    if data['unreal']:
        sp.import_unreal(ctx, sp.get(data, 'unreal-engine'))
        apps.unreal_launchers(ctx)
    write_owned(ctx, 'config/installer-profile.json', json.dumps(data, indent=2, sort_keys=True) + '\n')
    defaults = '# Generated by the installation wizard; override in scripts/local.env.\n'
    if data['license']:
        defaults += 'export OMNI_KIT_ACCEPT_EULA=YES\n'
    write_owned(ctx, 'scripts/wizard.env', defaults)
