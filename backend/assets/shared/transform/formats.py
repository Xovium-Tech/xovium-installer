"""Native geometry writers; dynamics and control remain simulator adapters."""
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET


def texture_copy(source, output):
    source=Path(source)
    name=hashlib.sha256(source.read_bytes()).hexdigest()[:20]+source.suffix.lower()
    target=output/'textures'/name; target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists(): shutil.copy2(source,target)
    return 'textures/'+name


def obj(meshes, path):
    path.parent.mkdir(parents=True,exist_ok=True)
    offset=1; uv_offset=1
    with path.open('w') as out,path.with_suffix('.mtl').open('w') as mtl:
        out.write('mtllib '+path.with_suffix('.mtl').name+'\n')
        for i,m in enumerate(meshes):
            name='part_'+str(i);out.write('o '+name+'\nusemtl '+name+'\n')
            for p in m['vertices']: out.write('v '+' '.join(f'{v:.9g}' for v in p)+'\n')
            for p in m['uv']: out.write('vt '+' '.join(f'{v:.9g}' for v in p)+'\n')
            for face in m['faces']:
                out.write('f '+' '.join(str(offset+j)+('/'+str(uv_offset+j) if m['uv'] else '') for j in face)+'\n')
            offset+=len(m['vertices']);uv_offset+=len(m['uv'])
            mtl.write('newmtl '+name+'\nKd '+' '.join(map(str,m['color'][:3]))+'\n')
            if m['texture']: mtl.write('map_Kd '+texture_copy(m['texture'],path.parent)+'\n')


