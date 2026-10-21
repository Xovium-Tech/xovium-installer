#!/usr/bin/env python3
"""Attach canonical world geometry to FlightGear's documented model manager.

Baylands loads once from an AC3D export. Dynamic terrain loads the exact meshes
and imagery emitted by the native Terrarium streamer, including for the rover's
ground-contact physics. Shared worlds use a private native scenery directory.
"""
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def parent_death_signal():
    # The upstream sitl_run.sh kills its stored FG PID. Keep children tied to
    # this supervisor even when that legacy cleanup uses SIGKILL.
    ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM)


def geographic_origin():
    env = os.environ
    values = {name: float(env.get("SIM_" + short, env.get("PX4_HOME_" + short, default)))
              for name, short, default in (("latitude", "LAT", "47.397742"),
                  ("longitude", "LON", "8.545594"), ("altitude", "ALT", "489.4"))}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("FlightGear scene origin must be finite")
    return values


def model_properties(path, origin):
    return {"name": "Xovium shared terrain", "path": str(path),
            "enable-hot": "true",
            "latitude-deg": str(origin["latitude"]), "longitude-deg": str(origin["longitude"]),
            "elevation-ft": str(origin["altitude"] / 0.3048),
            # FlightGear model coordinates are aft/right/up. Heading 270 maps
            # canonical ENU terrain X/Y to east/north after AC3D import.
            "heading-deg": "270", "pitch-deg": "0", "roll-deg": "0"}


def static_scene(path, destination, origin):
    path = Path(path).resolve(strict=True)
    # The converter's native XML preserves per-object collision metadata (for
    # example decorative water must not become a solid YASim landing surface).
    wrapper = path.with_suffix(".xml")
    if path.suffix == ".ac" and wrapper.is_file():
        path = wrapper
    tree = ET.Element("PropertyList")
    model = ET.SubElement(ET.SubElement(tree, "models"), "model", n="9000")
    for key, value in model_properties(path, origin).items():
        ET.SubElement(model, key).text = value
    ET.ElementTree(tree).write(destination, encoding="utf-8", xml_declaration=True)


class TileLoader:
    def __init__(self, port, manifest):
        self.port, self.manifest = port, Path(manifest)
        self.connection = None
        self.loaded = {}
        self.next_index = 9100

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def update(self):
        if not self.manifest.is_file():
            return
        if self.connection is None:
            try:
                self.connection = socket.create_connection(("127.0.0.1", self.port), timeout=0.2)
            except (ConnectionRefusedError, TimeoutError, OSError):
                return
            self.connection.settimeout(0.5)
            self.connection.sendall(b"data\r\n")
            for index in self.loaded.values():
                self.connection.sendall(f"set /sim/xovium/terrain/unload-index {index}\r\n".encode())
            self.loaded.clear()
        doc = json.loads(self.manifest.read_text())
        if doc.get("schema") != 1:
            raise ValueError("Unsupported dynamic FlightGear scene schema")
        models = {item["key"]: item["path"] for item in doc["models"]}
        commands = []
        for key in list(self.loaded):
            if key not in models:
                # Model manager unloads the scene node when its group is removed.
                commands.append(f"set /sim/xovium/terrain/unload-index {self.loaded.pop(key)}")
        for key, path in models.items():
            if key in self.loaded:
                continue
            index = self.next_index
            self.next_index += 1
            for prop, value in model_properties(Path(path).resolve(strict=True), doc["origin"]).items():
                if "\r" in value or "\n" in value:
                    raise ValueError("Invalid newline in FlightGear model property")
                commands.append(f"set /models/model[{index}]/{prop} {value}")
            commands.append(f"set /models/model[{index}]/load true")
            self.loaded[key] = index
        try:
            if commands:
                self.connection.sendall(("\r\n".join(commands) + "\r\n").encode())
            # Drain telnet acknowledgements so FlightGear cannot block on output.
            self.connection.setblocking(False)
            while self.connection.recv(65536):
                pass
            self.close()
        except BlockingIOError:
            pass
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.close()
        finally:
            if self.connection:
                self.connection.settimeout(0.5)


