"""GhostWorktree CLI: create / list / status / cleanup."""
from __future__ import annotations
import argparse, json, sys
from . import core
from .errors import WorktreeError

def _p_create(a):
    rec = core.create(a.repo, a.name, branch=a.branch, base=a.base, port=a.port,
                      port_range=a.port_range, path=a.path, no_env=a.no_env, root=a.root)
    if a.json: print(json.dumps(rec, indent=2))
    else:
        print(f"created {rec['name']}: {rec['path']}")
        print(f"  branch: {rec['branch']}  base: {rec['base']}  port: {rec['port']}")
        print(f"  env: {rec['env_source'] or 'no .env.example found'}"
              + (" (example only, .env not seeded)" if a.no_env and rec['env_source'] else ""))
    return 0

def _p_list(a):
    recs = core.list_worktrees(a.repo, root=a.root)
    if a.json: print(json.dumps(recs, indent=2))
    elif not recs: print("no registered worktrees")
    else:
        for r in recs:
            print(f"{r['name']}: {r['path']}  branch={r['branch']} port={r['port']} state={r['state']} created={r['created_at']}")
    return 0

def _p_status(a):
    rows = core.status(a.repo, name=a.name, root=a.root)
    if a.json: print(json.dumps(rows, indent=2))
    elif not rows: print("no registered worktrees")
    else:
        for r in rows:
            if r["status"] == "missing":
                print(f"{r['name']}: missing  path={r['path']} port={r['port']}")
            else:
                print(f"{r['name']}: {r['status']}  changed={r['changed']} "
                      f"(staged={r['staged']} unstaged={r['unstaged']} untracked={r['untracked']}) "
                      f"branch={r.get('current_branch') or r['branch']} port={r['port']} path={r['path']}")
    return 0

def _p_cleanup(a):
    res = core.cleanup(a.repo, a.name, force=a.force, delete_branch=a.delete_branch, root=a.root)
    if a.json: print(json.dumps(res, indent=2))
    else:
        if a.force: print("warning: --force used; any uncommitted work in the tree was discarded")
        print(f"cleaned up {res['name']}: removed_tree={res['removed_tree']} "
              f"branch_deleted={res['branch_deleted']} freed_port={res['freed_port']}")
        if res.get("branch_delete_error"):
            print(f"warning: branch {res['branch']} was kept: {res['branch_delete_error']}")
    return 0

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ghostworktree",
        description="Isolated git worktree + port + env-copy manager per agent task. Local git only, no network. Never reads or logs a real .env.")
    sub = p.add_subparsers(dest="command", required=True)
    def common(sp, repo_required=True):
        sp.add_argument("repo", help="path to the main git repository")
        sp.add_argument("--root", default=None, help="worktrees root (default: sibling <repo>-worktrees)")
        sp.add_argument("--json", action="store_true", help="machine-readable JSON output")
    c = sub.add_parser("create", help="create an isolated worktree with a unique port and an env seeded only from .env.example")
    common(c); c.add_argument("name")
    c.add_argument("--branch", default=None, help="branch (default: worktree/<name>)")
    c.add_argument("--base", default="HEAD", help="base ref for a new branch (default: HEAD)")
    c.add_argument("--port", type=int, default=None, help="explicit port; must be free")
    c.add_argument("--port-range", default=None, help="allocation range START-END (default: 4100-4999)")
    c.add_argument("--path", default=None, help="explicit worktree path")
    c.add_argument("--no-env", action="store_true", help="copy .env.example but do not seed .env from it")
    c.set_defaults(fn=_p_create)
    l = sub.add_parser("list", help="list registered worktrees"); common(l); l.set_defaults(fn=_p_list)
    s = sub.add_parser("status", help="show clean/dirty status per worktree"); common(s)
    s.add_argument("name", nargs="?", default=None); s.set_defaults(fn=_p_status)
    cl = sub.add_parser("cleanup", help="remove a worktree; refuses dirty trees unless --force discards uncommitted work")
    common(cl); cl.add_argument("name")
    cl.add_argument("--force", action="store_true", help="DISCARD uncommitted work and remove a dirty tree")
    cl.add_argument("--delete-branch", action="store_true", help="also safe-delete the branch (git branch -d) after removal; if git refuses, the branch is kept and a warning is shown, the cleanup itself still succeeds")
    cl.set_defaults(fn=_p_cleanup)
    return p

def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a)
    except WorktreeError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.exit_code
    except BrokenPipeError:
        return 0

if __name__ == "__main__":
    sys.exit(main())
