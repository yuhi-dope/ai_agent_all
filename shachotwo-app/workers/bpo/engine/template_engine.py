"""テンプレートエンジン"""
import json
from pathlib import Path
from typing import Any


TEMPLATE_DIR = Path(__file__).parent.parent / "construction" / "templates"


def load_template(template_name: str) -> dict:
    """JSONテンプレートを読み込む"""
    template_path = TEMPLATE_DIR / f"{template_name}.json"
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_name}")
    with open(template_path, encoding="utf-8") as f:
        return json.load(f)


def render_template(template: dict, data: dict[str, Any]) -> dict:
    """テンプレートにデータを埋め込む"""
    rendered = {}
    for key, value in template.items():
        if isinstance(value, str) and value.startswith("{{") and value.endswith("}}"):
            field_name = value[2:-2].strip()
            rendered[key] = data.get(field_name, value)
        elif isinstance(value, dict):
            rendered[key] = render_template(value, data)
        elif isinstance(value, list):
            rendered[key] = [
                render_template(item, data) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            rendered[key] = value
    return rendered
