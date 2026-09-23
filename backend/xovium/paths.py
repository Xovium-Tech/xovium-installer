import json
import os
from pathlib import Path
import sys

LOCATION = ".xovium-location.json"
MOVE = ".xovium-move.json"


class Error(RuntimeError):
    pass


def read_json(path):
    if path.is_symlink():
        raise Error(f"Configuration must not be a symlink: {path}")
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise Error(f"Cannot read {path}: {exc}") from exc


def check_owner(data, root):
    renamed = False
    old = root.parent / "xovium-installer"
    if (root.name == "xovium-installer-v0.2.0" and data.get("root") == str(old)
            and not old.exists() and not old.is_symlink()):
        marker = root / LOCATION
        if marker.is_file() and not marker.is_symlink():
            owner = read_json(marker)
            renamed = (owner.get("root") == str(old) and owner.get("uid") == os.getuid()
                       and owner.get("schema") == 1)
    if (data.get("schema") != 1 or (data.get("root") != str(root) and not renamed)
            or data.get("uid") != os.getuid()):
        raise Error("Installation state belongs to a different path/user or schema. Keep the installer kit in its original location.")


def validate_path(root, value, *, legacy=False, alias=False):
    if not str(value).strip() or any(c in str(value) for c in "\n\r\0"):
        raise Error("Installation path must be a nonempty directory path without line breaks.")
    path = Path(os.path.abspath(Path(value).expanduser()))
    if path.is_symlink() and not alias:
        raise Error(f"Installation directory must not be a symlink: {path}")
    path = path.parent.resolve() / path.name
    old = legacy and path == root / ".xovium"
    if (path == Path.home().resolve() or root.is_relative_to(path)
            or (path.is_relative_to(root) and not old)):
        raise Error("Choose a dedicated installation directory outside the installer kit, not a home directory or a parent of the kit.")
    return path


def location(root, override=None):
    root = Path(root).resolve()
    if (root / MOVE).exists():
        raise Error("An interrupted relocation needs recovery. Rerun ./install.sh --relocate PATH with the original destination.")
    config = root / LOCATION
    if config.exists() or config.is_symlink():
        data = read_json(config)
        check_owner(data, root)
        current = validate_path(root, data.get("path", ""), legacy=True)
    else:
        legacy = root / ".xovium"
        current = validate_path(root, legacy if legacy.exists() or legacy.is_symlink() else root.parent / "xovium", legacy=True)
    if override is not None:
        requested = validate_path(root, override)
        if requested != current and (current / "state.json").exists():
            raise Error(f"Existing installation is at {current}. Use --relocate {requested} to move it; --prefix selects the location for a new installation.")
        return requested
    return current


if __name__ == "__main__":
    try:
        print(location(Path(sys.argv[1])))
    except Error as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
