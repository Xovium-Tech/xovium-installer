#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import re
import tempfile

NAME = 'Xovium MAVLink Router'


def values(text, group):
    result = {}
    current = ''
    for line in text.splitlines():
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1]
        elif current == group and '=' in line and not line.lstrip().startswith(('#', ';')):
            key, value = line.split('=', 1)
            if key.strip() in result:
                raise ValueError(f'Duplicate QGC setting: {group}/{key.strip()}')
            result[key.strip()] = value.strip()
    return result


def update_group(text, group, changes):
    lines = text.splitlines(keepends=True)
    markers = [i for i, line in enumerate(lines) if line.rstrip('\r\n') == '[' + group + ']']
    if len(markers) > 1:
        raise ValueError(f'Duplicate QGC settings group: {group}')
    newline = '\r\n' if '\r\n' in text else '\n'
    if not markers:
        separator = '' if not text or text.endswith(('\n', '\r')) else newline
        return text + separator + newline + '[' + group + ']' + newline + ''.join(k+'='+v+newline for k,v in changes.items())
    start = markers[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('[')), len(lines))
    changes = dict(changes)
    for i in range(start, end):
        key = lines[i].split('=', 1)[0].strip() if '=' in lines[i] else None
        if key in changes:
            lines[i] = key + '=' + changes.pop(key) + newline
    if end > start and lines[end-1] and not lines[end-1].endswith(('\n', '\r')):
        lines[end-1] += newline
    lines[end:end] = [key+'='+value+newline for key,value in changes.items()]
    return ''.join(lines)


def configured_text(text, router, port=14552):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Router QGC port must be 1024..65535.')
    links = values(text, 'LinkConfigurations')
    count = int(links.get('count', '0'))
    if not 0 <= count <= 1000:
        raise ValueError('Invalid QGC communication link count.')
    indices = {int(m[1]) for key in links if (m := re.fullmatch(r'Link(\d+)\\.*', key))}
    count = max([count] + [i+1 for i in indices])
    named = [i for i in range(count) if links.get(f'Link{i}\\name') == NAME]
    if len(named) > 1:
        raise ValueError('More than one Xovium router link exists; remove the duplicate in QGC.')
    same_port = [i for i in range(count) if links.get(f'Link{i}\\type') == '1' and links.get(f'Link{i}\\port') == str(port)]
    if named:
        index = named[0]
        if any(i != index and links.get(f'Link{i}\\auto') == 'true' for i in same_port):
            raise ValueError(f'Another QGC automatic link already uses UDP {port}.')
    elif same_port:
        if len(same_port) > 1 or links.get(f'Link{same_port[0]}\\hostCount', '0') != '0':
            raise ValueError(f'QGC has custom links on UDP {port}; keep one receive-only link for the router.')
        index = same_port[0]
    else:
        index = count
        count += 1
    changes = {f'Link{index}\\name': NAME, f'Link{index}\\type': '1',
               f'Link{index}\\auto': 'true' if router else 'false', f'Link{index}\\high_latency': 'false',
               f'Link{index}\\port': str(port), f'Link{index}\\hostCount': '0', 'count': str(count)}
    text = update_group(text, 'LinkConfigurations', changes)
    routing = {'autoConnectUDP': 'false' if router else 'true', 'udpListenPort': '14550'}
    text = update_group(text, 'AutoConnect', routing)
    if '[LinkManager]' in text.splitlines():
        text = update_group(text, 'LinkManager', routing)
    return text


def running_qgc(proc=Path('/proc')):
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid == os.getuid() and (entry / 'comm').read_text().strip() == 'QGroundControl':
                if ') Z ' not in (entry / 'stat').read_text():
                    return int(entry.name)
        except (OSError, ValueError):
            continue
    return None


def configure(path, router, port=14552):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('QGC settings must not be a symlink.')
    old = path.read_bytes() if path.exists() else b''
    new = configured_text(old.decode('utf-8'), router, port).encode('utf-8')
    if new == old:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.xovium-qgc-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(new)
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--router', choices=('0', '1'), required=True)
    parser.add_argument('--port', type=int, default=14552)
    options = parser.parse_args()
    try:
        if running_qgc():
            print('QGC is already running; automatic link settings will be applied on its next launch.')
        else:
            configure(options.path, options.router == '1', options.port)
    except (OSError, ValueError) as exc:
        parser.exit(1, f'QGC communication setup: {exc}\n')
