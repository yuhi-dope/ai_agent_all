"""
Server-side settings management.
data/settings.json にファイルベースで保存する。スレッドセーフ。
"""

import json
import threading
from pathlib import Path

_SETTINGS_DIR = Path(__file__).resolve().parent.parent / "data"
_SETTINGS_PATH = _SETTINGS_DIR / "settings.json"
_lock = threading.Lock()

_DEFAULTS: dict = {
    "auto_execute": True,
}


def load_settings() -> dict:
    """設定を読み込む。ファイルがなければデフォルトを返す。"""
    with _lock:
        if not _SETTINGS_PATH.exists():
            return dict(_DEFAULTS)
        try:
            with open(_SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return {**_DEFAULTS, **data}
        except Exception:
            return dict(_DEFAULTS)


def save_settings(settings: dict) -> None:
    """設定をファイルに書き込む。"""
    with _lock:
        _SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        merged = {**_DEFAULTS, **settings}
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)


def get_auto_execute() -> bool:
    """auto_execute の現在値を返す。"""
    return bool(load_settings().get("auto_execute", True))


def set_auto_execute(value: bool) -> None:
    """auto_execute を更新する。"""
    s = load_settings()
    s["auto_execute"] = value
    save_settings(s)
