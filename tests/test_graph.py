"""グラフのビルドと状態型を検証。"""

from unicorn_agent.state import initial_state
from unicorn_agent.graph import get_graph, build_graph


def test_initial_state():
    s = initial_state("hello")
    assert s["user_requirement"] == "hello"
    assert s["spec_markdown"] == ""
    assert s["generated_code"] == {}
    assert s["retry_count"] == 0
    assert s["status"] == "started"
    assert s.get("workspace_root") == "."


def test_initial_state_workspace_root():
    s = initial_state("req", workspace_root="/tmp/repo")
    assert s["workspace_root"] == "/tmp/repo"


def test_build_graph():
    g = build_graph()
    assert g is not None


def test_get_graph_singleton():
    a = get_graph()
    b = get_graph()
    assert a is b
