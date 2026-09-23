import math
import time
from dataclasses import dataclass


def normalize_quaternion(q):
    values = tuple(float(value) for value in q)
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise ValueError("Quaternion must contain four finite values")
    length = math.hypot(*values)
    if not math.isfinite(length) or length < 1e-12:
        raise ValueError("Quaternion must have nonzero length")
    return tuple(value / length for value in values)


def quaternion_product(a, b):
    w, x, y, z = a
    r, i, j, k = b
    return (
        w * r - x * i - y * j - z * k,
        w * i + x * r + y * k - z * j,
        w * j - x * k + y * r + z * i,
        w * k + x * j - y * i + z * r,
    )


def ned_frd_to_enu_flu(q_wxyz):
    world = (0.0, math.sqrt(0.5), math.sqrt(0.5), 0.0)
    body = (0.0, 1.0, 0.0, 0.0)
    result = quaternion_product(quaternion_product(world, normalize_quaternion(q_wxyz)), body)
    result = normalize_quaternion(result)
    return tuple(-value for value in result) if result[0] < 0 else result


def validate_geodetic(latitude, longitude, altitude):
    if not all(math.isfinite(value) for value in (latitude, longitude, altitude)):
        raise ValueError("Position must contain finite values")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Latitude or longitude outside valid range")


def geodetic_to_enu(lat_deg, lon_deg, alt_m, origin):
    validate_geodetic(lat_deg, lon_deg, alt_m)
    validate_geodetic(*origin)

    def ecef(latitude, longitude, altitude):
        lat, lon = math.radians(latitude), math.radians(longitude)
        eccentricity_squared = 6.6943799901413165e-3
        radius = 6378137.0 / math.sqrt(1 - eccentricity_squared * math.sin(lat) ** 2)
        return (
            (radius + altitude) * math.cos(lat) * math.cos(lon),
            (radius + altitude) * math.cos(lat) * math.sin(lon),
            (radius * (1 - eccentricity_squared) + altitude) * math.sin(lat),
        )

    x, y, z = (a - b for a, b in zip(ecef(lat_deg, lon_deg, alt_m), ecef(*origin)))
    lat, lon = math.radians(origin[0]), math.radians(origin[1])
    return (
        -math.sin(lon) * x + math.cos(lon) * y,
        -math.sin(lat) * math.cos(lon) * x - math.sin(lat) * math.sin(lon) * y + math.cos(lat) * z,
        math.cos(lat) * math.cos(lon) * x + math.cos(lat) * math.sin(lon) * y + math.sin(lat) * z,
    )


@dataclass(frozen=True)
class Pose:
    position: tuple
    orientation: tuple
    received_at: float


class PoseTracker:
    def __init__(self, origin=None):
        if origin is not None:
            validate_geodetic(*origin)
        self.origin = tuple(origin) if origin is not None else None
        self.latest = None

    def update(self, message, received_at=None):
        if message.get_type() != "HIL_STATE_QUATERNION":
            return False
        try:
            latitude = float(message.lat) * 1e-7
            longitude = float(message.lon) * 1e-7
            altitude = float(message.alt) * 1e-3
            validate_geodetic(latitude, longitude, altitude)
            orientation = ned_frd_to_enu_flu(message.attitude_quaternion)
            origin = self.origin if self.origin is not None else (latitude, longitude, altitude)
            position = geodetic_to_enu(latitude, longitude, altitude, origin)
            received_at = time.monotonic() if received_at is None else float(received_at)
            if not math.isfinite(received_at):
                return False
        except (TypeError, ValueError, AttributeError, OverflowError):
            return False
        self.origin = origin
        self.latest = Pose(position, orientation, received_at)
        return True

    def stale(self, timeout, now=None):
        now = time.monotonic() if now is None else now
        return self.latest is None or now - self.latest.received_at > timeout
