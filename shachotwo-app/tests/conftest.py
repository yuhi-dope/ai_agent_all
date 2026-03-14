"""Pytest configuration — ensure project root is on sys.path for imports."""
import sys
from pathlib import Path

# Add shachotwo-app (project root) so that "security", "brain", "db" etc. resolve
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
