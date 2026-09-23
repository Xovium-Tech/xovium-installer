#!/usr/bin/env python3
import argparse
import codecs
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time


def identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return None if fields[0] == 'Z' else fields[19]
    except (OSError, IndexError):
        return None


def same_process(target):
    return (target.get('pid', 0) > 1 and target.get('identity') is not None
            and identity(target['pid']) == target['identity'])


def signal_target(target, sig, *, group=False):
    if not same_process(target):
        return False
    try:
        if group:
            if os.getpgid(target['pid']) != target['pid']:
                return False
            os.killpg(target['pid'], sig)
        else:
            os.kill(target['pid'], sig)
        return True
    except ProcessLookupError:
        return False


def write_view(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data))
    temporary.replace(path)


def register_view(directory, files):
    views = directory / 'views'
    views.mkdir(exist_ok=True)
    data = {'pid': os.getpid(), 'identity': identity(os.getpid()),
            'panel': files[0], 'finished': False}
    path = views / f"{data['pid']}-{data['identity']}.json"
    write_view(path, data)
    return path, data


class ViewMonitor:
    def __init__(self, directory, session, panel_list):
        self.directory = directory
        self.session = session
        self.keys = {files[0] for _, files in panel_list}
        self.handled = set()
        self.qgc_deadline = None
        self.stopping = False

    def running(self):
        return not (self.directory / 'session.exit').exists() and same_process(self.session)

    def tick(self):
        if not self.running() or self.stopping:
            return
        for path in (self.directory / 'views').glob('*.json'):
            if path in self.handled:
                continue
            try:
                view = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if view.get('finished') or view.get('panel') not in self.keys:
                self.handled.add(path)
                continue
            if same_process(view):
                continue
            self.handled.add(path)
            if view['panel'] == 'qgc.log':
                if self.qgc_deadline is None and signal_target(self.session['qgc'], signal.SIGTERM, group=True):
                    print('QGC output tab closed; stopping QGC. Simulation continues.', flush=True)
                    self.qgc_deadline = time.monotonic() + 2
            else:
                print('PX4/simulator output tab closed; stopping this session.', flush=True)
                signal_target(self.session, signal.SIGTERM)
                self.stopping = True
                return
        if self.qgc_deadline is not None and time.monotonic() >= self.qgc_deadline:
            signal_target(self.session['qgc'], signal.SIGKILL, group=True)
            self.qgc_deadline = None

    def wait(self):
        while self.running():
            self.tick()
            time.sleep(0.1)


def panels(simulator, main, qgc, headless, config=None):
    config = config or {}
    def versioned(name, key):
        version = config.get(key)
        return name + ' ' + version if isinstance(version, str) and version else name
    px4 = versioned('PX4', 'px4_version')
    result = [(versioned('QGC', 'qgc_version'), ['qgc.log'])] if qgc else []
    if simulator.startswith('gazebo-'):
        gazebo = 'Gazebo ' + simulator.removeprefix('gazebo-').capitalize()
        gazebo_logs = ['gazebo-server.log']
        if not headless:
            gazebo_logs.append('gazebo-gui.log')
        result += [(px4, ['px4-gazebo.log']), (gazebo, gazebo_logs)]
    elif simulator == 'flightgear':
        result += [(px4, ['flightgear-px4.log']), ('FlightGear', ['flightgear.log'])]
    elif simulator == 'unreal-engine':
        result += [(px4, ['unreal-px4.log']), ('Unreal Engine', ['unreal.log', 'unreal-engine.log'])]
    elif simulator == 'isaac':
        result += [(px4, ['isaac-px4.log']), ('Isaac', ['isaac.log', 'isaac-kit.log'])]
    elif main == 'sih-view':
        result += [(versioned('PX4 SIH', 'px4_version'), ['sih-px4.log']), ('SIH', ['sih-view.log'])]
    else:
        result += [(versioned('PX4 SIH', 'px4_version'), ['sih-px4.log'])]
    return result


def terminal_commands(panel_list, directory, which=shutil.which):
    def viewer(title, files):
        return [sys.executable, '-u', str(Path(__file__).resolve()), 'view', str(directory), title, *files]
    gnome = which('gnome-terminal')
    if gnome:
        command = [gnome]
        for index, (title, files) in enumerate(panel_list):
            command += ['--window' if index == 0 else '--tab', '--title=' + title,
                        '--working-directory=' + str(Path.home()), '--command=' + shlex.join(viewer(title, files))]
        return [command]
    terminal = which('x-terminal-emulator') or which('xterm')
    if terminal:
        return [[terminal, '-T', title, '-e', *viewer(title, files)] for title, files in panel_list]
    return []


