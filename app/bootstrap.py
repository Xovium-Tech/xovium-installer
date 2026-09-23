#!/usr/bin/env python3
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

root = Path(__file__).resolve().parent.parent
deps = root / '.build/deps'
for name, spec in json.loads((root / 'app/dependencies.json').read_text()).items():
    target = deps / name
    stamp = target / '.xovium-commit'
    if stamp.is_file() and stamp.read_text().strip() == spec['commit']:
        continue
    deps.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get('XOVIUM_IPA_LIBS', Path.home() / 'IPA/AI/lib')) / name
    with tempfile.TemporaryDirectory(dir=deps, prefix=name + '-') as temp:
        temp = Path(temp)
        archive = temp / 'source.tar'
        if not source.is_dir() or subprocess.run(['git', '-C', str(source), 'cat-file', '-e', spec['commit']], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            source = temp / 'git'
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'fetch', '--depth=1', spec['repository'], spec['commit']], check=True)
        print(f"GUI dependency: {name} {spec['tag']} ({spec['commit']})", flush=True)
        with archive.open('wb') as stream:
            subprocess.run(['git', '-C', str(source), 'archive', spec['commit']], stdout=stream, check=True)
        extracted = temp / 'extracted'
        extracted.mkdir()
        with tarfile.open(archive) as stream:
            stream.extractall(extracted, filter='data')
        (extracted / '.xovium-commit').write_text(spec['commit'] + '\n')
        if target.exists():
            shutil.rmtree(target)
        extracted.rename(target)
