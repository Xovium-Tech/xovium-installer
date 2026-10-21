"""SDF geometry conversion. The source frame is metres, X forward, Y left, Z up."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET


def matrix(text=None, scale=None):
    x, y, z, r, p, h = map(float, (text or '0 0 0 0 0 0').split())
    cr, sr, cp, sp, ch, sh = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(h), math.sin(h)
    result = [[ch*cp, ch*sp*sr-sh*cr, ch*sp*cr+sh*sr, x],
              [sh*cp, sh*sp*sr+ch*cr, sh*sp*cr-ch*sr, y], [-sp, cp*sr, cp*cr, z], [0,0,0,1]]
    for row in result[:3]:
        for i, value in enumerate(scale or (1,1,1)): row[i] *= value
    return result


def multiply(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def point(m, p):
    return [sum(m[i][k]*p[k] for k in range(3)) + m[i][3] for i in range(3)]


def pose(element, default_frame):
    value=element.find('pose')
    if value is None: return default_frame,matrix()
    if value.get('rotation_format','euler_rpy') != 'euler_rpy' or value.get('degrees','false') == 'true':
        raise ValueError('Shared SDF poses must use Euler angles in radians')
    # Some upstream worlds contain the older nested spelling.
    relative=value.get('relative_to') or value.findtext('relative_to') or default_frame
    return relative.strip(),matrix(value.text)


def frames(nodes, origin):
    resolved={origin:matrix()}; pending=set()
    def resolve(name):
        if name in resolved: return resolved[name]
        if name not in nodes: raise ValueError('Unknown SDF pose frame: '+name)
        if name in pending: raise ValueError('Cyclic SDF pose frame: '+name)
        pending.add(name)
        parent,local=nodes[name]
        resolved[name]=multiply(resolve(parent),local)
        pending.remove(name)
        return resolved[name]
    for name in nodes: resolve(name)
    return resolved


def primitive(geometry):
    box = geometry.find('box')
    if box is not None:
        sx, sy, sz = [float(v)/2 for v in box.findtext('size').split()]
        vertices = [[x*sx,y*sy,z*sz] for x,y,z in [(-1,-1,-1),(1,-1,-1),(1,1,-1),(-1,1,-1),(-1,-1,1),(1,-1,1),(1,1,1),(-1,1,1)]]
        faces = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]]
    elif geometry.find('cylinder') is not None:
        shape = geometry.find('cylinder'); radius=float(shape.findtext('radius')); length=float(shape.findtext('length'))
        vertices = [[radius*math.cos(i*math.tau/24), radius*math.sin(i*math.tau/24),z] for z in (-length/2,length/2) for i in range(24)]
        vertices += [[0,0,-length/2],[0,0,length/2]]; faces=[]
        for i in range(24):
            j=(i+1)%24; faces += [[i,j,j+24],[i,j+24,i+24],[48,j,i],[49,i+24,j+24]]
    elif geometry.find('sphere') is not None:
        radius=float(geometry.findtext('sphere/radius')); vertices=[]; faces=[]
        for j in range(13):
            p=math.pi*j/12
            for i in range(24): vertices.append([radius*math.sin(p)*math.cos(i*math.tau/24),radius*math.sin(p)*math.sin(i*math.tau/24),radius*math.cos(p)])
        for j in range(12):
            for i in range(24):
                a=j*24+i; b=j*24+(i+1)%24; faces += [[a,b,b+24],[a,b+24,a+24]]
    elif geometry.find('plane') is not None:
        sx,sy=[float(v)/2 for v in geometry.findtext('plane/size','1000 1000').split()]
        vertices=[[-sx,-sy,0],[sx,-sy,0],[sx,sy,0],[-sx,sy,0]]; faces=[[0,1,2],[0,2,3]]
    else:
        raise ValueError('Unsupported geometry: '+ET.tostring(geometry,encoding='unicode')[:200])
    return [dict(vertices=vertices,faces=faces,uv=[],color=[.65,.65,.65,1],texture='')]


class Reader:
    def __init__(self, shared):
        self.shared=Path(shared).resolve(); self.cache=self.shared/'transform/cache'
        self.source_signatures={}
        self.cache.mkdir(parents=True,exist_ok=True)
        self.helper=self.shared/'transform/build/mesh-export'
        self.roots={
            'rc_cessna':self.shared/'models/cessna/source',
            'r1_rover':self.shared/'models/rover/source',
            'custom':self.shared/'models/custom/source',
            'baylands':self.shared/'worlds/baylands/source/models/baylands',
            'Coast Water':self.shared/'worlds/baylands/source/models/coast_water',
            'coast_water':self.shared/'worlds/baylands/source/models/coast_water'}

    def resolve(self, uri, base):
        uri=uri.strip()
        if uri.startswith('model://'):
            model,_,relative=uri[8:].partition('/')
            if model not in self.roots: raise ValueError('Unknown shared model: '+model)
            path=self.roots[model]/relative
        elif uri.startswith('https://fuel.gazebosim.org/'):
            from urllib.parse import unquote
            name=unquote(uri.rstrip('/').rsplit('/',1)[-1])
            if name not in self.roots: raise ValueError('Unpack the Fuel model into shared first: '+uri)
            path=self.roots[name]
        else:
            path=Path(base)/uri.removeprefix('file://')
        path=path.resolve()
        if not path.is_relative_to(self.shared): raise ValueError('Resource escapes shared: '+str(path))
        if not path.exists(): raise FileNotFoundError(path)
        return path

    def mesh(self,path):
        # OBJ materials and glTF buffers can change without changing the mesh
        # document. Include the self-contained source bundle in the cache key.
        roots=[root for root in self.roots.values() if path.is_relative_to(root)]
        source=max(roots,key=lambda root:len(root.parts)) if roots else path.parent
        if source not in self.source_signatures:
            digest=hashlib.sha256()
            for resource in sorted(p for p in source.rglob('*') if p.is_file()):
                if not resource.resolve().is_relative_to(self.shared):
                    raise ValueError('Resource escapes shared: '+str(resource))
                digest.update(str(resource.relative_to(source)).encode()+b'\0')
                with resource.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''): digest.update(chunk)
            self.source_signatures[source]=digest.hexdigest()
        identity=hashlib.sha256(('bundle-v2\0'+self.source_signatures[source]+'\0'+str(path.relative_to(source))).encode()).hexdigest()
        target=self.cache/(identity+'.json')
        if not target.exists():
            if not self.helper.exists(): raise RuntimeError('Build shared/transform/mesh_export.cpp with libassimp before native conversion.')
            temporary=target.with_suffix('.tmp')
            subprocess.run([str(self.helper),str(path),str(temporary)],check=True)
            temporary.replace(target)
        meshes=json.loads(target.read_text())
        for mesh in meshes:
            if mesh['texture']:
                texture=(path.parent/mesh['texture'].replace('\\','/')).resolve()
                if not texture.is_relative_to(self.shared) or not texture.is_file():
                    raise ValueError('Missing or external texture: '+str(texture))
                mesh['texture']=str(texture)
        return meshes

    def model(self,path,transform=None, *, spawn_pose=False):
        models=ET.parse(path).getroot().findall('model')
        if len(models)!=1: raise ValueError('Expected exactly one top-level SDF model: '+str(path))
        model=models[0]
        if model.find('include') is not None or model.find('model') is not None:
            raise ValueError('Nested SDF models and includes are not supported by common model conversion: '+str(path))
        transform=transform or matrix()
        if spawn_pose: transform=multiply(transform,matrix(model.findtext('pose')))
        meshes=[]
        nodes={}
        for link in model.findall('link'): nodes[link.get('name')]=pose(link,'__model__')
        for joint in model.findall('joint'): nodes[joint.get('name')]=pose(joint,joint.findtext('child','__model__'))
        for frame in model.findall('frame'): nodes[frame.get('name')]=pose(frame,frame.get('attached_to','__model__'))
        transforms=frames(nodes,'__model__')
        for link in model.findall('link'):
            for visual in link.findall('visual'):
                geometry=visual.find('geometry')
                if geometry is None: continue
                mesh=geometry.find('mesh'); scale=list(map(float,mesh.findtext('scale','1 1 1').split())) if mesh is not None else None
                if mesh is not None and mesh.find('submesh') is not None:
                    raise ValueError('SDF submesh selection is not supported by common model conversion: '+str(path))
                relative,local=pose(visual,link.get('name'))
                vt=multiply(multiply(transform,transforms[relative]),multiply(local,matrix(scale=scale)))
                parts=self.mesh(self.resolve(mesh.findtext('uri'),path.parent)) if mesh is not None else primitive(geometry)
                color=visual.findtext('material/diffuse')
                for part in parts:
                    part['vertices']=[point(vt,p) for p in part['vertices']]
                    if color and not part['texture']: part['color']=list(map(float,color.split()))
                    part['name']=link.get('name','link')+'_'+visual.get('name','visual')
                    part['link']=link.get('name','link')
                    part['collision']=bool(link.findall('collision'))
                    meshes.append(part)
        if not meshes: raise ValueError('SDF model has no supported visual geometry: '+str(path))
        return meshes

    def world(self,path):
        world=ET.parse(path).getroot().find('world')
        if world is None: raise ValueError('Expected an SDF world')
        result=[]; nodes={}; includes=[]
        for frame in world.findall('frame'): nodes[frame.get('name')]=pose(frame,frame.get('attached_to','world'))
        for include in world.findall('include'):
            resource=self.resolve(include.findtext('uri'),path.parent)
            source=resource/'model.sdf' if resource.is_dir() else resource
            included=ET.parse(source).getroot().find('model')
            if included is None: raise ValueError('Included resource is not an SDF model: '+str(source))
            name=include.findtext('name',included.get('name'))
            # include/pose replaces the model's top-level pose (SDF semantics).
            nodes[name]=pose(include if include.find('pose') is not None else included,'world')
            includes.append((name,source))
        transforms=frames(nodes,'world')
        for name,source in includes: result.extend(self.model(source,transforms[name]))
        return result
