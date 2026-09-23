DYNAMIC_TERRAIN_REF="${DYNAMIC_TERRAIN_REF:-637a53a220e415d0a566a1cb03889939d4917c89}"
DYNAMIC_TERRAIN_SOURCE="${DYNAMIC_TERRAIN_SOURCE:-}"

dynamic_terrain_root() { printf '%s/gz_dynamic_terrain_plugin\n' "$TARGET_HOME"; }
require_dynamic_terrain_backend() {
  case "$GAZEBO_VARIANT" in
    harmonic|jetty) return 0 ;;
    *) echo "ERROR: Dynamic terrain supports only Gazebo Harmonic or Jetty; use static ground with Gazebo Classic." >&2; return 1 ;;
  esac
}

dynamic_terrain_build_dir() { require_dynamic_terrain_backend || return; printf '%s/build-%s\n' "$(dynamic_terrain_root)" "$GAZEBO_VARIANT"; }

dynamic_terrain_required_libraries() {
  require_dynamic_terrain_backend || return
  printf '%s\n' libgz-dynamic-terrain-core.so
  case "$GAZEBO_VARIANT" in
    harmonic|jetty) printf '%s\n' libgz-dynamic-terrain-system.so ;;
    *) echo "ERROR: Unsupported terrain backend: $GAZEBO_VARIANT" >&2; return 1 ;;
  esac
}

dynamic_terrain_ready() {
  require_dynamic_terrain_backend || return
  local build lib
  build="$(dynamic_terrain_build_dir)"
  [[ -f "$build/CMakeCache.txt" ]] || return 1
  grep -Fxq "GZ_DISTRO:STRING=$GAZEBO_VARIANT" "$build/CMakeCache.txt" || return 1
  while IFS= read -r lib; do [[ -s "$build/$lib" ]] || return 1; done < <(dynamic_terrain_required_libraries)
}

dynamic_terrain_dependency_packages() {
  require_dynamic_terrain_backend || return
  printf '%s\n' build-essential cmake pkg-config ca-certificates curl tar \
    libcurl4-openssl-dev libopencv-dev libprotobuf-dev protobuf-compiler
  case "$GAZEBO_VARIANT" in
    harmonic) printf '%s\n' libgz-sim8-dev libgz-plugin2-dev libgz-rendering8-ogre2-dev ;;
    jetty) printf '%s\n' libgz-sim10-dev libgz-plugin4-dev libgz-rendering10-ogre2-dev ;;
    *) echo "ERROR: Unsupported terrain backend: $GAZEBO_VARIANT" >&2; return 1 ;;
  esac
}

install_dynamic_terrain_source() (
  set -euo pipefail
  require_dynamic_terrain_backend || return
  local root staging source
  root="$(dynamic_terrain_root)"
  staging="$(mktemp -d "${TMPDIR:-/tmp}/gt-terrain-source.XXXXXX")"
  trap 'rm -rf -- "$staging"' EXIT
  mkdir -p "$staging/source"
  if [[ -n "$DYNAMIC_TERRAIN_SOURCE" ]]; then
    source="$(realpath -- "$DYNAMIC_TERRAIN_SOURCE")"
    [[ -d "$source" && -f "$source/CMakeLists.txt" ]] || {
      echo "ERROR: DYNAMIC_TERRAIN_SOURCE must name an extracted source directory." >&2
      return 1
    }
    tar -C "$source" --exclude='./.git' --exclude='./build' --exclude='./build-*' -cf - . |
      tar -C "$staging/source" -xf -
  else
    [[ "$DYNAMIC_TERRAIN_REF" =~ ^[0-9a-fA-F]{40}$ ]] || {
      echo "ERROR: DYNAMIC_TERRAIN_REF must be an immutable 40-character commit." >&2
      return 1
    }
    curl --fail --location --retry 3 \
      "https://github.com/Xovium-Tech/gz-dynamic-terrain-plugin/archive/${DYNAMIC_TERRAIN_REF}.tar.gz" \
      --output "$staging/source.tar.gz"
    tar -xzf "$staging/source.tar.gz" --strip-components=1 -C "$staging/source"
  fi
  for source in CMakeLists.txt build.sh cmake/GazeboBackend.cmake \
      examples/harmonic/plugin_snippet.sdf examples/jetty/plugin_snippet.sdf; do
    [[ -s "$staging/source/$source" ]] || {
      echo "ERROR: Terrain source is missing $source" >&2
      return 1
    }
  done
  mkdir -p "$root"
  rm -rf -- "$root/source"
  mv "$staging/source" "$root/source"
)

