"""Compose portable model visuals with the installer's existing vehicle adapters."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def _copy_export(source, target):
    if not source.is_dir():
        raise FileNotFoundError('Missing model export: ' + str(source))
    if any(path.is_symlink() for path in source.rglob('*')):
        raise ValueError('Model exports must contain local files, not symbolic links')
    shutil.copytree(source, target)


def _usd_name(name):
    name = re.sub(r'[^A-Za-z0-9_]', '_', str(name)) or 'model'
    return '_' + name if name[0].isdigit() else name


def _usd(shared, simulator, key, adapter, document, target, payload):
    if not (target / 'model.usda').is_file():
        raise FileNotFoundError('Model export has no model.usda')
    template = shared / 'transform/templates' / (adapter + '.usda')
    text = template.read_text()
    # Invisible adapter shapes remain colliders; only their authored visuals hide.
    text = re.sub(r'(def (?:Cube|Sphere|Cylinder) [\s\S]*?\n    \{)',
                  r'\1\n        token visibility = "invisible"', text)
    if any(sensor.get('enabled', True) and sensor.get('type') in ('camera', 'depth_camera')
           for sensor in document.get('sensors', [])):
        text = re.sub(r'(custom bool px4:camera:enabled\s*=\s*)(?:1|true)\b', r'\g<1>false', text)
    closing = text.rfind('}')
    if closing < 0:
        raise ValueError('Invalid native vehicle adapter: ' + str(template))
    additions = ['    def Xform "SharedVisuals" (',
                 '        prepend references = @./' + key + '/model.usda@',
                 '    )', '    {']
    if simulator == 'isaac' and adapter == 'r1_rover':
        # PhysX owns the four visible wheel bodies. Hide only source wheel visuals,
        # retaining sensors mounted on those links and their local transforms.
        wheels = {joint.get('child') for joint in document.get('joints', [])
                  if re.fullmatch(r'motor_[0-3]', joint.get('name', ''))}
        for index, link in enumerate(document.get('links', [])):
            if link['name'] not in wheels:
                continue
            additions += ['        over "link_' + str(index) + '_' + _usd_name(link['name']) + '"',
                          '        {']
            for visual_index, visual in enumerate(document.get('visuals', [])):
                if visual.get('link') == link['name']:
                    additions += ['            over "visual_' + str(visual_index) + '"',
                                  '            {', '                token visibility = "invisible"',
                                  '            }']
            additions += ['        }']
    additions += ['    }', '}']
    (payload / (key + '.usda')).write_text(text[:closing] + '\n'.join(additions) + '\n')


def _flightgear(shared, key, adapter, target):
    if not (target / 'model.ac').is_file():
        raise FileNotFoundError('Model export has no model.ac')
    template = shared / 'transform/templates/flightgear' / adapter
    descriptor = 'Cessna-set.xml' if adapter == 'rc_cessna' else 'R1-set.xml'
    if not (template / descriptor).is_file():
        raise FileNotFoundError('Missing FlightGear adapter: ' + str(template / descriptor))
    if adapter == 'rc_cessna' and not (template / 'Cessna.xml').is_file():
        raise FileNotFoundError('Missing FlightGear Cessna dynamics: ' + str(template / 'Cessna.xml'))
    for source in template.iterdir():
        if source.is_file():
            shutil.copy2(source, target / source.name)
    path = target / descriptor
    root = ET.parse(path).getroot()
    model_path = root.find('sim/model/path')
    if model_path is None:
        raise ValueError('FlightGear adapter has no sim/model/path')
    model_path.text = 'Aircraft/' + key + '/model.ac'
    description = root.find('sim/description')
    if description is not None:
        description.text = 'Xovium portable model (' + adapter + ' dynamics adapter)'
    ET.indent(root, space='  ')
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def _run(command, *, env=None, timeout=1200):
    process = subprocess.run([str(arg) for arg in command], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, errors='replace', timeout=timeout)
    if process.returncode:
        raise RuntimeError('Model adapter command failed (' + str(process.returncode) + '): '
                           + str(command[0]) + '\n' + process.stdout[-6000:])
    return process.stdout


def _unreal(shared, key, target, payload):
    editor = shared.parent / 'simulation/unreal-engine/engine/Engine/Binaries/Linux/UnrealEditor-Cmd'
    if not editor.is_file():
        raise FileNotFoundError('Unreal model conversion needs the installed editor: ' + str(editor))
    blender = shutil.which('blender')
    if blender is None:
        raise FileNotFoundError('Unreal model conversion needs Blender (blender on PATH)')
    source = target / 'model.obj'
    if not source.is_file() or not source.with_suffix('.mtl').is_file():
        raise FileNotFoundError('Unreal export needs canonical FLU/metre model.obj and model.mtl')
    fbx = target / (key + '.fbx')
    _run([blender, '--factory-startup', '-b', '--python', shared / 'transform/blender_export.py',
          '--', source, fbx])
    # The unattended FBX importer rebuilds materials from this sidecar. Paths
    # remain relative to the export folder, including preserved texture assets.
    if source.with_suffix('.mtl') != fbx.with_suffix('.mtl'):
        shutil.copy2(source.with_suffix('.mtl'), fbx.with_suffix('.mtl'))
    with tempfile.TemporaryDirectory(prefix='model-unreal-', dir=payload.parent) as temporary:
        temporary = Path(temporary)
        project = temporary / 'ModelImport.uproject'
        project.write_text(json.dumps({'FileVersion': 3, 'EngineAssociation': '',
            'Plugins': [{'Name': 'PythonScriptPlugin', 'Enabled': True},
                        {'Name': 'EditorScriptingUtilities', 'Enabled': True}]}))
        # Import into an isolated content-only project. The installed simulator
        # project is neither opened nor modified during cache generation.
        layout = temporary / 'export/models'
        layout.mkdir(parents=True)
        (layout / key).symlink_to(target.resolve(), target_is_directory=True)
        env = dict(os.environ, XOVIUM_SHARED_UE_EXPORT=str(layout.parent),
                   XOVIUM_SHARED_UE_MODEL_ONLY='1', XOVIUM_SHARED_UE_MODELS=json.dumps([key]),
                   XDG_CACHE_HOME=str(temporary / 'cache'),
                   XDG_CONFIG_HOME=str(temporary / 'config'), XDG_DATA_HOME=str(temporary / 'data'))
        env['UE-LocalDataCachePath'] = str(temporary / 'derived-data')
        log = _run([editor, project, '-unattended', '-nullrhi', '-nosound', '-nop4',
                    '-NoSplash', '-notraceserver', '-traceautostart=0', '-run=pythonscript',
                    '-script=' + str(shared / 'transform/unreal_import.py')], env=env)
        content = temporary / 'Content/Models' / key
        native = content / (key + '.uasset')
        if not native.is_file() or not native.stat().st_size:
            raise RuntimeError('Unreal did not produce the model package: ' + key + '\n' + log[-6000:])
        for package in content.rglob('*'):
            if package.is_file():
                destination = target / package.relative_to(content)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(package, destination)


def prepare(shared: Path, simulator: str, key: str, adapter: str, export: Path, payload: Path) -> None:
    """Write files relative to simulation/<simulator>/models in a fresh payload."""
    shared, export, payload = map(Path, (shared, export, payload))
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', key):
        raise ValueError('Model cache key must be a safe Unreal/USD asset identifier')
    if adapter not in ('rc_cessna', 'r1_rover'):
        raise ValueError('Model adapter must be rc_cessna or r1_rover')
    if simulator.startswith('gazebo-'):
        simulator = 'gazebo'
    if simulator == 'unreal-engine':
        simulator = 'unreal'
    if simulator not in ('gazebo', 'isaac', 'sih', 'flightgear', 'unreal'):
        raise ValueError('Unknown model simulator: ' + simulator)
    document = json.loads((export / 'project.json').read_text())
    payload.mkdir(parents=True, exist_ok=True)
    target = payload / key
    _copy_export(export, target)
    if simulator in ('isaac', 'sih'):
        _usd(shared, simulator, key, adapter, document, target, payload)
    elif simulator == 'flightgear':
        _flightgear(shared, key, adapter, target)
    elif simulator == 'unreal':
        _unreal(shared, key, target, payload)
    elif not (target / 'model.sdf').is_file():
        raise FileNotFoundError('Gazebo model export has no model.sdf')
    sensor_runtime = {
        'gazebo': 'native SDF definitions require world systems and bridge routing',
        'isaac': 'USD cameras and sensor metadata retained; measurements require native runtime support',
        'sih': 'USD cameras and sensor metadata retained; measurements require viewer/runtime support',
        'unreal': 'camera scene entries and sensor metadata retained; sampling requires runtime components',
        'flightgear': 'sensor mounting metadata retained; measurements require property/runtime integration',
    }
    (target / 'adapter.json').write_text(json.dumps({
        'schema': 1, 'simulator': simulator, 'adapter': adapter,
        'dynamics': 'source SDF' if simulator == 'gazebo' else 'existing ' + adapter + ' adapter',
        'sensor_runtime': sensor_runtime[simulator],
    }, indent=2) + '\n')
