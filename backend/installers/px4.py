import os
import shutil
import subprocess
from xovium.px4_compatibility import release, validate, notice
from xovium.core import Error
from xovium.layout import simulator, internal_link, adapt_assets


def paths(ctx):
    return ctx.managed("px4/source"), ctx.managed("px4/python")


def environment(ctx):
    source, venv = paths(ctx)
    return {"PX4_DIR": source, "PX4_ROOT": source, "PX4_VENV": venv,
            "XOVIUM_BUILD_ROOT": ctx.prefix,
            "VIRTUAL_ENV": venv, "PATH": f"{venv}/bin:{os.environ['PATH']}",
            "PYTHON_EXECUTABLE": venv / "bin/python", "CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++",
            "GZ_DISTRO": ctx.config["backend"], "DONT_RUN": "1"}


def apply_patch(ctx, source, filename):
    patch = ctx.assets / "patches" / filename
    if subprocess.run(["git", "-C", str(source), "apply", "--reverse", "--check", str(patch)],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        return
    ctx.run("git", "-C", source, "apply", "--check", patch)
    ctx.run("git", "-C", source, "apply", patch)


def adapt_runtime_paths(source):
    template = source / 'platforms/posix/src/px4/common/px4-alias.sh_in'
    old = 'R="`pwd`/"'
    new = old + '''
# Keep PX4 startup paths safe when the selected workspace contains spaces.
if [ -n "$XOVIUM_BUILD_ROOT" ]; then
    xovium_storage_root=$(cd -- "$XOVIUM_BUILD_ROOT" && pwd -P)
    case "$R" in
        "$xovium_storage_root/"*) R="$XOVIUM_BUILD_ROOT/$(realpath --relative-to="$xovium_storage_root" .)/" ;;
    esac
    unset xovium_storage_root
fi'''
    text = template.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise Error('Unsupported PX4 POSIX startup path template.')
    template.write_text(text.replace(old, new))


def adapt_source(ctx, source):
    adapt_runtime_paths(source)
    version = ctx.config['px4_version']
    info = release(version)
    if ctx.config.get('gazebo_enabled'):
        validate(version, ['gazebo-' + ctx.config['backend']])
        ctx.say(notice(version, ctx.config['backend']))
    if info['jetty'] == 'adapter' and ctx.config['backend'] == 'jetty' and ctx.config.get('gazebo_enabled'):
        apply_patch(ctx, source, "px4-v1.16.0-gazebo-jetty.patch")
    template = source / 'src/modules/simulation/gz_bridge/gz_env.sh.in'
    text = template.read_text()
    for key, fallback in [('PX4_GZ_MODELS','@PX4_SOURCE_DIR@/Tools/simulation/gz/models'),
                          ('PX4_GZ_WORLDS','@PX4_SOURCE_DIR@/Tools/simulation/gz/worlds'),
                          ('PX4_GZ_PLUGINS','@PX4_BINARY_DIR@/src/modules/simulation/gz_plugins'),
                          ('PX4_GZ_SERVER_CONFIG','@PX4_SOURCE_DIR@/src/modules/simulation/gz_bridge/server.config')]:
        if info['legacy_models'] and key in ('PX4_GZ_PLUGINS','PX4_GZ_SERVER_CONFIG'): continue
        old = f'export {key}={fallback}'
        new = f'export {key}="${{{key}:-{fallback}}}"'
        if old not in text and new not in text: raise Error(f'Unsupported PX4 {version} workspace template: {key}')
        text = text.replace(old,new)
    template.write_text(text)
    cmake = source / 'src/modules/simulation/gz_bridge/CMakeLists.txt'
    text = cmake.read_text()
    old = 'configure_file(gz_env.sh.in ${PX4_BINARY_DIR}/rootfs/gz_env.sh)'
    new = 'configure_file(gz_env.sh.in ${PX4_BINARY_DIR}/rootfs/gz_env.sh @ONLY)'
    if old not in text and new not in text: raise Error(f'Unsupported PX4 {version} Gazebo environment generation.')
    cmake.write_text(text.replace(old,new))


def build_sitl(ctx, source, board):
    _, venv = paths(ctx)
    build = source / 'build' / board
    if (build / 'CMakeCache.txt').exists():
        cache = (build / 'CMakeCache.txt').read_text()
        if ('CMAKE_GENERATOR:INTERNAL=Ninja' not in cache
                or f'CMAKE_HOME_DIRECTORY:INTERNAL={source}\n' not in cache):
            shutil.rmtree(build)
    ctx.run('cmake', '-S', source, '-B', build, '-G', 'Ninja', f'-DCONFIG={board}',
            f'-DPYTHON_EXECUTABLE={venv}/bin/python', env=environment(ctx))
    ctx.run('cmake', '--build', build, '--parallel', ctx.options.jobs, env=environment(ctx))
    return build


def install(ctx):
    info = release(ctx.config['px4_version'])
    source = ctx.clone("https://github.com/PX4/PX4-Autopilot.git", "v" + ctx.config["px4_version"],
                       "px4/source", info["commit"])
    _, venv = paths(ctx)
    if not (venv / "bin/python").exists():
        ctx.run("python3", "-m", "venv", "--copies", venv)
    requirements = ctx.run(venv / "bin/python", "-m", "pip", "--version", capture=True)
    ctx.say(requirements.strip())
    ctx.pip_install(venv / "bin/python", "-r", source / "Tools/setup/requirements.txt",
            "pymavlink==2.4.49", "numpy<2", "empy==3.3.4")
    adapt_source(ctx, source)
    build = source / "build/px4_sitl_default"
    old = ctx.state["components"].get("px4", {}).get("backend")
    if build.exists() and old != ctx.config["backend"]:
        shutil.rmtree(build)
    build_sitl(ctx, source, "px4_sitl_default")
    if ctx.config["gazebo_enabled"] and not info["legacy_models"]:
        ctx.run("cmake", "--build", build, "--target", "px4_gz_plugins", "--parallel", ctx.options.jobs,
                env=environment(ctx))
    executable = build / "bin/px4"
    if not executable.is_file():
        raise Error("PX4 SITL build did not produce its executable.")
    if ctx.config["gazebo_enabled"] and not (build / "bin/px4-gz_bridge").is_file():
        raise Error("PX4 built without its Gazebo bridge. Check the selected Gazebo development packages and repair the installation.")
    return [executable, venv / "bin/python", build / "etc/init.d-posix/airframes/10041_sihsim_airplane"]


def flightgear(ctx):
    source, _ = paths(ctx)
    base = simulator(ctx, "flightgear")
    upstream = source / "Tools/simulation/flightgear/flightgear_bridge"
    bridge = ctx.copy_tree(upstream, "simulation/flightgear/bridge", omit=(".git", "models", "build", "__pycache__"))
    ctx.copy_tree(upstream / "models", "simulation/flightgear/models")
    internal_link(ctx, "simulation/flightgear/bridge/models", "simulation/flightgear/models")
    for asset, destination in [("FG_run.py", "FG_run.py"), ("vehicle_state.cpp", "src/vehicle_state.cpp"),
                               ("Rascal110-Electric-YASim-set.xml", "models/Rascal/Rascal110-Electric-YASim-set.xml")]:
        relative = "simulation/flightgear/" + (destination if destination.startswith("models/") else "bridge/" + destination)
        ctx.copy_asset("flightgear/" + asset, relative)
    adapt_assets(ctx, 'flightgear-assets', bridge)
    (bridge / "FG_run.py").chmod(0o755)
    build = source / "build/px4_sitl_nolockstep"
    build_sitl(ctx, source, "px4_sitl_nolockstep")
    ctx.run("cmake", "-S", bridge, "-B", bridge / "build",
            f"-D_MAVLINK_INCLUDE_DIR={source}/build/px4_sitl_default/mavlink", env=environment(ctx))
    ctx.run("cmake", "--build", bridge / "build", "--parallel", ctx.options.jobs, env=environment(ctx))
    runner = ctx.copy_tree(source / "Tools/simulation/flightgear/sitl_run.sh", "simulation/flightgear/bridge/sitl_run.sh")
    ctx.adapt_text(runner, [('${src_path}/Tools/simulation/flightgear/flightgear_bridge/', '${XOVIUM_FG_BRIDGE}/'),
                            ('${build_path}/build_flightgear_bridge/flightgear_bridge', '${XOVIUM_FG_BRIDGE}/build/flightgear_bridge')])
    launch = flightgear_launcher(ctx)
    return [launch, build / "bin/px4", bridge / "build/flightgear_bridge"]


def flightgear_launcher(ctx):
    source, _ = paths(ctx)
    base = ctx.prefix / "simulation/flightgear"
    bridge = base / "bridge"
    build = source / "build/px4_sitl_nolockstep"
    px4 = ctx.launcher("flightgear-px4", ["bash", ctx.prefix / "scripts/lib/px4-console.sh", build / "bin/px4"])
    from xovium.simulation_profiles import get, launch_options
    item = get(ctx.config.get('wizard', {}), 'flightgear')
    model = item['model_base'] if item['model'] == 'custom' else item['model']
    _, extra = launch_options(ctx, 'flightgear')
    return ctx.launcher("flightgear", ["bash", bridge / "sitl_run.sh", px4, model, source, build],
                        env={"FG_HOME": base / "runtime", "XOVIUM_FG_BRIDGE": bridge,
                             "FG_SCENERY": f"{base}/worlds:/usr/share/games/flightgear/Scenery", **extra})
