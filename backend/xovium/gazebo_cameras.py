"""Install one multi-camera streamer after Gazebo's rendering sensors system."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import xml.etree.ElementTree as ET


def configure_systems(parent, *, server_config=False):
    attributes = dict(entity_name='*', entity_type='world') if server_config else {}
    streamers = [node for node in parent.findall('plugin')
                 if node.get('name') in ('custom::GstCameraSystem', 'custom::GstPlaneCameraSystem')
                 or Path(node.get('filename', '')).name.removeprefix('lib').removesuffix('.so')
                 in ('GstCameraSystem', 'GstPlaneCameraSystem')]
    for node in streamers:
        legacy = (node.get('name') == 'custom::GstCameraSystem'
                  or Path(node.get('filename', '')).name.removeprefix('lib').removesuffix('.so') == 'GstCameraSystem')
        if legacy and list(node):
            settings = ', '.join('<' + child.tag + '>' for child in node)
            raise ValueError('Cannot replace legacy GstCameraSystem with explicit settings: ' + settings
                             + '. Move UDP host/port and encoder settings to each RGB sensor\'s '
                             'GstPlaneCameraSystem configuration (udp_host, udp_port, use_cuda); '
                             'legacy RTMP output is not supported by the enhanced streamer.')
    sensors = [node for node in parent.findall('plugin')
               if node.get('name') == 'gz::sim::systems::Sensors']
    if len(sensors) > 1:
        raise ValueError('Gazebo configuration contains duplicate Sensors systems')
    enhanced = next((node for node in streamers
                     if 'GstPlaneCameraSystem' in node.get('name', '')
                     or 'GstPlaneCameraSystem' in node.get('filename', '')), None)
    for node in streamers:
        parent.remove(node)
    if not sensors:
        sensor = ET.SubElement(parent, 'plugin', **attributes,
                               filename='gz-sim-sensors-system', name='gz::sim::systems::Sensors')
        ET.SubElement(sensor, 'render_engine').text = 'ogre2'
    if enhanced is None:
        enhanced = ET.Element('plugin', **attributes,
                              filename='libGstPlaneCameraSystem.so', name='custom::GstPlaneCameraSystem')
    parent.append(enhanced)


def configure_file(ctx, path):
    """Preserve no-plugin worlds, which inherit the full server configuration."""
    from xovium.profiles import write_owned, xml_text
    root = ET.parse(path).getroot()
    plugins = root.find('plugins') if root.tag == 'server_config' else root.find('world')
    if plugins is None or (root.tag != 'server_config' and not plugins.findall('plugin')):
        return
    configure_systems(plugins, server_config=root.tag == 'server_config')
    write_owned(ctx, path.relative_to(ctx.prefix).as_posix(), xml_text(root))


def configure_model_file(ctx, path):
    """Keep native PX4 RGB sensors streamable with the enhanced world system.

    Annotation is staged separately and published through the same ownership
    checks as other managed configuration. Original PX4 model sources remain
    unchanged. Includes retain the annotated reusable sensor models they use.
    """
    from xovium.profiles import write_owned
    path = Path(path)
    source = ctx.assets / 'shared/transform/camera_streams.py'
    spec = importlib.util.spec_from_file_location('_xovium_camera_streams', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = module.read_sdf(path)[0].getroot()
    sensors = [{'name': sensor.get('name'), 'parent': link.get('name'), 'type': 'camera'}
               for link in root.iter('link') for sensor in link.findall('sensor')
               if sensor.get('type') == 'camera'
               and sensor.findtext('camera/image/format', 'R8G8B8').strip() == 'R8G8B8']
    if not sensors:
        return []
    with tempfile.TemporaryDirectory(prefix='xovium-camera-model-') as temporary:
        temporary = Path(temporary)
        staged = temporary / 'model.sdf'
        shutil.copy2(path, staged)
        result = module.configure({'sensors': sensors}, 'gazebo', temporary)
        if staged.read_bytes() != path.read_bytes():
            write_owned(ctx, path.relative_to(ctx.prefix).as_posix(), staged.read_text())
    return result