def run(command):
    env = os.environ
    if env.get("XOVIUM_FG_AIRCRAFT"):
        aircraft = Path(env["XOVIUM_FG_AIRCRAFT"]).resolve(strict=True)
        if not aircraft.is_file() or not aircraft.name.endswith("-set.xml"):
            raise ValueError("Selected FlightGear model requires an aircraft -set.xml file")
        # Custom exports reuse the adapter's descriptor name. Pin the exact
        # directory so FlightGear cannot pick the built-in model with that name.
        command = [*command, "--prop:/sim/aircraft-dir=" + str(aircraft.parent)]
    instance = int(env.get("PX4_INSTANCE", "0"))
    world = env.get("XOVIUM_WORLD", "")
    if world == "dynamic":
        world = "dynamic_terrain"
    terrain = env.get("XOVIUM_TERRAIN_CONFIG")
    if not world and terrain:
        world = "dynamic_terrain"
    if world not in ("baylands", "dynamic_terrain"):
        os.execvp(command[0], command)
    root = Path(__file__).resolve().parent.parent
    runtime = Path(env.get("FG_HOME", root / "runtime")) / f"shared-world-{instance}"
    runtime.mkdir(parents=True, exist_ok=True)
    origin = geographic_origin()
    children = []
    loader = None
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        scenery = runtime / "Scenery"
        scenery.mkdir(exist_ok=True)
        # Keep the scenery pager enabled: FlightGear needs its base tiles before
        # starting even an external FDM. Shared mesh models supply the actual
        # local surface; an isolated directory excludes downloaded native land.
        command = [*command, "--fg-scenery=" + str(scenery),
                   "--terrasync-dir=" + str(scenery), "--disable-terrasync",
                   "--prop:/sim/sceneryloaded-override=true",
                   "--prop:/sim/presets/onground=false",
                   "--prop:/sim/rendering/draw-mask/terrain=true",
                   "--prop:/sim/rendering/draw-mask/models=true"]
        if world == "baylands":
            model = Path(env.get("XOVIUM_FG_WORLD_AC", root / "worlds/baylands/model.ac"))
            static_scene(model, runtime / "world.xml", origin)
            command += ["--config=" + str(runtime / "world.xml")]
        else:
            if not terrain:
                raise ValueError("Dynamic terrain requires XOVIUM_TERRAIN_CONFIG")
            telnet_port = 15600 + instance
            tree = ET.Element("PropertyList")
            script = ET.SubElement(ET.SubElement(ET.SubElement(tree, "nasal"), "xovium_terrain"), "script")
            script.text = '''
setlistener("/sim/xovium/terrain/unload-index", func(node) {
    var index = num(node.getValue());
    if (index != nil and index >= 9100) {
        var model = props.globals.getNode("/models/model[" ~ index ~ "]");
        if (model != nil) model.remove();
    }
}, 0, 0);
'''
            ET.ElementTree(tree).write(runtime / "streaming.xml", encoding="utf-8", xml_declaration=True)
            command += ["--config=" + str(runtime / "streaming.xml")]
            # The property manager is local to this simulator instance.
            command += [f"--telnet=socket,in,5,127.0.0.1,{telnet_port},tcp"]
            export = Path(env.get("XOVIUM_TERRAIN_EXPORT", runtime / "terrain"))
            if env.get("XOVIUM_VEHICLE") != "r1_rover":
                common = Path(env.get("XOVIUM_ROVER_BRIDGE", root.parent / "sih/bridge"))
                position_port = 15900 + instance
                protocols = runtime / "Protocol"
                protocols.mkdir(exist_ok=True)
                tree = ET.Element("PropertyList")
                output = ET.SubElement(ET.SubElement(tree, "generic"), "output")
                for tag, text in (("binary_mode", "false"), ("var_separator", ","), ("line_separator", "newline")):
                    ET.SubElement(output, tag).text = text
                for name in ("latitude", "longitude"):
                    chunk = ET.SubElement(output, "chunk")
                    for tag, text in (("name", name), ("type", "double"), ("format", "%.10f"), ("node", f"/position/{name}-deg")):
                        ET.SubElement(chunk, tag).text = text
                ET.ElementTree(tree).write(protocols / "XoviumPosition.xml", encoding="utf-8", xml_declaration=True)
                command += ["--data=" + str(runtime), f"--generic=socket,out,10,127.0.0.1,{position_port},udp,XoviumPosition"]
                children.append(subprocess.Popen([str(common / "build/xovium_rover"), "--terrain-only",
                    "--terrain-config", terrain, "--terrain-export", str(export), "--fg-position-port", str(position_port),
                    "--origin", *[str(origin[key]) for key in ("latitude", "longitude", "altitude")]], preexec_fn=parent_death_signal))
            loader = TileLoader(telnet_port, export / "scene.json")
        renderer = subprocess.Popen(command, preexec_fn=parent_death_signal)
        children.append(renderer)
        while running:
            for child in children:
                if child.poll() is not None:
                    return child.returncode
            if loader:
                loader.update()
            time.sleep(0.1)
        return 130
    finally:
        if loader:
            loader.close()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments.pop(0)
    if not arguments:
        raise SystemExit("FG_scene.py requires a FlightGear command")
    try:
        sys.exit(run(arguments))
    except (OSError, ValueError) as error:
        raise SystemExit(f"FlightGear shared world: {error}")
