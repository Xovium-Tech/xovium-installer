#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys

from installers import apps, gazebo, px4
from xovium.catalog import PUBLIC, PX4_VERSION, QGC_VERSION, dependencies, packages_for, resolve
from xovium.core import Context, Error, uninstall, validate_zip
from xovium.layout import check_layout, ensure_layout, refresh
from xovium.relocate import relocate
from xovium import px4_compatibility as compatibility

ROOT = Path(os.environ.get("XOVIUM_KIT_ROOT", Path(__file__).resolve().parent)).resolve()


def parser():
    cli = argparse.ArgumentParser(description="Xovium: modular simulator installer for Ubuntu 24.04 x86-64.",
                                  epilog="Example: ./install.sh -PX4 1.16.0 -QGC -gazebo-harmonic -gazebo-plugins")
    cli.add_argument("action", nargs="?", choices=["install", "uninstall", "status"], default="install")
    cli.add_argument("-PX4", "-px4", "--px4", nargs="?", const=PX4_VERSION, metavar="VERSION")
    cli.add_argument("--px4-version", metavar="VERSION")
    cli.add_argument("-QGC", "-qgc", "--qgc", nargs="?", const=QGC_VERSION, metavar="VERSION")
    cli.add_argument("--qgc-version", metavar="VERSION")
    for name in PUBLIC:
        if name in {"px4", "qgc", "unreal-engine"}:
            continue
        flags = ["-" + name, "--" + name]
        if name == "sih":
            flags += ["-SIH", "--SIH"]
        cli.add_argument(*flags, action="store_true")
    cli.add_argument("-unreal-engine", "--unreal-engine", nargs="?", const="", metavar="UE_ZIP",
                     help="Official Linux UE 5.6 ZIP; omit path only when already installed")
    cli.add_argument("--all", action="store_true", help="All sections, both Gazebo distributions; Unreal archive required initially")
    cli.add_argument("--gazebo-backend", choices=["harmonic", "jetty"], help="PX4/plugin ABI; remembered between runs")
    cli.add_argument("--jobs", type=int, default=4)
    cli.add_argument("--prefix", metavar="PATH", help="New installation directory; remembered. Default: sibling folder ../xovium")
    cli.add_argument("--relocate", metavar="PATH", help="Move this kit's existing installation on the same filesystem; retain compatibility links")
    cli.add_argument("--print-prefix", action="store_true", help="Print the actual installation directory for scripts")
    cli.add_argument("--dry-run", action="store_true", help="Print plan without writing files or running installers")
    cli.add_argument("--status", action="store_true")
    cli.add_argument("--refresh-launchers", action="store_true", help="Refresh installed scripts and C++ Isaac/SIH support; no apt or simulator reinstall")
    cli.add_argument("--keep-system-packages", action="store_true", help="Uninstall local files while retaining apt packages and repository")
    return cli


def selection(options):
    selected = [name for name in PUBLIC if name != "sih"] if options.all else []
    if options.px4 is not None or options.px4_version is not None:
        selected.append("px4")
    if options.qgc is not None or options.qgc_version is not None:
        selected.append("qgc")
    if options.unreal_engine is not None:
        selected.append("unreal-engine")
    selected += [name for name in PUBLIC if name not in {"px4", "qgc", "unreal-engine"}
                 and getattr(options, name.replace("-", "_"))]
    return list(dict.fromkeys(selected))


