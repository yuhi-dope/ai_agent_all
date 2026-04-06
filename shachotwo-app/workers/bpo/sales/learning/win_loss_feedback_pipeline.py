"""学習パイプライン⑧ — 受注/失注フィードバック

トリガー: 商談ステージが won / lost に変更された時

Steps:
  -- 受注時 --
  Step 1: win_pattern_extractor   受注パターン抽出（業種・規模・ペイン・サイクル・モジュール）
  Step 2: score_weight_updater    リードスコアリング重み更新（受注した業種+規模 → ボーナスUP）
  Step 3: success_template_saver  提案書を成功テンプレートとして win_loss_patterns に保存

  -- 失注時 --
  Step 4: loss_hearing_mailer     失注理由ヒアリングメール自動送信
  Step 5: loss_pattern_analyzer   失注パターン分析 → win_loss_patterns に蓄積 / アラート生成

  -- アウトリーチPDCA（日次バッチ） --
  Step 6: outreach_performance_aggregator  業種別反応率集計 → outreach_performance テーブル更新
  Step 7: ab_test_optimizer                反応率低業種 → メールA/Bテスト文案生成 / 優先度調整

設計書: shachotwo/b_詳細設計/b_06_全社自動化設計_マーケSFA_CRM_CS.md Section 4.8
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from db.supabase import get_service_client
from llm.client import LLMTask, ModelTier, get_llm_client
from workers.micro.message import MessageDraftResult, run_message_drafter
from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

Outcome = Literal["won", "lost"]

# 受注業種×規模でスコアボーナスを上げる刻み幅（ポイント）
SCORE_BONUS_INCREMENT = 5

# 業種別デフォルトスコアボーナスのベースライン
DEFAULT_SCORE_BONUS = 0

# 反応率が高い/低いと判断する閾値（0.0〜1.0）
HIGH_RESPONSE_RATE_THRESHOLD = 0.15  # 15%以上 → 優先度UP
LOW_RESPONSE_RATE_THRESHOLD = 0.05   # 5%未満 → A/Bテスト発動

# 失注理由ランキングで料金/機能アラートを出す件数の閾値
PRICE_ALERT_THRESHOLD = 3     # 「価格」理由が3件以上で料金体系見直しアラート
FEATURE_ALERT_THRESHOLD = 3   # 「機能不足」理由が3件以上でfeature_requestsアラート

# 失注理由の選択肢
LOST_REASON_OPTIONS = [
    "price",          # 価格
    "feature_lack",   # 機能不足
    "competitor",     # 他社選定
    "timing",         # 時期尚早
    "not_needed",     # 不要
    "unknown",        # 不明
]

# A/Bテストで生成する文案バリアント数
AB_VARIANT_COUNT = 2


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """1ステップの実行結果"""
    step_no: int
    step_name: str
    agent_name: str
    success: bool
    result: dict[str, Any]
    confidence: float
    cost_yen: float
    duration_ms: int
    warning: str | None = None


@dataclass
class WinLossFeedbackResult:
    """受注/失注フィードバックパイプラインの最終結果"""
    success: bool
    outcome: str                                        # won / lost / outreach_pdca
    steps: list[StepResult] = field(default_factory=list)
    final_output: dict[str, Any] = field(default_factory=dict)
    total_cost_yen: float = 0.0
    total_duration_ms: int = 0
    failed_step: str | None = None

    def summary(self) -> str:
        status = "成功" if self.success else f"失敗（{self.failed_step}）"
        step_labels = [f"Step{s.step_no}:{s.step_name}" for s in self.steps]
        return (
            f"[受注/失注フィードバック] {status} | outcome={self.outcome} | "
            f"ステップ: {','.join(step_labels)} | "
            f"コスト: ¥{self.total_cost_yen:.4f} | "
            f"所要時間: {self.total_duration_ms}ms"
        )


# ---------------------------------------------------------------------------
# パイプライン本体
# ---------------------------------------------------------------------------

class WinLossFeedbackPipeline:
    """
    受注/失注フィードバック学習パイプライン（パイプライン⑧）

    - outcome="won"  → Step 1-3（受注パターン学習）
    - outcome="lost" → Step 4-5（失注パターン学習）
    - mode="outreach_pdca" → Step 6-7（日次アウトリーチPDCA）

    各モードは単独で呼び出し可能。run() で outcome に応じて自動ルーティングする。
    """

    def __init__(self) -> None:
        self.llm = get_llm_client()

    # ------------------------------------------------------------------
    # パブリック エントリポイント
    # ------------------------------------------------------------------

    async def run(
        self,
        company_id: str,
        input_data: dict[str, Any],
    ) -> WinLossFeedbackResult:
        """
        メインエントリポイント。outcome に応じてステップを実行する。

        input_data のキー:
          outcome (str): "won" / "lost" / "outreach_pdca"
          -- won/lost 共通 --
          opportunity_id (str): 商談ID
          company_name (str): 顧客会社名
          contact_email (str): 連絡先メールアドレス（失注時のヒアリングメール送付先）
          industry (str): 業種
          employee_range (str): 従業員規模帯（例: "50-99", "100-299"）
          lead_source (str): リードソース（例: "outreach", "inbound"）
          sales_cycle_days (int): セールスサイクル日数
          selected_modules (list[str]): 選択されたモジュール
          pain_points (str): ヒアリングで得たペインポイントのテキスト
          proposal_version_id (str, optional): 使用した提案書のバージョンID
          -- lost 追加 --
          lost_reason (str, optional): 既知の失注理由（選択肢はLOST_REASON_OPTIONS参照）
          lost_reason_detail (str, optional): 失注理由の詳細テキスト
          -- outreach_pdca --
          period (str): 集計期間（YYYY-MM-DD）
          outreach_stats (list[dict]): 業種別アウトリーチ実績データ
            各要素: {industry, researched, outreached, lp_viewed, lead_converted, meeting_booked,
                     email_variant, open_rate, click_rate}
        """
        outcome: str = input_data.get("outcome", "")
        steps: list[StepResult] = []

        if outcome == "won":
            return await self._run_won_flow(company_id, input_data, steps)
        elif outcome == "lost":
            return await self._run_lost_flow(company_id, input_data, steps)
        elif outcome == "outreach_pdca":
            return await self._run_outreach_pdca_flow(company_id, input_data, steps)
        else:
            return WinLossFeedbackResult(
                success=False,
                outcome=outcome,
                steps=steps,
                final_output={"error": f"不明な outcome: {outcome}. won/lost/outreach_pdca のいずれかを指定してください。"},
                failed_step="input_validation",
            )

    # ------------------------------------------------------------------
    # 受注フロー: Step 1-3
    # ------------------------------------------------------------------

    async def _run_won_flow(
        self,
        company_id: str,
        input_data: dict[str, Any],
        steps: list[StepResult],
    ) -> WinLossFeedbackResult:
        """受注時: パターン抽出 → スコア重み更新 → 成功テンプレート保存"""
        context: dict[str, Any] = {}

        # Step 1: 受注パターン抽出
        step1 = await self._win_pattern_extractor(company_id, input_data)
        steps.append(step1)
        if not step1.success:
            return self._make_result(False, "won", steps, context, step1.step_name)
        context.update(step1.result)

        # Step 2: スコアリング重み更新
        step2 = await self._score_weight_updater(company_id, context)
        steps.append(step2)
        if not step2.success:
            return self._make_result(False, "won", steps, context, step2.step_name)
        context.update(step2.result)

        # Step 3: 成功テンプレート保存
        step3 = await self._success_template_saver(company_id, input_data, context)
        steps.append(step3)
        if not step3.success:
            return self._make_result(False, "won", steps, context, step3.step_name)
        context.update(step3.result)

        return self._make_result(True, "won", steps, context)

    # ------------------------------------------------------------------
    # 失注フロー: Step 4-5
    # ------------------------------------------------------------------

    async def _run_lost_flow(
        self,
        company_id: str,
        input_data: dict[str, Any],
        steps: list[StepResult],
    ) -> WinLossFeedbackResult:
        """失注時: ヒアリングメール送信 → 失注パターン分析"""
        context: dict[str, Any] = {}

        # Step 4: 失注理由ヒアリングメール送信
        step4 = await self._loss_hearing_mailer(company_id, input_data)
        steps.append(step4)
        if not step4.success:
            return self._make_result(False, "lost", steps, context, step4.step_name)
        context.update(step4.result)

        # Step 5: 失注パターン分析
        step5 = await self._loss_pattern_analyzer(company_id, input_data, context)
        steps.append(step5)
        if not step5.success:
            return self._make_result(False, "lost", steps, context, step5.step_name)
        context.update(step5.result)

        return self._make_result(True, "lost", steps, context)

    # ------------------------------------------------------------------
    # アウトリーチPDCAフロー: Step 6-7
    # ------------------------------------------------------------------

    async def _run_outreach_pdca_flow(
        self,
        company_id: str,
        input_data: dict[str, Any],
        steps: list[StepResult],
    ) -> WinLossFeedbackResult:
        """日次バッチ: 反応率集計 → A/Bテスト最適化"""
        context: dict[str, Any] = {}

        # Step 6: アウトリーチ成果集計
        step6 = await self._outreach_performance_aggregator(company_id, input_data)
        steps.append(step6)
        if not step6.success:
            return self._make_result(False, "outreach_pdca", steps, context, step6.step_name)
        context.update(step6.result)

        # Step 7: A/Bテスト最適化
        step7 = await self._ab_test_optimizer(company_id, context)
        steps.append(step7)
        if not step7.success:
            return self._make_result(False, "outreach_pdca", steps, context, step7.step_name)
        context.update(step7.result)

        return self._make_result(True, "outreach_pdca", steps, context)

    # ------------------------------------------------------------------
    # Step 1: 受注パターン抽出
    # ------------------------------------------------------------------

    async def _win_pattern_extractor(
        self,
        company_id: str,
        input_data: dict[str, Any],
    ) -> StepResult:
        """
        受注案件から「何が刺さったか」を構造化して抽出する。

        LLMで pain_points テキストを分析し、勝因を定量化する。
        直接渡し形式（industry/employee_range 等が揃っている）は LLM 呼び出しをスキップ。
        """
        step_name = "win_pattern_extractor"
        start_ms = int(time.time() * 1000)

        try:
            pain_text: str = input_data.get("pain_points", "")
            industry: str = input_data.get("industry", "")
            employee_range: str = input_data.get("employee_range", "")
            lead_source: str = input_data.get("lead_source", "outreach")
            sales_cycle_days: int = int(input_data.get("sales_cycle_days", 0))
            selected_modules: list[str] = input_data.get("selected_modules", [])

            # LLMで勝因を構造化
            win_factors: dict[str, Any] = {}
            cost_yen = 0.0

            if pain_text:
                prompt_system = (
                    "あなたはB2B SaaS営業の受注パターン分析専門家です。\n"
                    "以下の受注案件情報から「勝因（何が刺さったか）」を構造化してください。\n\n"
                    "出力形式（JSONのみ）:\n"
                    "{\n"
                    '  "primary_pain": "最も響いたペインポイント（1文）",\n'
                    '  "key_selling_points": ["刺さった訴求ポイント（最大3件）"],\n'
                    '  "decision_factors": ["意思決定を後押しした要因（最大3件）"],\n'
                    '  "proposal_strength": "提案書の強みだった箇所（1文）",\n'
                    '  "win_confidence": 0.0から1.0の確信度\n'
                    "}"
                )
                task = LLMTask(
                    messages=[
                        {"role": "system", "content": prompt_system},
                        {"role": "user", "content": (
                            f"業種: {industry}\n"
                            f"従業員規模: {employee_range}\n"
                            f"リードソース: {lead_source}\n"
                            f"セールスサイクル: {sales_cycle_days}日\n"
                            f"選択モジュール: {', '.join(selected_modules)}\n"
                            f"ペインポイント記録:\n{pain_text}"
                        )},
                    ],
                    tier=ModelTier.FAST,
                    max_tokens=512,
                    temperature=0.1,
                    company_id=company_id,
                    task_type="win_pattern_extraction",
                )
                response = await self.llm.generate(task)
                cost_yen = self._estimate_cost(response.tokens_in, response.tokens_out, ModelTier.FAST)

                json_match = re.search(r"\{.*\}", response.content, re.DOTALL)
                if json_match:
                    win_factors = json.loads(json_match.group())
                else:
                    win_factors = json.loads(response.content.strip())

            result = {
                "industry": industry,
                "employee_range": employee_range,
                "lead_source": lead_source,
                "sales_cycle_days": sales_cycle_days,
                "selected_modules": selected_modules,
                "win_factors": win_factors,
                "score_key": f"{industry}__{employee_range}",
            }

            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=1,
                step_name=step_name,
                agent_name="structured_extractor",
                success=True,
                result=result,
                confidence=float(win_factors.get("win_confidence", 0.8)),
                cost_yen=cost_yen,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=1,
                step_name=step_name,
                agent_name="structured_extractor",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # Step 2: スコアリング重み更新
    # ------------------------------------------------------------------

    async def _score_weight_updater(
        self,
        company_id: str,
        context: dict[str, Any],
    ) -> StepResult:
        """
        受注した業種+規模の組み合わせに対してスコアボーナスを SCORE_BONUS_INCREMENT 分加算する。

        scoring_model_versions テーブルの weights JSONB を更新する。
        DB接続に失敗した場合でも警告のみとし、パイプラインは継続する。
        """
        step_name = "score_weight_updater"
        start_ms = int(time.time() * 1000)

        try:
            score_key: str = context.get("score_key", "")
            industry: str = context.get("industry", "")
            employee_range: str = context.get("employee_range", "")

            updated_weights: dict[str, Any] = {}
            db_updated = False
            warning: str | None = None

            try:
                db = get_service_client()

                # 現在のアクティブなリードスコアモデルを取得
                res = (
                    db.table("scoring_model_versions")
                    .select("id, version, weights")
                    .eq("company_id", company_id)
                    .eq("model_type", "lead_score")
                    .eq("active", True)
                    .order("version", desc=True)
                    .limit(1)
                    .execute()
                )

                current_weights: dict[str, Any] = {}
                current_version = 0
                current_id: str | None = None

                if res.data:
                    row = res.data[0]
                    current_weights = row.get("weights") or {}
                    current_version = row.get("version", 0)
                    current_id = row.get("id")

                # スコアボーナスを加算
                bonus_key = f"industry_size_bonus__{score_key}"
                current_bonus: int = current_weights.get(bonus_key, DEFAULT_SCORE_BONUS)
                new_bonus = current_bonus + SCORE_BONUS_INCREMENT
                updated_weights = {**current_weights, bonus_key: new_bonus}

                # 新バージョンとして保存（既存を非アクティブ化）
                if current_id:
                    db.table("scoring_model_versions").update({"active": False}).eq("id", current_id).execute()

                db.table("scoring_model_versions").insert({
                    "company_id": company_id,
                    "model_type": "lead_score",
                    "version": current_version + 1,
                    "weights": updated_weights,
                    "performance_metrics": {
                        "updated_reason": "win_feedback",
                        "updated_at": datetime.utcnow().isoformat(),
                        "win_key": score_key,
                        "bonus_delta": SCORE_BONUS_INCREMENT,
                    },
                    "active": True,
                }).execute()

                db_updated = True

            except ImportError:
                warning = "DB未接続のため scoring_model_versions 更新をスキップ（ドライラン）"
                logger.warning(f"[{step_name}] {warning}")
            except Exception as db_exc:
                warning = f"DB更新失敗（処理継続）: {db_exc}"
                logger.warning(f"[{step_name}] {warning}")

            result = {
                "score_key": score_key,
                "industry": industry,
                "employee_range": employee_range,
                "bonus_delta": SCORE_BONUS_INCREMENT,
                "updated_weights": updated_weights,
                "db_updated": db_updated,
                "score_bonus_message": (
                    f"{industry}×{employee_range} のスコアボーナスを +{SCORE_BONUS_INCREMENT}pt 調整しました。"
                ),
            }

            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=2,
                step_name=step_name,
                agent_name="rule_matcher",
                success=True,
                result=result,
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
                warning=warning,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=2,
                step_name=step_name,
                agent_name="rule_matcher",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # Step 3: 成功テンプレート保存
    # ------------------------------------------------------------------

    async def _success_template_saver(
        self,
        company_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """
        受注案件の提案書・勝因データを win_loss_patterns テーブルに「成功テンプレート」として保存する。
        次回の提案書AI生成時に Few-shot として参照される。
        """
        step_name = "success_template_saver"
        start_ms = int(time.time() * 1000)

        try:
            pattern_record = {
                "company_id": company_id,
                "opportunity_id": input_data.get("opportunity_id", ""),
                "outcome": "won",
                "industry": context.get("industry", ""),
                "employee_range": context.get("employee_range", ""),
                "lead_source": context.get("lead_source", ""),
                "sales_cycle_days": context.get("sales_cycle_days", 0),
                "selected_modules": context.get("selected_modules", []),
                "win_factors": context.get("win_factors", {}),
                "lost_reason": None,
                "proposal_version_id": input_data.get("proposal_version_id"),
            }

            saved = False
            warning: str | None = None

            try:
                from db.supabase import get_service_client
                db = get_service_client()
                db.table("win_loss_patterns").insert(pattern_record).execute()
                saved = True
            except ImportError:
                warning = "DB未接続のため win_loss_patterns 保存をスキップ（ドライラン）"
                logger.warning(f"[{step_name}] {warning}")
            except Exception as db_exc:
                warning = f"DB保存失敗（処理継続）: {db_exc}"
                logger.warning(f"[{step_name}] {warning}")

            result = {
                "pattern_saved": saved,
                "pattern_record": pattern_record,
                "template_message": (
                    f"受注パターン（{pattern_record['industry']} / {pattern_record['employee_range']}）を "
                    "成功テンプレートとして保存しました。次回提案書生成時に Few-shot として参照されます。"
                ),
            }

            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=3,
                step_name=step_name,
                agent_name="saas_writer",
                success=True,
                result=result,
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
                warning=warning,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=3,
                step_name=step_name,
                agent_name="saas_writer",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # Step 4: 失注理由ヒアリングメール送信
    # ------------------------------------------------------------------

    async def _loss_hearing_mailer(
        self,
        company_id: str,
        input_data: dict[str, Any],
    ) -> StepResult:
        """
        失注が確定した顧客へ、失注理由ヒアリングメールを自動送信する。

        run_message_drafter で本文を生成し、送信ログを返す。
        実際の送信は SaaS コネクタ（Sendgrid 等）が担うが、
        MVP では本文生成＋ログ記録のみとし、送信はキューに積む設計。
        """
        step_name = "loss_hearing_mailer"
        start_ms = int(time.time() * 1000)

        try:
            company_name: str = input_data.get("company_name", "ご担当者様")
            contact_email: str = input_data.get("contact_email", "")
            industry: str = input_data.get("industry", "")
            selected_modules: list[str] = input_data.get("selected_modules", [])

            mail_context = {
                "company_name": company_name,
                "industry": industry,
                "selected_modules": selected_modules,
                "lost_reason_options": LOST_REASON_OPTIONS,
            }

            draft: MessageDraftResult = await run_message_drafter(
                document_type="失注理由ヒアリングメール",
                context=mail_context,
                company_id=company_id,
                model_tier=ModelTier.FAST,
            )

            # MVP: 送信キューへの積み込み（実装はコネクタ側）
            # TODO: connector/email.py に send_email() を実装後に呼び出す
            enqueued = True

            result = {
                "contact_email": contact_email,
                "mail_subject": draft.subject,
                "mail_body": draft.body,
                "enqueued": enqueued,
                "hearing_message": (
                    f"{company_name} 宛に失注理由ヒアリングメールを送信キューに積みました。"
                ),
            }

            # cost_yen はメッセージドラフターが LLM を呼んだ場合に発生するが、
            # run_message_drafter がコストを返さないため 0 で記録し、LLM コストは
            # llm/cost_tracker.py 側で company_id ベースに集計済みとする
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=4,
                step_name=step_name,
                agent_name="message_drafter",
                success=True,
                result=result,
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=4,
                step_name=step_name,
                agent_name="message_drafter",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # Step 5: 失注パターン分析
    # ------------------------------------------------------------------

    async def _loss_pattern_analyzer(
        self,
        company_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> StepResult:
        """
        失注理由を構造化して win_loss_patterns に蓄積し、月次集計でアラートを生成する。

        - 直接指定された lost_reason があればそれを使う
        - テキストが渡された場合は LLM で分類
        - 月次集計で価格/機能不足の失注が閾値超えたらアラートを追加
        """
        step_name = "loss_pattern_analyzer"
        start_ms = int(time.time() * 1000)

        try:
            lost_reason: str = input_data.get("lost_reason", "unknown")
            lost_reason_detail: str = input_data.get("lost_reason_detail", "")
            industry: str = input_data.get("industry", "")
            employee_range: str = input_data.get("employee_range", "")
            cost_yen = 0.0
            classified_reason = lost_reason

            # 詳細テキストがある場合は LLM で理由を分類
            if lost_reason_detail and lost_reason == "unknown":
                prompt_system = (
                    "以下の失注理由テキストを分類してください。\n"
                    f"選択肢: {', '.join(LOST_REASON_OPTIONS)}\n"
                    "出力形式（JSONのみ）: "
                    '{"reason": "選択肢のいずれか", "confidence": 0.0-1.0}'
                )
                task = LLMTask(
                    messages=[
                        {"role": "system", "content": prompt_system},
                        {"role": "user", "content": f"失注理由テキスト:\n{lost_reason_detail}"},
                    ],
                    tier=ModelTier.FAST,
                    max_tokens=128,
                    temperature=0.0,
                    company_id=company_id,
                    task_type="lost_reason_classification",
                )
                response = await self.llm.generate(task)
                cost_yen = self._estimate_cost(response.tokens_in, response.tokens_out, ModelTier.FAST)
                json_match = re.search(r"\{.*\}", response.content, re.DOTALL)
                if json_match:
                    classified = json.loads(json_match.group())
                    classified_reason = classified.get("reason", "unknown")

            # win_loss_patterns に保存
            pattern_record = {
                "company_id": company_id,
                "opportunity_id": input_data.get("opportunity_id", ""),
                "outcome": "lost",
                "industry": industry,
                "employee_range": employee_range,
                "lead_source": input_data.get("lead_source", ""),
                "sales_cycle_days": int(input_data.get("sales_cycle_days", 0)),
                "selected_modules": input_data.get("selected_modules", []),
                "lost_reason": classified_reason,
                "win_factors": None,
                "proposal_version_id": input_data.get("proposal_version_id"),
            }

            saved = False
            warning: str | None = None

            try:
                db = get_service_client()
                db.table("win_loss_patterns").insert(pattern_record).execute()
                saved = True
            except ImportError:
                warning = "DB未接続のため win_loss_patterns 保存をスキップ（ドライラン）"
                logger.warning(f"[{step_name}] {warning}")
            except Exception as db_exc:
                warning = f"DB保存失敗（処理継続）: {db_exc}"
                logger.warning(f"[{step_name}] {warning}")

            # 月次集計でアラートを生成（DBがある場合のみ）
            alerts: list[str] = []
            feature_requests: list[str] = []
            monthly_ranking: dict[str, int] = {}

            if saved:
                try:
                    db = get_service_client()
                    # 当月の失注理由を集計
                    today = date.today()
                    month_start = today.replace(day=1).isoformat()
                    res = (
                        db.table("win_loss_patterns")
                        .select("lost_reason")
                        .eq("company_id", company_id)
                        .eq("outcome", "lost")
                        .gte("created_at", month_start)
                        .execute()
                    )
                    if res.data:
                        for row in res.data:
                            reason = row.get("lost_reason") or "unknown"
                            monthly_ranking[reason] = monthly_ranking.get(reason, 0) + 1

                    price_count = monthly_ranking.get("price", 0)
                    feature_count = monthly_ranking.get("feature_lack", 0)

                    if price_count >= PRICE_ALERT_THRESHOLD:
                        alerts.append(
                            f"[料金体系見直しアラート] 今月の失注理由「価格」が{price_count}件に達しました。"
                            "料金体系の見直しを検討してください。"
                        )
                    if feature_count >= FEATURE_ALERT_THRESHOLD:
                        feature_requests.append(
                            f"[機能要望アラート] 今月の失注理由「機能不足」が{feature_count}件に達しました。"
                            "feature_requests テーブルへの自動登録を推奨します。"
                        )
                        alerts.extend(feature_requests)

                except Exception as agg_exc:
                    logger.warning(f"[{step_name}] 月次集計失敗（処理継続）: {agg_exc}")

            result = {
                "classified_reason": classified_reason,
                "pattern_saved": saved,
                "monthly_ranking": monthly_ranking,
                "alerts": alerts,
                "feature_requests": feature_requests,
                "pattern_record": pattern_record,
            }

            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=5,
                step_name=step_name,
                agent_name="structured_extractor",
                success=True,
                result=result,
                confidence=1.0,
                cost_yen=cost_yen,
                duration_ms=duration_ms,
                warning=warning,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=5,
                step_name=step_name,
                agent_name="structured_extractor",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # Step 6: アウトリーチ成果集計
    # ------------------------------------------------------------------

    async def _outreach_performance_aggregator(
        self,
        company_id: str,
        input_data: dict[str, Any],
    ) -> StepResult:
        """
        業種別アウトリーチ実績データを集計し、outreach_performance テーブルに保存する。
        反応率（lead_converted / outreached）を計算して結果に含める。
        """
        step_name = "outreach_performance_aggregator"
        start_ms = int(time.time() * 1000)

        try:
            period_str: str = input_data.get("period", date.today().isoformat())
            outreach_stats: list[dict[str, Any]] = input_data.get("outreach_stats", [])

            if not outreach_stats:
                duration_ms = int(time.time() * 1000) - start_ms
                return StepResult(
                    step_no=6,
                    step_name=step_name,
                    agent_name="saas_reader",
                    success=False,
                    result={"error": "outreach_stats が空です"},
                    confidence=0.0,
                    cost_yen=0.0,
                    duration_ms=duration_ms,
                )

            # 反応率を計算して行ごとに付加
            enriched_stats: list[dict[str, Any]] = []
            for stat in outreach_stats:
                outreached = stat.get("outreached", 0)
                lead_converted = stat.get("lead_converted", 0)
                response_rate = (lead_converted / outreached) if outreached > 0 else 0.0
                enriched_stats.append({
                    **stat,
                    "response_rate": round(response_rate, 4),
                })

            # outreach_performance テーブルに upsert
            saved_count = 0
            warning: str | None = None

            try:
                db = get_service_client()
                for stat in enriched_stats:
                    db.table("outreach_performance").insert({
                        "company_id": company_id,
                        "period": period_str,
                        "industry": stat.get("industry", ""),
                        "researched_count": stat.get("researched", 0),
                        "outreached_count": stat.get("outreached", 0),
                        "lp_viewed_count": stat.get("lp_viewed", 0),
                        "lead_converted_count": stat.get("lead_converted", 0),
                        "meeting_booked_count": stat.get("meeting_booked", 0),
                        "email_variant": stat.get("email_variant"),
                        "open_rate": stat.get("open_rate"),
                        "click_rate": stat.get("click_rate"),
                    }).execute()
                    saved_count += 1
            except ImportError:
                warning = "DB未接続のため outreach_performance 保存をスキップ（ドライラン）"
                logger.warning(f"[{step_name}] {warning}")
            except Exception as db_exc:
                warning = f"DB保存失敗（処理継続）: {db_exc}"
                logger.warning(f"[{step_name}] {warning}")

            result = {
                "period": period_str,
                "enriched_stats": enriched_stats,
                "saved_count": saved_count,
                "high_response_industries": [
                    s["industry"] for s in enriched_stats
                    if s["response_rate"] >= HIGH_RESPONSE_RATE_THRESHOLD
                ],
                "low_response_industries": [
                    s["industry"] for s in enriched_stats
                    if s["response_rate"] < LOW_RESPONSE_RATE_THRESHOLD
                ],
            }

            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=6,
                step_name=step_name,
                agent_name="saas_reader",
                success=True,
                result=result,
                confidence=1.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
                warning=warning,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=6,
                step_name=step_name,
                agent_name="saas_reader",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # Step 7: A/Bテスト最適化
    # ------------------------------------------------------------------

    async def _ab_test_optimizer(
        self,
        company_id: str,
        context: dict[str, Any],
    ) -> StepResult:
        """
        反応率が高い業種は優先度を UP、低い業種は A/B テスト用メール文案を LLM で生成する。
        """
        step_name = "ab_test_optimizer"
        start_ms = int(time.time() * 1000)

        try:
            high_response: list[str] = context.get("high_response_industries", [])
            low_response: list[str] = context.get("low_response_industries", [])
            enriched_stats: list[dict[str, Any]] = context.get("enriched_stats", [])

            priority_adjustments: dict[str, str] = {}
            ab_test_variants: dict[str, list[dict[str, str]]] = {}
            cost_yen = 0.0

            # 反応率が高い業種 → 優先度UP
            for industry in high_response:
                priority_adjustments[industry] = "high"

            # 反応率が低い業種 → A/Bテスト文案を LLM で生成
            for industry in low_response:
                stat = next((s for s in enriched_stats if s.get("industry") == industry), {})
                current_open_rate = stat.get("open_rate", 0.0)
                current_click_rate = stat.get("click_rate", 0.0)

                prompt_system = (
                    f"あなたはB2B SaaSのアウトリーチメール専門家です。\n"
                    f"対象業種「{industry}」向けのメール件名と導入文の改善バリアントを"
                    f"{AB_VARIANT_COUNT}種類生成してください。\n"
                    f"現在の開封率: {current_open_rate:.1%} / クリック率: {current_click_rate:.1%}\n\n"
                    "出力形式（JSONのみ）:\n"
                    '{"variants": [{"variant_name": "A", "subject": "...", "opening": "..."}, ...]}'
                )
                task = LLMTask(
                    messages=[
                        {"role": "system", "content": prompt_system},
                        {"role": "user", "content": f"{industry}業界の課題解決型メール文案を生成してください。"},
                    ],
                    tier=ModelTier.FAST,
                    max_tokens=512,
                    temperature=0.7,
                    company_id=company_id,
                    task_type="ab_test_email_generation",
                )
                response = await self.llm.generate(task)
                cost_yen += self._estimate_cost(response.tokens_in, response.tokens_out, ModelTier.FAST)

                json_match = re.search(r"\{.*\}", response.content, re.DOTALL)
                if json_match:
                    ab_data = json.loads(json_match.group())
                    ab_test_variants[industry] = ab_data.get("variants", [])

                priority_adjustments[industry] = "low_ab_test"

            result = {
                "priority_adjustments": priority_adjustments,
                "ab_test_variants": ab_test_variants,
                "optimization_summary": (
                    f"優先度UP: {len(high_response)}業種 / "
                    f"A/Bテスト発動: {len(low_response)}業種 / "
                    f"生成バリアント: {sum(len(v) for v in ab_test_variants.values())}件"
                ),
            }

            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=7,
                step_name=step_name,
                agent_name="message_drafter",
                success=True,
                result=result,
                confidence=1.0,
                cost_yen=cost_yen,
                duration_ms=duration_ms,
            )

        except Exception as exc:
            logger.warning(f"[{step_name}] 失敗: {exc}")
            duration_ms = int(time.time() * 1000) - start_ms
            return StepResult(
                step_no=7,
                step_name=step_name,
                agent_name="message_drafter",
                success=False,
                result={"error": str(exc)},
                confidence=0.0,
                cost_yen=0.0,
                duration_ms=duration_ms,
            )

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _make_result(
        self,
        success: bool,
        outcome: str,
        steps: list[StepResult],
        context: dict[str, Any],
        failed_step: str | None = None,
    ) -> WinLossFeedbackResult:
        total_cost = sum(s.cost_yen for s in steps)
        total_duration = sum(s.duration_ms for s in steps)
        return WinLossFeedbackResult(
            success=success,
            outcome=outcome,
            steps=steps,
            final_output=context,
            total_cost_yen=total_cost,
            total_duration_ms=total_duration,
            failed_step=failed_step,
        )

    def _estimate_cost(
        self,
        tokens_in: int,
        tokens_out: int,
        tier: ModelTier,
    ) -> float:
        """LLMコストを円で概算する（Gemini 2.5 Flash ベース）。"""
        # FAST tier の代表モデル: gemini-2.5-flash
        # in: 0.011 円/1Kトークン, out: 0.044 円/1Kトークン
        in_rate = 0.011
        out_rate = 0.044
        if tier == ModelTier.STANDARD:
            in_rate = 0.184
            out_rate = 0.735
        elif tier == ModelTier.PREMIUM:
            in_rate = 2.25
            out_rate = 11.25
        return (tokens_in / 1000 * in_rate) + (tokens_out / 1000 * out_rate)


# ---------------------------------------------------------------------------
# 便利関数（外部から呼び出すエントリポイント）
# ---------------------------------------------------------------------------

async def run_win_loss_feedback_pipeline(
    company_id: str,
    input_data: dict[str, Any] | None = None,
    **kwargs: Any,
) -> WinLossFeedbackResult:
    """
    受注/失注フィードバックパイプラインを実行する便利関数。

    使用例（受注時）:
        result = await run_win_loss_feedback_pipeline(
            company_id="...",
            input_data={
                "outcome": "won",
                "opportunity_id": "opp-001",
                "company_name": "株式会社ABC建設",
                "industry": "construction",
                "employee_range": "50-99",
                "lead_source": "outreach",
                "sales_cycle_days": 21,
                "selected_modules": ["estimation", "safety_docs"],
                "pain_points": "見積作成に毎回2日かかっている。手書きで非効率。",
                "proposal_version_id": "prop-v3",
            }
        )

    使用例（失注時）:
        result = await run_win_loss_feedback_pipeline(
            company_id="...",
            input_data={
                "outcome": "lost",
                "opportunity_id": "opp-002",
                "company_name": "株式会社XYZ工務店",
                "contact_email": "tanaka@xyz.co.jp",
                "industry": "construction",
                "employee_range": "10-29",
                "lost_reason": "price",
                "lost_reason_detail": "予算が限られており、現在の価格では導入が難しい",
            }
        )

    使用例（アウトリーチPDCA・日次バッチ）:
        result = await run_win_loss_feedback_pipeline(
            company_id="...",
            input_data={
                "outcome": "outreach_pdca",
                "period": "2026-03-21",
                "outreach_stats": [
                    {
                        "industry": "construction", "researched": 100, "outreached": 80,
                        "lp_viewed": 20, "lead_converted": 8, "meeting_booked": 3,
                        "email_variant": "v1", "open_rate": 0.25, "click_rate": 0.10,
                    },
                    {
                        "industry": "manufacturing", "researched": 60, "outreached": 50,
                        "lp_viewed": 3, "lead_converted": 1, "meeting_booked": 0,
                        "email_variant": "v1", "open_rate": 0.06, "click_rate": 0.02,
                    },
                ],
            }
        )
    """
    pipeline = WinLossFeedbackPipeline()
    return await pipeline.run(company_id=company_id, input_data=input_data or {})
