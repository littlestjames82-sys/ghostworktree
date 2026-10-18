"""Local git access via subprocess only. No network git operations."""
from __future__ import annotations
import subprocess
from pathlib import Path
from .errors import CannotRunError, WorktreeError

# Explicitly forbidden in v0.1 (network): fetch, pull, push, clone, ls-remote

def _run(args, cwd=None, check=True):
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise CannotRunError("git executable not found on PATH") from e
    except subprocess.TimeoutExpired as e:
        raise WorktreeError(f"git {' '.join(args[:2])} timed out") from e
    if check and r.returncode != 0:
        msg = (r.stderr or r.stdout or "git failed").strip().splitlines()
        raise WorktreeError(msg[-1] if msg else "git failed")
    return r

def is_git_repo(path: Path) -> bool:
    if not Path(path).is_dir():
        return False
    r = _run(["rev-parse", "--is-inside-work-tree"], cwd=path, check=False)
    return r.returncode == 0 and r.stdout.strip() == "true"

def repo_root(path: Path) -> Path:
    if not is_git_repo(path):
        raise CannotRunError(f"not a git working tree: {path}")
    r = _run(["rev-parse", "--show-toplevel"], cwd=path)
    return Path(r.stdout.strip()).resolve()

def branch_exists(repo: Path, branch: str) -> bool:
    r = _run(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo, check=False)
    return r.returncode == 0

def ref_exists(repo: Path, ref: str) -> bool:
    r = _run(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd=repo, check=False)
    return r.returncode == 0

def current_branch(path: Path) -> str | None:
    r = _run(["rev-parse", "--abbrev-ref", "HEAD"], cwd=path, check=False)
    if r.returncode != 0:
        return None
    v = r.stdout.strip()
    return None if v == "HEAD" else v

def worktree_list(repo: Path) -> list[dict]:
    """Parse `git worktree list --porcelain` -> [{path, branch, detached, bare}]"""
    r = _run(["worktree", "list", "--porcelain"], cwd=repo)
    out, cur = [], {}
    for line in r.stdout.splitlines():
        if not line.strip():
            if cur: out.append(cur); cur = {}
            continue
        k, _, v = line.partition(" ")
        if k == "worktree": cur["path"] = v
        elif k == "branch": cur["branch"] = v.removeprefix("refs/heads/")
        elif k == "detached": cur["branch"] = None
        elif k == "bare": cur["bare"] = True
    if cur: out.append(cur)
    return out

def worktree_add(repo: Path, dest: Path, branch: str, base: str, create_branch: bool):
    args = ["worktree", "add"]
    if create_branch: args += ["-b", branch]
    args += [str(dest)]
    args += [branch] if not create_branch else [base]
    _run(args, cwd=repo)

def worktree_remove(repo: Path, dest: Path, force: bool = False):
    args = ["worktree", "remove"]
    if force: args.append("--force")
    args.append(str(dest))
    _run(args, cwd=repo)

def worktree_prune(repo: Path):
    _run(["worktree", "prune"], cwd=repo, check=False)

def branch_delete_safe(repo: Path, branch: str):
    r = _run(["branch", "-d", branch], cwd=repo, check=False)
    if r.returncode != 0:
        # Do not echo git's hint lines: they suggest `git branch -D`, which
        # this tool deliberately never runs or recommends.
        raise WorktreeError(
            f"git branch -d refused to delete branch {branch!r}; the branch was kept")

def status_porcelain(path: Path) -> list[dict]:
    """[{code, path}] from git status --porcelain=v1 -uall.

    -uall lists every untracked file individually; without it git collapses
    a new directory to one `?? dir/` entry and dirty counts understate how
    much uncommitted work a cleanup would discard.
    """
    r = _run(["status", "--porcelain=v1", "-uall"], cwd=path)
    entries = []
    for line in r.stdout.splitlines():
        if len(line) < 4: continue
        code, fname = line[:2], line[3:]
        if " -> " in fname: fname = fname.split(" -> ", 1)[1]
        entries.append({"code": code, "path": fname.strip('"')})
    return entries

def classify(entries: list[dict]) -> dict:
    staged = unstaged = untracked = 0
    for e in entries:
        x, y = e["code"][0], e["code"][1] if len(e["code"]) > 1 else " "
        if x == "?" and y == "?": untracked += 1
        else:
            if x not in " ?": staged += 1
            if y not in " ?": unstaged += 1
    return {"staged": staged, "unstaged": unstaged, "untracked": untracked,
            "total": len(entries), "dirty": len(entries) > 0}
