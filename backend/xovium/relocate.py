import errno
import os
from pathlib import Path

from xovium.core import Context, assert_idle, atomic_json, operation_lock
from xovium.layout import ensure_layout
from xovium.paths import Error, LOCATION, MOVE, check_owner, read_json, validate_path


def relocate(root, options):
    root = Path(root).resolve()
    if options.dry_run:
        return _relocate(root, options)
    with operation_lock(root):
        return _relocate(root, options)


def _relocate(root, options):
    destination = validate_path(root, options.relocate)
    journal = root / MOVE
    if journal.exists() or journal.is_symlink():
        move = read_json(journal)
        check_owner(move, root)
        if move.get("destination") != str(destination):
            raise Error(f"Resume the interrupted relocation with --relocate {move.get('destination')}.")
    else:
        ctx = Context(root, options)
        if not ctx.state_file.exists():
            raise Error("Nothing installed to relocate. Use --prefix PATH with your section selection.")
        if ctx.storage.is_relative_to(root) or any(Path(p).is_relative_to(root) for p in ctx.state.get("aliases", [])):
            raise Error("The legacy installation lives inside the kit. Uninstall it and reinstall with --prefix; the installer no longer creates shortcuts to installed software.")
        if destination == ctx.storage:
            ctx.say(f"Already installed at {destination}.")
            return
        ensure_layout(ctx, check_only=True)
        move = {"schema": 1, "root": str(root), "uid": os.getuid(),
                "source": str(ctx.storage), "destination": str(destination),
                "build_prefix": str(ctx.prefix),
                "aliases": list(dict.fromkeys([*ctx.state.get("aliases", []), str(ctx.storage)]))}
    source = validate_path(root, move.get("source", ""), legacy=True, alias=True)
    aliases = [validate_path(root, value, legacy=True, alias=True) for value in move["aliases"]]
    if str(source) != move["source"] or source not in aliases or move["build_prefix"] not in move["aliases"]:
        raise Error("Invalid relocation journal paths.")
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise Error("Relocation source and destination must be separate directories.")
    for alias in aliases:
        if destination.is_relative_to(alias) or alias.is_relative_to(destination):
            raise Error("Destination overlaps a compatibility path.")
    moved = destination.exists()
    if moved and source.exists() and not source.is_symlink():
        raise Error(f"Destination already exists: {destination}. Choose a new directory.")
    actual = destination if moved else source
    state = read_json(actual / "state.json")
    check_owner(state, root)
    if state.get("storage", str(source)) not in {str(source), str(destination)}:
        raise Error("Relocation state does not match its source/destination.")
    if moved and not journal.exists():
        raise Error(f"Destination already exists: {destination}.")
    for alias in aliases:
        if alias == source and not moved:
            if alias.is_symlink():
                raise Error("Relocation source must be a real directory.")
            continue
        if alias.is_symlink():
            if alias.readlink() not in {source, destination}:
                raise Error(f"Compatibility link was redirected: {alias}")
        elif alias.exists():
            raise Error(f"Compatibility path would overwrite a file: {alias}")
        elif not journal.exists():
            raise Error(f"Missing managed compatibility link: {alias}")
    print(f"Move installation: {source}\nTo: {destination}\nKeep compatibility links for existing absolute build paths: "
          + ", ".join(str(path) for path in aliases), flush=True)
    if options.dry_run:
        print("Dry run: no files, packages or configuration changed.")
        return
    assert_idle([actual, *aliases], strict_cwd=True)
    if not journal.exists():
        atomic_json(journal, move)
    if not moved:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.rename(destination)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                journal.unlink()
                raise Error("Relocation requires the same filesystem (atomic rename). For another disk, uninstall and install with --prefix PATH; preserve personal models first.") from exc
            raise
    for alias in aliases:
        if alias.is_symlink():
            if alias.readlink() == destination:
                continue
            alias.unlink()
        alias.symlink_to(destination, target_is_directory=True)
    state.update({"storage": str(destination), "build_prefix": move["build_prefix"],
                  "aliases": [str(alias) for alias in aliases]})
    atomic_json(destination / "state.json", state)
    atomic_json(root / LOCATION, {"schema": 1, "root": str(root), "uid": os.getuid(), "path": str(destination)})
    journal.unlink()
    ensure_layout(Context(root, options))
    print(f"Relocation complete. Workspace: {destination}\nScripts: {destination / 'scripts'}\nSimulators: {destination / 'simulation'}")
