"""Assign portable RGB cameras independent RTP destinations after native export.

This only adds runtime stream metadata. The converter owns sensor optics,
resolution, rate, transforms and geometry. Omitted hosts retain runtime defaults.
"""
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def _cameras(document):
    return [(index, sensor) for index, sensor in enumerate(document.get('sensors', []))
            if sensor.get('type') == 'camera' and sensor.get('enabled', True)]


def _port(value, name):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value).strip()):
        raise ValueError('Camera ' + name + ' has an invalid UDP port: ' + str(value))
    result = int(value)
    if not 1 <= result <= 65535:
        raise ValueError('Camera ' + name + ' UDP port must be in 1..65535')
    return result


def _assign(entries):
    used = {}
    for entry in entries:
        if entry['port'] is None:
            continue
        port = entry['port'] = _port(entry['port'], entry['name'])
        if port in used:
            raise ValueError(f'Camera UDP port {port} is assigned to both '
                             f'{used[port]} and {entry["name"]}')
        used[port] = entry['name']
    # Preferred names have priority over other automatic assignments, even if
    # they occur later in the document. Explicit destinations always win.
    preferences = {'camera_front': 5600, 'camera_down': 5601}
    ordered = sorted(entries, key=lambda item: (preferences.get(item['name'], 65536),
                                                item['name'], item['parent'], item['index']))
    for entry in ordered:
        port = preferences.get(entry['name'])
        if entry['port'] is None and port is not None and port not in used:
            entry['port'] = port
            used[port] = entry['name']
    for entry in ordered:
        if entry['port'] is not None:
            continue
        port = 5600
        while port in used and port <= 65535:
            port += 1
        if port > 65535:
            raise ValueError('No available camera UDP ports in 5600..65535')
        entry['port'] = port
        used[port] = entry['name']


def _rgb(sensor):
    parameters = sensor.get('parameters', {})
    return parameters.get('format', parameters.get('image.format', 'R8G8B8')) == 'R8G8B8'


def read_sdf(path):
    """Read SDF, retaining Gazebo's permissive undeclared ``gz:type`` syntax.

    PX4 optical_flow declares a custom sensor with this extension attribute
    without an XML namespace. Mask only that attribute for strict XML parsing;
    the writer restores its exact name before publishing the staged document.
    """
    try:
        return ET.parse(path), False
    except ET.ParseError as error:
        text = Path(path).read_text()
        marker = 'xovium_private_gz_type'
        if 'unbound prefix' not in str(error) or marker in text:
            raise
        masked, count = re.subn(r'\bgz:type(?=\s*=)', marker, text)
        if not count:
            raise
        return ET.ElementTree(ET.fromstring(masked)), True


def _gazebo(document, path):
    tree, restore_gz_type = read_sdf(path)
    root = tree.getroot()
    entries = []
    for index, sensor in _cameras(document):
        matches = [node for link in root.iter('link') if link.get('name') == sensor['parent']
                   for node in link.findall('sensor') if node.get('name') == sensor['name']]
        if len(matches) != 1:
            raise ValueError('Expected one exported Gazebo camera: ' + sensor['parent'] + '::' + sensor['name'])
        node = matches[0]
        if node.get('type') != 'camera' or node.findtext('camera/image/format', 'R8G8B8').strip() != 'R8G8B8':
            continue
        plugins = [plugin for plugin in node.findall('plugin')
                   if plugin.get('name') == 'custom::GstPlaneCameraSystem'
                   or 'GstPlaneCameraSystem' in plugin.get('filename', '')]
        if len(plugins) > 1:
            raise ValueError('Duplicate camera streaming configuration: ' + sensor['name'])
        plugin = plugins[0] if plugins else None
        entries.append({'index': index, 'name': sensor['name'], 'parent': sensor['parent'],
                        'port': plugin.findtext('udp_port') if plugin is not None else None,
                        'node': node, 'plugin': plugin})
    _assign(entries)
    for entry in entries:
        plugin = entry['plugin']
        if plugin is None:
            plugin = ET.SubElement(entry['node'], 'plugin',
                                   filename='libGstPlaneCameraSystem.so', name='custom::GstPlaneCameraSystem')
        values = {'camera_name': entry['name'], 'udp_port': str(entry['port'])}
        # The streamer's sensor-rate fallback is the authoritative behavior.
        # Author an equivalent integer only for finite, positive rates; a zero
        # rate means every sensor update and must retain the streamer's default.
        rate = float(entry['node'].findtext('update_rate', '0'))
        if math.isfinite(rate) and rate > 0:
            values['rate'] = str(max(1, min(240, math.floor(rate + .5))))
        for name, value in values.items():
            if plugin.find(name) is None:
                ET.SubElement(plugin, name).text = value
    if entries:
        data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        if restore_gz_type:
            data = data.replace(b'xovium_private_gz_type=', b'gz:type=')
        path.write_bytes(data)
    return entries


