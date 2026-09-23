#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

import xovium_installer as cli
from xovium.core import Context, Error
from xovium.paths import location, validate_path
from xovium import profiles, simulation_profiles, router_config
from xovium.catalog import QGC_VERSION, QGC_RELEASES
from xovium import px4_compatibility as compatibility

FIELDS = {'schema', 'autopilot', 'mode', 'prefix', 'jetty', 'harmonic', 'backend', 'sih',
          'flightgear', 'unreal', 'unreal_archive', 'isaac_sih', 'isaac_physx', 'qgc',
          'router', 'world', 'seed', 'gazebo_model', 'flightgear_model', 'airspeed',
          'lidar', 'camera', 'license', 'jobs'}
BOOLS = {'jetty', 'harmonic', 'sih', 'flightgear', 'unreal', 'isaac_sih', 'isaac_physx',
         'qgc', 'router', 'airspeed', 'lidar', 'camera', 'license'}


def validate(data, root):
    expected = FIELDS | ({'simulations'} if isinstance(data, dict) and data.get('schema') in (2, 3, 4) else set())
    if isinstance(data, dict) and data.get('schema') in (3, 4): expected |= {'qgc_version', 'router_ports'}
    if isinstance(data, dict) and data.get('schema') == 4: expected.add('px4_version')
    if not isinstance(data, dict) or set(data) != expected:
        raise Error('Invalid wizard request fields; reopen the wizard and review the selection.')
    if data['schema'] not in (1, 2, 3, 4) or data['autopilot'] != 'PX4' or data['mode'] != 'SITL':
        raise Error('This version supports PX4 SITL only.')
    if any(type(data[k]) is not bool for k in BOOLS):
        raise Error('Checkbox values must be booleans.')
    if type(data['jobs']) is not int or not 1 <= data['jobs'] <= 128:
        raise Error('Build jobs must be 1..128.')
    if type(data['seed']) is not int or not 0 <= data['seed'] <= 1000000:
        raise Error('World seed must be 0..1000000.')
    if not any(data[k] for k in ('jetty', 'harmonic', 'sih', 'flightgear', 'unreal', 'isaac_sih', 'isaac_physx')):
        raise Error('Select at least one simulator.')
    if data['backend'] not in ('jetty', 'harmonic'):
        raise Error('Unknown Gazebo backend.')
    gazebo = data['jetty'] or data['harmonic']
    if gazebo and not data[data['backend']]:
        raise Error('The active Gazebo backend must also be checked.')
    if data['world'] not in profiles.WORLDS or data['gazebo_model'] not in profiles.MODELS:
        raise Error('Unsupported world or Gazebo model.')
    if data['flightgear_model'] not in profiles.FG_MODELS:
        raise Error('Unsupported FlightGear model.')
    for key in ('prefix', 'unreal_archive'):
        if not isinstance(data[key], str) or any(c in data[key] for c in '\r\n\0'):
            raise Error(f'Invalid {key}.')
    data = dict(data)
    if data.get('qgc_version', QGC_VERSION) not in QGC_RELEASES:
        raise Error('Select a supported QGC version from the list.')
    if 'router_ports' in data: data['router_ports'] = router_config.validate(data['router_ports'])
    data['prefix'] = str(validate_path(root, data['prefix']))
    if data['unreal']:
        archive = Path(data['unreal_archive']).expanduser()
        if not archive.is_file() or archive.suffix.lower() != '.zip':
            raise Error('Select the official Linux Unreal Engine 5.6.1 ZIP downloaded from Epic.')
        data['unreal_archive'] = str(archive.resolve())
    if (data['isaac_sih'] or data['isaac_physx']) and not data['license']:
        raise Error('The Isaac options require acceptance of the NVIDIA Omniverse license.')
    result = simulation_profiles.validate(data)
    compatibility.validate_profile(result)
    return result


def arguments(data):
    args = ['install', '--prefix', data['prefix'], '--px4', data.get('px4_version', compatibility.DEFAULT), '--jobs', str(data['jobs'])]
    for checkbox, flag in [('jetty', 'gazebo-jetty'), ('harmonic', 'gazebo-harmonic'),
                           ('sih', 'sih-core'), ('isaac_sih', 'sih'), ('isaac_physx', 'isaac'),
                           ('flightgear', 'flightgear'), ('qgc', 'qgc'), ('router', 'mavlink-router')]:
        if data[checkbox]:
            args.append('--' + flag)
            if checkbox == 'qgc': args.append(data.get('qgc_version', QGC_VERSION))
    if data['jetty'] or data['harmonic']:
        args += ['--gazebo-backend', data['backend']]
        if any(simulation_profiles.selected(data, name) and simulation_profiles.get(data, name)['dynamic_terrain'] for name in ('gazebo-jetty', 'gazebo-harmonic')):
            args.append('--gazebo-plugins')
    if data['unreal']:
        args += ['--unreal-engine', data['unreal_archive']]
    return args


def execute(data, *, dry):
    data = validate(data, cli.ROOT)
    options = cli.parser().parse_args(arguments(data) + (['--dry-run'] if dry else []))
    ctx = Context(cli.ROOT, options)
    def operation():
        plan = cli.configure(ctx, cli.selection(options))
        ctx.config['wizard'] = profiles.merge_selection(ctx.config.get('wizard', {}), data)
        print('Wizard profile: ' + json.dumps(data, sort_keys=True), flush=True)
        if ctx.state.get('components'):
            print('Existing components are retained; unchecked boxes do not uninstall previous selections.', flush=True)
        cli.install_plan(ctx, plan)
        if not dry:
            print(f"Ready. Launch with: {ctx.storage}/scripts/run.sh", flush=True)
    if dry:
        operation()
    else:
        with ctx.locked():
            operation()


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--defaults', action='store_true')
    modes.add_argument('--preview', metavar='REQUEST')
    modes.add_argument('--install', metavar='REQUEST')
    modes.add_argument('--uninstall', action='store_true')
    modes.add_argument('--uninstall-preview', action='store_true')
    options = parser.parse_args()
    if options.defaults:
        prefix = location(cli.ROOT)
        print(prefix)
        accepted = False
        for name in ('wizard.env', 'local.env'):
            settings = prefix / 'scripts' / name
            if settings.is_file():
                for line in settings.read_text().splitlines():
                    value = line.strip().removeprefix('export ')
                    if value in ('OMNI_KIT_ACCEPT_EULA=YES', 'OMNI_KIT_ACCEPT_EULA=NO'):
                        accepted = value.endswith('=YES')
        print('accepted' if accepted else 'not-accepted')
        state_path = prefix / 'state.json'
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        print(state.get('config', {}).get('qgc_version', QGC_VERSION))
        for port in router_config.read(prefix / 'tools/mavlink-router/main.conf').values(): print(port)
        print(state.get('config', {}).get('px4_version', compatibility.DEFAULT))
        print('installed' if state.get('components', {}).get('px4') else 'new')
    elif options.uninstall or options.uninstall_preview:
        return cli.main(['uninstall'] + (['--dry-run'] if options.uninstall_preview else []))
    else:
        request = Path(options.preview or options.install)
        if request.stat().st_size > 131072:
            raise Error('Wizard request is too large.')
        execute(json.loads(request.read_text()), dry=bool(options.preview))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Error, OSError, ValueError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr, flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Stopped. Completed components are recorded. Review and start again to resume.', flush=True)
        sys.exit(130)
