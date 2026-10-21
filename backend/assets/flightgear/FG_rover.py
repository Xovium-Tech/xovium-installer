#!/usr/bin/env python3
"""Render the shared rover using FlightGear's external FDM protocol.

run_rover.py owns the physics, PX4 transport and process lifecycle. This process
execs FlightGear so stopping the session also stops its renderer.
"""
import os
from pathlib import Path
import shlex
import xml.etree.ElementTree as ET


def command():
    raw = os.environ.get("PX4_INSTANCE", "0")
    if not raw.isascii() or not raw.isdecimal() or str(int(raw)) != raw or not 0 <= int(raw) <= 254:
        raise ValueError("PX4_INSTANCE must be an integer from 0 to 254")
    instance = int(raw)
    aircraft = Path(os.environ["XOVIUM_FG_AIRCRAFT"]).resolve(strict=True)
    if not aircraft.name.endswith("-set.xml"):
        raise ValueError("FlightGear rover requires a generated -set.xml aircraft descriptor")
    ET.parse(aircraft)
    # Each session gets a private Protocol directory, including parallel PX4s.
    runtime = Path(os.environ.get("FG_HOME", Path(__file__).resolve().parent / ".runtime")) / f"rover-{instance}"
    protocols = runtime / "Protocol"
    protocols.mkdir(parents=True, exist_ok=True)
    tree = ET.Element("PropertyList")
    input_node = ET.SubElement(ET.SubElement(tree, "generic"), "input")
    for tag, text in (("binary_mode", "false"), ("var_separator", ","), ("line_separator", "newline")):
        ET.SubElement(input_node, tag).text = text
    for name, node in (("latitude", "/position/latitude-deg"), ("longitude", "/position/longitude-deg"),
                       ("altitude", "/position/altitude-ft"), ("roll", "/orientation/roll-deg"),
                       ("pitch", "/orientation/pitch-deg"), ("heading", "/orientation/heading-deg"),
                       ("north", "/velocities/speed-north-fps"), ("east", "/velocities/speed-east-fps"),
                       ("down", "/velocities/speed-down-fps"), ("time", "/sim/time/elapsed-sec")):
        chunk = ET.SubElement(input_node, "chunk")
        for tag, text in (("name", name), ("type", "double"), ("node", node)):
            ET.SubElement(chunk, tag).text = text
    ET.ElementTree(tree).write(protocols / "XoviumRover.xml", encoding="utf-8", xml_declaration=True)
    env = os.environ
    result = [env.get("FG_BINARY", "fgfs"), "--data=" + str(runtime),
        "--aircraft=" + aircraft.name.removesuffix("-set.xml"), "--fg-aircraft=" + str(aircraft.parent.parent),
        "--fdm=null", f"--generic=socket,in,50,127.0.0.1,{15500 + instance},udp,XoviumRover",
        "--lat=" + env.get("SIM_LAT", env.get("PX4_HOME_LAT", "47.397742")),
        "--lon=" + env.get("SIM_LON", env.get("PX4_HOME_LON", "8.545594")),
        "--altitude=" + str(float(env.get("SIM_ALT", env.get("PX4_HOME_ALT", "489.4"))) / 0.3048),
        "--timeofday=noon", "--disable-sound", "--disable-ai-traffic", "--disable-real-weather-fetch"]
    if env.get("FG_SCENERY"):
        result.append("--fg-scenery=" + env["FG_SCENERY"])
    result.extend(shlex.split(env.get("FG_ARGS_EX", "")))
    result.extend(shlex.split(env.get("FG_ARGS_ADD", "")))
    return result


if __name__ == "__main__":
    from FG_scene import run
    args = command()
    print(shlex.join(args), flush=True)
    raise SystemExit(run(args))
