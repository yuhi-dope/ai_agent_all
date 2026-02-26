"""Spec review checkpoint 関連のテスト。"""

import os
import tempfile

# テスト実行時は認証をスキップする
os.environ.setdefault("REQUIRE_AUTH", "false")

from develop_agent.state import initial_state
from develop_agent.graph import build_spec_graph, build_impl_graph, get_spec_graph, get_impl_graph


# --- Graph ---

def test_build_spec_graph():
    g = build_spec_graph()
    assert g is not None


def test_build_impl_graph():
    g = build_impl_graph()
    assert g is not None


def test_get_spec_graph_singleton():
    a = get_spec_graph()
    b = get_spec_graph()
    assert a is b


def test_get_impl_graph_singleton():
    a = get_impl_graph()
    b = get_impl_graph()
    assert a is b


# --- State ---

def test_initial_state_with_notion_page_id():
    s = initial_state("req", notion_page_id="abc123")
    assert s["notion_page_id"] == "abc123"


def test_initial_state_without_notion_page_id():
    s = initial_state("req")
    assert "notion_page_id" not in s


def test_initial_state_notion_page_id_stripped():
    s = initial_state("req", notion_page_id="  id-with-spaces  ")
    assert s["notion_page_id"] == "id-with-spaces"


def test_initial_state_empty_notion_page_id():
    s = initial_state("req", notion_page_id="   ")
    assert "notion_page_id" not in s


# --- Settings ---

def test_settings_load_default():
    from server.settings import load_settings

    # Save original path and temporarily point to non-existent file
    import server.settings as mod
    orig = mod._SETTINGS_PATH
    try:
        mod._SETTINGS_PATH = orig.parent / "nonexistent_test_settings.json"
        s = load_settings()
        assert s["auto_execute"] is True
    finally:
        mod._SETTINGS_PATH = orig


def test_settings_save_and_load():
    import server.settings as mod

    orig = mod._SETTINGS_PATH
    orig_dir = mod._SETTINGS_DIR
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            mod._SETTINGS_DIR = type(orig)(tmpdir)
            mod._SETTINGS_PATH = mod._SETTINGS_DIR / "settings.json"

            mod.set_auto_execute(False)
            assert mod.get_auto_execute() is False

            mod.set_auto_execute(True)
            assert mod.get_auto_execute() is True
    finally:
        mod._SETTINGS_PATH = orig
        mod._SETTINGS_DIR = orig_dir


# --- API (requires FastAPI test client) ---

def test_api_settings_get():
    """GET /api/settings returns auto_execute."""
    try:
        from fastapi.testclient import TestClient
        from server.main import app
    except ImportError:
        return  # skip if httpx not available

    client = TestClient(app)
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert "auto_execute" in r.json()


def test_api_implement_not_found():
    """POST /run/{run_id}/implement returns 404 for unknown run_id."""
    try:
        from fastapi.testclient import TestClient
        from server.main import app
    except ImportError:
        return

    client = TestClient(app)
    r = client.post("/run/nonexistent_id_12345/implement")
    assert r.status_code == 404


def test_api_run_spec_not_found():
    """GET /api/runs/{run_id}/spec returns 404 for unknown run_id."""
    try:
        from fastapi.testclient import TestClient
        from server.main import app
    except ImportError:
        return

    client = TestClient(app)
    r = client.get("/api/runs/nonexistent_id_12345/spec")
    assert r.status_code == 404
