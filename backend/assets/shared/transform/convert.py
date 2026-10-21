#!/usr/bin/env python3
"""Export the installed shared library into native simulator asset layouts.

Output is a staging directory. The installer installs it using its managed-file
ownership checks. Direct invocation never overwrites existing simulator assets.
"""
import argparse
import json
import re
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
from geometry import Reader
from formats import ac3d, heightfield, obj, usd

SIMULATORS=('gazebo-jetty','gazebo-harmonic','isaac','sih','unreal-engine','flightgear')


def copy(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    if source.is_dir(): shutil.copytree(source,target,dirs_exist_ok=True)
    else: shutil.copy2(source,target)


def local_world(shared):
    root=ET.parse(shared/'worlds/baylands/source/world.sdf').getroot()
    for uri in root.iter('uri'):
        if 'fuel.gazebosim.org' in (uri.text or ''):
            uri.text='model://coast_water' if 'Coast' in uri.text else 'model://baylands'
    # Correct the old nested relative_to tag into the SDF pose attribute.
    for pose in root.iter('pose'):
        relative=pose.find('relative_to')
        if relative is not None:
            pose.set('relative_to',relative.text.strip());pose.remove(relative)
    return root


def model_assets(shared,catalog):
    models=dict(catalog['models'])
    custom=shared/'models/custom/source/model.sdf'
    if custom.is_file():
        selection=json.loads((shared/'models/custom/selection.json').read_text())
        base=selection.get('model_base')
        if base not in ('rc_cessna','r1_rover'):
            raise ValueError('Custom model_base must be rc_cessna or r1_rover')
        models['custom']={'source':'models/custom/source/model.sdf','model_base':base}
    return models


def flightgear_model(shared,models,key,base,meshes):
    target=models/key
    ac3d(meshes,target/'model.ac',vehicle=True)
    templates=shared/'transform/templates/flightgear'/base
    descriptor='Cessna-set.xml' if base=='rc_cessna' else 'R1-set.xml'
    if not (templates/descriptor).is_file():
        raise FileNotFoundError('Missing FlightGear adapter descriptor: '+str(templates/descriptor))
    for file in templates.glob('*'): copy(file,target/file.name)
    if key!=base:
        root=ET.parse(target/descriptor).getroot()
        path=root.find('sim/model/path')
        if path is None: raise ValueError('FlightGear adapter has no model path: '+descriptor)
        path.text='Aircraft/'+key+'/model.ac'
        description=root.find('sim/description')
        if description is not None: description.text='Xovium custom model ('+base+' dynamics adapter)'
        ET.indent(root,space='  ')
        ET.ElementTree(root).write(target/descriptor,encoding='utf-8',xml_declaration=True)


def export(shared,simulator,output):
    shared=Path(shared).resolve();output=Path(output).resolve()
    if simulator not in SIMULATORS: raise ValueError('Unknown simulator: '+simulator)
    if output==shared or shared.is_relative_to(output): raise ValueError('Choose a dedicated export directory')
    output.mkdir(parents=True,exist_ok=True)
    models=output/'models';worlds=output/'worlds';models.mkdir(exist_ok=True);worlds.mkdir(exist_ok=True)
    catalog=json.loads((shared/'catalog.json').read_text())
    assets=model_assets(shared,catalog)
    # Validate the custom common source even for Gazebo, where the native SDF
    # is copied instead of converted. Unsupported structures must not silently
    # disappear when the same selection is used by another simulator.
    reader=Reader(shared)
    custom_meshes=reader.model(shared/assets['custom']['source']) if 'custom' in assets else None
    if simulator.startswith('gazebo-'):
        for key,asset in assets.items():
            copy((shared/asset['source']).parent,models/key)
            if key=='custom' and not (models/key/'model.config').is_file():
                sdf=ET.parse(shared/asset['source']).getroot()
                config=ET.Element('model')
                ET.SubElement(config,'name').text=sdf.find('model').get('name','custom')
                ET.SubElement(config,'version').text='1.0'
                ET.SubElement(config,'sdf',version=sdf.get('version','1.7')).text='model.sdf'
                ET.indent(config,space='  ')
                ET.ElementTree(config).write(models/key/'model.config',encoding='utf-8',xml_declaration=True)
        for name in ('baylands','coast_water'):
            copy(shared/'worlds/baylands/source/models'/name,models/name)
        world=local_world(shared);ET.indent(world,space='  ')
        ET.ElementTree(world).write(worlds/'baylands.sdf',encoding='utf-8',xml_declaration=True)
        copy(shared/'worlds/dynamic_terrain/source/world.sdf',worlds/'dynamic_terrain.sdf')
    else:
        for key,asset in assets.items():
            base=asset.get('model_base',key)
            meshes=custom_meshes if key=='custom' else reader.model(shared/asset['source'])
            if simulator=='isaac' and base=='r1_rover':
                # Isaac supplies four visible wheels driven by native tire physics.
                source=ET.parse(shared/asset['source']).getroot().find('model')
                wheel_links={joint.findtext('child') for joint in source.findall('joint')
                             if joint.get('name') in ('motor_0','motor_1','motor_2','motor_3')}
                meshes=[m for m in meshes if m.get('link') not in wheel_links]
                if not meshes: raise ValueError('Rover model must contain body visuals in addition to driven wheels')
            if simulator in ('isaac','sih'):
                template=shared/'transform/templates'/(base+'.usda')
                usd(meshes,models/(key+'.usda'),template=template)
            elif simulator=='flightgear':
                flightgear_model(shared,models,key,base,meshes)
            else:
                target=models/key/(key+'.obj');obj(meshes,target)
                subprocess.run(['blender','--factory-startup','-b','--python',str(shared/'transform/blender_export.py'),'--',str(target),str(target.with_suffix('.fbx'))],check=True)
        # All native formats are derived from the same local Baylands scene.
        path=shared/'worlds/baylands/source/world.sdf'
        meshes=reader.world(path)
        if simulator in ('isaac','sih'): usd(meshes,worlds/'baylands.usda',world=True)
        elif simulator=='flightgear': ac3d(meshes,worlds/'baylands/model.ac')
        else:
            target=worlds/'baylands/baylands.obj';obj(meshes,target)
            subprocess.run(['blender','--factory-startup','-b','--python',str(shared/'transform/blender_export.py'),'--',str(target),str(target.with_suffix('.fbx'))],check=True)
            # Water and other visual-only meshes must never become solid ground.
            collisions=[dict(m,texture='') for m in meshes if m.get('collision',False)]
            if not collisions: raise ValueError('Baylands has no collision meshes')
            target=worlds/'baylands/baylands_collision.obj';obj(collisions,target)
            subprocess.run(['blender','--factory-startup','-b','--python',str(shared/'transform/blender_export.py'),'--',str(target),str(target.with_suffix('.fbx'))],check=True)
        if simulator in ('flightgear','sih'):
            heightfield(meshes,worlds/'baylands-heightfield.json',cache=reader.cache)
        copy(shared/'worlds/dynamic_terrain/terrain.json',worlds/'dynamic-terrain.json')
        if simulator in ('isaac','sih'):
            template=(shared/'transform/templates/dynamic.usda').read_text()
            # The launcher references the selected vehicle. SIH's viewer adds its
            # own pose hierarchy; neither world should contain another vehicle.
            template=re.sub(r'\s*def Xform "Aircraft"\s*\([^)]*\)\s*\{[^}]*\}', '', template)
            (worlds/'dynamic.usda').write_text(template)
    manifest=dict(schema=1,simulator=simulator,models=list(assets),worlds=list(catalog['worlds']),source='shared/catalog.json',
                  model_adapters={key:asset.get('model_base',key) for key,asset in assets.items()})
    if 'custom' in assets:
        manifest['custom_model_limits']='Native converters reuse the selected adapter physics and collision layout. SDF dynamics, sensors and plugins are not translated. Gazebo keeps the supplied SDF and requires compatible PX4 joints and plugins.'
    (output/'export.json').write_text(json.dumps(manifest,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shared',type=Path,default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--simulator',choices=SIMULATORS,required=True)
    parser.add_argument('--output',type=Path,required=True,help='Empty staging directory for generated models/worlds')
    args=parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()): parser.error('--output must be empty; preserve existing exports first')
    export(args.shared,args.simulator,args.output)


if __name__=='__main__': main()
