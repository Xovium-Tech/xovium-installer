import json
from xovium.catalog import ISAAC_VERSION, QGC_VERSION, QGC_SHA256, QGC_RELEASES
from xovium.core import Error, validate_zip, digest
from installers.px4 import environment
from xovium.px4_compatibility import release
from xovium.layout import internal_link, native_project, simulator, adapt_assets
from xovium.simulation_profiles import launch_options


def qgc(ctx):
    version = ctx.config["qgc_version"]
    asset = "QGroundControl-x86_64.AppImage"
    sha = QGC_RELEASES.get(version, "")
    if not sha:
        metadata_file = ctx.download(f"https://api.github.com/repos/mavlink/qgroundcontrol/releases/tags/v{version}",
                                     f"downloads/qgc-{version}.json")
        metadata = json.loads(metadata_file.read_text())
        selected = next((a for a in metadata.get("assets", []) if a["name"] == asset), None)
        if not selected:
            raise Error(f"Release v{version} has no {asset} asset.")
        sha = (selected.get("digest") or "").removeprefix("sha256:")
    if len(sha) != 64:
        raise Error("QGC release does not publish a SHA-256 asset digest; refusing an unverified download.")
    app = ctx.download(f"https://github.com/mavlink/qgroundcontrol/releases/download/v{version}/{asset}",
                       f"tools/qgc/{version}/{asset}", sha256=sha)
    app.chmod(0o755)
    launcher = qgc_launcher(ctx)
    return [app, launcher]


def qgc_launcher(ctx):
    version = ctx.config["qgc_version"]
    app = ctx.prefix / f"tools/qgc/{version}/QGroundControl-x86_64.AppImage"
    from xovium.qgc_link import port, enabled
    return ctx.launcher("qgc", ["bash", ctx.prefix / "scripts/lib/qgc.sh", app],
                        {"QGC_COMPAT_GLIB": "1" if version in QGC_RELEASES else "0",
                         "XOVIUM_ROUTER_QGC_PORT": port(ctx), "XOVIUM_QGC_ROUTER_DEFAULT": "1" if enabled(ctx) else "0"})


def isaac_environment(ctx):
    result = environment(ctx)
    result.update({"ISAAC_ROOT": ctx.prefix / "simulation/isaac/runtime/lib/python3.12/site-packages/isaacsim",
                   "ISAAC_PYTHON": ctx.prefix / "simulation/isaac/runtime/bin/python",
                   "NATIVE_SDK": ctx.prefix / "simulation/isaac/sdk"})
    result.pop("DONT_RUN", None)
    return result


def isaac_runtime(ctx):
    simulator(ctx, "isaac")
    venv = ctx.managed("simulation/isaac/runtime")
    if not (venv / "bin/python").exists():
        ctx.run("python3.12", "-m", "venv", "--copies", venv)
    python = venv / "bin/python"
    ctx.pip_install(python, "--upgrade", "pip")
    ctx.pip_install(python, "torch==2.11.0", "--index-url", "https://download.pytorch.org/whl/cu128")
    ctx.pip_install(python, f"isaacsim[all,extscache]=={ISAAC_VERSION}",
            "pymavlink==2.4.49", "--extra-index-url", "https://pypi.nvidia.com")
    ctx.run(python, "-c", f"from importlib.metadata import version; import pymavlink; assert version('isaacsim') == '{ISAAC_VERSION}'")
    launch = ctx.launcher("isaac-sim", [venv / "bin/isaacsim"])
    return [python, venv / "lib/python3.12/site-packages/isaacsim/VERSION", launch]


