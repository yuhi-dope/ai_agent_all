"""company_researcher マイクロエージェント。企業のペイン推定・規模判定・トーン調整を行う。"""
import json
import logging
import re
import time
from typing import Any

from workers.micro.models import MicroAgentInput, MicroAgentOutput, MicroAgentError
from llm.client import get_llm_client, LLMTask, ModelTier

logger = logging.getLogger(__name__)

# 業種→業務リスト・訴求マッピング
INDUSTRY_MAPPING: dict[str, dict[str, Any]] = {
    "建設業": {
        "tasks": ["積算AI", "安全書類自動作成", "出来高請求", "原価管理", "日報集計", "工程管理", "施工写真管理", "協力業者管理"],
        "appeal": "現場監督の事務作業を月40時間削減",
    },
    "製造業": {
        "tasks": ["見積AI", "生産計画", "在庫最適化", "FAX受発注", "品質管理", "出荷管理", "原価計算", "設備保全"],
        "appeal": "FAX受発注の手入力ゼロ。在庫回転率20%改善",
    },
    "歯科": {
        "tasks": ["レセプトAI", "予約最適化", "リコール管理", "カルテ入力支援", "在庫管理", "患者対応", "会計", "経営分析"],
        "appeal": "レセプト返戻率50%減。予約キャンセル率30%減",
    },
    "介護": {
        "tasks": ["ケアプラン書類", "介護報酬請求", "シフト管理", "記録業務", "モニタリング", "家族連絡", "実績管理", "LIFE対応"],
        "appeal": "記録業務2h→30分。離職原因No.1を解消",
    },
    "士業": {
        "tasks": ["顧問先Q&A", "月次巡回準備", "届出期限管理", "書類作成", "判例検索", "顧客管理", "請求管理", "ナレッジ共有"],
        "appeal": "同じ質問に何度も答える問題を解消",
    },
    "不動産": {
        "tasks": ["物件査定AI", "契約書自動作成", "賃料収支管理", "入退去管理", "修繕管理", "オーナー報告", "空室対策", "内見予約"],
        "appeal": "管理物件200→500でも人員増なし",
    },
}

# 企業規模→提案トーン
SCALE_TONE: dict[str, dict[str, Any]] = {
    "零細": {"range": (1, 10), "tone": "社長の右腕"},
    "小規模": {"range": (11, 50), "tone": "人を雇うより安い"},
    "中規模": {"range": (51, 300), "tone": "属人化リスク解消"},
}

_SYSTEM_PROMPT = """あなたは企業分析の専門家です。
企業情報から、その企業が抱えている「業務上の痛み」を推定してください。

ルール:
- 痛みは具体的に（抽象的な表現は避ける）
- 根拠を明確に示す
- 訴求メッセージは1文で簡潔に
- 必ずJSON配列のみを返す（説明文不要）
"""


async def run_company_researcher(input: MicroAgentInput) -> MicroAgentOutput:
    """
    企業情報からペイン推定・規模判定・トーン調整を実行する。

    payload:
        name (str): 企業名
        industry (str): 業種
        employee_count (int | None): 従業員数
        business_overview (str): 事業概要
        raw_html (str, optional): HP抜粋テキスト
        job_postings (list[dict], optional): 求人情報

    result:
        pain_points (list[dict]): 推定ペイン
        scale (str): 企業規模 (零細/小規模/中規模)
        tone (str): 提案トーン
        industry_tasks (list[str]): 業種別業務リスト
        industry_appeal (str): 業種別訴求ポイント
    """
    start_ms = int(time.time() * 1000)
    agent_name = "company_researcher"
    llm = get_llm_client()

    try:
        name = input.payload.get("name", "")
        industry = input.payload.get("industry", "")
        employee_count = input.payload.get("employee_count")
        business_overview = input.payload.get("business_overview", "")
        raw_html = input.payload.get("raw_html", "")
        job_postings = input.payload.get("job_postings", [])

        if not name:
            raise MicroAgentError(agent_name, "input_validation", "name が空です")

        # 規模判定
        scale = _determine_scale(employee_count)
        tone = SCALE_TONE.get(scale, SCALE_TONE["小規模"])["tone"]

        # 業種別タスク取得
        industry_data = _get_industry_tasks(industry)

        # 求人情報テキスト
        job_info = "\n".join(
            f"- {jp.get('title', '')}（{'急募' if jp.get('is_urgent') else '通常'}）"
            for jp in job_postings
        ) or "求人情報なし"

        user_prompt = f"""以下の企業情報から、この企業が抱えている「業務上の痛み」を3つ推定してください。

企業名: {name}
業種: {industry}
従業員数: {employee_count or '不明'}名
事業概要: {business_overview}
HP抜粋: {(raw_html or '')[:2000]}

求人情報:
{job_info}

各痛みについて以下のJSON形式で回答してください:
[
  {{
    "category": "staffing|process|cost|compliance|growth",
    "detail": "痛みの詳細",
    "evidence": "推定根拠（何からそう判断したか）",
    "appeal_message": "この痛みに対する訴求メッセージ（1文）"
  }}
]

JSON配列のみを返してください。"""

        response = await llm.generate(LLMTask(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            tier=ModelTier.FAST,
            task_type="company_researcher",
            company_id=input.company_id,
            temperature=0.2,
        ))

        # JSON抽出
        raw = response.content.strip()
        json_match = re.search(r'\[[\s\S]*\]', raw)
        if not json_match:
            raise MicroAgentError(agent_name, "parse", f"LLMがJSON配列を返しませんでした: {raw[:200]}")

        pain_points: list[dict] = json.loads(json_match.group())

        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=True,
            result={
                "pain_points": pain_points,
                "scale": scale,
                "tone": tone,
                "industry_tasks": industry_data["tasks"],
                "industry_appeal": industry_data["appeal"],
            },
            confidence=0.8 if pain_points else 0.3,
            cost_yen=response.cost_yen,
            duration_ms=duration_ms,
        )

    except MicroAgentError:
        raise
    except json.JSONDecodeError as e:
        duration_ms = int(time.time() * 1000) - start_ms
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": f"JSON parse error: {e}"},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )
    except Exception as e:
        duration_ms = int(time.time() * 1000) - start_ms
        logger.error(f"company_researcher error: {e}")
        return MicroAgentOutput(
            agent_name=agent_name,
            success=False,
            result={"error": str(e)},
            confidence=0.0,
            cost_yen=0.0,
            duration_ms=duration_ms,
        )


def _determine_scale(employee_count: int | None) -> str:
    """企業規模から提案トーンカテゴリを決定"""
    if not employee_count:
        return "小規模"
    for scale, info in SCALE_TONE.items():
        low, high = info["range"]
        if low <= employee_count <= high:
            return scale
    return "中規模"


def _get_industry_tasks(industry: str) -> dict[str, Any]:
    """業種から業務リスト・訴求ポイントを取得"""
    for key, val in INDUSTRY_MAPPING.items():
        if key in industry:
            return val
    return {"tasks": [], "appeal": ""}
