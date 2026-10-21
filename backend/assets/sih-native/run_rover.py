#!/usr/bin/env python3
"""Own the complete lightweight/FlightGear rover session and its child processes."""
import argparse
import fcntl
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from prepare_rover import prepare


def instance_number(value):
    if not value.isascii() or not value.isdecimal() or str(int(value)) != value or not 0 <= int(value) <= 254:
        raise ValueError("PX4_INSTANCE must be an integer from 0 to 254")
    return int(value)


def main():
    parser = argparse.ArgumentParser(description="PX4 rover with native ground physics and optional FlightGear rendering")
    parser.add_argument("--flightgear", action="store_true")
    parser.add_argument("px4_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.px4_args[:1] == ["--"]:
        args.px4_args.pop(0)
    bridge = Path(__file__).resolve().parent
    instance = instance_number(os.environ.get("PX4_INSTANCE", "0"))
    px4 = Path(os.environ.get("PX4_DIR", bridge.parents[2] / "px4/source"))
    build = Path(os.environ.get("PX4_BUILD", px4 / "build/px4_sitl_default")).resolve()
    executable = build / "bin/px4"
    if not executable.is_file() or not (build / "etc/init.d-posix/airframes/4009_gz_r1_rover").is_file():
        raise ValueError("PX4 build with the 4009_gz_r1_rover airframe is required; repair PX4 in the installer")
    native = bridge / "build/xovium_rover"
    config = Path(os.environ.get("XOVIUM_ROVER_CONFIG", bridge / "rover.json")).resolve(strict=True)
    if not native.is_file():
        raise ValueError("Native rover bridge is missing; repair the simulator in the installer")
    backend = "flightgear" if args.flightgear else "sih"
    runtime = Path(os.environ.get("PX4_RUNTIME", bridge / ".runtime" / f"{backend}-rover-{instance}"))
    runtime.mkdir(parents=True, exist_ok=True)
    runtime = runtime.resolve()
    with (runtime / ".launcher.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        router = os.environ.get("PX4_MAVLINK_ROUTER", "0")
        if router not in ("0", "1"):
            raise ValueError("PX4_MAVLINK_ROUTER must be 0 or 1")
        prepare(build / "etc/init.d-posix", runtime, instance, router == "1",
                int(os.environ.get("XOVIUM_ROUTER_PX4_PORT", "14550")))
        env = dict(os.environ, PX4_SIM_MODEL="gz_r1_rover", PX4_SYS_AUTOSTART="4009", PX4_SIMULATOR="xovium_rover",
                   PX4_SIM_SPEED_FACTOR="1", XOVIUM_ROVER_AIRFRAME=str(bridge / "px4_rover_airframe.sh"),
                   PX4_MAVLINK_STARTUP=str(runtime / "px4-rc.mavlink"))
        env.pop("PX4_SIM_HOSTNAME", None)
        env["PX4_SIM_HOST_ADDR"] = "127.0.0.1"
        origin = [float(env.get(f"SIM_{key}", env.get(f"PX4_HOME_{key}", default)))
                  for key, default in (("LAT", "47.397742"), ("LON", "8.545594"), ("ALT", "489.4"))]
        if not all(math.isfinite(v) for v in origin):
            raise ValueError("Simulation geographic origin must be finite")
        native_args = [str(native), "--config", str(config), "--instance", str(instance), "--origin", *map(str, origin)]
        for variable, option in (("XOVIUM_HEIGHTFIELD", "--heightfield"), ("XOVIUM_TERRAIN_CONFIG", "--terrain-config")):
            if env.get(variable):
                native_args += [option, str(Path(env[variable]).resolve(strict=True))]
        if env.get("XOVIUM_WORLD") in ("baylands", "dynamic", "dynamic_terrain") and not any(env.get(v) for v in ("XOVIUM_HEIGHTFIELD", "XOVIUM_TERRAIN_CONFIG")):
            raise ValueError("The selected shared world has no collision terrain; regenerate its native conversion")
        if env.get("XOVIUM_ROVER_STATE_FILE"):
            native_args += ["--state-file", env["XOVIUM_ROVER_STATE_FILE"]]
        if env.get("XOVIUM_TERRAIN_CONFIG"):
            env["XOVIUM_TERRAIN_EXPORT"] = str(runtime / "terrain")
            native_args += ["--terrain-export", env["XOVIUM_TERRAIN_EXPORT"]]
        children = []
        interrupted = False

        def stop(*_):
            nonlocal interrupted
            interrupted = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            if args.flightgear:
                fg_script = Path(env.get("XOVIUM_FG_ROVER_LAUNCHER", bridge.parents[1] / "flightgear/bridge/FG_rover.py")).resolve(strict=True)
                native_args += ["--fg-port", str(15500 + instance)]
                children.append(subprocess.Popen([sys.executable, str(fg_script)], env=env))
            print(f"Starting {backend} rover: differential-drive ground physics; PX4 controller with reversible motors.", flush=True)
            simulator = subprocess.Popen(native_args, env=env)
            children.append(simulator)
            deadline = time.monotonic() + 125
            # Do not probe by connecting: the MAVLink TCP server has one client.
            # /proc reports readiness without consuming PX4's simulation socket.
            port = f"{4560 + instance:04X}"
            while not interrupted:
                if simulator.poll() is not None:
                    return simulator.returncode or 1
                listening = any(line.split()[1].endswith(":" + port) and line.split()[3] == "0A"
                                for line in Path("/proc/net/tcp").read_text().splitlines()[1:])
                if listening:
                    break
                if time.monotonic() > deadline:
                    raise RuntimeError("Rover simulator did not start its PX4 transport within 125 seconds")
                time.sleep(0.1)
            if interrupted:
                return 130
            children.append(subprocess.Popen([str(executable), str(build / "etc"), "-i", str(instance),
                "-w", str(runtime), "-s", str(runtime / "rcS-rover"), *args.px4_args], env=env))
            while not interrupted:
                for child in children:
                    if child.poll() is not None:
                        return child.returncode
                time.sleep(0.1)
            return 130
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Cannot start rover simulation: {error}", file=sys.stderr)
        sys.exit(1)
