"""BPO分類テーマ別セッション管理 + 自動compacting。

knowledge_sessions テーブルをテーマ（ゲノムのdepartment名）ごとに分割し、
Q&Aターン数が COMPACT_THRESHOLD を超えたら LLM で自動要約・圧縮する。
"""
import json
import logging
from typing import Any
from uuid import uuid4

from brain.genome.loader import apply_template  # ゲノムロードはapply_template経由でJSONを参照
from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client

logger = logging.getLogger(__name__)

COMPACT_THRESHOLD: int = 10

_COMPACT_SYSTEM_PROMPT = (
    "以下の会話で収集したナレッジ情報を、JSONキー\"summary\"に箇条書きで要約してください。"
    "業務ルール・数値・固有名詞を優先して保持すること。"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_or_create_theme_session(
    company_id: str,
    user_id: str,
    bpo_theme: str,
) -> dict[str, Any]:
    """指定テーマのアクティブなセッションを取得、なければ新規作成。

    Args:
        company_id: テナントID
        user_id: ユーザーID
        bpo_theme: ゲノムの department 名（例: "製造", "品質管理"）

    Returns:
        knowledge_sessions テーブルの行 dict
    """
    db = get_service_client()

    # アクティブなセッションを検索（最新1件）
    result = (
        db.table("knowledge_sessions")
        .select("*")
        .eq("company_id", company_id)
        .eq("bpo_theme", bpo_theme)
        .eq("session_status", "active")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if result.data:
        return result.data[0]

    # 新規作成
    new_session = {
        "id": str(uuid4()),
        "company_id": company_id,
        "user_id": user_id,
        "input_type": "interactive",
        "bpo_theme": bpo_theme,
        "question_count": 0,
        "session_status": "active",
        "raw_context_archive": [],
        "extraction_status": "pending",
    }
    created = db.table("knowledge_sessions").insert(new_session).execute()
    return created.data[0]


async def get_theme_progress(
    company_id: str,
    industry_code: str,
) -> list[dict[str, Any]]:
    """業種のテーマ一覧と各テーマの進捗を返す。

    Args:
        company_id: テナントID
        industry_code: 業種コード（例: "manufacturing", "construction"）

    Returns:
        テーマ別進捗リスト。例:
        [
          {
            "theme": "製造",
            "display_name": "製造",
            "coverage_rate": 0.8,
            "question_count": 12,
            "status": "completed"
          },
          ...
        ]
    """
    # ゲノムからテーマ一覧を取得
    themes = await _get_themes_for_industry(industry_code)

    # 全テーマのセッション情報を一括取得
    db = get_service_client()
    sessions_result = (
        db.table("knowledge_sessions")
        .select("bpo_theme, question_count, session_status")
        .eq("company_id", company_id)
        .in_("bpo_theme", themes)
        .order("created_at", desc=True)
        .execute()
    )

    # テーマごとに最新セッションをまとめる
    latest_by_theme: dict[str, dict] = {}
    for row in sessions_result.data or []:
        theme = row["bpo_theme"]
        if theme not in latest_by_theme:
            latest_by_theme[theme] = row

    progress = []
    for theme in themes:
        session = latest_by_theme.get(theme)
        if session:
            question_count = session.get("question_count", 0)
            session_status = session.get("session_status", "active")
        else:
            question_count = 0
            session_status = "not_started"

        coverage_rate = _estimate_coverage_rate(question_count, session_status)
        progress.append({
            "theme": theme,
            "display_name": theme,
            "coverage_rate": coverage_rate,
            "question_count": question_count,
            "status": _resolve_display_status(session_status, question_count),
        })

    return progress


async def record_qa_turn(
    session_id: str,
    question: str,
    answer: str,
) -> dict[str, Any]:
    """Q&Aの1ターンを記録し、question_count をインクリメント。

    question_count が COMPACT_THRESHOLD を超えたら auto_compact() を呼ぶ。

    Args:
        session_id: knowledge_sessions.id
        question: ユーザーの質問
        answer: LLMの回答

    Returns:
        更新後の knowledge_sessions 行 dict
    """
    db = get_service_client()

    # 現在のセッションを取得
    session_result = (
        db.table("knowledge_sessions")
        .select("*")
        .eq("id", session_id)
        .single()
        .execute()
    )
    session = session_result.data

    # raw_context_archive に1ターン追記
    archive: list[dict] = list(session.get("raw_context_archive") or [])
    archive.append({"q": question, "a": answer})

    new_count = (session.get("question_count") or 0) + 1

    updated = (
        db.table("knowledge_sessions")
        .update({
            "question_count": new_count,
            "raw_context_archive": archive,
        })
        .eq("id", session_id)
        .execute()
    )
    updated_session = updated.data[0]

    # 閾値を超えたら圧縮
    if new_count > COMPACT_THRESHOLD:
        try:
            await auto_compact(session_id)
            # 圧縮後の最新状態を再取得
            refreshed = (
                db.table("knowledge_sessions")
                .select("*")
                .eq("id", session_id)
                .single()
                .execute()
            )
            updated_session = refreshed.data
        except Exception as exc:
            logger.warning(
                "auto_compact failed for session %s (non-critical): %s", session_id, exc
            )

    return updated_session


async def auto_compact(session_id: str) -> None:
    """会話履歴を LLM で要約して compressed_context に保存。

    処理フロー:
    1. raw_context_archive の全ターンを LLM に渡して要約
    2. compressed_context に要約を保存
    3. raw_context_archive を空リストにリセット（元データはすでにDBに保持）
    4. session_status を 'compacted' → 'active' に戻す

    Args:
        session_id: knowledge_sessions.id
    """
    db = get_service_client()

    session_result = (
        db.table("knowledge_sessions")
        .select("id, company_id, raw_context_archive, compressed_context")
        .eq("id", session_id)
        .single()
        .execute()
    )
    session = session_result.data
    company_id: str = session["company_id"]

    archive: list[dict] = list(session.get("raw_context_archive") or [])
    if not archive:
        logger.debug("auto_compact: session %s has empty archive, skipping", session_id)
        return

    # 既存のcompressed_contextがあれば先頭に含める
    prior_context: str = session.get("compressed_context") or ""
    turns_text = "\n".join(
        f"Q: {t.get('q', '')}\nA: {t.get('a', '')}" for t in archive
    )
    if prior_context:
        turns_text = f"[前回の要約]\n{prior_context}\n\n[今回の会話]\n{turns_text}"

    # LLM要約
    llm = get_llm_client()
    response = await llm.generate(LLMTask(
        messages=[
            {"role": "system", "content": _COMPACT_SYSTEM_PROMPT},
            {"role": "user", "content": turns_text},
        ],
        tier=ModelTier.FAST,
        task_type="compaction",
        company_id=company_id,
    ))

    summary_text = _extract_summary(response.content)

    # DBへ書き戻し: 圧縮済みコンテキストを更新、raw_context_archiveをリセット
    db.table("knowledge_sessions").update({
        "session_status": "compacted",
    }).eq("id", session_id).execute()

    db.table("knowledge_sessions").update({
        "compressed_context": summary_text,
        "raw_context_archive": [],
        "session_status": "active",
    }).eq("id", session_id).execute()

    logger.info(
        "auto_compact: session %s compacted (%d turns → summary)", session_id, len(archive)
    )


async def get_session_context_for_llm(session_id: str) -> str:
    """LLM に渡すコンテキスト文字列を返す。

    - compacted 済み: compressed_context + 直近3ターンの生データ
    - 未compact: raw_context_archive 全体（COMPACT_THRESHOLD 以下なので安全）

    Args:
        session_id: knowledge_sessions.id

    Returns:
        LLM プロンプトに埋め込む文字列
    """
    db = get_service_client()
    session_result = (
        db.table("knowledge_sessions")
        .select("compressed_context, raw_context_archive, question_count")
        .eq("id", session_id)
        .single()
        .execute()
    )
    session = session_result.data

    compressed: str = session.get("compressed_context") or ""
    archive: list[dict] = list(session.get("raw_context_archive") or [])

    if compressed:
        # compacted: 要約 + 直近3ターン
        recent = archive[-3:] if len(archive) > 3 else archive
        recent_text = "\n".join(
            f"Q: {t.get('q', '')}\nA: {t.get('a', '')}" for t in recent
        )
        parts = [f"[これまでの要約]\n{compressed}"]
        if recent_text:
            parts.append(f"[直近の会話]\n{recent_text}")
        return "\n\n".join(parts)
    else:
        # 未compact: 全ターンをそのまま返す
        if not archive:
            return ""
        return "\n".join(
            f"Q: {t.get('q', '')}\nA: {t.get('a', '')}" for t in archive
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

async def _get_themes_for_industry(industry_code: str) -> list[str]:
    """ゲノム JSON から department 名一覧を取得する。

    ゲノムJSONは brain/genome/data/{industry_code}.json に存在する。
    JSONを直接読み込む（apply_template はDB操作を伴うため使わない）。
    """
    import json
    from pathlib import Path

    genome_path = (
        Path(__file__).parent.parent / "genome" / "data" / f"{industry_code}.json"
    )
    if not genome_path.exists():
        logger.warning("Genome file not found: %s", genome_path)
        return []

    with open(genome_path, encoding="utf-8") as f:
        genome = json.load(f)

    return [dept["name"] for dept in genome.get("departments", [])]


def _estimate_coverage_rate(question_count: int, session_status: str) -> float:
    """質問数とステータスから進捗率を推定する（簡易モデル）。

    - 0問: 0.0
    - COMPACT_THRESHOLD 問で 0.8 に到達（線形）
    - compacted 状態は +0.1 ボーナス、ただし上限 1.0
    """
    if question_count <= 0:
        return 0.0
    base = min(question_count / COMPACT_THRESHOLD, 1.0) * 0.8
    if session_status == "compacted":
        base = min(base + 0.1, 1.0)
    return round(base, 2)


def _resolve_display_status(session_status: str, question_count: int) -> str:
    """DBのsession_statusをフロント向けの表示ステータスに変換する。"""
    if session_status == "not_started" or question_count == 0:
        return "not_started"
    if session_status == "closed":
        return "completed"
    if session_status == "compacted" or question_count >= COMPACT_THRESHOLD:
        return "completed"
    return "active"


def _extract_summary(llm_response: str) -> str:
    """LLMレスポンスから summary テキストを抽出する。

    JSON {"summary": [...]} 形式のパースを試み、
    失敗した場合はレスポンス全体をそのまま返す。
    """
    text = llm_response.strip()
    # コードフェンスを除去
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
        summary = data.get("summary")
        if isinstance(summary, list):
            return "\n".join(f"- {item}" for item in summary)
        if isinstance(summary, str):
            return summary
    except (json.JSONDecodeError, AttributeError):
        pass

    return llm_response.strip()
