"""Generate a rover-specific PX4 runtime without modifying the PX4 checkout."""
import socket
from pathlib import Path


def replace_once(text, anchor, replacement):
    lines = text.splitlines()
    if lines.count(anchor) != 1:
        raise ValueError(f"Unsupported PX4 startup layout: expected exactly one {anchor!r}")
    return "\n".join(replacement if line == anchor else line for line in lines) + "\n"


def prepare(source, runtime, instance, router=False, router_port=14550, check_ports=True):
    source, runtime = Path(source), Path(runtime)
    if not 0 <= instance <= 254 or not 1024 <= router_port <= 65535:
        raise ValueError("Invalid PX4 instance or router port")
    if check_ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            connection.bind(("127.0.0.1", 4560 + instance))
        ports = [18570, 14280, 13030, 19450] + ([] if router else [14580])
        for base in ports:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
                connection.bind(("0.0.0.0", base + instance))
    startup = (source / "rcS").read_text()
    startup = replace_once(startup, '\t. "$autostart_file"',
                           '\t. "$autostart_file"\n\t. "$XOVIUM_ROVER_AIRFRAME"')
    startup = replace_once(startup, '. px4-rc.mavlink', '. "$PX4_MAVLINK_STARTUP"')
    dds = "uxrce_dds_client start -t udp -h 127.0.0.1 -p $uxrce_dds_port $uxrce_dds_ns"
    if dds not in startup.splitlines():
        dds = "uxrce_dds_client start -t udp -p $uxrce_dds_port $uxrce_dds_ns"
    startup = replace_once(startup, dds, "# Rover uses MAVLink; DDS is not required.")
    mavlink = (source / "px4-rc.mavlink").read_text()
    if router:
        mavlink = replace_once(mavlink,
            "mavlink start -x -u $udp_offboard_port_local -r 4000000 -f -m onboard -o $udp_offboard_port_remote",
            "# Offboard traffic is routed through mavlink-router.")
        mavlink = replace_once(mavlink, "mavlink start -x -u $udp_gcs_port_local -r 4000000 -f",
            f"mavlink start -x -u $udp_gcs_port_local -r 4000000 -f -o {router_port}")
    mavlink += (f"\nmavlink start -x -u {19450 + instance} -r 4000000 -f -m custom -o {19410 + instance}\n"
                f"mavlink stream -r 25 -s HIL_STATE_QUATERNION -u {19450 + instance}\n")
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "rcS-rover").write_text(startup)
    (runtime / "px4-rc.mavlink").write_text(mavlink)