def ac3d(meshes,path, *, vehicle=False):
    path.parent.mkdir(parents=True,exist_ok=True)
    # FlightGear 2020.3 optimizes vertex caches per geometry. Its old optimizer
    # takes minutes on the disconnected, half-million-vertex Baylands meshes.
    # Separate named objects bound that work without reducing scene detail.
    face_limit = 4096
    def parts(mesh):
        if vehicle or len(mesh['faces']) <= face_limit:
            yield mesh['vertices'], mesh['faces'], mesh['uv']
            return
        for start in range(0, len(mesh['faces']), face_limit):
            indices = {}; vertices = []; faces = []; uv = []
            for face in mesh['faces'][start:start + face_limit]:
                local = []
                for index in face:
                    if index not in indices:
                        indices[index] = len(vertices)
                        vertices.append(mesh['vertices'][index])
                        if mesh['uv']: uv.append(mesh['uv'][index])
                    local.append(indices[index])
                faces.append(local)
            yield vertices, faces, uv
    objects = sum(1 if vehicle else max(1, (len(m['faces']) + face_limit - 1) // face_limit) for m in meshes)
    visual_only = []
    with path.open('w') as out:
        out.write('AC3Db\n')
        for i,m in enumerate(meshes):
            out.write(f'MATERIAL "part_{i}" rgb '+ ' '.join(map(str,m['color'][:3]))+' amb 0.2 0.2 0.2 emis 0 0 0 spec 0 0 0 shi 0 trans 0\n')
        out.write('OBJECT world\nkids '+str(objects)+'\n')
        for i,m in enumerate(meshes):
            texture = texture_copy(m['texture'],path.parent) if m['texture'] else ''
            for chunk,(vertices,faces,uv) in enumerate(parts(m)):
                name = f'part_{i}' if vehicle else f'part_{i}_{chunk}'
                if not vehicle and not m.get('collision', False):
                    visual_only.append(name)
                out.write(f'OBJECT poly\nname "{name}"\n')
                if texture: out.write('texture "'+texture+'"\n')
                out.write('numvert '+str(len(vertices))+'\n')
                # AC3D is Y-up; FlightGear's AC loader maps (x,-z,y) back to Z-up.
                for x,y,z in vertices:
                    # FlightGear model placement uses X aft, Y right, Z up for
                    # vehicles. World objects retain ENU and get a world heading.
                    if vehicle: x,y=-x,-y
                    out.write(f'{x:.9g} {z:.9g} {-y:.9g}\n')
                out.write('numsurf '+str(len(faces))+'\n')
                for face in faces:
                    out.write(f'SURF 0x30\nmat {i}\nrefs 3\n')
                    for j in face:
                        u,v=uv[j] if uv else (0,0)
                        out.write(f'{j} {u:.9g} {v:.9g}\n')
                out.write('kids 0\n')
    if not vehicle:
        wrapper = ET.Element('PropertyList')
        ET.SubElement(wrapper, 'path').text = path.name
        if visual_only:
            animation = ET.SubElement(wrapper, 'animation')
            for name in visual_only:
                ET.SubElement(animation, 'object-name').text = name
            ET.SubElement(animation, 'enable-hot', type='bool').text = 'false'
        ET.indent(wrapper, space='  ')
        ET.ElementTree(wrapper).write(path.with_suffix('.xml'), encoding='utf-8', xml_declaration=True)


def usd(meshes,path, *, template=None,world=False):
    path.parent.mkdir(parents=True,exist_ok=True); root='World' if world else 'Aircraft'
    if template:
        text=Path(template).read_text()
        # Hide the adapter's simple visual shapes while preserving their collisions.
        text=re.sub(r'(def (?:Cube|Sphere|Cylinder) [\s\S]*?\n    \{)',r'\1\n        token visibility = "invisible"',text)
        prefix=text[:text.rfind('}')]
    else:
        prefix=f'#usda 1.0\n(\n defaultPrim = "{root}"\n metersPerUnit = 1\n upAxis = "Z"\n)\ndef Xform "{root}"\n{{\n'
    with path.open('w') as out:
        out.write(prefix)
        if world:
            out.write('    def DomeLight "Sky" {\n        float inputs:intensity = 600\n    }\n')
            out.write('    def DistantLight "Sun" {\n        float inputs:intensity = 1800\n    }\n')
            out.write('    def Camera "ChaseCamera" {\n        float2 clippingRange = (0.05, 20000)\n        float focalLength = 24\n    }\n')
        out.write('    def Scope "SharedMaterials" {\n')
        for i,m in enumerate(meshes):
            name=f'Mat{i}'; base=f'/{root}/SharedMaterials/{name}'
            out.write(f'        def Material "{name}" {{\n            token outputs:surface.connect = <{base}/Surface.outputs:surface>\n')
            out.write('            def Shader "Surface" {\n                uniform token info:id = "UsdPreviewSurface"\n                float inputs:roughness = 0.9\n')
            if m['texture']:
                out.write(f'                color3f inputs:diffuseColor.connect = <{base}/Texture.outputs:rgb>\n')
            else:
                out.write('                color3f inputs:diffuseColor = ('+', '.join(map(str,m['color'][:3]))+')\n')
            out.write('                token outputs:surface\n            }\n')
            if m['texture']:
                texture=texture_copy(m['texture'],path.parent)
                out.write(f'            def Shader "UV" {{\n uniform token info:id = "UsdPrimvarReader_float2"\n string inputs:varname = "st"\n float2 outputs:result\n }}\n')
                out.write(f'            def Shader "Texture" {{\n uniform token info:id = "UsdUVTexture"\n asset inputs:file = @{texture}@\n float2 inputs:st.connect = <{base}/UV.outputs:result>\n float3 outputs:rgb\n }}\n')
            out.write('        }\n')
        out.write('    }\n    def Xform "SharedVisuals" {\n')
        for i,m in enumerate(meshes):
            collision=world and m.get('collision',False)
            apis='"MaterialBindingAPI"'+(', "PhysicsCollisionAPI", "PhysicsMeshCollisionAPI"' if collision else '')
            out.write(f'        def Mesh "Part{i}" (prepend apiSchemas = [{apis}]) {{\n')
            out.write(f'            rel material:binding = </{root}/SharedMaterials/Mat{i}>\n')
            out.write('            uniform token subdivisionScheme = "none"\n            bool doubleSided = true\n')
            if collision: out.write('            bool physics:collisionEnabled = true\n            uniform token physics:approximation = "none"\n')
            out.write('            point3f[] points = ['+', '.join('('+', '.join(f'{v:.9g}' for v in p)+')' for p in m['vertices'])+']\n')
            out.write('            int[] faceVertexCounts = ['+', '.join('3' for _ in m['faces'])+']\n')
            out.write('            int[] faceVertexIndices = ['+', '.join(str(j) for f in m['faces'] for j in f)+']\n')
            if m['uv']:
                out.write('            texCoord2f[] primvars:st = ['+', '.join(f'({u:.9g}, {v:.9g})' for u,v in m['uv'])+'] (interpolation = "vertex")\n')
            out.write('        }\n')
        out.write('    }\n}\n')


def heightfield(meshes,path,max_side=1025,cache=None):
    """Rasterize walkable collision triangles for headless / FlightGear ground physics."""
    import numpy as np
    parts=[m for m in meshes if m.get('collision') and m['vertices']]
    if not parts: raise ValueError('World has no collision geometry for a heightfield')
    cached=None
    if cache is not None:
        signature=hashlib.sha256(f'heightfield-v1-{max_side}'.encode())
        for m in parts:
            signature.update(np.asarray(m['vertices'],dtype='<f8').tobytes())
            signature.update(np.asarray(m['faces'],dtype='<i8').tobytes())
        cached=Path(cache)/('heightfield-'+signature.hexdigest()+'.json')
        if cached.is_file():
            path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(cached,path);return
    low=np.min([np.min(m['vertices'],axis=0) for m in parts],axis=0)
    high=np.max([np.max(m['vertices'],axis=0) for m in parts],axis=0)
    spacing=max(.5,float(max(high[:2]-low[:2]))/(max_side-1))
    width,height=[int(math.ceil(v/spacing))+1 for v in high[:2]-low[:2]]
    grid=np.full((height,width),np.nan)
    for mesh in parts:
        vertices=np.asarray(mesh['vertices'],dtype=float)
        for face in mesh['faces']:
            a,b,c=vertices[face]
            cross=np.cross(b-a,c-a)
            if abs(cross[2]) < .5*np.linalg.norm(cross) or abs(cross[2])<1e-9: continue
            x0,y0=np.maximum(0,np.floor((np.minimum(np.minimum(a,b),c)[:2]-low[:2])/spacing).astype(int))
            x1,y1=np.minimum([width-1,height-1],np.ceil((np.maximum(np.maximum(a,b),c)[:2]-low[:2])/spacing).astype(int))
            x=low[0]+np.arange(x0,x1+1)*spacing; y=low[1]+np.arange(y0,y1+1)*spacing
            xx,yy=np.meshgrid(x,y)
            den=(b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(den)<1e-9: continue
            u=((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1]))/den
            v=((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1]))/den
            mask=(u>=-1e-6)&(v>=-1e-6)&(u+v<=1+1e-6)
            z=u*a[2]+v*b[2]+(1-u-v)*c[2]
            target=grid[y0:y1+1,x0:x1+1]
            target[mask]=np.fmax(target[mask],z[mask])
    # No imaginary ground outside the actual scene. Missing cells remain explicit.
    values=[None if not math.isfinite(v) else round(float(v),5) for v in grid.ravel()]
    if not np.isfinite(grid).any(): raise ValueError('World collision geometry produced an empty heightfield')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(schema=1,origin=[float(low[0]),float(low[1]),0],spacing=[spacing,spacing],width=width,height=height,heights=values),separators=(',',':'))+'\n')
    if cached is not None:
        cached.parent.mkdir(parents=True,exist_ok=True)
        temporary=cached.with_suffix('.tmp');shutil.copy2(path,temporary);temporary.replace(cached)
