from installers.px4 import paths, environment
from xovium.core import digest
from xovium.layout import simulator


def resources(ctx, distro):
    base = simulator(ctx, f"gazebo-{distro}")
    source, _ = paths(ctx)
    for folder in ("models", "worlds"):
        ctx.copy_tree(source / "Tools/simulation/gz" / folder, f"simulation/gazebo-{distro}/{folder}", omit=(".git",))
    from xovium.px4_compatibility import release
    server = source / "src/modules/simulation/gz_bridge/server.config"
    if release(ctx.config.get('px4_version', '1.16.0'))['legacy_models']:
        server = ctx.assets / 'patches/px4-1.15-server.config'
    ctx.copy_tree(server, f"simulation/gazebo-{distro}/bridge/server.config")
    if distro == ctx.config["backend"]:
        built = source / "build/px4_sitl_default/src/modules/simulation/gz_plugins"
        for library in built.rglob("*.so"):
            ctx.copy_tree(library, f"simulation/gazebo-{distro}/plugins/px4/{library.name}")
    return base


def gazebo_environment(ctx, distro):
    base = ctx.prefix / f"simulation/gazebo-{distro}"
    major, physics = (8, 7) if distro == "harmonic" else (10, 9)
    system = "/usr/lib/x86_64-linux-gnu"
    plugin_path = ":".join(str(base / path) for path in ("plugins/px4", "plugins/camera/build", "plugins/terrain/build"))
    plugin_path += f":{system}/gz-sim-{major}/plugins"
    return {"PX4_GZ_MODELS": base / "models", "PX4_GZ_WORLDS": base / "worlds",
            "PX4_GZ_PLUGINS": base / "plugins/px4", "PX4_GZ_SERVER_CONFIG": base / "bridge/server.config",
            "GZ_SIM_RESOURCE_PATH": f"{base}/models:{base}/worlds",
            "GZ_SIM_SYSTEM_PLUGIN_PATH": plugin_path, "GZ_SIM_SERVER_CONFIG_PATH": base / "bridge/server.config",
            "GZ_RENDERING_PLUGIN_PATH": f"{system}/gz-rendering-{major}/engine-plugins",
            "GZ_SIM_RENDER_ENGINE_PATH": f"{system}/gz-rendering-{major}/engine-plugins",
            "GZ_SIM_PHYSICS_ENGINE_PATH": f"{system}/gz-physics-{physics}/engine-plugins"}


def launchers(ctx, distro):
    major, transport, msgs = (8, 13, 10) if distro == "harmonic" else (10, 15, 12)
    selector = ctx.managed(f"simulation/gazebo-{distro}/bridge/gz")
    selector.parent.mkdir(parents=True, exist_ok=True)
    contents = (ctx.assets / "runtime/gazebo-selector.sh.in").read_text()
    values = {"SIM_MAJOR": major, "TRANSPORT_MAJOR": transport, "MSGS_MAJOR": msgs,
              "ENGINE_DIRECTORY": f"/usr/lib/x86_64-linux-gnu/gz-rendering-{major}/engine-plugins",
              "RENDERING_PREFIX": "gz-rendering8" if distro == "harmonic" else "gz-rendering"}
    for key, value in values.items():
        contents = contents.replace("@" + key + "@", str(value))
    selector.write_text(contents)
    selector.chmod(0o755)
    launcher = ctx.launcher(f"gazebo-{distro}", [selector, "sim"], gazebo_environment(ctx, distro))
    return [selector, launcher]


def install(ctx, distro):
    resources(ctx, distro)
    result = launchers(ctx, distro)
    ctx.run(result[0], "sim", "--versions")
    if distro == ctx.config["backend"]:
        result.append(px4_launcher(ctx))
    return result


def px4_launcher(ctx):
    source, _ = paths(ctx)
    backend = ctx.config["backend"]
    env = environment(ctx)
    env.pop("DONT_RUN", None)
    env["PATH"] = f"{ctx.prefix}/simulation/gazebo-{backend}/bridge:" + env["PATH"]
    env.update(gazebo_environment(ctx, backend))
    env["PX4_SIM_MODEL"] = "gz_rc_cessna"
    env["GZ_IP"] = "127.0.0.1"
    env["PX4_GZ_WORLD"] = "default"
    if ctx.config["plugins"]:
        env["PX4_GZ_WORLD"] = "mcmillan_airfield_gz"
    if ctx.config.get("wizard"):
        from xovium.profiles import gazebo_settings
        env.update(gazebo_settings(ctx, ctx.config["wizard"]))
    build = source / "build/px4_sitl_default"
    return ctx.launcher("px4-gazebo", ["bash", ctx.prefix / "scripts/lib/px4-console.sh",
                                      build / "bin/px4", build / "etc", "-w", build / "rootfs"], env)


