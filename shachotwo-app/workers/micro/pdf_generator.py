"""pdf_generator マイクロエージェント。HTML テンプレート + データから PDF を生成する。"""
import logging
import time
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

try:
    from weasyprint import HTML as _WeasyprintHTML
    _WEASYPRINT_AVAILABLE = True
except (ModuleNotFoundError, OSError):  # CI/macOSローカルなどシステムライブラリ不在の場合
    _WeasyprintHTML = None  # type: ignore[assignment]
    _WEASYPRINT_AVAILABLE = False

HTML = _WeasyprintHTML

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError

logger = logging.getLogger(__name__)

# テンプレート検索パス（業種別 templates ディレクトリを追加可能）
_TEMPLATE_DIRS: list[str] = [
    str(Path(__file__).resolve().parent.parent / "bpo" / "sales" / "templates"),
]

_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    """Jinja2 Environment をシングルトンで取得する。"""
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(_TEMPLATE_DIRS),
            autoescape=select_autoescape(["html"]),
        )
        # カスタムフィルタ: 数値カンマ区切り
        _jinja_env.filters["commafy"] = lambda v: f"{int(v):,}" if v is not None else "0"
    return _jinja_env


async def run_pdf_generator(input: MicroAgentInput) -> MicroAgentOutput:
    """
    HTML テンプレートにデータを埋め込んで PDF バイナリを生成する。

    payload:
        template_name (str): テンプレートファイル名（例: "quotation_template.html"）
        data (dict): テンプレートに渡す変数
        extra_template_dirs (list[str], optional): 追加テンプレートディレクトリ

    result:
        pdf_bytes (bytes): 生成された PDF バイナリ
        size_kb (float): PDF サイズ (KB)
        template_name (str): 使用テンプレート名
    """
    start_ms = int(time.time() * 1000)
    agent_name = "pdf_generator"

    try:
        template_name: str = input.payload.get("template_name", "")
        data: dict[str, Any] = input.payload.get("data", {})
        extra_dirs: list[str] = input.payload.get("extra_template_dirs", [])

        if not template_name:
            raise MicroAgentError(agent_name, "input_validation", "template_name が空です")

        # 追加ディレクトリがあれば新しい env を作る
        if extra_dirs:
            env = Environment(
                loader=FileSystemLoader(_TEMPLATE_DIRS + extra_dirs),
                autoescape=select_autoescape(["html"]),
            )
            env.filters["commafy"] = lambda v: f"{int(v):,}" if v is not None else "0"
        else:
            env = _get_jinja_env()

        template = env.get_template(template_name)
        html_str = template.render(**data)

        # WeasyPrint で PDF 生成
        pdf_bytes: bytes = HTML(string=html_str).write_pdf()
        size_kb = round(len(pdf_bytes) / 1024, 1)

        duration_ms = int(time.time() * 1000) - start_ms
        logger.info(
            f"PDF generated: {template_name} ({size_kb} KB, {duration_ms}ms)"
        )

        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={
                "pdf_bytes": pdf_bytes,
                "size_kb": size_kb,
                "template_name": template_name,
            },
            confidence=1.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"pdf_generator error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )


async def render_html(template_name: str, data: dict[str, Any]) -> str:
    """テンプレートを HTML 文字列としてレンダリングする（メール等の用途）。

    Args:
        template_name: テンプレートファイル名
        data:          テンプレート変数

    Returns:
        レンダリングされた HTML 文字列
    """
    env = _get_jinja_env()
    template = env.get_template(template_name)
    return template.render(**data)
