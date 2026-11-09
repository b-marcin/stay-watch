from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}


def check_public(root: Path, terms: Sequence[str]) -> list[str]:
    root = root.resolve()
    needles = [term for term in terms if term and term.strip()]
    findings: list[str] = []
    if not needles:
        return ["no hotel terms configured"]
    for path in _files(root):
        text = _read(path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            folded = line.casefold()
            if any(term.casefold() in folded for term in needles):
                findings.append(f"{path.relative_to(root).as_posix()}:{number}: hotel term found")
                break
    return findings


def _files(root: Path) -> list[Path]:
    found: dict[Path, None] = {}
    docs = root / "docs"
    if docs.is_dir():
        for path in _walk(docs):
            found[path.resolve()] = None
    tracked = _git_tracked(root)
    if tracked is None:
        for path in _walk(root):
            found[path.resolve()] = None
    else:
        for path in tracked:
            if path.is_file():
                found[path.resolve()] = None
    return [path for path in found]


def _walk(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            files.append(Path(dirpath) / name)
    return files


def _git_tracked(root: Path) -> list[Path] | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    paths: list[Path] = []
    for rel in proc.stdout.split(b"\0"):
        if not rel:
            continue
        paths.append(root / rel.decode("utf-8", "surrogateescape"))
    return paths


def _read(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
