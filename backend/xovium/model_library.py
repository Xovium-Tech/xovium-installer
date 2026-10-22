"""Read model cards and carry explicitly edited starter projects into installations."""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from xovium.core import Error, atomic_json, digest, operation_lock
from xovium.paths import check_owner, location, read_json

MODELS = {'cessna': ('Cessna', 'fixed-wing'), 'rover': ('Rover', 'rover')}
MAX_DOCUMENT = 16 * 1024**2


def _ordinary(path, root):
    path, root = Path(path).absolute(), Path(root).resolve()
    if not path.is_relative_to(root) or '..' in path.parts:
        raise Error('Model path escapes its library: ' + str(path))
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink(): raise Error('Model library paths must not be symlinks: ' + str(current))
    return path


def _string(value, label, *, empty=False):
    if (not isinstance(value, str) or len(value) > 4096 or (not empty and not value.strip())
            or any(ord(char) < 32 for char in value)):
        raise Error('Invalid ' + label + ' in model project.')
    return value


def _xml(value):
    if not isinstance(value, str) or len(value.encode()) > MAX_DOCUMENT:
        raise Error('Invalid plugin XML in model project.')
    if '<!DOCTYPE' in value.upper() or '<!ENTITY' in value.upper():
        raise Error('Model plugin XML must not contain entities or DTDs.')
    try: return ET.fromstring(value)
    except ET.ParseError as error: raise Error('Invalid plugin XML: ' + str(error)) from error


def _flag(value, label):
    if isinstance(value, bool): return value
    if isinstance(value, str) and value.strip().lower() in ('true', 'false', '1', '0'):
        return value.strip().lower() in ('true', '1')
    raise Error('Invalid enabled flag for ' + label)


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise Error('Model document contains a non-finite number.')
    if isinstance(value, list):
        for item in value: _finite(item)
    elif isinstance(value, dict):
        for item in value.values(): _finite(item)


def read_bundle(path):
    """Read bounded metadata only; the native editor validates all geometry on open."""
    path = Path(path)
    if path.is_symlink() or not path.is_file(): raise Error('Model bundle is missing: ' + str(path))
    try:
        with zipfile.ZipFile(path) as archive:
            seen, total = set(), 0
            for entry in archive.infolist():
                name = entry.filename.rstrip('/')
                parts = name.split('/')
                mode = stat.S_IFMT(entry.external_attr >> 16)
                total += entry.file_size
                if (not name or PurePosixPath(name).is_absolute() or '\\' in name or ':' in name
                        or any(part in ('', '.', '..') for part in parts)
                        or any(ord(char) < 32 for char in name) or name in seen
                        or (name != 'project.json' and parts[0] != 'assets')
                        or mode not in (0, stat.S_IFREG, stat.S_IFDIR) or entry.flag_bits & 1
                        or entry.file_size > 512 * 1024**2 or total > 2 * 1024**3 or len(seen) >= 10000):
                    raise Error('Unsafe or oversized model bundle: ' + str(path))
                seen.add(name)
            info = archive.getinfo('project.json')
            if info.file_size > MAX_DOCUMENT: raise Error('Model document is too large.')
            document = json.loads(archive.read(info))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, Error): raise
        raise Error('Cannot read model bundle: ' + str(error)) from error
    if (not isinstance(document, dict) or document.get('format') != 'xovium-model'
            or type(document.get('version')) is not int or document['version'] != 1):
        raise Error('Unsupported portable model format.')
    _finite(document)
    _string(document.get('name'), 'model name')
    if document.get('vehicle') not in ('fixed-wing', 'rover', 'custom'):
        raise Error('Invalid model vehicle family.')
    if document.get('frame') != 'FLU' or document.get('units') != 'm':
        raise Error('Models must use FLU axes and metres.')
    for key in ('links', 'visuals', 'sensors', 'plugins'):
        if not isinstance(document.get(key), list): raise Error('Model ' + key + ' must be a list.')
    links = set()
    for link in document['links']:
        if not isinstance(link, dict): raise Error('Invalid model link.')
        name = _string(link.get('name'), 'link name')
        if name in links: raise Error('Duplicate model link: ' + name)
        links.add(name)
    if not links: raise Error('Model has no links.')
    return document


def _content_signature(path):
    # ZIP timestamps/compression and JSON whitespace are not model edits.
    document = read_bundle(path)
    value = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode())
    try:
        with zipfile.ZipFile(path) as archive:
            for entry in sorted(archive.infolist(), key=lambda item: item.filename):
                if entry.filename == 'project.json' or entry.is_dir(): continue
                value.update(entry.filename.encode())
                with archive.open(entry) as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''): value.update(block)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as error:
        raise Error('Cannot verify starter project assets: ' + str(error)) from error
    return value.hexdigest()


def _changed(path, record):
    if digest(path) == record['baseline']: return False
    return not record.get('baseline_content') or _content_signature(path) != record['baseline_content']