def follow(directory, panel_list, *, console=False, monitor=None):
    session = json.loads((directory / 'session.json').read_text())
    streams = []
    for title, files in panel_list:
        for filename in files:
            streams.append({'name': title if len(files) == 1 else title + '/' + filename,
                            'path': directory / filename, 'stream': None,
                            'decoder': codecs.getincrementaldecoder('utf-8')('replace'), 'line_start': True})
    qgc_only = not console and panel_list[0][1] == ['qgc.log']
    if console:
        print('Live output. Ctrl-C here stops the session.', flush=True)
    else:
        target = 'QGC only' if qgc_only else 'this PX4/simulator session'
        print(f'Live output. Closing this tab or pressing Ctrl-C stops {target}.', flush=True)
    try:
        while True:
            if monitor is not None:
                monitor.tick()
            received = False
            for item in streams:
                try:
                    if item['stream'] is None:
                        item['stream'] = item['path'].open('rb')
                    stream = item['stream']
                    if os.fstat(stream.fileno()).st_size < stream.tell():
                        stream.seek(0)
                        item['decoder'].reset()
                    chunk = stream.read(65536)
                except FileNotFoundError:
                    continue
                if not chunk:
                    continue
                received = True
                text = item['decoder'].decode(chunk)
                if console or len(streams) > 1:
                    for line in text.splitlines(keepends=True):
                        if item['line_start']:
                            sys.stdout.write('[' + item['name'] + '] ')
                        sys.stdout.write(line)
                        item['line_start'] = line.endswith(('\n', '\r'))
                else:
                    sys.stdout.write(text)
                sys.stdout.flush()
            marker = directory / ('qgc.exit' if qgc_only and (directory / 'qgc.exit').exists() else 'session.exit')
            ended = marker.exists() or not same_process(session)
            if ended and not received:
                status = ' (status ' + marker.read_text().strip() + ')' if marker.exists() else ''
                name = 'QGC' if marker.name == 'qgc.exit' else 'Session'
                print('\n' + name + ' ended' + status + '. Logs: ' + str(directory), flush=True)
                return
            time.sleep(0.001 if received else 0.1)
    finally:
        for item in streams:
            if item['stream'] is not None:
                item['stream'].close()


def start(args):
    try:
        state = json.loads((Path(__file__).resolve().parents[2] / 'state.json').read_text())
        config = state.get('config', {})
        if not isinstance(config, dict):
            config = {}
    except (OSError, ValueError, AttributeError):
        config = {}
    panel_list = panels(args.simulator, args.main, args.qgc == 'yes', args.headless == '1', config)
    directory = Path(args.directory)
    session = {'pid': args.pid, 'identity': identity(args.pid),
               'qgc': {'pid': args.qgc_pid, 'identity': identity(args.qgc_pid)}}
    (directory / 'session.json').write_text(json.dumps(session))
    monitor = ViewMonitor(directory, session, panel_list)
    display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))
    use_tabs = args.mode == 'tabs' or (args.mode == 'auto' and display and args.headless == '0')
    if use_tabs and display:
        commands = terminal_commands(panel_list, directory)
        if commands:
            try:
                for command in commands:
                    if Path(command[0]).name == 'gnome-terminal':
                        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
                        if result.returncode:
                            raise RuntimeError(result.stderr.strip() or 'terminal returned an error')
                    else:
                        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, start_new_session=True)
                print('Live output opened in terminal tabs/windows.', flush=True)
                monitor.wait()
                return
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                print(f'Cannot open output terminals: {exc}. Showing output here.', flush=True)
        else:
            print('No supported terminal emulator found. Showing output here.', flush=True)
    elif args.mode == 'tabs':
        print('No graphical display available. Showing output here.', flush=True)
    follow(directory, panel_list, console=True, monitor=monitor)


def main():
    parser = argparse.ArgumentParser(description='Live log tabs whose lifetime controls their own managed launch.')
    commands = parser.add_subparsers(dest='command', required=True)
    launcher = commands.add_parser('start')
    for name in ('directory', 'mode', 'simulator', 'main', 'qgc', 'headless'):
        launcher.add_argument(name)
    launcher.add_argument('pid', type=int)
    launcher.add_argument('qgc_pid', type=int, nargs='?', default=0)
    viewer = commands.add_parser('view')
    viewer.add_argument('directory'); viewer.add_argument('title'); viewer.add_argument('files', nargs='+')
    args = parser.parse_args()
    try:
        if args.command == 'start':
            start(args)
        else:
            registration, view = register_view(Path(args.directory), args.files)
            print(args.title, flush=True)
            follow(Path(args.directory), [(args.title, args.files)])
            view['finished'] = True
            write_view(registration, view)
            if sys.stdin.isatty():
                os.chdir(Path.home())
                os.execlp('bash', 'bash', '-c', 'printf "\\nPress Enter to close this output tab..."; read -r')
    except (KeyboardInterrupt, BrokenPipeError):
        return


if __name__ == '__main__':
    main()
