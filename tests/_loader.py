"""Import a repository script by path, for tests that exercise the checks themselves.

The checkers live in ``scripts/`` and under ``skills/*/scripts/`` rather than in an
importable package, because an agent or a human may run them standalone in a
repository that has no tooling. This loader keeps the tests from having to care.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_script(relative_path: str) -> ModuleType:
    """Import ``relative_path`` (relative to the repo root) without touching sys.path."""
    path = REPO_ROOT / relative_path
    name = path.stem.replace("-", "_") + "_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve annotations through sys.modules, so register before exec.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
