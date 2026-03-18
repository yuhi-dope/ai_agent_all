"""Pytest configuration — ensure project root is on sys.path for imports."""
import sys
from pathlib import Path

import pytest

# Add shachotwo-app (project root) so that "security", "brain", "db" etc. resolve
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False)


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-integration"):
        skip = pytest.mark.skip(reason="need --run-integration flag")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)
