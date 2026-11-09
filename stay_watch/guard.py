from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from stay_watch.store import term_pattern

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "node_modules"}


class PublicFilesError(Exception):
    pass


def check_public(root: Path, terms: Sequence[str]) -> list[str]:
    root = root.resolve()
    pattern = term_pattern(terms)
    if pattern is None:
        return ["no hotel terms configured"]
    findings: list[str] = []
    for path in _files(root):
        findings.extend(_scan_file(path, root, pattern))
    return findings


def check_tree(root: Path, terms: Sequence[str]) -> list[str]:
    """Scan one directory tree. Used on the docs that were just written."""
    root = Path(root)
    pattern = term_pattern(terms)
    if pattern is None:
        return ["no hotel terms configured"]
    if not root.is_dir():
        return []
    findings: list[str] = []
    for path in _walk(root):
        findings.extend(_scan_file(path, root, pattern))
    return findings


def _scan_file(path: Path, root: Path, pattern) -> list[str]:
    text = _read(path)
    if text is None:
        return []
    findings: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
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
