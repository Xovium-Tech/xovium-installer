"""Download pinned terrain source releases and preserve managed-file ownership."""
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile

from xovium.core import Error, digest

# Keep existing installation paths so upgrades preserve project references.
GAZEBO_SOURCE = "source-bundled"
GAZEBO_BUILD = "build-bundled"
RELEASES = Path(__file__).with_name("terrain-releases.json")
REPOSITORIES = {"gazebo": "gz-dynamic-terrain-plugin", "unreal": "ue-dynamic-terrain-plugin",
                "isaac": "isaac-dynamic-terrain-plugin"}


def release(kind):
    try:
        data = json.loads(RELEASES.read_text())
        if data.get("schema") != 1:
            raise ValueError("unsupported release manifest schema")
        item = data["plugins"][kind]
        repo = REPOSITORIES[kind]
        tag = item["tag"]
        if (item["repository"] != "Xovium-Tech/" + repo
                or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
                or item["asset"] != repo + "-" + tag + ".zip"
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("invalid pinned release")
        return item
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise Error(f"Invalid {kind} terrain release manifest: {error}") from error


def source_entries(archive, root):
    """Validate the entire archive before writing any files."""
    entries = {}
    names = set()
    total = 0
    for item in archive.infolist():
        name = item.filename
        path = PurePosixPath(name)
        mode = item.external_attr >> 16
        if (not path.parts or path.is_absolute() or ".." in path.parts or "\\" in name
                or path.parts[0] != root or name.rstrip("/") != str(path)
                or str(path) in names or item.flag_bits & 1
                or stat.S_ISLNK(mode)
                or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))):
            raise Error(f"Unsafe terrain release archive member: {name}")
        names.add(str(path))
        total += item.file_size
        if len(names) > 10000 or total > 256 * 1024 * 1024:
            raise Error("Terrain source release exceeds extraction limits")
        if not item.is_dir():
            if len(path.parts) < 2:
                raise Error("Terrain release root must be a directory")
            entries[str(PurePosixPath(*path.parts[1:]))] = item
    if not {"README.md", "LICENSE"} <= set(entries):
        raise Error("Terrain source release is missing README or license")
    return entries


def verify(ctx, kind):
    item = release(kind)
    archive_root = item["asset"].removesuffix(".zip")
    cache = f"cache/terrain/{kind}/{item['sha256']}"
    url = f"https://github.com/{item['repository']}/releases/download/{item['tag']}/{item['asset']}"
    archive = ctx.download(url, cache + "/" + item["asset"], item["sha256"])
    if digest(archive) != item["sha256"]:
        raise Error(f"Terrain source checksum mismatch: {archive}")
    target = ctx.managed(cache + "/source")
    try:
        with zipfile.ZipFile(archive) as zipped:
            entries = source_entries(zipped, archive_root)
            # Compare the extracted disposable cache against the verified ZIP;
            # repairing it must never bypass ownership checks at installation.
            actual = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
            valid = (target.is_dir() and actual == set(entries)
                     and not any(p.is_symlink() for p in target.rglob("*")))
            if valid:
                for name, entry in entries.items():
                    path = target / name
                    if (path.is_symlink() or not path.resolve().is_relative_to(target.resolve())
                            or path.read_bytes() != zipped.read(entry)):
                        valid = False
                        break
            if not valid:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="extract-", dir=target.parent) as temporary:
                    staged = Path(temporary) / "source"
                    staged.mkdir()
                    for name, entry in entries.items():
                        path = staged / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(zipped.read(entry))
                        path.chmod(0o755 if (entry.external_attr >> 16) & 0o111 else 0o644)
                    if target.exists():
                        shutil.rmtree(target)
                    staged.replace(target)
        return target
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        raise Error(f"Cannot extract verified {kind} terrain release: {error}") from error


def install_source(ctx, kind, target):
    item = release(kind)
    source = verify(ctx, kind)
    destination = ctx.prefix / target
    changed = [path.relative_to(source) for path in source.rglob('*') if path.is_file()
               and (not (destination / path.relative_to(source)).is_file()
                    or digest(path) != digest(destination / path.relative_to(source)))]
    incoming = {str(Path(target) / p.relative_to(source)) for p in source.rglob('*') if p.is_file()}
    owned = ctx.state.get("copied_assets", {})
    obsolete = [name for name in owned if name.startswith(str(target) + "/") and name not in incoming]
    # Check all replacements/removals before touching the installed source tree.
    for name in obsolete + [str(Path(target) / p) for p in changed]:
        path = ctx.managed(name)
        if path.exists() and (not path.is_file() or digest(path) != owned.get(name)):
            raise Error(f"Locally modified managed terrain source: {path}. Preserve it before upgrading.")
    installed = ctx.copy_tree(source, target)
    for name in obsolete:
        ctx.managed(name).unlink(missing_ok=True)
        del ctx.state["copied_assets"][name]
    # ZIP timestamps must not hide changed headers from incremental compilers.
    for relative in changed:
        (installed / relative).touch()
    ctx.state.setdefault("terrain_releases", {})[kind] = dict(item)
    ctx.save()
    return installed
