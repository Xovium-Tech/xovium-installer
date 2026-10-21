"""Blender background adapter: OBJ metres/FLU -> FBX with unit metadata."""
import sys
from pathlib import Path
import bpy

source,output=map(Path,sys.argv[sys.argv.index('--')+1:])
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
bpy.ops.wm.obj_import(filepath=str(source),forward_axis='Y',up_axis='Z')
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.transform_apply(location=True,rotation=True,scale=True)
output.parent.mkdir(parents=True,exist_ok=True)
bpy.ops.export_scene.fbx(filepath=str(output),use_selection=True,object_types={'MESH'},axis_forward='X',axis_up='Z',bake_anim=False,add_leaf_bones=False,path_mode='COPY',embed_textures=True,mesh_smooth_type='FACE')
