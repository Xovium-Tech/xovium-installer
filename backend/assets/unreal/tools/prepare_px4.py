#!/usr/bin/env python3

import argparse
import errno
import os
from pathlib import Path
import socket
import sys
import tempfile


AIRFRAME_ANCHOR = '\t. "$autostart_file"'
MAVLINK_ANCHOR = '. px4-rc.mavlink'
DDS_ANCHOR = 'uxrce_dds_client start -t udp -h 127.0.0.1 -p $uxrce_dds_port $uxrce_dds_ns'
OFFBOARD_ANCHOR = 'mavlink start -x -u $udp_offboard_port_local -r 4000000 -f -m onboard -o $udp_offboard_port_remote'


def instance_number(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value or not 0 <= int(value) <= 254:
        raise argparse.ArgumentTypeError("instance must be an integer from 0 to 254")
    return int(value)


def replace_line(text: str, anchor: str, replacement: str, label: str) -> str:
    lines = text.splitlines()
    if lines.count(anchor) != 1:
        raise ValueError(f"Unsupported PX4 startup layout in {label}: expected exactly one {anchor!r}. Use PX4 v1.16.")
    return "\n".join(replacement if line == anchor else line for line in lines) + "\n"


def startup_text(source: Path, router: bool = False) -> tuple[str, str]:
    startup = (source / "rcS").read_text()
    mavlink = (source / "px4-rc.mavlink").read_text()
    startup = replace_line(startup, AIRFRAME_ANCHOR,
                           AIRFRAME_ANCHOR + '\n\t. "$PX4_UNREAL_AIRFRAME"', "rcS")
    startup = replace_line(startup, MAVLINK_ANCHOR, '. "$PX4_MAVLINK_STARTUP"', "rcS")
    startup = replace_line(startup, DDS_ANCHOR, '# DDS client omitted: this simulator uses MAVLink.', "rcS")
    mavlink = replace_line(mavlink, OFFBOARD_ANCHOR,
                           '# The external MAVLink router owns the offboard connection.' if router else OFFBOARD_ANCHOR,
                           "px4-rc.mavlink")
    return startup, mavlink


def check_udp_ports(instance: int, router: bool = False) -> None:
    ports = [("GCS", 18570), ("camera", 14280), ("gimbal", 13030)]
    if not router:
        ports.append(("offboard", 14580))
    conflicts = []
    for label, base in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind(("0.0.0.0", base + instance))
            except OSError as error:
                if error.errno != errno.EADDRINUSE:
                    raise
                conflicts.append(f"{label}: UDP {base + instance}")
    if conflicts:
        raise ValueError("PX4 ports already in use: " + ", ".join(conflicts)
                         + ". Stop the previous instance or choose a different PX4_INSTANCE for BOTH applications."
                         + (" Set PX4_MAVLINK_ROUTER=1 only if your router already forwards PX4 UDP 14550."
                            if not router else ""))


def atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare(source: Path, runtime: Path, instance: int, router: bool = False,
            check_ports: bool = True) -> None:
    if not 0 <= instance <= 254:
        raise ValueError("instance must be between 0 and 254")
    startup, mavlink = startup_text(source, router)
    if check_ports:
        check_udp_ports(instance, router)
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_write(runtime / "rcS-unreal", startup)
    atomic_write(runtime / "px4-rc.mavlink", mavlink)


def main() -> int:
    parser = argparse.ArgumentParser(description='Generate an isolated PX4 v1.16 startup without modifying the PX4 checkout.')
    parser.add_argument("--source", type=Path, required=True, help="PX4 build etc/init.d-posix directory")
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--instance", type=instance_number, default=0)
    parser.add_argument("--router", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.source, args.runtime, args.instance, args.router)
    except (OSError, ValueError) as error:
        print(f"Cannot prepare PX4: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