def configure(ctx, selected):
    check_layout(ctx)
    old = ctx.state.get("config", {}) if ctx.state.get("components") else {}
    requested = list(dict.fromkeys(old.get("requested", []) + selected))
    backend = ctx.options.gazebo_backend or old.get("backend")
    if not backend:
        backend = "jetty" if "gazebo-jetty" in requested and "gazebo-harmonic" not in requested else "harmonic"
    pv = (ctx.options.px4_version or ctx.options.px4 or old.get("px4_version", PX4_VERSION)).removeprefix("v")
    qv = (ctx.options.qgc_version or ctx.options.qgc or old.get("qgc_version", QGC_VERSION)).removeprefix("v")
    compatibility.release(pv)
    if not re.fullmatch(r"5\.[0-9]+\.[0-9]+", qv):
        raise Error("QGC version must be a stable 5.x.y release (default 5.0.8).")
    if ctx.options.gazebo_backend and not any(n.startswith("gazebo-") for n in requested):
        requested.append(f"gazebo-{backend}")
    if any(n.startswith("gazebo-") for n in requested) and f"gazebo-{backend}" not in requested:
        requested.append(f"gazebo-{backend}")
    current = list(selected)
    if ctx.options.gazebo_backend:
        current.append(f"gazebo-{backend}")
    compatibility.validate(pv, current)
    previous_px4 = old.get('px4_version')
    if previous_px4 and pv != previous_px4 and ('px4' in ctx.state.get('components', {}) or (ctx.prefix / 'px4/source').exists()):
        choice = f'PX4 {pv}'
        if ctx.options.gazebo_backend:
            choice += f' with Gazebo {backend.title()}'
        raise Error(f'{choice} is supported, but this workspace contains PX4 {previous_px4}. Keep PX4 {previous_px4} to change simulators here, or use Manage installation to uninstall this workspace before installing PX4 {pv}. The installer does not overwrite an existing PX4 checkout with another release.')
    compatibility.validate(pv, requested)
    config = {"requested": requested, "backend": backend, "px4_version": pv, "qgc_version": qv,
              "plugins": "gazebo-plugins" in requested,
              "gazebo_enabled": any(n.startswith("gazebo-") for n in requested)}
    archive = ctx.options.unreal_engine or old.get("unreal_archive")
    if archive:
        config["unreal_archive"] = str(Path(archive).expanduser().resolve())
        ctx.options.unreal_engine = config["unreal_archive"]
    if "wizard" in old:
        config["wizard"] = old["wizard"]
    ctx.config = config
    return resolve(requested, backend)


def recipe_hash(ctx):
    sha = hashlib.sha256()
    source_root = ctx.root / "backend" if (ctx.root / "backend/xovium_installer.py").is_file() else ctx.root
    files = [source_root / "xovium_installer.py"]
    for folder in ["xovium", "installers", "assets"]:
        files += sorted(p for p in (source_root / folder).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    for file in files:
        sha.update(str(file.relative_to(source_root)).encode())
        sha.update(file.read_bytes())
    return sha.hexdigest()


def fingerprints(ctx, plan):
    recipe = recipe_hash(ctx)
    result = {}
    for name in plan:
        config = {}
        if name == "px4":
            config = {key: ctx.config[key] for key in ["px4_version", "backend", "gazebo_enabled"]}
        elif name == "qgc":
            config = {"version": ctx.config["qgc_version"]}
        elif name.startswith("gazebo"):
            config = {key: ctx.config[key] for key in ["backend", "plugins"]}
        if name == 'gazebo-plugins':
            from xovium.simulation_profiles import get, selected
            wizard = ctx.config.get('wizard', {})
            config['terrain_backends'] = [d for d in ('jetty', 'harmonic') if selected(wizard, 'gazebo-' + d) and get(wizard, 'gazebo-' + d)['dynamic_terrain']]
        payload = {"recipe": recipe, "config": config,
                   "dependencies": [result[n] for n in dependencies(name, ctx.config["backend"])]}
        result[name] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return result


def ready(ctx, name, fingerprint=None):
    entry = ctx.state["components"].get(name, {})
    if entry.get("status") != "installed" or (fingerprint and entry.get("fingerprint") != fingerprint):
        return False
    artifacts = entry.get("artifacts", [])
    if not artifacts:
        return False
    for artifact in artifacts:
        path = ctx.managed(artifact)
        if not path.is_file() or path.stat().st_size == 0:
            return False
    return True


def preflight(ctx, plan):
    if os.geteuid() == 0:
        raise Error("Run as your normal user, without sudo. Only apt/repository operations use sudo.")
    release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines() if "=" in line)
    if release.get("ID", "").strip('"') != "ubuntu" or release.get("VERSION_ID", "").strip('"') != "24.04" or platform.machine() != "x86_64":
        raise Error("This initial kit supports Ubuntu 24.04 x86-64.")
    if "isaac-runtime" in plan:
        if not shutil.which("nvidia-smi"):
            raise Error("Isaac requires a working NVIDIA RTX driver. Install/check it before running this section.")
        ctx.run("nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader")
    if "unreal-engine" in plan and not ctx.managed("simulation/unreal-engine/engine/Engine/Binaries/Linux/UnrealEditor").is_file():
        archive = ctx.config.get("unreal_archive")
        if not archive or not Path(archive).is_file():
            raise Error("Supply -unreal-engine /path/to/the/official/Linux_Unreal_Engine_5.6.1.zip.")
        validate_zip(archive)


