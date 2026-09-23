import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import time
import zipfile

from xovium.paths import Error, LOCATION, check_owner, location, read_json, validate_path
from xovium.processes import assert_idle


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, data):
    temp = path.with_suffix(".tmp")
    if path.is_symlink() or temp.is_symlink():
        raise Error(f"Refusing to write configuration through a symlink: {path}")
    with temp.open("w") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


@contextlib.contextmanager
def operation_lock(root):
    with (root / ".xovium.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("Another Xovium operation is running.") from exc
        yield


class Context:
    def __init__(self, root, options):
        self.root = Path(root).resolve()
        self.previous_storage = location(self.root)
        self.storage = location(self.root, getattr(options, "prefix", None))
        self.prefix = self.storage
        self.state_file = self.storage / "state.json"
        self.options = options
        self.dry = options.dry_run
        self.assets = self.root / "backend/assets" if (self.root / "backend/assets").is_dir() else self.root / "assets"
        self.log = None
        self.state = {"schema": 1, "root": str(self.root), "uid": os.getuid(),
                      "components": {}, "packages": [], "system_files": {}, "config": {}}
        if self.state_file.exists():
            self.state = read_json(self.state_file)
            check_owner(self.state, self.root)
            self.state["root"] = str(self.root)
            if self.state.get("storage", str(self.storage)) != str(self.storage):
                raise Error("Installation was moved manually. Restore its path and use --relocate.")
            self.prefix = Path(self.state.get("build_prefix", str(self.storage)))
            self.validate_aliases()
            if self.state.get("uninstalling") and options.action != "uninstall":
                raise Error("An interrupted uninstall needs recovery. Rerun ./uninstall.sh.")
        elif self.storage.exists() and (not self.storage.is_dir() or any(self.storage.iterdir())):
            raise Error(f"Untracked installation directory found: {self.storage}. Choose an empty dedicated directory.")

    def validate_aliases(self):
        aliases = self.state.get("aliases", [])
        if self.prefix != self.storage and str(self.prefix) not in aliases:
            raise Error("Untracked build path in installation state.")
        for value in aliases:
            alias = validate_path(self.root, value, legacy=True, alias=True)
            if str(alias) == value and self.state.get("uninstalling") and not alias.exists() and not alias.is_symlink():
                continue
            if str(alias) != value or not alias.is_symlink() or alias.readlink() != self.storage:
                raise Error(f"Managed compatibility link was changed: {alias}")

    def remember_location(self):
        atomic_json(self.root / LOCATION, {"schema": 1, "root": str(self.root),
                    "uid": os.getuid(), "path": str(self.storage)})

    @contextlib.contextmanager
    def locked(self):
        with operation_lock(self.root):
            refreshed = Context(self.root, self.options)
            self.state = refreshed.state
            self.storage, self.prefix, self.state_file = refreshed.storage, refreshed.prefix, refreshed.state_file
            self.previous_storage = refreshed.previous_storage
            yield

    def save(self):
        if not self.dry:
            self.storage.mkdir(parents=True, exist_ok=True)
            self.state["storage"] = str(self.storage)
            atomic_json(self.state_file, self.state)
            self.remember_location()

    def start_log(self):
        if self.log:
            self.log.close()
        self.save()
        directory = self.prefix / "logs"
        directory.mkdir(exist_ok=True)
        self.log = (directory / f"install-{time.time_ns()}.log").open("a", buffering=1)

    def say(self, text):
        print(text, flush=True)
        if self.log:
            self.log.write(text + "\n")

    def run(self, *args, cwd=None, env=None, capture=False):
        args = [str(x) for x in args]
        if args and args[0] == "sudo" and os.environ.get("SUDO_ASKPASS"):
            args.insert(1, "-A")
        self.say("+ " + shlex.join(args))
        effective = dict(os.environ)
        for name in ("BASH_ENV", "ENV", "PYTHONHOME", "PYTHONPATH"):
            effective.pop(name, None)
        effective.update({"PIP_CACHE_DIR": str(self.prefix / "cache/pip"),
                          "CCACHE_DIR": str(self.prefix / "cache/ccache"),
                          "XDG_CACHE_HOME": str(self.prefix / "cache"),
                          "PYTHONNOUSERSITE": "1", "JOBS": str(self.options.jobs), "LC_ALL": "C"})
        effective.update({k: str(v) for k, v in (env or {}).items()})
        result = subprocess.run(args, cwd=cwd, env=effective, text=True,
                                stdout=subprocess.PIPE if capture else None,
                                stderr=subprocess.PIPE if capture else None)
        if result.returncode:
            summary = shlex.join(args)
            if len(summary) > 300:
                summary = shlex.join(args[:4]) + " ... (full command shown above)"
            raise Error(f"Command failed ({result.returncode}): {summary}\n{result.stderr or ''}")
        return result.stdout or ""

    def pip_install(self, python, *arguments):
        self.run(python, "-m", "pip", "--isolated", "--cache-dir", self.prefix / "cache/pip",
                 "install", *arguments, env={"PIP_CONFIG_FILE": os.devnull})

    def managed(self, relative):
        path = self.prefix / relative
        if not path.resolve().is_relative_to(self.storage) or path.is_symlink():
            raise Error(f"Path escapes managed installation or is a symlink: {path}")
        return path

    def copy_asset(self, name, target, *, omit=()):
        return self.copy_tree(self.assets / name, target, omit=omit)

    def copy_tree(self, source, target, *, omit=()):
        target = self.managed(target)
        files = [(p, target / p.relative_to(source)) for p in source.rglob("*")
                 if p.is_file() and not set(p.relative_to(source).parts).intersection(omit)] if source.is_dir() else [(source, target)]
        previous = self.state.setdefault("copied_assets", {})
        for original, destination in files:
            relative = str(destination.relative_to(self.prefix))
            self.managed(relative)
            expected = digest(original)
            if destination.exists():
                current = digest(destination)
                if current != expected and current != previous.get(relative):
                    raise Error(f"Locally modified managed source/config: {destination}. Preserve or move it aside before repairing this section.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
            previous[relative] = expected
        self.save()
        return target

    def adapt_text(self, path, replacements):
        text = path.read_text()
        for old, new in replacements:
            if old not in text:
                raise Error(f"Unsupported source layout in {path}: missing {old!r}")
            text = text.replace(old, new)
        path.write_text(text)
        self.state.setdefault("copied_assets", {})[str(path.relative_to(self.prefix))] = digest(path)
        self.save()

    def launcher(self, name, command, env=None, cwd=None):
        path = self.managed(f"scripts/.commands/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        variables = {"XDG_CACHE_HOME": self.prefix / "cache",
                     "XDG_CONFIG_HOME": self.prefix / "config",
                     "XDG_DATA_HOME": self.prefix / "data",
                     "CCACHE_DIR": self.prefix / "cache/ccache",
                     "PIP_CACHE_DIR": self.prefix / "cache/pip"}
        variables.update(env or {})
        lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
        lines += [f"export {k}={shlex.quote(str(v))}" for k, v in variables.items()]
        if cwd:
            lines.append("cd -- " + shlex.quote(str(cwd)))
        lines.append("exec " + shlex.join([str(x) for x in command]) + ' "$@"')
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o755)
        return path

    def download(self, url, relative, sha256=None):
        path = self.managed(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and (sha256 is None or digest(path) == sha256):
            return path
        part = path.with_suffix(path.suffix + ".part")
        self.run("curl", "--fail", "--location", "--retry", "3", "--connect-timeout", "30",
                 "--proto", "=https", "--proto-redir", "=https", "--output", part, url)
        if not part.stat().st_size or (sha256 and digest(part) != sha256):
            raise Error(f"Download checksum/size verification failed: {part}")
        part.replace(path)
        return path

    def clone(self, url, ref, relative, commit=None):
        path = self.managed(relative)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            if re.fullmatch(r"[0-9a-f]{40}", ref):
                self.run("git", "init", path)
                self.run("git", "-C", path, "remote", "add", "origin", url)
            else:
                self.run("git", "clone", "--branch", ref, "--depth", "1", url, path)
        if not (path / ".git").exists():
            raise Error(f"Incomplete Git checkout: {path}; move it aside and retry.")
        wanted = commit or ref
        probe = subprocess.run(["git", "-C", str(path), "rev-parse", "--verify", f"{wanted}^{{commit}}"],
                               capture_output=True, text=True)
        if probe.returncode:
            self.run("git", "-C", path, "fetch", "--depth", "1", "origin", ref)
            if commit is None:
                wanted = "FETCH_HEAD"
        expected = self.run("git", "-C", path, "rev-parse", f"{wanted}^{{commit}}", capture=True).strip()
        head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
        if head.returncode:
            self.run("git", "-C", path, "checkout", "--detach", expected)
        elif head.stdout.strip() != expected:
            raise Error(f"Existing checkout differs from requested revision: {path}. No reset was performed.")
        self.run("git", "-C", path, "submodule", "update", "--init", "--recursive", "--jobs", self.options.jobs)
        return path

    def installed_packages(self):
        result = self.run("dpkg-query", "-W", "-f=${binary:Package}\t${db:Status-Status}\n", capture=True)
        return {line.split("\t")[0] for line in result.splitlines() if line.endswith("\tinstalled")}

    def recover_apt(self):
        pending = self.state.get("apt_pending")
        if pending is None:
            return
        new = (self.installed_packages() - set(pending["before"]))
        allowed = set(pending["candidates"]) | {p.split(":")[0] for p in pending["candidates"]}
        new = {p for p in new if p in allowed or p.split(":")[0] in allowed}
        self.state["packages"] = sorted(set(self.state["packages"]) | new)
        del self.state["apt_pending"]
        self.save()

    def ensure_packages(self, packages):
        before = self.installed_packages()
        names = {p.split(":")[0] for p in before}
        missing = sorted(set(packages) - names)
        if not missing:
            self.say("[apt] All requested packages already installed.")
            return
        self.run("sudo", "apt-get", "update")
        simulated = self.run("apt-get", "--simulate", "install", "--no-install-recommends", "--no-remove",
                             *missing, capture=True)
        candidates = [line.split()[1] for line in simulated.splitlines() if line.startswith("Inst ")]
        self.state["apt_pending"] = {"before": sorted(before), "candidates": candidates}
        self.save()
        try:
            self.run("sudo", "apt-get", "install", "-y", "--no-install-recommends", "--no-remove", *missing)
        finally:
            self.recover_apt()

    def gazebo_repository(self):
        sources = [Path("/etc/apt/sources.list")]
        sources += list(Path("/etc/apt/sources.list.d").glob("*.list"))
        sources += list(Path("/etc/apt/sources.list.d").glob("*.sources"))
        if any(p.is_file() and "packages.osrfoundation.org/gazebo/ubuntu" in p.read_text() for p in sources):
            return
        key = self.download("https://packages.osrfoundation.org/gazebo.gpg", "downloads/gazebo.gpg")
        key_dest = "/usr/share/keyrings/xovium-gazebo.gpg"
        source_dest = "/etc/apt/sources.list.d/xovium-gazebo.list"
        source = self.managed("downloads/gazebo.list")
        source.write_text(f"deb [arch=amd64 signed-by={key_dest}] https://packages.osrfoundation.org/gazebo/ubuntu-stable noble main\n")
        for original, destination in [(key, key_dest), (source, source_dest)]:
            if Path(destination).exists():
                if self.state["system_files"].get(destination) != digest(destination):
                    raise Error(f"Refusing to overwrite an unowned/changed system file: {destination}")
            self.state["system_files"][destination] = digest(original)
            self.save()
            self.run("sudo", "install", "-m", "644", original, destination)


def validate_zip(archive):
    if not zipfile.is_zipfile(archive):
        raise Error("Unreal installer path must be the official Linux UE 5.6 ZIP archive.")
    with zipfile.ZipFile(archive) as zipped:
        links = {}
        for info in zipped.infolist():
            path = Path(info.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
                raise Error(f"Unsafe Unreal ZIP entry: {info.filename}")
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                links[str(path)] = zipped.read(info).decode("utf-8")
        def resolve_link(name, seen):
            if name in seen:
                raise Error("Cyclic Unreal ZIP symlink")
            target = links[name]
            if target.startswith("/") or "\\" in target:
                raise Error("Absolute Unreal ZIP symlink")
            parts = list(Path(name).parent.parts)
            for part in target.split("/"):
                if part == "..":
                    if not parts:
                        raise Error("Unreal ZIP symlink escapes the archive")
                    parts.pop()
                elif part not in {"", "."}:
                    parts.append(part)
                candidate = "/".join(parts)
                if candidate in links:
                    parts = resolve_link(candidate, seen | {name}).split("/")
            return "/".join(parts)
        for name in links:
            resolve_link(name, set())
        for name in zipped.namelist():
            if any(str(parent) in links for parent in Path(name).parents):
                raise Error("Unreal ZIP contains a write through a symlink")
        suffix = "Engine/Binaries/Linux/UnrealEditor"
        if sum(name.endswith(suffix) for name in zipped.namelist()) != 1:
            raise Error("Expected one Linux UnrealEditor in the official Unreal ZIP archive.")
        versions = [name for name in zipped.namelist() if name.endswith("Engine/Build/Build.version")]
        if len(versions) != 1:
            raise Error("Unreal ZIP has no unique Engine/Build/Build.version.")
        version = json.loads(zipped.read(versions[0]))
        if (version.get("MajorVersion"), version.get("MinorVersion")) != (5, 6):
            raise Error("The bundled Unreal project requires a Linux Unreal Engine 5.6 archive.")



def uninstall(ctx, keep_packages=False):
    if not ctx.state_file.exists():
        ctx.say("Nothing installed by this kit.")
        return
    current = Context(ctx.root, ctx.options)
    if current.storage != ctx.storage or current.state != ctx.state:
        raise Error("Installation state changed; retry uninstall.")
    ctx.say(f"Remove managed downloads, builds, apps, caches and runtime data: {ctx.storage}")
    ctx.say("Tracked apt packages: " + (", ".join(ctx.state["packages"]) or "none"))
    assert_idle([ctx.storage, ctx.prefix, *ctx.state.get("aliases", [])])
    if ctx.dry:
        if ctx.state.get("apt_pending"):
            ctx.say("Interrupted apt transaction: uninstall will first recover ownership of installed additions from: "
                    + ", ".join(ctx.state["apt_pending"]["candidates"]))
        ctx.say("Dry run: no changes. Original references and pre-existing packages are preserved.")
        return
    ctx.recover_apt()
    owned = set(ctx.state["packages"])
    if owned and not keep_packages:
        present = ctx.installed_packages() & owned
        if present:
            simulation = ctx.run("apt-get", "--simulate", "purge", *sorted(present), capture=True)
            removed = {line.split()[1] for line in simulation.splitlines() if line.startswith(("Remv ", "Purg "))}
            owned_names = owned | {p.split(":")[0] for p in owned}
            extra = removed - owned_names
            if extra:
                raise Error("APT would also remove unowned packages: " + ", ".join(sorted(extra))
                            + ". Resolve these dependents or use --keep-system-packages.")
            ctx.run("sudo", "apt-get", "purge", "-y", *sorted(present))
    elif owned:
        ctx.say("Keeping system packages by request; this is not a full system-package uninstall.")
    allowed_files = {"/usr/share/keyrings/xovium-gazebo.gpg", "/etc/apt/sources.list.d/xovium-gazebo.list"}
    for file, expected in ctx.state["system_files"].items():
        if file not in allowed_files:
            raise Error(f"Unknown system file in state: {file}")
        if Path(file).exists():
            if digest(file) != expected:
                raise Error(f"Managed system file changed since installation: {file}; preserve/resolve it before retrying.")
            if not keep_packages:
                ctx.run("sudo", "rm", "--", file)
    validate_path(ctx.root, ctx.storage, legacy=True)
    ctx.validate_aliases()
    ctx.state["uninstalling"] = True
    ctx.state.pop("build_prefix", None)
    ctx.prefix = ctx.storage
    ctx.save()
    for alias in ctx.state.get("aliases", []):
        Path(alias).unlink(missing_ok=True)
    for child in ctx.storage.iterdir():
        if child == ctx.state_file:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    ctx.state_file.unlink()
    ctx.storage.rmdir()
    from xovium.build_paths import cleanup as cleanup_build_alias
    cleanup_build_alias(ctx.state)
    ctx.say("Uninstalled. Installer code, bundled sources and original references retained.")