def plugins(ctx):
    from xovium.simulation_profiles import get, selected
    wizard = ctx.config.get('wizard', {})
    backends = [d for d in ('jetty', 'harmonic') if selected(wizard, 'gazebo-' + d) and get(wizard, 'gazebo-' + d)['dynamic_terrain']]
    if not backends:
        backends = [ctx.config['backend']]
    outputs = []
    for distro in backends:
        outputs.extend(plugins_for(ctx, distro))
    return outputs


def plugins_for(ctx, backend):
    base = resources(ctx, backend)
    plugin_root = f"simulation/gazebo-{backend}/plugins"
    camera = ctx.clone("https://github.com/Xovium-Tech/px4-gazebo-gstreamer-camera-plugin.git", "v0.3.0",
                       plugin_root + "/camera/source", "6a2ab847fae0629b222867c09c19ca0cc77d6fdd")
    terrain = ctx.clone("https://github.com/Xovium-Tech/gz-dynamic-terrain-plugin.git",
                        "637a53a220e415d0a566a1cb03889939d4917c89", plugin_root + "/terrain/source")
    outputs = []
    for source, build, extra, libraries in [
        (camera, ctx.managed(plugin_root + "/camera/build"), [], ["libGstPlaneCameraSystem.so"]),
        (terrain, ctx.managed(plugin_root + "/terrain/build"),
         ["-DBUILD_SIMULATOR=ON", "-DBUILD_GUI=OFF"],
         ["libgz-dynamic-terrain-core.so", "libgz-dynamic-terrain-system.so"]),
    ]:
        ctx.run("cmake", "-S", source, "-B", build, f"-DGZ_DISTRO={backend}", "-DBUILD_TESTING=OFF",
                "-DCMAKE_BUILD_TYPE=Release", *extra, env={"CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++"})
        ctx.run("cmake", "--build", build, "--parallel", ctx.options.jobs)
        outputs.extend(build / name for name in libraries)
    if ctx.config.get('wizard'):
        return outputs
    for name in ["models", "worlds"]:
        ctx.copy_asset("gazebo/" + name, f"simulation/gazebo-{backend}/{name}")
    server = base / "bridge/server.config"
    contents = server.read_text().replace("libGstCameraSystem.so", "libGstPlaneCameraSystem.so").replace(
        "custom::GstCameraSystem", "custom::GstPlaneCameraSystem")
    server.write_text(contents)
    fragment = ctx.assets / "patches/dynamic-terrain-functions.sh"
    configure = '''source "$1"
dynamic_terrain_root() { printf '%s/plugins/terrain\\n' "$XOVIUM_SIM"; }
dynamic_terrain_paths() {
    DYNAMIC_TERRAIN_WORLD="$XOVIUM_SIM/worlds/mcmillan_airfield_gz.sdf"
    DYNAMIC_TERRAIN_MODEL="$XOVIUM_SIM/models/rc_cessna/model.sdf"
    DYNAMIC_TERRAIN_STATIC_MODEL="$XOVIUM_SIM/models/uneven_ground"
    DYNAMIC_TERRAIN_SERVER_CONFIG="$XOVIUM_SIM/bridge/server.config"
}
configure_dynamic_terrain_ground_mode
configure_dynamic_terrain_plugin
'''
    ctx.run("bash", "--noprofile", "--norc", "-euc", configure,
            "xovium-terrain", fragment, env={"XOVIUM_SIM": base, "GAZEBO_VARIANT": backend,
                                          "DYNAMIC_GAZEBO_HEADLESS": "true"})
    for path in [server, base / "worlds/mcmillan_airfield_gz.sdf", base / "models/rc_cessna/model.sdf"]:
        ctx.state["copied_assets"][str(path.relative_to(ctx.prefix))] = digest(path)
    ctx.save()
    outputs.extend([server, px4_launcher(ctx)])
    return outputs
