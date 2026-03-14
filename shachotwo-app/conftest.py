"""Pytest root config — ensure project root is first on sys.path so 'security', 'brain', 'db' resolve."""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
