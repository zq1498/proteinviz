"""Shared pytest fixtures and path setup.

Adds the repo root to sys.path so `import tools.*` and `import preflight.*`
work when pytest is run from the repo root without a package install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def smoke_test_dir(repo_root: Path) -> Path:
    """ColabFold output directory checked into jobs/smoke_test."""
    d = repo_root / "jobs" / "smoke_test"
    if not d.exists():
        pytest.skip(f"smoke_test fixture not present at {d}")
    return d