def sih(ctx):
    simulator(ctx, "sih")
    for folder in ("models", "worlds"):
        ctx.copy_asset("isaac/SIH/" + folder, f"simulation/sih/{folder}")
    native = ctx.copy_asset("sih-native", "simulation/sih/bridge")
    for file in ("run_px4.sh", "px4_startup.sh"):
        ctx.copy_asset("isaac/SIH/" + file, "simulation/sih/bridge/" + file)
    adapt_assets(ctx, 'sih-router', native)
    env = isaac_environment(ctx)
    headers = ctx.prefix / "px4/source/build/px4_sitl_default/mavlink"
    ctx.run("cmake", "-S", native, "-B", native / "build", f"-DMAVLINK_INCLUDE_DIR={headers}",
            "-DCMAKE_BUILD_TYPE=Release")
    ctx.run("cmake", "--build", native / "build", "--parallel", ctx.options.jobs)
    px4 = ctx.launcher("sih-px4", ["bash", native / "run_px4.sh"], env)
    view = sih_view_launcher(ctx)
    return [px4, view, native / "build/libxovium_sih.so", native / "native_receiver.py",
            native / "isaac_view.py", native / "px4_startup.sh"]


def bind_px4_release(ctx, project, relative):
    version = ctx.config['px4_version']
    ctx.adapt_text(project / relative, [('@XOVIUM_PX4_COMMIT@', release(version)['commit']),
                                       ('@XOVIUM_PX4_VERSION@', version)])


def isaac(ctx):
    project = native_project(ctx, "isaac/Isaac sim", "isaac")
    adapt_assets(ctx, 'isaac-assets', project)
    bind_px4_release(ctx, project, 'build_native.sh')
    ctx.adapt_text(project / "run_isaac.sh", [("$SHARED_ROOT/.venv-isaac", "$SHARED_ROOT/runtime")])
    env = isaac_environment(ctx)
    ctx.run("bash", project / "build_native.sh", env=env)
    px4 = ctx.launcher("isaac-px4", ["bash", project / "run_px4.sh"], env)
    view = isaac_launcher(ctx)
    return [px4, view, project / "bin/isaac_native", project / "bin/px4_launcher",
            ctx.prefix / "simulation/isaac/plugins/px4.isaac.flight/bin/libpx4.isaac.flight.plugin.so",
            ctx.prefix / "simulation/isaac/plugins/px4.isaac.camera/bin/libpx4.isaac.camera.plugin.so",
            ctx.prefix / "simulation/isaac/plugins/px4.isaac.terrain/bin/libpx4.isaac.terrain.plugin.so"]


def unreal(ctx):
    simulator(ctx, "unreal-engine")
    project = ctx.copy_asset("unreal", "simulation/unreal-engine/bridge")
    adapt_assets(ctx, 'unreal-assets', project)
    adapt_assets(ctx, 'unreal-output', project)
    bind_px4_release(ctx, project, 'scripts/run_px4.sh')
    for folder, upstream in [("models", "Content/Models"), ("worlds", "Content/Worlds"), ("plugins", "Plugins")]:
        internal_link(ctx, f"simulation/unreal-engine/bridge/Unreal/{upstream}", f"simulation/unreal-engine/{folder}")
    engine = ctx.managed("simulation/unreal-engine/engine")
    env = environment(ctx)
    env.pop("DONT_RUN", None)
    env.update({"UE_ROOT": engine, "PX4_UNREAL_CONFIG": project / "config/xovium.env",
                "RUNTIME_DIR": project / ".runtime"})
    (project / "config/xovium.env").write_text("# Paths are supplied by the Xovium manager.\n")
    editor = engine / "Engine/Binaries/Linux/UnrealEditor"
    if not editor.exists():
        archive = ctx.options.unreal_engine
        if not archive:
            archive = ctx.config.get("unreal_archive")
        if not archive:
            raise Error("Unreal needs -unreal-engine /path/to/Linux_Unreal_Engine_5.6.1.zip.")
        validate_zip(archive)
        ctx.run("bash", project / "scripts/install_unreal.sh", "--archive", archive, env=env)
    else:
        ctx.run("bash", project / "scripts/install_unreal.sh", "--check", env=env)
        setup = engine / "Engine/Build/BatchFiles/Linux/SetupToolchain.sh"
        if setup.is_file():
            ctx.run("bash", setup, env=env)
    for script in (project / "scripts").glob("*.sh"):
        script.chmod(0o755)
    ctx.run("bash", project / "scripts/generate_mavlink.sh", env=env)
    ctx.run("bash", project / "scripts/build_unreal.sh", env=env)
    launchers = unreal_launchers(ctx)
    return [editor, project / "Unreal/Binaries/Linux/libUnrealEditor-PX4Unreal.so", *launchers]


