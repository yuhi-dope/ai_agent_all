"""utils/guardrails の Secret Scan を検証。"""

from unicorn_agent.utils.guardrails import run_secret_scan


def test_secret_scan_clean():
    code = {"main.py": "def hello(): return 1"}
    r = run_secret_scan(code)
    assert r.passed is True
    assert r.findings == []


def test_secret_scan_sk_key():
    code = {"main.py": "key = 'sk-abcdefghijklmnopqrstuvwxyz123456'"}
    r = run_secret_scan(code)
    assert r.passed is False
    assert len(r.findings) >= 1


def test_secret_scan_api_key():
    code = {"main.py": "API_KEY = \"my-secret-key-12345\""}
    r = run_secret_scan(code)
    assert r.passed is False
    assert any("API_KEY" in f for f in r.findings)


def test_secret_scan_multiple_files():
    code = {
        "a.py": "x = 1",
        "b.py": "token = \"sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"",
    }
    r = run_secret_scan(code)
    assert r.passed is False
    assert any("b.py" in f for f in r.findings)
