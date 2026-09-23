#!/usr/bin/env python3
import shutil
import subprocess
import sys

packages = set()
for tool, package in {'git':'git', 'cmake':'cmake', 'c++':'build-essential',
                      'pkg-config':'pkg-config', 'zenity':'zenity', 'xdg-open':'xdg-utils', 'flock':'util-linux'}.items():
    if not shutil.which(tool): packages.add(package)
for library, package in {'x11':'libx11-dev', 'xrandr':'libxrandr-dev', 'xi':'libxi-dev',
                         'xcursor':'libxcursor-dev', 'xinerama':'libxinerama-dev', 'gl':'libgl1-mesa-dev'}.items():
    if not shutil.which('pkg-config') or subprocess.run(['pkg-config', '--exists', library]).returncode:
        packages.add(package)
if not packages: sys.exit(0)
print('The installer UI needs these packages: ' + ', '.join(sorted(packages)), flush=True)
print('They remain installed with the kit when simulator software is uninstalled.', flush=True)
subprocess.run(['sudo', 'apt-get', 'update'], check=True)
subprocess.run(['sudo', 'apt-get', 'install', '-y', '--no-install-recommends', '--no-remove', *sorted(packages)], check=True)
