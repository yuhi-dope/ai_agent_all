"""table_parser マイクロエージェント。テキスト内の表形式データをdict[]に変換する。LLM不使用。"""
import csv
import io
import time
import logging
import re
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)


def _parse_pipe_table(text: str) -> list[dict[str, Any]]:
    """パイプ区切り表（建設積算書形式 or Markdown表）をパース。"""
    rows = []
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    header: list[str] = []
    for line in lines:
        if "|" not in line:
            continue
        # セパレーター行をスキップ（例: |---|---|）
        if re.match(r"^\|[\s\-:]+(\|[\s\-:]+)*\|?$", line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not header:
            header = cells
        else:
            if len(cells) >= len(header):
                rows.append(dict(zip(header, cells[:len(header)])))
            else:
                padded = cells + [""] * (len(header) - len(cells))
                rows.append(dict(zip(header, padded)))
    return rows


def _parse_csv_table(text: str, delimiter: str = ",") -> list[dict[str, Any]]:
    """CSV/TSV形式をパース。"""
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return [dict(row) for row in reader]


async def run_table_parser(input: MicroAgentInput) -> MicroAgentOutput:
    """
    テキスト内の表形式データをdict[]に変換する。

    payload:
        text (str): パース対象テキスト
        format (str): "pipe" | "tsv" | "csv" | "markdown" | "auto"

    result:
        tables (list[list[dict]]): 検出した各テーブルのdict列
        count (int): テーブル数
    """
    start_ms = int(time.time() * 1000)
    agent_name = "table_parser"

    try:
        text: str = input.payload.get("text", "")
        fmt: str = input.payload.get("format", "auto").lower()

        if not text:
            return MicroAgentOutput(
                agent_name=agent_name, success=True,
                result={"tables": [], "count": 0},
                confidence=1.0, cost_yen=0.0,
                duration_ms=int(time.time() * 1000) - start_ms,
            )

        tables: list[list[dict]] = []

        if fmt in ("pipe", "markdown", "auto"):
            rows = _parse_pipe_table(text)
            if rows:
                tables.append(rows)

        if fmt in ("tsv", "auto") and not tables:
            if "\t" in text:
                rows = _parse_csv_table(text, delimiter="\t")
                if rows:
                    tables.append(rows)

        if fmt in ("csv", "auto") and not tables:
            rows = _parse_csv_table(text, delimiter=",")
            if rows:
                tables.append(rows)

        total_rows = sum(len(t) for t in tables)
        confidence = 1.0 if tables else 0.0

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name, success=True,
            result={"tables": tables, "count": len(tables), "total_rows": total_rows},
            confidence=confidence, cost_yen=0.0, duration_ms=duration_ms,
        )

    except Exception as e:
        logger.error(f"table_parser error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name, success=False,
            result={"error": str(e)},
            confidence=0.0, cost_yen=0.0,
            duration_ms=int(time.time() * 1000) - start_ms,
        )