build_dynamic_terrain() {
  require_dynamic_terrain_backend || return
  local root build lib jobs
  local -a packages=()
  root="$(dynamic_terrain_root)"
  build="$(dynamic_terrain_build_dir)"
  jobs="${DYNAMIC_TERRAIN_JOBS:-2}"
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: DYNAMIC_TERRAIN_JOBS must be positive." >&2; return 1; }
  mapfile -t packages < <(dynamic_terrain_dependency_packages)
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"
  install_dynamic_terrain_source
  rm -rf -- "$build"
  env CC=/usr/bin/gcc CXX=/usr/bin/g++ cmake -S "$root/source" -B "$build" \
    -DGZ_DISTRO="$GAZEBO_VARIANT" -DBUILD_SIMULATOR=ON \
    -DBUILD_GUI=OFF -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release
  env CC=/usr/bin/gcc CXX=/usr/bin/g++ cmake --build "$build" --parallel "$jobs"
  dynamic_terrain_ready || { echo "ERROR: Terrain build did not produce all selected libraries." >&2; return 1; }
}

dynamic_terrain_paths() {
  local px4="$TARGET_HOME/PX4-Autopilot"
  case "$GAZEBO_VARIANT" in
    classic)
      [[ "${DYNAMIC_GAZEBO_HEADLESS:-false}" != true ]] || { require_dynamic_terrain_backend; return 1; }
      : "${PX4_CLASSIC_REL:?Set PX4_CLASSIC_REL for the selected PX4 release}"
      DYNAMIC_TERRAIN_WORLD="$px4/$PX4_CLASSIC_REL/worlds/mcmillan_airfield.world"
      DYNAMIC_TERRAIN_MODEL="$px4/$PX4_CLASSIC_REL/models/plane_cam/plane_cam.sdf"
      DYNAMIC_TERRAIN_STATIC_MODEL="$px4/$PX4_CLASSIC_REL/models/uneven_ground"
      DYNAMIC_TERRAIN_STATIC_SOURCE="$SCRIPT_DIR/PX4-Autopilot/Tools/gz-classic/models/uneven_ground"
      DYNAMIC_TERRAIN_SERVER_CONFIG=""
      ;;
    harmonic|jetty)
      DYNAMIC_TERRAIN_WORLD="$px4/Tools/simulation/gz/worlds/mcmillan_airfield_gz.sdf"
      DYNAMIC_TERRAIN_MODEL="$px4/Tools/simulation/gz/models/rc_cessna/model.sdf"
      DYNAMIC_TERRAIN_STATIC_MODEL="$px4/Tools/simulation/gz/models/uneven_ground"
      DYNAMIC_TERRAIN_STATIC_SOURCE="$SCRIPT_DIR/PX4-Autopilot/Tools/gz-harmonic/models/uneven_ground"
      DYNAMIC_TERRAIN_SERVER_CONFIG="$px4/src/modules/simulation/gz_bridge/server.config"
      ;;
    *) echo "ERROR: Unsupported terrain backend: $GAZEBO_VARIANT" >&2; return 1 ;;
  esac
}

configure_dynamic_terrain_ground_mode() {
  if [[ "$DYNAMIC_GAZEBO_HEADLESS" == true ]]; then
    require_dynamic_terrain_backend || return
  fi
  dynamic_terrain_paths || return
  if [[ "$DYNAMIC_GAZEBO_HEADLESS" != true ]]; then
    [[ -d "$DYNAMIC_TERRAIN_STATIC_SOURCE" ]] || {
      echo "ERROR: Static ground source is missing: $DYNAMIC_TERRAIN_STATIC_SOURCE" >&2; return 1;
    }
    mkdir -p "$DYNAMIC_TERRAIN_STATIC_MODEL"
    cp -a "$DYNAMIC_TERRAIN_STATIC_SOURCE/." "$DYNAMIC_TERRAIN_STATIC_MODEL/"
  fi
  python3 - "$DYNAMIC_TERRAIN_WORLD" "$DYNAMIC_GAZEBO_HEADLESS" \
    "$DYNAMIC_TERRAIN_MODEL" "$DYNAMIC_TERRAIN_SERVER_CONFIG" \
    "$GAZEBO_VARIANT" "$DYNAMIC_TERRAIN_STATIC_MODEL" <<'PY_TERRAIN_GROUND'
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

world_path, dynamic, model_path, server_path, variant, static_model = sys.argv[1:]
def parse(path):
    return ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
def terrain(plugin):
    return plugin.tag == 'plugin' and (
        'dynamic-terrain' in plugin.get('filename', '') or
        plugin.get('name') in {'custom::DynamicTerrainSystem', 'custom::DynamicTerrainConfig', 'dynamic_terrain'})
def save(tree, path):
    path = Path(path)
    temporary = path.with_name(path.name + '.terrain.' + str(os.getpid()))
    tree.write(temporary, encoding='utf-8', xml_declaration=True)
    temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)