def install_plan(ctx, plan):
    compatibility.validate(ctx.config['px4_version'], ctx.config['requested'])
    if ctx.config.get('wizard'): compatibility.validate_profile(ctx.config['wizard'])
    for distro in ('harmonic', 'jetty'):
        if 'gazebo-' + distro in ctx.config['requested']: ctx.say(compatibility.notice(ctx.config['px4_version'], distro))
    signatures = fingerprints(ctx, plan)
    ctx.say(f"PX4 {ctx.config['px4_version']}; QGC {ctx.config['qgc_version']}; Gazebo backend {ctx.config['backend']}")
    ctx.say("Shared install directory: " + str(ctx.storage))
    ctx.say("Order: " + " -> ".join(plan))
    for name in plan:
        ctx.say(f"  {'reuse' if ready(ctx, name, signatures[name]) else 'install/repair'}: {name}")
    packages = packages_for(plan, ctx.config["backend"])
    ctx.say("APT checks once, installs only missing packages: " + " ".join(packages))
    if ctx.dry:
        from xovium.build_paths import prepare as prepare_build_path
        prepare_build_path(ctx, plan)
        from xovium.qgc_link import setup as setup_qgc_link
        from xovium.router_config import configure as configure_router
        configure_router(ctx)
        setup_qgc_link(ctx)
        if "unreal-engine" in plan and not ctx.config.get("unreal_archive"):
            ctx.say("Before installation: provide the official UE 5.6 Linux ZIP with -unreal-engine PATH.")
        ctx.say("Dry run complete; no downloads, builds, package changes or state writes.")
        return
    preflight(ctx, plan)
    from xovium.build_paths import prepare as prepare_build_path
    prepare_build_path(ctx, plan)
    ctx.state["config"] = ctx.config
    ctx.start_log()
    ensure_layout(ctx)
    ctx.recover_apt()
    if any(n.startswith("gazebo-") for n in plan):
        ctx.ensure_packages(["ca-certificates", "curl"])
        ctx.gazebo_repository()
    ctx.ensure_packages(packages)
    handlers = {"px4": px4.install, "qgc": apps.qgc, "flightgear": px4.flightgear,
                "isaac-runtime": apps.isaac_runtime, "isaac": apps.isaac, "sih": apps.sih, "sih-core": apps.sih_core,
                "unreal-engine": apps.unreal, "mavlink-router": apps.router,
                "gazebo-plugins": gazebo.plugins,
                "gazebo-harmonic": lambda c: gazebo.install(c, "harmonic"),
                "gazebo-jetty": lambda c: gazebo.install(c, "jetty")}
    rebuilt = set()
    for name in plan:
        dependency_rebuilt = rebuilt.intersection(dependencies(name, ctx.config["backend"]))
        if ready(ctx, name, signatures[name]) and not dependency_rebuilt:
            ctx.say(f"[reuse] {name}")
            continue
        ctx.say(f"[install] {name}")
        entry = ctx.state["components"].setdefault(name, {})
        entry.update({"status": "installing", "fingerprint": signatures[name]})
        ctx.save()
        try:
            artifacts = handlers[name](ctx)
            if not all(p.is_file() and p.stat().st_size for p in artifacts):
                raise Error(f"{name} did not produce all required artifacts: {artifacts}")
            entry.update({"status": "installed", "artifacts": [str(p.relative_to(ctx.prefix)) for p in artifacts],
                          "backend": ctx.config["backend"]})
            rebuilt.add(name)
        except BaseException:
            entry["status"] = "failed"
            ctx.save()
            raise
        ctx.save()
    from xovium.qgc_link import setup as setup_qgc_link
    from xovium.router_config import configure as configure_router
    configure_router(ctx)
    if "qgc" in ctx.config["requested"]: apps.qgc_launcher(ctx)
    setup_qgc_link(ctx)
    if ctx.config.get("wizard"):
        from xovium.profiles import apply
        apply(ctx, ctx.config["wizard"])
        ctx.save()
    ctx.say(f"Installation complete. Session scripts: {ctx.storage}/scripts\nModels, worlds, bridges and plugins: {ctx.storage}/simulation\nRerun the same command to check/repair; use ./install.sh --uninstall to remove this installation.")


