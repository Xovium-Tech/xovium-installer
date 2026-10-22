#!/usr/bin/env python3
import shutil
import subprocess
import sys
from pathlib import Path

packages = set()
for tool, package in {'git':'git', 'cmake':'cmake', 'c++':'build-essential',
                      'pkg-config':'pkg-config', 'zenity':'zenity', 'xdg-open':'xdg-utils', 'flock':'util-linux'}.items():
    if not shutil.which(tool): packages.add(package)
libraries = {'x11':'libx11-dev', 'xrandr':'libxrandr-dev', 'xi':'libxi-dev',
             'xcursor':'libxcursor-dev', 'xinerama':'libxinerama-dev', 'gl':'libgl1-mesa-dev'}
editor = Path(__file__).resolve().parents[1] / 'tools/xovium-model-editor'
stamp = editor / 'tools/build-state.sh'
editor_ready = stamp.is_file() and subprocess.run(
    ['bash', str(stamp), 'check', str(editor), str(editor / '.build/cmake'), 'gui'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
if not editor_ready:
    libraries.update({'assimp':'libassimp-dev', 'libpng':'libpng-dev',
                      'tinyxml2':'libtinyxml2-dev', 'libarchive':'libarchive-dev'})
for library, package in libraries.items():
    if not shutil.which('pkg-config') or subprocess.run(['pkg-config', '--exists', library]).returncode:
        packages.add(package)
if not packages: sys.exit(0)
print('The installer and model editor need these packages: ' + ', '.join(sorted(packages)), flush=True)
print('They remain installed with the kit when simulator software is uninstalled.', flush=True)
subprocess.run(['sudo', 'apt-get', 'update'], check=True)
subprocess.run(['sudo', 'apt-get', 'install', '-y', '--no-install-recommends', '--no-remove', *sorted(packages)], check=True)
