#!/usr/bin/env python3

import json
import sys
import os
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import shutil
import shlex
from pathlib import Path

import subprocess

exparameters = [
    "--enable-terrasync",
    "--timeofday=noon",
    "--disable-sound",
    "--disable-random-objects",
    "--prop:/sim/rendering/texture-compression=off",
    "--prop:/sim/rendering/quality-level=0",
    "--prop:/sim/rendering/shaders/quality-level=0",
    "--disable-ai-traffic",
    "--prop:/sim/ai/enabled=0",
    "--prop:/sim/rendering/random-vegetation=0",
    "--prop:/sim/rendering/random-buildings=0",
    "--disable-specular-highlight",
    "--disable-ai-models",
    "--disable-clouds",
    "--disable-clouds3d",
    "--fog-fastest",
    "--visibility=2000",
    "--disable-distance-attenuation",
    "--disable-real-weather-fetch",
    "--prop:/sim/rendering/particles=0",
    "--prop:/sim/rendering/multi-sample-buffers=1",
    "--prop:/sim/rendering/multi-samples=2",
    "--prop:/sim/rendering/draw-mask/clouds=false",
    "--prop:/sim/rendering/draw-mask/aircraft=true",
    "--prop:/sim/rendering/draw-mask/models=true",
    "--prop:/sim/rendering/draw-mask/terrain=true",
    "--prop:/sim/menubar/overlap-hide=true",
    "--prop:/sim/menubar/visibility=false",
    "--disable-random-vegetation",
    "--disable-random-buildings",
    "--disable-rembrandt",
    "--disable-horizon-effect"
]


if len(sys.argv)!=3:
    print('FG_run.py -- bad argument count')
    exit(-1)

filename=sys.argv[1]
px4id=int(sys.argv[2])

if not os.path.isfile(filename):
    print('FG_run.py -- file not found: '+filename)
    exit(-1)

fgbin=os.getenv("FG_BINARY")
if fgbin is None:
    fgbin='fgfs'

fgmodelsdir=os.getenv("FG_MODELS_DIR")
if fgmodelsdir is None:
    fgmodelsdir='./models'

fgargsex=os.getenv("FG_ARGS_EX")
print(fgargsex)
if fgargsex is None:
    fgargsex=" ".join(exparameters);

fgargsadd=os.getenv("FG_ARGS_ADD")
print(fgargsadd)
if fgargsadd is None:
    fgargsadd="";

data_home = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share').expanduser()
if not data_home.is_absolute():
    data_home = Path.home() / '.local/share'
flightgear_data = data_home / 'gt-flightgear' / ('instance-' + str(px4id))
protocols = flightgear_data / 'Protocol'
protocols.mkdir(parents=True, exist_ok=True)


with open(filename) as json_file:
    data = json.load(json_file)
    model=data['FgModel']
    url=data['Url']
    controls=data['Controls']

print(model)
print(url)
for c in controls:
    print(c[0]+' '+c[1]+' '+c[2])

propertyList=ET.Element('PropertyList')
generic= ET.SubElement(propertyList, 'generic')
input=ET.SubElement(generic,'input')

binary_mode=ET.SubElement(input,'binary_mode')
binary_mode.text='true'

for c in controls:
    chunk=ET.SubElement(input,'chunk')
    name=ET.SubElement(chunk,'name')
    name.text=c[0]
    type=ET.SubElement(chunk,'type')
    type.text='double'
    node=ET.SubElement(chunk,'node')
    node.text=c[1]

rough_string = ET.tostring(propertyList, 'utf-8')
reparsed = minidom.parseString(rough_string)
xmlstring=reparsed.toprettyxml(indent="  ")

with open(protocols / 'PX4toFG.xml', 'w') as xml_file:
    xml_file.write(xmlstring)

shutil.copy('px4bridge.xml', protocols / 'FGtoPX4.xml')


baseparameters = [
    "--data=" + str(flightgear_data),
    "--aircraft=" + model,
    "--fg-aircraft=" + fgmodelsdir,
    "--telnet=" + str(15400 + px4id),
    "--timeofday=noon",
    "--generic=socket,out,100,127.0.0.1," + str(15200 + px4id) + ",udp,FGtoPX4",
    "--generic=socket,in,100,," + str(15300 + px4id) + ",udp,PX4toFG",
    "--model-hz=100",
    "--enable-random-objects",
    "--enable-ai-traffic",
    "--enable-clouds",
    "--enable-clouds3d",
    "--enable-distance-attenuation",
    "--enable-real-weather-fetch",
    "--enable-random-vegetation",
    "--enable-random-buildings",
    "--enable-horizon-effect",
    "--enable-ai-models",
    "--enable-specular-highlight",
    "--fog-nicest",
    "--visibility=5000",
    "--prop:/sim/ai/enabled=1",
    "--prop:/sim/rendering/texture-compression=on",
    "--prop:/sim/rendering/quality-level=5",
    "--prop:/sim/rendering/shaders/quality-level=5",
    "--prop:/sim/rendering/particles=1",
    "--prop:/sim/rendering/multi-sample-buffers=1",
    "--prop:/sim/rendering/multi-samples=4",
    "--prop:/sim/rendering/draw-mask/clouds=true",
    "--prop:/sim/rendering/draw-mask/aircraft=true",
    "--prop:/sim/rendering/draw-mask/models=true",
    "--prop:/sim/rendering/draw-mask/terrain=true",
    "--prop:/sim/rendering/random-vegetation=1",
    "--prop:/sim/rendering/random-buildings=1",
    "--prop:/sim/rendering/trees=true",
    "--prop:/sim/rendering/powerlines=true",
    "--prop:/sim/rendering/roads=true",
    "--prop:/sim/menubar/visibility=false",
    "--prop:/sim/rendering/materials-file=Materials/dds/materials.xml",
    "--prop:/sim/rendering/maximum-scenery-distance=500000"
]

command = [fgbin] + baseparameters + shlex.split(fgargsex) + shlex.split(fgargsadd)
print(shlex.join(command))
process = subprocess.Popen(command)
with open('/tmp/px4fgfspid_' + str(px4id), 'w') as pid_file:
    pid_file.write(str(process.pid) + '\n')
