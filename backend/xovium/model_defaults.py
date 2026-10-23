"""Sensor-only defaults for the shared Cessna; never replace authored sensors."""
import hashlib
import json
import math
import uuid


def plane_cameras():
    return [dict(id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'https://xovium.com/models/cessna/' + name)),
                 name=name, type='camera', parent='base_link', enabled=True,
                 pose=pose, rate_hz=30.0, topic='',
                 parameters=dict(width=1280, height=960, horizontal_fov=1.74, near=near, far=1000000.0))
            for name, pose, near in (
                ('camera_front', [0.85, 0.0, 0.15, 0.0, 0.0, 0.0], 0.1),
                # Below the 8 cm fuselage underside, above the 14.5 cm wheel
                # contact plane. A 1 cm near clip keeps parked ground visible.
                ('camera_down', [0.0, 0.0, -0.10, 0.0, math.pi / 2, 0.0], 0.01))]


def plane_defaults_signature():
    return hashlib.sha256(json.dumps(plane_cameras(), sort_keys=True).encode()).hexdigest()


def add_plane_cameras(document):
    """Apply to a fresh/pristine seed only; return the names added.

    Both sensors attach to the existing body without adding links, mass,
    collision geometry or visual components. Named authored sensors win.
    """
    if not any(link.get('name') == 'base_link' for link in document.get('links', [])):
        raise ValueError('The default Cessna camera mounts require base_link')
    sensors = document.setdefault('sensors', [])
    names = {sensor['name'] for sensor in sensors}
    additions = [sensor for sensor in plane_cameras() if sensor['name'] not in names]
    sensors.extend(additions)
    return [sensor['name'] for sensor in additions]


def upgrade_pristine_plane_cameras(document):
    """Upgrade only exact former defaults inside an ownership-verified seed.

    Authored settings, including a deliberate far-plane change, remain intact.
    The caller must first verify that the complete bundle is still unedited.
    """
    changed = []
    for expected in plane_cameras():
        legacy = dict(expected, parameters=dict(expected['parameters'], far=6000.0))
        for sensor in document.get('sensors', []):
            if sensor == legacy:
                sensor['parameters']['far'] = expected['parameters']['far']
                changed.append(sensor['name'])
    return changed