def router(ctx):
    source = ctx.clone("https://github.com/mavlink-router/mavlink-router.git", "v4", "tools/mavlink-router/source",
                       "42529d55b665e0a9a29e424e186f514c56c2e5b5")
    build = ctx.managed("tools/mavlink-router/build")
    if not (build / "build.ninja").exists():
        ctx.run("meson", "setup", build, source, f"--prefix={ctx.prefix}/tools/mavlink-router",
                f"-Dsystemdsystemunitdir={ctx.prefix}/tools/mavlink-router/systemd")
    ctx.run("meson", "compile", "-C", build, "-j", ctx.options.jobs)
    ctx.run("meson", "install", "-C", build)
    binary = ctx.managed("tools/mavlink-router/bin/mavlink-routerd")
    from xovium.router_config import configure
    configure(ctx)
    config = ctx.managed("tools/mavlink-router/main.conf")
    launch = ctx.launcher("mavlink-router", [binary, "-c", config])
    return [binary, launch, config]


def sih_core(ctx):
    simulator(ctx, 'sih')
    for file in ('run_px4.sh', 'px4_startup.sh'):
        ctx.copy_asset('isaac/SIH/' + file, 'simulation/sih/bridge/' + file)
    adapt_assets(ctx, 'sih-router', ctx.prefix / 'simulation/sih/bridge')
    env = environment(ctx)
    env.pop('DONT_RUN', None)
    return [ctx.launcher('sih-px4', ['bash', ctx.prefix / 'simulation/sih/bridge/run_px4.sh'], env),
            ctx.managed('simulation/sih/bridge/run_px4.sh'), ctx.managed('simulation/sih/bridge/px4_startup.sh')]


def isaac_launcher(ctx):
    args, extra = launch_options(ctx, 'isaac')
    return ctx.launcher('isaac', ['bash', ctx.prefix / 'simulation/isaac/bridge/run_isaac.sh', *args],
                        {**isaac_environment(ctx), **extra})


def sih_view_launcher(ctx):
    args, extra = launch_options(ctx, 'sih')
    return ctx.launcher('sih-view', ['bash', ctx.prefix / 'simulation/sih/bridge/run_view.sh', *args],
                        {**isaac_environment(ctx), **extra})


def refresh_unreal_output(ctx):
    patches = json.loads((ctx.assets / 'patches/unreal-output.json').read_text())
    for relative, replacements in patches.items():
        path = ctx.managed('simulation/unreal-engine/bridge/' + relative)
        text = path.read_text()
        pending = [(old, new) for old, new in replacements if new not in text]
        if not pending:
            continue
        expected = ctx.state.get('copied_assets', {}).get(str(path.relative_to(ctx.prefix)))
        if digest(path) != expected:
            raise Error(f'Locally modified Unreal launcher: {path}. Preserve your edits before refreshing.')
        ctx.adapt_text(path, pending)


def unreal_launchers(ctx):
    project = ctx.prefix / 'simulation/unreal-engine/bridge'
    env = environment(ctx)
    env.pop('DONT_RUN', None)
    _, extra = launch_options(ctx, 'unreal-engine')
    env.update({'UE_ROOT': ctx.prefix / 'simulation/unreal-engine/engine',
                'PX4_UNREAL_CONFIG': project / 'config/xovium.env', 'RUNTIME_DIR': project / '.runtime', **extra})
    return [ctx.launcher(name, ['bash', project / 'scripts' / script], env)
            for name, script in [('unreal', 'run_sim.sh'), ('unreal-editor', 'run_editor.sh'), ('unreal-px4', 'run_px4.sh')]]
