"""Env seeding: ONLY .env.example / .env.sample / .env.template are ever read or copied.

The real .env is never opened, copied, parsed, or logged. Contents are copied
byte-for-byte and never printed.
"""
from __future__ import annotations
import shutil
from pathlib import Path

EXAMPLE_NAMES = (".env.example", ".env.sample", ".env.template")

def find_example(repo: Path) -> Path | None:
    for n in EXAMPLE_NAMES:
        p = Path(repo) / n
        if p.is_file(): return p
    return None

def seed_env(repo: Path, dest: Path, seed_dotenv: bool = True) -> str | None:
    """Copy example into dest as same name; optionally seed dest/.env from it.

    Returns the example file NAME only (never contents), or None.
    """
    src = find_example(repo)
    if src is None: return None
    target = Path(dest) / src.name
    if not target.exists():
        shutil.copyfile(src, target)
    if seed_dotenv:
        dotenv = Path(dest) / ".env"
        if not dotenv.exists():
            shutil.copyfile(src, dotenv)
    return src.name