def attachments(document):
    sensors, plugins, seen = [], [], set()
    links = {item['name'] for item in document['links']}
    for item in document['sensors']:
        if not isinstance(item, dict): raise Error('Invalid model sensor.')
        name, kind = _string(item.get('name'), 'sensor name'), _string(item.get('type'), 'sensor type')
        parent = item.get('parent')
        if not isinstance(parent, str) or parent not in links: raise Error('Unknown sensor parent: ' + name)
        if (parent, name) in seen: raise Error('Duplicate sensor name: ' + name)
        seen.add((parent, name))
        if type(item.get('enabled')) is not bool: raise Error('Invalid sensor enabled flag: ' + name)
        sensors.append(dict(name=name, type=kind, enabled=item['enabled']))
    for item in document['plugins']:
        if not isinstance(item, dict): raise Error('Invalid model plugin.')
        name = _string(item.get('name'), 'plugin name')
        node = _xml(item.get('xml'))
        if node.tag != 'plugin': raise Error('Plugin XML must contain a plugin element: ' + name)
        parent = item.get('parent', '__model__')
        if parent != '__model__' and parent not in links: raise Error('Unknown plugin parent: ' + name)
        kind = _string(item.get('filename', ''), 'plugin filename', empty=True) or _string(item.get('target'), 'plugin target')
        value = item.get('enabled', node.get('enabled', node.findtext('enabled', 'true')))
        plugins.append(dict(name=name, type=kind, enabled=_flag(value, name)))
    return sensors, plugins


def _assets(root):
    return Path(root) / ('backend/assets' if (Path(root) / 'backend/assets').is_dir() else 'assets') / 'model-library'


def _baseline(root, key):
    folder = _assets(root)
    metadata = read_json(_ordinary(folder / 'sources.json', root))
    if metadata.get('schema') != 1: raise Error('Unsupported starter model catalog.')
    item = metadata.get('models', {}).get(key)
    if not isinstance(item, dict): raise Error('Starter model is not bundled: ' + key)
    filename = item.get('file')
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.xovmodel'):
        raise Error('Invalid starter model filename.')
    path = _ordinary(folder / filename, root)
    expected = item.get('sha256')
    if not isinstance(expected, str) or len(expected) != 64 or digest(path) != expected:
        raise Error('Starter model checksum failed: ' + str(path))
    return path, expected, str(metadata.get('px4_version', 'unknown'))


def _stage(root, prefix):
    key = hashlib.sha256(str(prefix).encode()).hexdigest()[:24]
    return _ordinary(Path(root) / '.build/model-library' / key, root)


def _stage_metadata(root, prefix):
    stage = _stage(root, prefix)
    path = _ordinary(stage / 'staging.json', root)
    if not path.exists(): return stage, dict(schema=1, prefix=str(prefix), models={})
    metadata = read_json(path)
    if (metadata.get('schema') != 1 or metadata.get('prefix') != str(prefix)
            or not isinstance(metadata.get('models'), dict)):
        raise Error('Starter model staging belongs to another installation path.')
    return stage, metadata


def _state(root, prefix):
    file = _ordinary(prefix / 'state.json', prefix)
    if not file.exists(): return {}
    state = read_json(file)
    check_owner(state, root)
    if state.get('uninstalling'): raise Error('Finish interrupted installation removal before editing models.')
    return state


def _choice(root, prefix, key):
    state = _state(root, prefix)
    installed = _ordinary(prefix / 'shared/models' / key / 'model.xovmodel', prefix)
    if state and installed.is_file(): return installed, 'installed', None
    baseline, checksum, version = _baseline(root, key)
    stage, metadata = _stage_metadata(root, prefix)
    staged = _ordinary(stage / (key + '.xovmodel'), root)
    record = metadata['models'].get(key)
    if staged.exists():
        if not isinstance(record, dict) or not isinstance(record.get('baseline'), str):
            raise Error('Starter project has no source record; preserve it and reopen the installer: ' + str(staged))
        if _changed(staged, record):
            return staged, 'edited', record
    return baseline, 'starter', dict(baseline=checksum, px4_version=version)


def info(root, prefix=None):
    root = Path(root).resolve()
    prefix = location(root, prefix)
    entries = []
    for key, (label, family) in MODELS.items():
        entry = dict(id=key, name=label, path='', available=False, editable=False, status='', sensors=[], plugins=[])
        try:
            path, source, record = _choice(root, prefix, key)
            document = read_bundle(path)
            if document['vehicle'] not in (family, 'custom'):
                raise Error(label + ' must retain its ' + family + ' physics adapter; choose a custom model to change vehicle family.')
            sensors, plugins = attachments(document)
            if source == 'installed': status = 'Installed shared model. Save edits here; they apply at the next simulator launch.'
            elif source == 'edited': status = 'Edited starter model from PX4 ' + record.get('px4_version', 'unknown') + '. Saved changes will carry into this installation.'
            else: status = 'Starter model from PX4 ' + record['px4_version'] + '. Change model opens an editable copy; only saved edits override the selected PX4 release.'
            entry.update(path=str(path), available=True, editable=True, status=status, sensors=sensors, plugins=plugins)
        except (Error, OSError, ValueError, TypeError) as error:
            entry['status'] = str(error) + ' Repair the shared model or reinstall its model library.'
        entries.append(entry)
    return dict(schema=1, prefix=str(prefix), models=entries)


