#!/usr/bin/env python3
"""Choose a shared model and apply it to one simulator session."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import zipfile

from gazebo_model import native_models, prepare as prepare_gazebo

SIMULATORS = ('gazebo-jetty', 'gazebo-harmonic', 'flightgear', 'isaac', 'sih', 'unreal-engine')
PREFIX = Path(__file__).resolve().parents[2]
LABELS = {'rc_cessna': 'Cessna', 'r1_rover': 'Rover'}
ALIASES = {'cessna': 'rc_cessna', 'rover': 'r1_rover'}
LAUNCHERS = {'isaac': 'isaac', 'isaac-px4': 'isaac', 'sih-view': 'sih', 'sih-px4': 'sih',
             'flightgear': 'flightgear', 'flightgear-px4': 'flightgear',
             'unreal': 'unreal-engine', 'unreal-px4': 'unreal-engine'}


def shared_models(prefix):
    # Use the converter's naming and adapter rules so menu keys always identify
    # the same output directory, including custom bundles saved by the editor.
    transform = prefix / 'shared/transform'
    if not (transform / 'model_cache.py').is_file():
        raise ValueError('Shared model support is missing. Run install.sh --refresh-launchers.')
    sys.path.insert(0, str(transform))
    from model_cache import bundles, adapter_for
    entries = {}
    for key, bundle in bundles(prefix / 'shared').items():
        with zipfile.ZipFile(bundle) as archive:
            metadata = [info for info in archive.infolist() if info.filename == 'project.json']
            if len(metadata) != 1 or metadata[0].file_size > 16 * 1024**2:
                raise ValueError('Missing, duplicate or oversized project.json: ' + str(bundle))
            document = json.loads(archive.read(metadata[0]))
        if not isinstance(document, dict) or document.get('format') != 'xovium-model' or document.get('version') != 1:
            raise ValueError('Unsupported model document: ' + str(bundle))
        label = LABELS.get(key, document.get('name', key))
        if not isinstance(label, str) or not label.strip() or len(label) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in label):
            raise ValueError('Invalid model name: ' + str(bundle))
        entries[key] = dict(label=label, adapter=adapter_for(document, bundle, key), portable=True, bundle=bundle)
    return entries


def inventory(prefix, simulator):
    entries = shared_models(prefix)
    if simulator.startswith('gazebo-'):
        native = native_models(prefix, simulator)
        entries = {key: item for key, item in entries.items() if item['adapter'] in native}
        for key in native:
            entries.setdefault(key, dict(label=key + ' (PX4)', adapter=key, portable=False))
    keys = sorted(entries, key=lambda key: (0 if key == 'rc_cessna' else 1 if key == 'r1_rover' else 2, key))
    if not keys:
        raise ValueError('No models are installed. Add .xovmodel files to shared/models or refresh the installation.')
    return {key: entries[key] for key in keys}


def saved_model(prefix, simulator):
    state = json.loads((prefix / 'state.json').read_text())
    wizard = state.get('config', {}).get('wizard', {})
    selected = wizard.get('shared_selection', wizard.get('simulations', {}).get(simulator, {}))
    key = selected.get('model', 'rc_cessna')
    return 'rc_cessna' if key == 'default' else key


def show(entries, default, stream):
    for number, (key, item) in enumerate(entries.items(), 1):
        suffix = ' [default]' if key == default else ''
        print(f"  {number}) {item['label']} ({key}){suffix}", file=stream)


def resolve(value, entries, *, numbered=False):
    key = value if value in entries else ALIASES.get(value.lower(), value)
    if numbered and value.isdecimal() and 1 <= int(value) <= len(entries):
        key = list(entries)[int(value) - 1]
    if key not in entries:
        raise ValueError('Unknown or unavailable model: ' + value + '. Use --list-models to see the choices.')
    return key


def select(prefix, simulator, value, interactive):
    entries = inventory(prefix, simulator)
    default = saved_model(prefix, simulator)
    if value:
        return resolve(value, entries)
    if not interactive:
        return resolve(default, entries)
    print(f'Choose a model for {simulator}:', file=sys.stderr)
    show(entries, default, sys.stderr)
    while True:
        print(f'Number or model key (Enter: {default}, q: cancel): ', end='', file=sys.stderr, flush=True)
        line = sys.stdin.readline()
        if not line or line.strip().lower() in ('q', 'quit', 'cancel'):
            return None
        try:
            return resolve(line.strip() or default, entries, numbered=True)
        except ValueError as error:
            print(str(error), file=sys.stderr)


def replace_option(command, option, value):
    """Replace only installer preset arguments; explicit user arguments follow."""
    result, index = [], 0
    while index < len(command):
        if command[index] == option:
            if index + 1 == len(command):
                raise ValueError('Missing value in installed launcher: ' + option)
            index += 2
        elif command[index].startswith(option + '='):
            index += 1
        else:
            result.append(command[index])
            index += 1
    return [*result, option, str(value)]


def launch(prefix, name, count, arguments):
    if count < 1 or count > len(arguments):
        raise ValueError('Invalid installed launcher command.')
    command, user = arguments[:count], arguments[count:]
    env = dict(os.environ)
    key = env.get('XOVIUM_SESSION_MODEL')
    if key:
        simulator = env.get('XOVIUM_SESSION_SIMULATOR', '')
        expected = LAUNCHERS.get(name)
        if simulator not in SIMULATORS or (expected and simulator != expected) or (name == 'px4-gazebo' and not simulator.startswith('gazebo-')):
            raise ValueError('The session model does not belong to this simulator launcher.')
        entries = inventory(prefix, simulator)
        key = resolve(key, entries)
        item, base = entries[key], prefix / 'simulation' / simulator
        adapter = item['adapter']
        if item['portable']:
            record = prefix / 'shared/transform/cache/model-exports' / simulator / (key + '.json')
            converted = json.loads(record.read_text()) if record.is_file() else {}
            if converted.get('adapter') != adapter:
                raise ValueError('Model conversion is missing or outdated. Start with scripts/run.sh.')
            with item['bundle'].open('rb') as stream:
                if converted.get('source_sha256') != hashlib.file_digest(stream, 'sha256').hexdigest():
                    raise ValueError('The model changed after conversion. Start scripts/run.sh again to use the saved model.')
        env['XOVIUM_VEHICLE'] = adapter
        env.pop('XOVIUM_ROVER_CONFIG', None)
        if adapter == 'r1_rover':
            env['XOVIUM_ROVER_CONFIG'] = str(prefix / 'shared/models/rover/physics.json')
        if name == 'px4-gazebo':
            env.update(prepare_gazebo(prefix, simulator, key, adapter, env))
        elif name == 'isaac':
            command = replace_option(command, '--model', base / 'models' / (key + '.usda'))
            config = env['XOVIUM_ROVER_CONFIG'] if adapter == 'r1_rover' else base / 'bridge/aircraft.json'
            command = replace_option(command, '--config', config)
        elif name == 'sih-view':
            command = replace_option(command, '--aircraft-usd', base / 'models' / (key + '.usda'))
        elif simulator == 'flightgear':
            filename = 'R1-set.xml' if adapter == 'r1_rover' else 'Cessna-set.xml'
            env['XOVIUM_FG_MODEL'] = 'rascal-electric'
            env['XOVIUM_FG_AIRCRAFT'] = str(base / 'models' / key / filename)
        elif simulator == 'unreal-engine':
            env['XOVIUM_UE_MODEL'] = f'/Game/Models/{key}/{key}.{key}'
    os.execvpe(command[0], [*command, *user], env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('list', 'select'):
        item = sub.add_parser(action)
        item.add_argument('--simulator', choices=SIMULATORS, required=True)
        if action == 'select':
            item.add_argument('--model')
            item.add_argument('--interactive', action='store_true')
    item = sub.add_parser('launch')
    item.add_argument('name', choices=(*LAUNCHERS, 'px4-gazebo'))
    item.add_argument('count', type=int)
    item.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == 'launch':
            launch(PREFIX, args.name, args.count, args.command)
        elif args.action == 'list':
            show(inventory(PREFIX, args.simulator), saved_model(PREFIX, args.simulator), sys.stdout)
        else:
            key = select(PREFIX, args.simulator, args.model, args.interactive)
            if key: print(key)
    except (ValueError, OSError, RuntimeError, zipfile.BadZipFile, KeyError) as error:
        parser.exit(1, f'Model selection failed: {error}\n')
    except KeyboardInterrupt:
        parser.exit(130, '\nModel selection cancelled.\n')


if __name__ == '__main__':
    main()