def main(argv=None):
    cli = parser()
    options = cli.parse_args(argv)
    if options.jobs < 1 or options.jobs > 128:
        cli.error("--jobs must be from 1 to 128")
    if options.relocate:
        if (options.action != "install" or selection(options) or options.prefix or options.status
                or options.refresh_launchers or options.gazebo_backend or options.keep_system_packages or options.print_prefix):
            cli.error("Use --relocate PATH on its own, optionally with --dry-run")
        relocate(ROOT, options)
        return 0
    ctx = Context(ROOT, options)
    if options.print_prefix:
        if options.action != "install" or selection(options) or options.refresh_launchers or options.status or options.gazebo_backend:
            cli.error("Use --print-prefix on its own")
        print(ctx.storage)
        return 0
    if options.refresh_launchers:
        if options.action != "install" or selection(options) or options.gazebo_backend or options.status:
            cli.error("--refresh-launchers is used on its own, optionally with --jobs or --dry-run")
        if options.dry_run:
            refresh(ctx)
        else:
            with ctx.locked():
                refresh(ctx)
        return 0
    if options.status or options.action == "status":
        if not ctx.state.get("components"):
            print(f"Nothing installed by this kit. Installation directory: {ctx.storage}")
        else:
            print("Managed installation: " + str(ctx.storage))
            if ctx.prefix != ctx.storage:
                print("Compatibility build path: " + str(ctx.prefix))
            config = ctx.state["config"]
            print(f"PX4 {config.get('px4_version', '?')}; QGC {config.get('qgc_version', '?')}; backend {config.get('backend', '?')}")
            for name, entry in ctx.state["components"].items():
                print(f"{name}: {entry['status']} ({'artifacts present' if ready(ctx, name) else 'incomplete'})")
        return 0
    if options.action == "uninstall":
        if selection(options):
            cli.error("Uninstall removes all components managed by this kit; do not pass section flags.")
        if options.dry_run:
            uninstall(ctx, options.keep_system_packages)
        else:
            with ctx.locked():
                uninstall(ctx, options.keep_system_packages)
        return 0
    if options.keep_system_packages:
        cli.error("--keep-system-packages is an uninstall option")
    selected = selection(options)
    if not selected and not options.gazebo_backend:
        cli.print_help()
        return 0
    if options.dry_run:
        install_plan(ctx, configure(ctx, selected))
    else:
        with ctx.locked():
            install_plan(ctx, configure(ctx, selected))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Error, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Interrupted. Completed sections are recorded; rerun to resume or use ./install.sh --uninstall.", file=sys.stderr)
        raise SystemExit(130)