def print_info(data):
    def line(value):
        print(''.join(char if ord(char) >= 32 and ord(char) != 127 else ' ' for char in str(value)), flush=True)
    print('XOVIUM_MODEL_INFO_V1', flush=True)
    for item in data['models']:
        for key in ('id', 'name', 'path'): line(item[key])
        for key in ('available', 'editable'): line('1' if item[key] else '0')
        line(item['status'])
        for key in ('sensors', 'plugins'):
            line(len(item[key]))
            for attachment in item[key]:
                line(attachment['name'] + ' — ' + attachment['type'] + ' — ' + ('enabled' if attachment['enabled'] else 'off'))


def _editable_copy(root, prefix, key):
    chosen, source, record = _choice(root, prefix, key)
    if source == 'installed': return chosen
    stage, metadata = _stage_metadata(root, prefix)
    stage.mkdir(parents=True, exist_ok=True)
    target = _ordinary(stage / (key + '.xovmodel'), root)
    if source == 'starter':
        with tempfile.NamedTemporaryFile(prefix='.bundle-', dir=stage, delete=False) as temporary:
            temporary = Path(temporary.name)
        try:
            shutil.copy2(chosen, temporary)
            temporary.replace(target)
        finally: temporary.unlink(missing_ok=True)
        metadata['models'][key] = dict(record, baseline_content=_content_signature(chosen))
        atomic_json(stage / 'staging.json', metadata)
    return target


def edit(root, key, prefix=None):
    if key not in MODELS: raise Error('Choose Cessna or rover to edit.')
    root = Path(root).resolve()
    prefix = location(root, prefix)
    with operation_lock(root):
        summary = next(item for item in info(root, prefix)['models'] if item['id'] == key)
        if not summary['editable']: raise Error(summary['status'])
        bundle = _editable_copy(root, prefix, key)
        launcher = _ordinary(prefix / 'tools/xovium-model-editor/run.sh', prefix)
        if _state(root, prefix) and launcher.is_file():
            command = ['bash', str(launcher), str(bundle)]
        else:
            from xovium.editor_source import resolve
            source = resolve(root)
            command = ['bash', str(source / 'run.sh'), str(bundle)]
        print('Opening ' + MODELS[key][0] + ' in Xovium Model Editor. Save the project and close the editor to return.', file=sys.stderr, flush=True)
        try: result = subprocess.run(command, stdout=sys.stderr, stderr=sys.stderr)
        except OSError as error: raise Error('Cannot start Xovium Model Editor: ' + str(error)) from error
        if result.returncode:
            raise Error('Xovium Model Editor exited with code ' + str(result.returncode) + '. Check the build/runtime message above; saved model edits are preserved.')
        return info(root, prefix)


def apply_staged(ctx):
    """Import edits only; pristine starter bundles never replace release-specific PX4 models."""
    root, prefix = Path(ctx.root).resolve(), Path(ctx.storage).resolve()
    stage, metadata = _stage_metadata(root, prefix)
    imported = ctx.state.setdefault('model_library_staged', {})
    for key, (_, family) in MODELS.items():
        source = _ordinary(stage / (key + '.xovmodel'), root)
        record = metadata['models'].get(key)
        if not source.exists(): continue
        if not isinstance(record, dict) or not isinstance(record.get('baseline'), str):
            raise Error('Edited starter project has no source record: ' + str(source))
        source_hash = digest(source)
        if not _changed(source, record): continue
        document = read_bundle(source)
        attachments(document)
        if document['vehicle'] not in (family, 'custom'):
            raise Error(key + ' starter project has a different vehicle family; select it as a custom model instead.')
        relative = f'shared/models/{key}/model.xovmodel'
        destination = ctx.managed(relative)
        previous = imported.get(key, {})
        if destination.is_file() and previous.get('source') == source_hash: continue
        if destination.exists():
            current = digest(destination)
            pristine = ctx.state.get('shared_portable_seeds', {}).get(relative, {}).get('bundle')
            if current not in (previous.get('bundle'), pristine, source_hash):
                raise Error('Both the installed shared model and the starter project were edited. Preserve one before replacing: ' + str(destination))
        from xovium.model_tool import import_bundle
        ctx.say('[models] Carry edited ' + key + ' starter project into the shared library')
        try: import_bundle(ctx, source, destination)
        except RuntimeError as error:
            raise Error('The edited ' + key + ' starter project could not be imported: ' + str(error)) from error
        imported[key] = dict(source=source_hash, bundle=digest(destination), baseline=record['baseline'])
        ctx.save()
