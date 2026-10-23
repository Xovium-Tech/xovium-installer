#!/usr/bin/env python3
"""Vendor reproducible standalone terrain source snapshots into the installer.

Run after testing the sibling plugin checkouts. --check never changes files.
Generated builds and engine/SDK binaries are deliberately not distribution inputs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

INSTALLER = Path(__file__).resolve().parents[1]
NAMES = {"gazebo": "gz-dynamic-terrain-plugin", "unreal": "ue-dynamic-terrain-plugin",
         "isaac": "isaac-dynamic-terrain-plugin"}
SKIP = {".git", ".github", ".agents", ".codex", "__pycache__", ".pytest_cache",
        ".cache", "cache", "build", "dist", "Binaries", "Intermediate", "Saved",
        "DerivedDataCache", "CMakeFiles", ".sdk", "artifacts"}
SUFFIXES = {".o", ".a", ".so", ".pyc", ".log", ".zip", ".7z"}
REVISION = "28202c64196ffd7b50475e4df20ddf45d44b11a8"
UPSTREAM = "https://github.com/Xovium-Tech/gz-dynamic-terrain-plugin"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP for part in relative.parts):
            continue
        if any(part.startswith(("build-", "build_", ".build")) for part in relative.parts[:-1]):
            continue
        # build_example.sh creates a link back to the plugin for local builds.
        if relative.parts[:3] == ("examples", "TerrainDemo", "Plugins"):
            continue
        if path.is_symlink():
            raise ValueError(f"Standalone packages must contain real files: {path}")
        if not path.is_file() or path.suffix in SUFFIXES or ".so." in path.name:
            continue
        if path.name in {"CMakeCache.txt", "compile_commands.json", "AGENTS.md"}:
            continue
        result[relative.as_posix()] = digest(path)
    return result


def synchronize_shared(workspace, check=False):
    """Copy canonical code without any simulator SDK or generated build files."""
    canonical = workspace / NAMES["gazebo"]
    full = ["LICENSE"] + sorted(path.relative_to(canonical).as_posix()
        for folder in ("include/dynamic_terrain/core", "src/core")
        for path in (canonical / folder).rglob("*")
        if path.is_file() and path.suffix in {".hh", ".cc"})
    minimal = ["LICENSE", "include/dynamic_terrain/core/DownloadCache.hh",
               "include/dynamic_terrain/core/TerrainCoverage.hh",
               "src/core/DownloadCache.cc", "src/core/DownloadRetry.hh"]
    changes = []
    for kind, subfolder, names in (("unreal", "native/vendor/terrain", full),
                                    ("isaac", "vendor/terrain", minimal)):
        target = workspace / NAMES[kind] / subfolder
        hashes = {}
        for relative in names:
            source = canonical / relative
            if not source.is_file() or source.is_symlink():
                raise ValueError(f"Missing canonical shared source: {source}")
            hashes[relative] = digest(source)
            destination = target / relative
            if destination.is_symlink():
                raise ValueError(f"Shared source must not be a link: {destination}")
            if destination.is_file() and digest(destination) == hashes[relative]:
                continue
            changes.append(f"{NAMES[kind]}/{subfolder}/{relative}")
            if not check:
                destination.parent.mkdir(parents=True, exist_ok=True)
                # Changed source must get a fresh mtime: preserving an older
                # snapshot timestamp can make incremental CMake skip new code.
                shutil.copyfile(source, destination)
                shutil.copymode(source, destination)
        manifest = {"schema": 1, "source": UPSTREAM, "base_commit": REVISION,
                    "description": "Local optimized source snapshot; includes uncommitted workspace changes.",
                    "sha256": hashes}
        serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        path = target / "SOURCE_MANIFEST.json"
        if not path.is_file() or path.read_text() != serialized:
            changes.append(f"{NAMES[kind]}/{subfolder}/SOURCE_MANIFEST.json")
            if not check:
                target.mkdir(parents=True, exist_ok=True)
                path.write_text(serialized)
        if kind == "unreal":
            path = workspace / NAMES[kind] / "source-manifest.json"
            data = json.dumps({"schema": 1, "upstream": UPSTREAM, "revision": REVISION,
                "core": subfolder, "manifest": subfolder + "/SOURCE_MANIFEST.json",
                "local_changes": True}, indent=2, sort_keys=True) + "\n"
            if not path.is_file() or path.read_text() != data:
                changes.append(f"{NAMES[kind]}/source-manifest.json")
                if not check:
                    path.write_text(data)
    return changes


def synchronize(workspace, destination, check=False):
    manifest_path = destination / "SOURCE_MANIFEST.json"
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"plugins": {}}
    manifest = {"schema": 1, "upstream": UPSTREAM, "upstream_revision": REVISION, "plugins": {}}
    changes = []
    for kind, name in NAMES.items():
        source = workspace / name
        if not source.is_dir():
            raise ValueError(f"Missing standalone source: {source}")
        expected = files(source)
        if not expected:
            raise ValueError(f"Empty standalone source: {source}")
        target = destination / kind
        actual = files(target) if target.exists() else {}
        for relative, checksum in expected.items():
            if actual.get(relative) == checksum:
                continue
            if relative in actual:
                owned = previous.get("plugins", {}).get(kind, {}).get("files", {}).get(relative)
                if owned != actual[relative]:
                    raise ValueError(f"Untracked or locally modified snapshot file: {target / relative}")
            changes.append(f"{kind}/{relative}")
            if not check:
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, output)
                shutil.copymode(source / relative, output)
        for relative in actual.keys() - expected.keys():
            old = previous.get("plugins", {}).get(kind, {}).get("files", {}).get(relative)
            if old != actual[relative]:
                raise ValueError(f"Untracked or locally modified snapshot file: {target / relative}")
            changes.append(f"remove {kind}/{relative}")
            if not check:
                (target / relative).unlink()
        manifest["plugins"][kind] = {"source": name, "files": expected}
    serialized = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if not manifest_path.exists() or manifest_path.read_text() != serialized:
        changes.append("SOURCE_MANIFEST.json")
        if not check:
            destination.mkdir(parents=True, exist_ok=True)
            temporary = manifest_path.with_suffix(".tmp")
            temporary.write_text(serialized)
            temporary.replace(manifest_path)
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=INSTALLER.parent)
    parser.add_argument("--destination", type=Path, default=INSTALLER / "backend/assets/terrain")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--sync-core", action="store_true", help="Update both shared-code copies and revision/hash manifests from the canonical Gazebo folder first")
    args = parser.parse_args()
    try:
        workspace = args.workspace.resolve()
        changes = synchronize_shared(workspace, args.check) if args.sync_core else []
        if args.check and not args.sync_core:
            changes.extend(synchronize_shared(workspace, check=True))
        changes.extend(synchronize(workspace, args.destination.resolve(), args.check))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Terrain source synchronization failed: {error}\n")
    if args.check and changes:
        parser.exit(1, "Terrain snapshots differ; run tools/sync_terrain_plugins.py:\n" + "\n".join(changes) + "\n")
    print(f"Terrain source snapshots {'verified' if args.check else 'synchronized'} ({len(changes)} changed files).")


if __name__ == "__main__":
    main()
