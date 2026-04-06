"""Q&A Evaluation framework: ゴールデンデータセットで精度を測定する。"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from brain.knowledge.qa import answer_question

logger = logging.getLogger(__name__)


class GoldenSample(BaseModel):
    """ゴールデンデータセットの1件。"""
    question: str
    expected_keywords: list[str]  # 回答に含まれるべきキーワード
    category: str  # "construction", "manufacturing", etc.
    difficulty: Literal["easy", "medium", "hard"]


class EvalResult(BaseModel):
    """1件のQ&A評価結果。"""
    question: str
    answer: str
    confidence: float
    keyword_hit_rate: float  # expected_keywordsの何割がanswerに含まれたか
    search_mode: str
    cost_yen: float
    latency_ms: float


class EvalReport(BaseModel):
    """ゴールデンデータセット全体の評価レポート。"""
    total: int
    avg_confidence: float
    avg_keyword_hit_rate: float
    avg_cost_yen: float
    avg_latency_ms: float
    by_difficulty: dict[str, dict]  # easy/medium/hard別の集計
    by_category: dict[str, dict]
    timestamp: str


# ゴールデンデータセット（建設業）
CONSTRUCTION_GOLDEN: list[GoldenSample] = [
    # ---- 既存3件（変更しない） ----
    GoldenSample(
        question="コンクリート打設の単価はいくらですか？",
        expected_keywords=["円", "m3", "単価"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="足場の設置から撤去までの標準工期はどれくらいですか？",
        expected_keywords=["日", "週", "工期"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="鉄筋コンクリート造と鉄骨造で見積もりが大きく変わる部分はどこですか？",
        expected_keywords=["鉄筋", "鉄骨", "コスト", "工期"],
        category="construction",
        difficulty="hard",
    ),
    # ---- easy: 追加7件（計8件） ----
    GoldenSample(
        question="土工事における掘削の歩掛かりはどのくらいですか？",
        expected_keywords=["掘削", "m3", "歩掛かり", "工"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="型枠工事の単価の相場を教えてください。",
        expected_keywords=["型枠", "m2", "円", "単価"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="鉄筋工事における鉄筋加工・組立の単価はいくらですか？",
        expected_keywords=["鉄筋", "t", "円", "加工"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="左官工事（モルタル塗り）の単価を教えてください。",
        expected_keywords=["左官", "m2", "円", "モルタル"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="防水工事（ウレタン塗膜防水）の標準的な単価はいくらですか？",
        expected_keywords=["防水", "ウレタン", "m2", "円"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="外壁塗装工事の一般的な単価（円/m²）の目安を教えてください。",
        expected_keywords=["塗装", "外壁", "m2", "円"],
        category="construction",
        difficulty="easy",
    ),
    GoldenSample(
        question="電気工事でコンセント・スイッチの増設にかかる費用の目安を教えてください。",
        expected_keywords=["電気", "コンセント", "費用", "円"],
        category="construction",
        difficulty="easy",
    ),
    # ---- medium: 追加9件（計10件） ----
    GoldenSample(
        question="延べ床面積500m²の木造建築の概算工事費はいくらになりますか？",
        expected_keywords=["m2", "万円", "木造", "概算"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="コンクリートのロス率はどのくらいを見込めばよいですか？",
        expected_keywords=["ロス率", "%", "コンクリート", "割増"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="グリーンサイトで施工体制台帳を作成する際に必要な書類は何ですか？",
        expected_keywords=["グリーンサイト", "施工体制台帳", "書類", "下請"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="変更工事が発生した場合、追加見積もりはどのような手順で作成しますか？",
        expected_keywords=["変更工事", "追加", "見積", "手順"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="電気工事の幹線ケーブル敷設の単価を積算する際の考え方を教えてください。",
        expected_keywords=["電気", "ケーブル", "敷設", "m"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="給排水設備工事の配管工事における歩掛かりの考え方を教えてください。",
        expected_keywords=["給排水", "配管", "歩掛かり", "m"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="実行予算と当初見積もりの乖離が大きい場合、どのような原因が考えられますか？",
        expected_keywords=["実行予算", "見積", "乖離", "原因"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="下請け業者の選定時に確認すべき建設業許可の種類と内容を教えてください。",
        expected_keywords=["建設業許可", "下請", "選定", "許可"],
        category="construction",
        difficulty="medium",
    ),
    GoldenSample(
        question="有資格者証の管理で、グリーンサイトに登録できる資格の種類を教えてください。",
        expected_keywords=["有資格者", "グリーンサイト", "資格", "登録"],
        category="construction",
        difficulty="medium",
    ),
    # ---- hard: 追加6件（計7件） ----
    GoldenSample(
        question="地盤改良工事（柱状改良）と杭工事（既製コンクリート杭）をコスト・工期・地耐力改善効果で比較してください。",
        expected_keywords=["地盤改良", "柱状改良", "杭", "コスト", "工期"],
        category="construction",
        difficulty="hard",
    ),
    GoldenSample(
        question="積算ソフトで補正係数を設定する際、地域補正・規模補正・施工難易度補正はどのように組み合わせて適用しますか？",
        expected_keywords=["積算", "補正係数", "地域", "規模", "難易度"],
        category="construction",
        difficulty="hard",
    ),
    GoldenSample(
        question="建設業法上、下請け金額が4,500万円以上になる場合に必要な特定建設業許可の取得要件を教えてください。",
        expected_keywords=["特定建設業", "許可", "4,500万", "要件"],
        category="construction",
        difficulty="hard",
    ),
    GoldenSample(
        question="雨天施工不可の工種が多い現場で、工程遅延が発生した場合のコスト増加をどう見積もりますか？",
        expected_keywords=["雨天", "工程遅延", "コスト", "見積"],
        category="construction",
        difficulty="hard",
    ),
    GoldenSample(
        question="既存建物の解体工事でアスベスト含有材が発見された場合の対応手順と法的義務を教えてください。",
        expected_keywords=["アスベスト", "解体", "法的", "対応"],
        category="construction",
        difficulty="hard",
    ),
    GoldenSample(
        question="RC造マンション建設において、設計変更によりスラブ厚が180mmから200mmに変更された場合のコンクリート数量と鉄筋数量の増加量を算出する方法を説明してください。",
        expected_keywords=["スラブ", "コンクリート", "鉄筋", "数量", "mm"],
        category="construction",
        difficulty="hard",
    ),
]

# ゴールデンデータセット（製造業）
MANUFACTURING_GOLDEN: list[GoldenSample] = [
    # ---- 既存3件（変更しない） ----
    GoldenSample(
        question="製品の品質検査の基準を教えてください。",
        expected_keywords=["検査", "基準", "合格"],
        category="manufacturing",
        difficulty="easy",
    ),
    GoldenSample(
        question="材料費の見積もりはどのように計算しますか？",
        expected_keywords=["材料", "単価", "計算"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="設備の保全計画と突発故障時の対応フローを教えてください。",
        expected_keywords=["保全", "故障", "対応", "フロー"],
        category="manufacturing",
        difficulty="hard",
    ),
    # ---- easy: 追加6件（計7件） ----
    GoldenSample(
        question="鉄鋼材料（SS400）の市場単価の目安を教えてください。",
        expected_keywords=["SS400", "円", "kg", "単価"],
        category="manufacturing",
        difficulty="easy",
    ),
    GoldenSample(
        question="旋盤加工の標準的な加工費（時間チャージ）はいくらですか？",
        expected_keywords=["旋盤", "円", "時間", "加工費"],
        category="manufacturing",
        difficulty="easy",
    ),
    GoldenSample(
        question="製品の外観検査でNG品が出た場合の基本的な対応手順を教えてください。",
        expected_keywords=["外観検査", "NG", "対応", "手順"],
        category="manufacturing",
        difficulty="easy",
    ),
    GoldenSample(
        question="ロット生産における段取り時間とはどういうものですか？",
        expected_keywords=["段取り", "ロット", "時間", "準備"],
        category="manufacturing",
        difficulty="easy",
    ),
    GoldenSample(
        question="プレス加工品の見積もりで材料費・プレス加工費・金型償却費の3項目を教えてください。",
        expected_keywords=["プレス", "材料費", "加工費", "金型"],
        category="manufacturing",
        difficulty="easy",
    ),
    GoldenSample(
        question="溶接工程でよく使われるTIG溶接とMIG溶接の違いを教えてください。",
        expected_keywords=["TIG", "MIG", "溶接", "違い"],
        category="manufacturing",
        difficulty="easy",
    ),
    # ---- medium: 追加7件（計8件） ----
    GoldenSample(
        question="タクトタイムが20秒の場合、1直8時間で何個生産できますか？",
        expected_keywords=["タクトタイム", "個", "生産", "8時間"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="設備稼働率が85%の場合、月間の損失時間はどのくらいになりますか？",
        expected_keywords=["稼働率", "損失", "時間", "%"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="外注加工と内製加工のどちらが有利かを判断する際の基準を教えてください。",
        expected_keywords=["外注", "内製", "判断", "コスト"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="図面上の公差±0.05mmを満たすための加工方法と測定器を教えてください。",
        expected_keywords=["公差", "mm", "加工", "測定"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="受注ロット数が増えると単価が下がる理由を原価計算の観点から説明してください。",
        expected_keywords=["ロット", "単価", "原価", "固定費"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="アルミ材の表面仕上げでアルマイト処理とクロムめっきの違いを教えてください。",
        expected_keywords=["アルマイト", "めっき", "アルミ", "表面"],
        category="manufacturing",
        difficulty="medium",
    ),
    GoldenSample(
        question="切削加工品の見積もりで材料費・加工費・管理費の割合の目安を教えてください。",
        expected_keywords=["材料費", "加工費", "管理費", "割合"],
        category="manufacturing",
        difficulty="medium",
    ),
    # ---- hard: 追加4件（計5件） ----
    GoldenSample(
        question="設備の総合設備効率（OEE）が60%の工場で、可用性・性能・品質の各ロスを特定して改善優先度を決める方法を教えてください。",
        expected_keywords=["OEE", "可用性", "性能", "品質", "改善"],
        category="manufacturing",
        difficulty="hard",
    ),
    GoldenSample(
        question="新規受注品の初回量産立ち上げ時に、工程FMEAを用いてリスクを評価する手順を教えてください。",
        expected_keywords=["FMEA", "工程", "リスク", "量産", "評価"],
        category="manufacturing",
        difficulty="hard",
    ),
    GoldenSample(
        question="材料費高騰で原価が10%上昇した場合、製品単価への転嫁率と利益率への影響を試算する方法を教えてください。",
        expected_keywords=["材料費", "原価", "単価", "利益率", "転嫁"],
        category="manufacturing",
        difficulty="hard",
    ),
    GoldenSample(
        question="多品種少量生産ラインで段取り替え時間を50%削減するためのSMED手法の適用ステップを教えてください。",
        expected_keywords=["SMED", "段取り", "削減", "多品種", "少量"],
        category="manufacturing",
        difficulty="hard",
    ),
]


def _calculate_keyword_hit_rate(answer: str, expected_keywords: list[str]) -> float:
    """expected_keywordsの何割がanswerに含まれるかを計算する。

    大文字小文字を区別しない。キーワードが空の場合は1.0を返す。
    """
    if not expected_keywords:
        return 1.0
    answer_lower = answer.lower()
    hits = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
    return round(hits / len(expected_keywords), 4)


def _aggregate_group(results: list[EvalResult]) -> dict:
    """グループ（difficulty/category単位）の集計を返す。"""
    if not results:
        return {
            "count": 0,
            "avg_confidence": 0.0,
            "avg_keyword_hit_rate": 0.0,
            "avg_cost_yen": 0.0,
            "avg_latency_ms": 0.0,
        }
    n = len(results)
    return {
        "count": n,
        "avg_confidence": round(sum(r.confidence for r in results) / n, 4),
        "avg_keyword_hit_rate": round(sum(r.keyword_hit_rate for r in results) / n, 4),
        "avg_cost_yen": round(sum(r.cost_yen for r in results) / n, 6),
        "avg_latency_ms": round(sum(r.latency_ms for r in results) / n, 1),
    }


async def _evaluate_single(
    sample: GoldenSample,
    company_id: str,
    use_enhanced_search: bool,
) -> EvalResult:
    """1件のゴールデンサンプルを評価する。"""
    start_ms = time.monotonic() * 1000
    qa_result = await answer_question(
        question=sample.question,
        company_id=company_id,
        use_enhanced_search=use_enhanced_search,
    )
    latency_ms = time.monotonic() * 1000 - start_ms

    keyword_hit_rate = _calculate_keyword_hit_rate(qa_result.answer, sample.expected_keywords)

    return EvalResult(
        question=sample.question,
        answer=qa_result.answer,
        confidence=qa_result.confidence,
        keyword_hit_rate=keyword_hit_rate,
        search_mode=qa_result.search_mode,
        cost_yen=qa_result.cost_yen,
        latency_ms=round(latency_ms, 1),
    )


async def run_eval(
    company_id: str,
    golden_dataset: list[GoldenSample],
    use_enhanced_search: bool = True,
    concurrency: int = 3,
) -> EvalReport:
    """ゴールデンデータセットに対してQ&Aを実行し精度レポートを返す。

    Args:
        company_id: テナントID（RLS適用）
        golden_dataset: 評価用ゴールデンサンプルのリスト
        use_enhanced_search: Trueのとき enhanced_search を使用
        concurrency: 同時実行数の上限（LLMコスト・レート制限対策）

    Returns:
        EvalReport: 全体・difficulty別・category別の集計レポート
    """
    if not golden_dataset:
        return EvalReport(
            total=0,
            avg_confidence=0.0,
            avg_keyword_hit_rate=0.0,
            avg_cost_yen=0.0,
            avg_latency_ms=0.0,
            by_difficulty={},
            by_category={},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_eval(sample: GoldenSample) -> EvalResult:
        async with semaphore:
            try:
                return await _evaluate_single(sample, company_id, use_enhanced_search)
            except Exception as e:
                logger.error(f"eval failed for question='{sample.question}': {e}")
                # 失敗した場合はゼロスコアのEvalResultを返す
                return EvalResult(
                    question=sample.question,
                    answer="",
                    confidence=0.0,
                    keyword_hit_rate=0.0,
                    search_mode="enhanced" if use_enhanced_search else "hybrid",
                    cost_yen=0.0,
                    latency_ms=0.0,
                )

    eval_results = await asyncio.gather(*(bounded_eval(s) for s in golden_dataset))
    eval_results_list = list(eval_results)

    n = len(eval_results_list)
    avg_confidence = round(sum(r.confidence for r in eval_results_list) / n, 4)
    avg_keyword_hit_rate = round(sum(r.keyword_hit_rate for r in eval_results_list) / n, 4)
    avg_cost_yen = round(sum(r.cost_yen for r in eval_results_list) / n, 6)
    avg_latency_ms = round(sum(r.latency_ms for r in eval_results_list) / n, 1)

    # difficulty別集計
    difficulty_groups: dict[str, list[EvalResult]] = {}
    for sample, result in zip(golden_dataset, eval_results_list):
        difficulty_groups.setdefault(sample.difficulty, []).append(result)
    by_difficulty = {k: _aggregate_group(v) for k, v in difficulty_groups.items()}

    # category別集計
    category_groups: dict[str, list[EvalResult]] = {}
    for sample, result in zip(golden_dataset, eval_results_list):
        category_groups.setdefault(sample.category, []).append(result)
    by_category = {k: _aggregate_group(v) for k, v in category_groups.items()}

    return EvalReport(
        total=n,
        avg_confidence=avg_confidence,
        avg_keyword_hit_rate=avg_keyword_hit_rate,
        avg_cost_yen=avg_cost_yen,
        avg_latency_ms=avg_latency_ms,
        by_difficulty=by_difficulty,
        by_category=by_category,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
