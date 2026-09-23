import os
from pathlib import Path
import re

from xovium.paths import Error

RUNTIMES = {'px4', 'fgfs', 'flightgear', 'gz', 'gz-sim', 'gz-sim-server', 'gz-sim-gui',
            'QGroundControl', 'UnrealEditor', 'isaacsim', 'isaac-sim', 'kit', 'mavlink-routerd',
            'cmake', 'make', 'gmake', 'ninja', 'gcc', 'g++', 'cc', 'c++', 'cc1', 'cc1plus', 'ld'}
INTERPRETERS = {'sh', 'bash', 'dash', 'zsh', 'ksh', 'perl', 'ruby'}


def optional_text(path):
    try:
        return path.read_text(errors='replace')
    except OSError:
        return ''


def process_reason(process, paths, *, strict_cwd=False):
    roots = [Path(p).resolve() for p in paths]
    spellings = list(dict.fromkeys([Path(p).absolute() for p in paths] + roots))
    def actual(value):
        return Path(str(value).removesuffix(' (deleted)')).resolve()
    def inside(value):
        if not value or not Path(value).is_absolute():
            return False
        lexical = Path(os.path.normpath(str(value).removesuffix(' (deleted)')))
        if not any(lexical.is_relative_to(root) for root in spellings):
            return False
        return any(actual(value).is_relative_to(root) for root in roots)
    def link(name):
        try:
            return str((process / name).readlink())
        except OSError:
            return ''
    executable, cwd = link('exe'), link('cwd')
    if inside(executable):
        return 'executing ' + executable
    if strict_cwd and inside(cwd):
        return 'working in ' + cwd
    try:
        args = [os.fsdecode(arg) for arg in (process / 'cmdline').read_bytes().split(b'\0') if arg]
    except OSError:
        args = []
    if args and inside(args[0]):
        return 'executing ' + args[0]
    program = Path(executable.removesuffix(' (deleted)')).name or (Path(args[0]).name if args else '')
    if args and (program in INTERPRETERS or re.fullmatch(r'python[0-9.]*', program)):
        arguments = iter(args[1:])
        for arg in arguments:
            if arg in ('-c', '-m', '--command', '-s') or arg.startswith('-c'):
                break
            if arg in ('-W', '-X', '--rcfile', '--init-file') or (program in INTERPRETERS and arg in ('-O', '+O')):
                next(arguments, None)
                continue
            if arg.startswith('-'):
                continue
            script = str(Path(cwd) / arg) if cwd and not Path(arg).is_absolute() else arg
            if inside(script):
                return 'running script ' + script
            break
    for line in optional_text(process / 'maps').splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6 and 'x' in fields[1] and inside(fields[5]):
            return 'using library ' + fields[5]
    names = {program, *(Path(arg).name for arg in args[:2])}
    if names.intersection(RUNTIMES):
        if inside(cwd):
            return 'working in ' + cwd
        for arg in args[1:]:
            value = arg.split('=', 1)[-1] if arg.startswith('-') else arg
            if inside(value):
                return 'using simulation path ' + value
        try:
            environment = (process / 'environ').read_bytes().split(b'\0')
        except OSError:
            environment = []
        for item in environment:
            key, _, value = os.fsdecode(item).partition('=')
            if key in ('GZ_SIM_RESOURCE_PATH', 'GZ_SIM_SERVER_CONFIG_PATH', 'FG_HOME', 'ISAAC_ROOT', 'PX4_DIR'):
                if any(inside(path) for path in value.split(':')):
                    return 'using ' + key + '=' + value
    return None


def assert_idle(paths, *, strict_cwd=False):
    paths = list(dict.fromkeys(Path(p) for p in paths))
    running = []
    for process in Path('/proc').iterdir():
        if not process.name.isdecimal() or int(process.name) == os.getpid():
            continue
        try:
            reason = process_reason(process, paths, strict_cwd=strict_cwd)
            if reason:
                name = optional_text(process / 'comm').strip() or 'process'
                running.append(f'  PID {process.name} ({name}): {reason}')
                if len(running) == 5:
                    break
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    if running:
        raise Error('Managed runtime or build processes are running:\n' + '\n'.join(running)
                    + '\nStop these sessions before changing the installation. Open documents do not block uninstall.')
