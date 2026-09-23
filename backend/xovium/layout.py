import os

from xovium.core import Error

SIMULATORS = ("gazebo-jetty", "gazebo-harmonic", "flightgear", "isaac", "sih", "unreal-engine")
FOLDERS = ("models", "bridge", "worlds", "plugins")
LAYOUT_VERSION = 2


def check_layout(ctx):
    if ctx.state.get("components") and ctx.state.get("layout_version") != LAYOUT_VERSION:
        raise Error("This installation uses the old directory layout. Uninstall it before installing the new simulation/ layout; save personal models first.")


def simulator(ctx, name):
    if name not in SIMULATORS:
        raise Error(f"Unknown simulator: {name}")
    base = ctx.managed(f"simulation/{name}")
    for folder in FOLDERS:
        ctx.managed(f"simulation/{name}/{folder}").mkdir(parents=True, exist_ok=True)
    return base


def internal_link(ctx, relative, target):
    link = ctx.prefix / relative
    target = ctx.managed(target)
    target.mkdir(parents=True, exist_ok=True)
    if not link.parent.resolve().is_relative_to(ctx.storage):
        raise Error(f"Internal project link escapes the installation: {link}")
    desired = os.path.relpath(target, link.parent)
    if link.is_symlink():
        if str(link.readlink()) != desired:
            raise Error(f"Internal project link changed locally: {link}")
    elif link.exists():
        raise Error(f"Internal project link would overwrite a file: {link}")
    else:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(desired, target_is_directory=True)


def native_project(ctx, asset, name):
    simulator(ctx, name)
    base = f"simulation/{name}"
    project = ctx.copy_asset(asset, base + "/bridge", omit=("models", "worlds", "extensions"))
    for original, folder in [("models", "models"), ("worlds", "worlds"), ("extensions", "plugins")]:
        if (ctx.assets / asset / original).is_dir():
            ctx.copy_asset(f"{asset}/{original}", f"{base}/{folder}")
            internal_link(ctx, f"{base}/bridge/{original}", f"{base}/{folder}")
    return project


def ensure_layout(ctx, *, check_only=False):
    check_layout(ctx)
    if check_only:
        return
    ctx.state["layout_version"] = LAYOUT_VERSION
    ctx.copy_asset("runtime/scripts", "scripts", omit=("local.env", "__pycache__"))
    requested = ctx.state.get("config", {}).get("requested", [])
    for name in SIMULATORS:
        if name in requested or (name == "isaac" and "sih" in requested) or (name == "sih" and "sih-core" in requested):
            simulator(ctx, name)
    ctx.save()


def refresh(ctx):
    if not ctx.state.get("components"):
        raise Error("Install a simulator section first; launcher refresh does not install dependencies.")
    check_layout(ctx)
    ctx.say("Refresh installed session scripts and native Isaac/SIH support.")
    if ctx.dry:
        ctx.say("Dry run: no packages, downloads, simulator rebuilds or files changed.")
        return
    ensure_layout(ctx)
    from installers import apps, gazebo, px4
    ctx.config = ctx.state["config"]
    from xovium.router_config import configure as configure_router
    configure_router(ctx)
    installed = ctx.state["components"]
    if installed.get("qgc", {}).get("status") == "installed":
        apps.qgc_launcher(ctx)
    if installed.get("flightgear", {}).get("status") == "installed":
        px4.flightgear_launcher(ctx)
    for distro in ("harmonic", "jetty"):
        if installed.get("gazebo-" + distro, {}).get("status") == "installed":
            gazebo.launchers(ctx, distro)
    if installed.get("gazebo-" + ctx.config["backend"], {}).get("status") == "installed":
        gazebo.px4_launcher(ctx)
    if installed.get("isaac", {}).get("status") == "installed":
        artifacts = apps.isaac(ctx)
        if not all(path.is_file() and path.stat().st_size for path in artifacts):
            raise Error("Native Isaac refresh did not produce its required artifacts.")
        installed["isaac"]["artifacts"] = [str(path.relative_to(ctx.prefix)) for path in artifacts]
        ctx.save()
    if ctx.state["components"].get("sih", {}).get("status") == "installed":
        artifacts = apps.sih(ctx)
        if not all(path.is_file() and path.stat().st_size for path in artifacts):
            raise Error("Native SIH refresh did not produce its required artifacts.")
        ctx.state["components"]["sih"]["artifacts"] = [str(path.relative_to(ctx.prefix)) for path in artifacts]
        ctx.save()
    if installed.get('unreal-engine', {}).get('status') == 'installed':
        apps.refresh_unreal_output(ctx)
    if ctx.config.get('wizard'):
        from xovium.profiles import apply
        apply(ctx, ctx.config['wizard'])
    from xovium.qgc_link import setup as setup_qgc_link
    setup_qgc_link(ctx)
    ctx.say(f"Launchers: {ctx.storage}/scripts\nSimulators: {ctx.storage}/simulation")


def adapt_assets(ctx, group, project):
    import json
    changes = json.loads((ctx.assets / ('patches/' + group + '.json')).read_text())
    for name, replacements in changes.items():
        ctx.adapt_text(project / name, replacements)
