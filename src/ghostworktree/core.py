"""Core create / list / status / cleanup logic for GhostWorktree."""
from __future__ import annotations
import re
from datetime import datetime, timezone
from pathlib import Path
from . import envcopy, gitops, ports as portmod, registry as reg
from .errors import WorktreeError

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

def validate_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\\" in name or not _NAME_RE.match(name):
        raise WorktreeError(
            f"invalid worktree name {name!r}: use letters, digits, '-', '_', '.' as a single path component")
    return name

def _resolve_repo(repo) -> Path:
    return gitops.repo_root(Path(repo))

def _root_for(repo: Path, root=None) -> Path:
    return Path(root).resolve() if root else reg.default_root(repo)

def _reconcile(repo: Path, data: dict) -> list[dict]:
    """Annotate registry records with exists/missing from git's own list."""
    known = {str(Path(w["path"]).resolve()) for w in gitops.worktree_list(repo) if w.get("path")}
    out = []
    for w in data["worktrees"]:
        rec = dict(w)
        rec["exists"] = Path(w["path"]).is_dir() and str(Path(w["path"]).resolve()) in known
        rec["state"] = "present" if rec["exists"] else "missing"
        out.append(rec)
    return out

def create(repo, name, branch=None, base="HEAD", port=None, port_range=None,
           path=None, no_env=False, root=None) -> dict:
    validate_name(name)
    repo = _resolve_repo(repo)
    root = _root_for(repo, root)
    with reg.locked(root):
        return _create_locked(repo, name, branch=branch, base=base, port=port,
                              port_range=port_range, path=path, no_env=no_env, root=root)

def _create_locked(repo, name, branch=None, base="HEAD", port=None, port_range=None,
                   path=None, no_env=False, root=None) -> dict:
    data = reg.load(root)
    if reg.find(data, name):
        raise WorktreeError(f"worktree name {name!r} is already registered for this repo")
    dest = Path(path).resolve() if path else (root / name)
    if dest.exists():
        raise WorktreeError(f"destination already exists: {dest}")
    if str(dest) == str(repo) or repo in dest.parents and dest == repo:
        raise WorktreeError("destination must not be the main checkout")
    branch = branch or f"worktree/{name}"
    exists_branch = gitops.branch_exists(repo, branch)
    if not exists_branch and not gitops.ref_exists(repo, base):
        raise WorktreeError(f"base ref not found: {base}")
    # Check branch not already checked out elsewhere
    for w in gitops.worktree_list(repo):
        if w.get("branch") == branch:
            raise WorktreeError(f"branch {branch!r} is already checked out at {w.get('path')}")
    rng = portmod.parse_range(port_range) if port_range else portmod.DEFAULT_RANGE
    allocated = portmod.allocate(reg.used_ports(data), requested=port, rng=rng)
    dest.parent.mkdir(parents=True, exist_ok=True)
    gitops.worktree_add(repo, dest, branch, base, create_branch=not exists_branch)
    try:
        try:
            env_source = envcopy.seed_env(repo, dest, seed_dotenv=not no_env)
        except Exception:
            env_source = None  # env seeding must not orphan a created tree silently; record null
        rec = {
            "name": name,
            "repo": str(repo),
            "path": str(dest),
            "branch": branch,
            "base": base,
            "port": allocated,
            "env_source": env_source,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        data["worktrees"].append(rec)
        reg.save(root, data)
    except Exception:
        # Roll back the just-created tree: it is brand new (only env-example
        # seeding has touched it), so a forced remove discards nothing of the
        # user's. Leaving it would orphan a tree no registry record names.
        try: gitops.worktree_remove(repo, dest, force=True)
        except Exception: pass
        raise
    return {**rec, "exists": True, "state": "present"}

def list_worktrees(repo, root=None) -> list[dict]:
    repo = _resolve_repo(repo)
    root = _root_for(repo, root)
    return _reconcile(repo, reg.load(root))

def status_one(repo: Path, rec: dict) -> dict:
    out = {k: rec.get(k) for k in ("name", "path", "branch", "port", "base", "created_at", "env_source")}
    out["state"] = rec.get("state", "present")
    if not rec.get("exists", True) or not Path(rec["path"]).is_dir():
        out.update({"state": "missing", "dirty": None, "status": "missing",
                    "staged": None, "unstaged": None, "untracked": None, "changed": None})
        return out
    entries = gitops.status_porcelain(Path(rec["path"]))
    c = gitops.classify(entries)
    out.update({"dirty": c["dirty"], "status": "dirty" if c["dirty"] else "clean",
                "staged": c["staged"], "unstaged": c["unstaged"],
                "untracked": c["untracked"], "changed": c["total"],
                "current_branch": gitops.current_branch(Path(rec["path"]))})
    return out

def status(repo, name=None, root=None) -> list[dict]:
    repo = _resolve_repo(repo)
    root = _root_for(repo, root)
    recs = _reconcile(repo, reg.load(root))
    if name is not None:
        recs = [r for r in recs if r["name"] == name]
        if not recs:
            raise WorktreeError(f"no registered worktree named {name!r}")
    return [status_one(repo, r) for r in recs]

def cleanup(repo, name, force=False, delete_branch=False, root=None) -> dict:
    validate_name(name)
    repo = _resolve_repo(repo)
    root = _root_for(repo, root)
    with reg.locked(root):
        return _cleanup_locked(repo, name, force=force, delete_branch=delete_branch, root=root)

def _cleanup_locked(repo, name, force=False, delete_branch=False, root=None) -> dict:
    data = reg.load(root)
    rec = reg.find(data, name)
    if rec is None:
        raise WorktreeError(f"no registered worktree named {name!r}")
    dest = Path(rec["path"])
    present = dest.is_dir() and any(
        str(Path(w["path"]).resolve()) == str(dest.resolve())
        for w in gitops.worktree_list(repo) if w.get("path"))
    removed_tree, branch_deleted = False, False
    if present:
        entries = gitops.status_porcelain(dest)
        c = gitops.classify(entries)
        if c["dirty"] and not force:
            raise WorktreeError(
                f"refusing to clean up dirty worktree {name!r}: {c['total']} uncommitted "
                f"entr{'y' if c['total']==1 else 'ies'} ({c['staged']} staged, {c['unstaged']} unstaged, "
                f"{c['untracked']} untracked). Re-run with --force to discard uncommitted work.")
        gitops.worktree_remove(repo, dest, force=force)
        removed_tree = True
    else:
        gitops.worktree_prune(repo)
    data["worktrees"] = [w for w in data["worktrees"] if w["name"] != name]
    reg.save(root, data)
    branch_delete_error = None
    if delete_branch and rec.get("branch"):
        try:
            gitops.branch_delete_safe(repo, rec["branch"])
            branch_deleted = True
        except WorktreeError as e:
            # The tree cleanup itself succeeded; only the optional branch
            # deletion was refused (safe -d). That is a warning, not a
            # failed cleanup: the branch is kept, which is the safe state.
            branch_delete_error = str(e)
    return {"name": name, "path": rec["path"], "branch": rec.get("branch"),
            "port": rec.get("port"), "removed_tree": removed_tree,
            "was_missing": not present, "branch_deleted": branch_deleted,
            "branch_delete_error": branch_delete_error,
            "freed_port": rec.get("port")}
