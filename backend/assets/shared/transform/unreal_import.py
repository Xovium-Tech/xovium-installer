"""Run with UnrealEditor-Cmd -run=pythonscript after the shared FBX export."""
import os
from pathlib import Path
import unreal


base = Path(os.environ['XOVIUM_SHARED_UE_EXPORT'])
assets = unreal.AssetToolsHelpers.get_asset_tools()


def material_asset(folder, name):
    path = folder + '/' + name
    material = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if material is None:
        material = assets.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    unreal.MaterialEditingLibrary.delete_all_material_expressions(material)
    return material


def mesh_materials(source, folder, mesh):
    # The automated FBX factory can leave material slots empty. Build from our
    # canonical OBJ sidecar so colors/textures survive unattended imports too.
    definitions = {}
    current = None
    for line in source.with_suffix('.mtl').read_text().splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            continue
        key, value = fields
        if key == 'newmtl':
            current = definitions.setdefault(value, {})
        elif current is not None:
            current[key] = value
    textures = {}
    slots = list(mesh.static_materials)
    for slot in slots:
        name = str(slot.get_editor_property('imported_material_slot_name'))
        definition = definitions.get(name)
        if definition is None:
            raise RuntimeError('Missing shared material definition: ' + name)
        material = material_asset(folder + '/Materials', name)
        color = unreal.MaterialEditingLibrary.create_material_expression(material, unreal.MaterialExpressionConstant3Vector)
        color.set_editor_property('constant', unreal.LinearColor(*map(float, definition.get('Kd', '1 1 1').split()), 1.0))
        output = color
        if 'map_Kd' in definition:
            image_path = source.parent / definition['map_Kd']
            if str(image_path) not in textures:
                task = unreal.AssetImportTask()
                task.filename = str(image_path)
                task.destination_path = folder + '/Textures'
                task.destination_name = 'T_' + image_path.stem
                task.automated = True
                task.replace_existing = True
                task.save = True
                task.factory = unreal.TextureFactory()
                assets.import_asset_tasks([task])
                texture = unreal.load_asset(task.destination_path + '/' + task.destination_name)
                if not isinstance(texture, unreal.Texture2D):
                    raise RuntimeError('Cannot import shared texture: ' + str(image_path))
                textures[str(image_path)] = texture
            texture_node = unreal.MaterialEditingLibrary.create_material_expression(material, unreal.MaterialExpressionTextureSample)
            texture_node.set_editor_property('texture', textures[str(image_path)])
            output = unreal.MaterialEditingLibrary.create_material_expression(material, unreal.MaterialExpressionMultiply)
            unreal.MaterialEditingLibrary.connect_material_expressions(color, '', output, 'A')
            unreal.MaterialEditingLibrary.connect_material_expressions(texture_node, 'RGB', output, 'B')
        unreal.MaterialEditingLibrary.connect_material_property(output, '', unreal.MaterialProperty.MP_BASE_COLOR)
        roughness = unreal.MaterialEditingLibrary.create_material_expression(material, unreal.MaterialExpressionConstant)
        roughness.set_editor_property('r', .8)
        unreal.MaterialEditingLibrary.connect_material_property(roughness, '', unreal.MaterialProperty.MP_ROUGHNESS)
        unreal.MaterialEditingLibrary.recompile_material(material)
        unreal.EditorAssetLibrary.save_loaded_asset(material)
        slot.set_editor_property('material_interface', material)
    mesh.set_editor_property('static_materials', slots)


