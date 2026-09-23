import configparser
from io import StringIO
from pathlib import Path
from xovium.core import Error

DEFAULTS = dict(px4_source=14550, qgc=14552, primary_app=14540, primary_app_commands=14580,
                secondary_app_1=14541, secondary_app_1_commands=14581,
                secondary_app_2=14542, secondary_app_2_commands=14582,
                secondary_app_3=14543, secondary_app_3_commands=14583, tcp=5760)


def validate(data):
    if not isinstance(data, dict) or set(data) != set(DEFAULTS):
        raise Error('Invalid MAVLink Router port fields.')
    for key, port in data.items():
        if type(port) is not int or not (1024 <= port <= 65535 or key == 'tcp' and port == 0):
            raise Error(f'MAVLink Router {key}: use port 1024..65535 (TCP may be 0 to disable).')
        if key != 'tcp' and any(base <= port <= base + 254 for base in (18570, 14280, 13030, 19450)):
            raise Error(f'MAVLink Router {key}: UDP {port} overlaps a PX4 local socket range.')
    udp = [value for key, value in data.items() if key != 'tcp']
    if len(udp) != len(set(udp)):
        raise Error('MAVLink Router UDP ports must be distinct: one port per role.')
    return dict(data)


def parse(text):
    config = configparser.ConfigParser(interpolation=None)
    config.optionxform = str
    try:
        config.read_string(text)
    except configparser.Error as exc:
        raise Error(f'Cannot read MAVLink Router configuration: {exc}') from exc
    return config


def read(path):
    if not Path(path).is_file(): return dict(DEFAULTS)
    config = parse(Path(path).read_text())
    result = dict(DEFAULTS)
    try:
        for key in DEFAULTS:
            group, option = ('General', 'TcpServerPort') if key == 'tcp' else ('UdpEndpoint ' + key, 'Port')
            if config.has_option(group, option): result[key] = config.getint(group, option)
    except ValueError as exc:
        raise Error(f'Invalid MAVLink Router port: {exc}') from exc
    return validate(result)


def settings(ctx):
    data = ctx.config.get('wizard', {})
    return validate(data['router_ports']) if data.get('router') and 'router_ports' in data else read(ctx.prefix / 'tools/mavlink-router/main.conf')


def render(text, ports):
    ports = validate(ports)
    config = parse(text)
    for key, port in ports.items():
        group, option = ('General', 'TcpServerPort') if key == 'tcp' else ('UdpEndpoint ' + key, 'Port')
        if not config.has_section(group):
            config.add_section(group)
            if key != 'tcp':
                server = key == 'px4_source' or key.endswith('_commands')
                config.set(group, 'Mode', 'Server' if server else 'Normal')
                config.set(group, 'Address', '0.0.0.0' if server else '127.0.0.1')
        config.set(group, option, str(port))
    qgc = config['UdpEndpoint qgc']
    if qgc.get('Mode', '').lower() != 'normal' or qgc.get('Address') not in ('127.0.0.1', 'localhost'):
        raise Error('The wizard QGC endpoint must be Normal on 127.0.0.1 or localhost.')
    output = StringIO(); config.write(output)
    return '# Managed MAVLink Router ports; edit them in the installer Advanced dialog.\n' + output.getvalue()


def configure(ctx):
    if 'mavlink-router' not in ctx.config.get('requested', []): return
    ports = settings(ctx)
    ctx.say('MAVLink Router ports: ' + ', '.join(f'{key}={value}' for key, value in ports.items()))
    if ctx.dry: return
    from xovium.profiles import write_owned
    target = ctx.prefix / 'tools/mavlink-router/main.conf'
    source = target if target.exists() else ctx.assets / 'mavlink-router/main.conf'
    write_owned(ctx, 'tools/mavlink-router/main.conf', render(source.read_text(), ports))
