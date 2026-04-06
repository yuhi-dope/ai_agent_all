"""Pytest configuration for routers tests — ensure project root is on sys.path."""
import sys
from pathlib import Path

# shachotwo-app root
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