def _usd_name(value):
    # Native exporter sanitizes UTF-8 bytes, not Unicode code points.
    result = ''.join(chr(byte) if byte < 128 and (chr(byte).isalnum() or byte == 95) else '_'
                     for byte in str(value).encode('utf-8')) or 'model'
    return '_' + result if result[0].isdigit() else result


def _closing_brace(text, opening):
    depth, quoted, escaped, comment = 0, False, False, False
    for index in range(opening, len(text)):
        char = text[index]
        if comment:
            comment = char != '\n'
            continue
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '#':
            comment = True
        elif char == '"':
            quoted = True
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return index
    raise ValueError('Unclosed exported USD camera prim')


def _usd(document, path):
    text = path.read_text()
    entries = []
    for index, sensor in _cameras(document):
        if not _rgb(sensor):
            continue
        name = 'sensor_' + str(index) + '_' + _usd_name(sensor['name'])
        pattern = r'(?m)^(?P<indent>[ \t]*)def Camera "' + re.escape(name) + r'"\s*\n[ \t]*\{'
        matches = list(re.finditer(pattern, text))
        if len(matches) != 1:
            raise ValueError('Expected one exported USD camera prim: ' + name)
        match = matches[0]
        opening = match.end() - 1
        closing = _closing_brace(text, opening)
        body = text[opening + 1:closing]
        if re.search(r'(?m)^\s*custom bool px4:camera:enabled\s*=\s*(?:false|0)\s*$', body):
            continue
        ports = re.findall(r'(?m)^\s*custom int px4:camera:port\s*=\s*([^\n]+)', body)
        if len(ports) > 1:
            raise ValueError('Duplicate USD camera UDP port attribute: ' + name)
        entries.append({'index': index, 'name': sensor['name'], 'parent': sensor['parent'],
                        'port': ports[0].strip() if ports else None,
                        'opening': opening, 'indent': match['indent'] + '    ', 'has_port': bool(ports)})
    _assign(entries)
    # Reverse insertions preserve every pre-existing byte, including transform
    # matrices, material definitions, metadata and explicitly authored hosts.
    for entry in sorted(entries, key=lambda item: item['opening'], reverse=True):
        if not entry['has_port']:
            index = entry['opening'] + 1
            text = text[:index] + '\n' + entry['indent'] + 'custom int px4:camera:port = ' + str(entry['port']) + text[index:]
    if any(not entry['has_port'] for entry in entries):
        path.write_text(text)
    return entries


def configure(document, simulator, target_model_dir):
    """Configure exported camera streams; fail before writing on collisions.

    ``target_model_dir`` contains the native export's model.sdf or model.usda.
    Explicit ports are preserved and must be unique within the exported model.
    Native host fallback remains available because no host is invented here.
    """
    target = Path(target_model_dir)
    if simulator == 'gazebo' or simulator.startswith('gazebo-'):
        entries = _gazebo(document, target / 'model.sdf')
    elif simulator in ('isaac', 'sih'):
        entries = _usd(document, target / 'model.usda')
    else:
        return []
    return [{key: entry[key] for key in ('index', 'name', 'parent', 'port')} for entry in entries]
