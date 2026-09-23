import configparser
import runpy
from xovium.core import Error


def port(ctx):
    wizard = ctx.config.get('wizard', {})
    if wizard.get('router') and 'router_ports' in wizard:
        from xovium.router_config import validate
        return validate(wizard['router_ports'])['qgc']
    path = ctx.prefix / 'tools/mavlink-router/main.conf'
    if not path.is_file():
        path = ctx.assets / 'mavlink-router/main.conf'
    if not path.is_file() and ctx.dry:
        return 14552
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    try:
        endpoint = config['UdpEndpoint qgc']
        if endpoint.get('Mode', '').lower() != 'normal' or endpoint.get('Address') not in ('127.0.0.1', 'localhost'):
            raise ValueError('expected a Normal endpoint on 127.0.0.1')
        result = endpoint.getint('Port')
        if not 1024 <= result <= 65535: raise ValueError('port outside 1024..65535')
        return result
    except (KeyError, ValueError, configparser.Error) as exc:
        raise Error(f'Cannot configure local QGC from {path}: {exc}') from exc


def enabled(ctx):
    return 'mavlink-router' in ctx.config.get('requested', [])


def setup(ctx):
    if enabled(ctx) and not ctx.dry:
        from xovium.profiles import write_owned
        from xovium.router_config import settings
        ports = settings(ctx)
        write_owned(ctx, 'scripts/network.env', '# Generated router defaults; override in scripts/local.env or with --no-router.\nexport PX4_MAVLINK_ROUTER="${PX4_MAVLINK_ROUTER:-1}"\n'
                    + f'export XOVIUM_ROUTER_PX4_PORT="${{XOVIUM_ROUTER_PX4_PORT:-{ports["px4_source"]}}}"\n')
    if 'qgc' not in ctx.config.get('requested', []):
        return
    target = ctx.managed('config/QGroundControl/QGroundControl.ini')
    chosen = port(ctx)
    ctx.say(f"QGC communication: {'automatic MAVLink Router link' if enabled(ctx) else 'direct PX4; router link saved'} (router UDP {chosen}).")
    if ctx.dry:
        return
    module = runpy.run_path(str(ctx.assets / 'runtime/scripts/lib/qgc-link.py'))
    if module['running_qgc']():
        ctx.say('QGC is running; the installed QGC launcher will apply the communication link on its next launch.')
        return
    try:
        module['configure'](target, enabled(ctx), chosen)
    except (ValueError, OSError) as exc:
        raise Error(f'QGC communication setup: {exc}') from exc