def import_mesh(source, folder, name, ground=False, materials=True):
    task = unreal.AssetImportTask()
    for key, value in dict(filename=str(source), destination_path=folder,
                           destination_name=name, automated=True, replace_existing=True,
                           replace_existing_settings=True, save=True).items():
        task.set_editor_property(key, value)
    options = unreal.FbxImportUI()
    options.set_editor_property('import_mesh', True)
    options.set_editor_property('import_as_skeletal', False)
    options.set_editor_property('import_materials', False)
    options.set_editor_property('import_textures', False)
    options.set_editor_property('automated_import_should_detect_type', False)
    options.set_editor_property('mesh_type_to_import', unreal.FBXImportType.FBXIT_STATIC_MESH)
    data = options.static_mesh_import_data
    for key, value in dict(combine_meshes=True, auto_generate_collision=False,
                           convert_scene=True, convert_scene_unit=True,
                           generate_lightmap_u_vs=False).items():
        data.set_editor_property(key, value)
    task.options = options
    task.factory = unreal.FbxFactory()
    assets.import_asset_tasks([task])
    mesh = unreal.load_asset(folder + '/' + name)
    if not isinstance(mesh, unreal.StaticMesh):
        raise RuntimeError('Shared FBX import failed: ' + str(source))
    if materials:
        mesh_materials(source, folder, mesh)
    body = mesh.get_editor_property('body_setup')
    body.set_editor_property('collision_trace_flag', unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE if ground else unreal.CollisionTraceFlag.CTF_USE_DEFAULT)
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
    return mesh


vehicles=['rc_cessna','r1_rover']
if (base/'models/custom/custom.fbx').is_file(): vehicles.append('custom')
for vehicle in vehicles:
    import_mesh(base / 'models' / vehicle / (vehicle + '.fbx'), '/Game/Models/' + vehicle, vehicle)

terrain = material_asset('/Game/Worlds', 'M_Terrain')
texture = unreal.MaterialEditingLibrary.create_material_expression(terrain, unreal.MaterialExpressionTextureSampleParameter2D)
texture.set_editor_property('parameter_name', 'TerrainTexture')
texture.set_editor_property('texture', unreal.load_asset('/Engine/EngineResources/WhiteSquareTexture'))
unreal.MaterialEditingLibrary.connect_material_property(texture, 'RGB', unreal.MaterialProperty.MP_BASE_COLOR)
roughness = unreal.MaterialEditingLibrary.create_material_expression(terrain, unreal.MaterialExpressionConstant)
roughness.set_editor_property('r', .95)
unreal.MaterialEditingLibrary.connect_material_property(roughness, '', unreal.MaterialProperty.MP_ROUGHNESS)
unreal.MaterialEditingLibrary.recompile_material(terrain)
unreal.EditorAssetLibrary.save_loaded_asset(terrain)

mesh = import_mesh(base / 'worlds/baylands/baylands.fbx', '/Game/Worlds/BaylandsAssets', 'Baylands')
collision = import_mesh(base / 'worlds/baylands/baylands_collision.fbx', '/Game/Worlds/BaylandsAssets', 'BaylandsCollision', ground=True, materials=False)
world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
# FBX maps the canonical ENU scene to (east, south, up); the native PX4
# bridge uses Unreal X=north, Y=east. Vehicles retain their local X-forward.
actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(), unreal.Rotator(pitch=0, yaw=90, roll=0))
actor.set_actor_label('Shared Baylands')
actor.static_mesh_component.set_static_mesh(mesh)
actor.static_mesh_component.set_collision_profile_name('NoCollision')
ground = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(), unreal.Rotator(pitch=0, yaw=90, roll=0))
ground.set_actor_label('Shared Baylands collision')
ground.static_mesh_component.set_static_mesh(collision)
ground.static_mesh_component.set_collision_profile_name('BlockAll')
ground.set_actor_hidden_in_game(True)
ground.static_mesh_component.set_visibility(False)
sun = actors.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 10000), unreal.Rotator(pitch=-45, yaw=-30, roll=0))
sun.light_component.set_editor_property('intensity', 3.0)
sky = actors.spawn_actor_from_class(unreal.SkyLight, unreal.Vector())
sky.light_component.set_editor_property('intensity', 1.0)
if not unreal.EditorLoadingAndSavingUtils.save_map(world, '/Game/Worlds/baylands'):
    raise RuntimeError('Cannot save shared Baylands map')
unreal.log('Xovium shared native models and Baylands map imported successfully.')
