"""Discover supported recordings without following directory links."""
import os
from pathlib import Path

SUPPORTED = {".edf", ".edf+", ".bdf", ".npy"}


def discover_files(paths, cancelled=lambda: False):
    found, seen, directories, errors = [], set(), set(), 0
    stack = list(reversed([Path(p) for p in paths]))
    while stack:
        if cancelled():
            return [], errors
        path = stack.pop()
        try:
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                continue
            if path.is_dir():
                key = os.path.normcase(str(path.resolve()))
                if key in directories:
                    continue
                directories.add(key)
                with os.scandir(path) as entries:
                    children = []
                    for entry in entries:
                        if cancelled():
                            return [], errors
                        children.append(Path(entry.path))
                    children.sort(key=lambda p: p.name.casefold())
                stack.extend(reversed(children))
            elif path.suffix.lower() in SUPPORTED and path.is_file():
                resolved = str(path.resolve())
                key = os.path.normcase(resolved)
                if key not in seen:
                    seen.add(key)
                    found.append(resolved)
            elif not path.exists():
                errors += 1
        except OSError:
            errors += 1
    return found, errors
