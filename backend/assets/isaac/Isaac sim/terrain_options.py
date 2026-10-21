#!/usr/bin/env python3
"""Translate the installed shared terrain manifest into native Kit arguments."""

import json
import math
from pathlib import Path
import sys


def options(path):
    path = Path(path).resolve(strict=True)
    data = json.loads(path.read_text())
    if data.get('schema') != 1:
        raise ValueError('Unsupported shared terrain schema')
    origin = data['origin']
    lat, lon, alt = (origin[key] for key in ('latitude', 'longitude', 'altitude'))
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon, alt)):
        raise ValueError('Terrain origin must be finite')
    if not -85 <= lat <= 85 or not -180 <= lon <= 180 or not -1000 <= alt <= 20000:
        raise ValueError('Terrain origin is outside supported geographic bounds')
    zoom, radius = data['zoom'], data['radius']
    if type(zoom) is not int or not 1 <= zoom <= 22 or type(radius) is not int or not 0 <= radius <= 8:
        raise ValueError('Invalid terrain zoom/radius')
    cache = (path.parent / data['cache']).resolve()
    result = {
        '/px4/terrain/enabled': 'true', '/px4/terrain/source': 'xyz',
        '/px4/terrain/cacheDirectory': cache, '/px4/terrain/zoom': zoom,
        '/px4/terrain/radius': radius, '/px4/terrain/imageryExtension': data.get('imagery_extension', 'png'),
        '/px4/terrain/imageryUrl': data['imagery_url'], '/px4/terrain/elevationUrl': data['elevation_url'],
    }
    for key, value in (('Latitude', lat), ('Longitude', lon), ('Altitude', alt)):
        result['/px4/terrain/origin' + key] = value
        result['/px4/flight/' + key.lower()] = value
    for key, value in result.items():
        value = str(value)
        if any(c in value for c in '\r\n\0'):
            raise ValueError('Invalid terrain argument')
        if key.endswith('Url') and (not value.startswith('https://') or not all('{' + c + '}' in value for c in ('x', 'y', 'z'))):
            raise ValueError('Terrain URLs must use HTTPS and {x}/{y}/{z} placeholders')
        yield '--' + key + '=' + value


if __name__ == '__main__':
    try:
        print('\n'.join(options(sys.argv[1])))
    except (KeyError, ValueError, TypeError, OSError, IndexError) as error:
        sys.exit(f'Cannot configure Isaac terrain: {error}')