if variant == 'classic' and dynamic != 'true':
    static_path = Path(static_model) / 'model.sdf'
    static_tree = parse(static_path)
    static_root = static_tree.getroot()
    static_ground = static_root.find('model')
    if static_root.tag != 'sdf' or static_ground is None or static_ground.get('name') != 'uneven_ground':
        raise SystemExit('ERROR: Expected the kit uneven_ground model: ' + str(static_path))
    static_root.set('version', '1.6')
    for heightmap in static_ground.findall('.//visual/geometry/heightmap'):
        for size in heightmap.findall('texture/size'):
            values = (size.text or '').split()
            if len(values) == 3:
                size.text = str(max(float(values[0]), float(values[1])))
    save(static_tree, static_path)
    config_path = Path(static_model) / 'model.config'
    if config_path.is_file():
        config_tree = parse(config_path)
        for entry in config_tree.getroot().findall('sdf'):
            if (entry.text or '').strip() == 'model.sdf':
                entry.set('version', '1.6')
        save(config_tree, config_path)
tree = parse(world_path)
world = tree.getroot().find('world')
if world is None:
    raise SystemExit('ERROR: Terrain world has no top-level <world>: ' + world_path)
for child in list(world):
    if child.tag == 'include' and (child.findtext('uri') or '').strip() == 'model://uneven_ground':
        world.remove(child)
    elif dynamic != 'true' and terrain(child):
        world.remove(child)
if dynamic != 'true':
    ET.SubElement(ET.SubElement(world, 'include'), 'uri').text = 'model://uneven_ground'
save(tree, world_path)
if dynamic != 'true':
    for path in (model_path, server_path):
        if not path or not Path(path).is_file():
            continue
        tree = parse(path)
        for parent in tree.iter():
            for child in list(parent):
                if terrain(child):
                    parent.remove(child)
        save(tree, path)
PY_TERRAIN_GROUND
}

configure_dynamic_terrain_plugin() {
  require_dynamic_terrain_backend || return
  dynamic_terrain_paths || return
  python3 - "$(dynamic_terrain_root)/source" "$GAZEBO_VARIANT" \
    "$DYNAMIC_TERRAIN_WORLD" "$DYNAMIC_TERRAIN_MODEL" "$DYNAMIC_TERRAIN_SERVER_CONFIG" <<'PY_TERRAIN_CONFIG'
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

source, variant, world_path, model_path, server_path = sys.argv[1:]
def parse(path):
    return ET.parse(path, parser=ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
def terrain(plugin):
    return plugin.tag == 'plugin' and (
        'dynamic-terrain' in plugin.get('filename', '') or
        plugin.get('name') in {'custom::DynamicTerrainSystem', 'custom::DynamicTerrainConfig', 'dynamic_terrain'})
def remove_terrain(parent):
    for child in list(parent):
        if terrain(child):
            parent.remove(child)
def setting(plugin, name, value):
    for child in list(plugin):
        if child.tag == name:
            plugin.remove(child)
    ET.SubElement(plugin, name).text = value
def save(tree, path):
    path = Path(path)
    temporary = path.with_name(path.name + '.terrain.' + str(os.getpid()))
    tree.write(temporary, encoding='utf-8', xml_declaration=True)
    temporary.chmod(path.stat().st_mode & 0o777)
    temporary.replace(path)
world_tree = parse(world_path)
world = world_tree.getroot().find('world')
if world is None:
    raise SystemExit('ERROR: Terrain world has no top-level <world>: ' + world_path)
remove_terrain(world)
plugin = parse(Path(source) / ('examples/' + variant + '/plugin_snippet.sdf')).getroot()
if plugin.tag != 'plugin' or plugin.get('name') != 'custom::DynamicTerrainConfig':
    raise SystemExit('ERROR: Source example has no modern model terrain configuration')
setting(plugin, 'camera_names', 'camera_front,camera_down')
model_tree = parse(model_path)
model = model_tree.getroot().find('model')
if model is None:
    raise SystemExit('ERROR: Aircraft SDF has no top-level <model>: ' + model_path)
remove_terrain(model)
model.append(plugin)
server_tree = parse(server_path)
plugins = server_tree.getroot().find('plugins')
if plugins is None:
    raise SystemExit('ERROR: PX4 server.config has no <plugins>: ' + server_path)
remove_terrain(plugins)
sensors = [p for p in plugins if p.tag == 'plugin' and p.get('name') == 'gz::sim::systems::Sensors']
if len(sensors) != 1 or (sensors[0].findtext('render_engine') or '').strip() != 'ogre2':
    raise SystemExit('ERROR: Terrain requires exactly one existing Ogre2 Sensors system in PX4 server.config')
system = ET.Element('plugin', {'entity_name': '*', 'entity_type': 'world',
    'filename': 'libgz-dynamic-terrain-system.so', 'name': 'custom::DynamicTerrainSystem'})
plugins.insert(list(plugins).index(sensors[0]) + 1, system)
save(model_tree, model_path)
save(server_tree, server_path)
save(world_tree, world_path)
PY_TERRAIN_CONFIG
}
