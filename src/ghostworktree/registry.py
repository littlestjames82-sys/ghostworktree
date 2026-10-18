"""Atomic JSON registry at <worktrees-root>/registry.json. Never stores file/env contents."""
from __future__ import annotations
import json, os, tempfile
from contextlib import contextmanager
from pathlib import Path
from .errors import RegistryError

try:
    import fcntl
except ImportError:  # non-POSIX: locking degrades to no-op, atomic save still applies
    fcntl = None

REGISTRY_NAME = "registry.json"
SCHEMA = 1

@contextmanager
def locked(root: Path):
    """Exclusive advisory lock serialising registry read-modify-write cycles.

    Create and cleanup hold this across load -> allocate -> git -> save, so
    two concurrent processes cannot allocate the same port or overwrite
    each other's records. Reads (list/status) do not take it: save() swaps
    the file atomically, so a reader always sees a complete old or new file.
    """
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    fh = open(root / ".registry.lock", "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try: fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally: fh.close()

def default_root(repo: Path) -> Path:
    repo = Path(repo).resolve()
    return repo.parent / f"{repo.name}-worktrees"

def registry_path(root: Path) -> Path:
    return Path(root) / REGISTRY_NAME

def load(root: Path) -> dict:
    p = registry_path(root)
    if not p.exists():
        return {"schema": SCHEMA, "worktrees": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise RegistryError(f"registry is corrupt or unreadable: {p}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("worktrees"), list):
        raise RegistryError(f"registry has unexpected shape: {p}")
    for i, w in enumerate(data["worktrees"]):
        if not isinstance(w, dict):
            raise RegistryError(f"registry record #{i} in {p} is not an object")
        for field in ("name", "path", "branch"):
            if not isinstance(w.get(field), str) or not w[field]:
                raise RegistryError(
                    f"registry record #{i} in {p} is missing a valid {field!r} field")
        if w.get("port") is not None and not isinstance(w.get("port"), int):
            raise RegistryError(
                f"registry record #{i} in {p} has a non-integer 'port' field")
    return data

def save(root: Path, data: dict) -> Path:
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    p = registry_path(root)
    fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True); f.write("\n")
        os.replace(tmp, p)
    except Exception as e:
        try: os.unlink(tmp)
        except OSError: pass
        raise RegistryError(f"could not write registry: {p}: {e}") from e
    return p

def find(data: dict, name: str) -> dict | None:
    for w in data["worktrees"]:
        if w.get("name") == name: return w
    return None

def used_ports(data: dict) -> set[int]:
    return {int(w["port"]) for w in data["worktrees"] if w.get("port") is not None}
