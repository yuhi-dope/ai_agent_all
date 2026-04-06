"""BPO共通基底パイプライン。

全業種パイプラインが継承するテンプレートメソッドパターン実装。
OCR -> 抽出 -> 補完 -> 計算 -> 検証 -> 異常検知 -> 生成 の7ステップを共通化する。

使い方:
    class MyPipeline(BasePipeline):
        pipeline_name = "my_pipeline"
        extract_schema = {"field": "説明"}
        validation_rules = {"required_fields": ["field"]}

    result = await MyPipeline().run(company_id="cid", payload={"text": "..."})
"""
from abc import ABC
from typing import Any, Callable, Coroutine, Optional
import asyncio
import logging

from workers.micro.models import MicroAgentInput, MicroAgentOutput

logger = logging.getLogger(__name__)


class PipelineStepResult:
    """単一ステップの実行結果。

    Attributes:
        compensatable: 失敗時に補償（ロールバック）が必要かどうか。
            SaaS書き込み等の副作用を持つステップでTrueにする。
        compensation_data: 補償に必要なデータ（IDや変更前の値など）。
            Phase 1ではログに記録するのみ。実際のSaaS取消はPhase 2で実装。
    """

    def __init__(
        self,
        name: str,
        success: bool,
        skipped: bool = False,
        confidence: float = 1.0,
        cost_yen: float = 0.0,
        duration_ms: int = 0,
        data: Optional[dict[str, Any]] = None,
        compensatable: bool = False,
        compensation_data: Optional[dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.success = success
        self.skipped = skipped
        self.confidence = confidence
        self.cost_yen = cost_yen
        self.duration_ms = duration_ms
        self.data = data or {}
        self.compensatable = compensatable
        self.compensation_data = compensation_data or {}

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name}
        if self.skipped:
            d["skipped"] = True
        else:
            d["success"] = self.success
            d["confidence"] = self.confidence
            d["cost_yen"] = self.cost_yen
            d["duration_ms"] = self.duration_ms
        return d


class BasePipeline(ABC):
    """全BPOパイプラインの基底クラス。

    継承して以下クラス変数をオーバーライドする:
        pipeline_name (str): パイプライン識別子。ログ・結果に使用される。
        extract_schema (dict): 構造化抽出スキーマ。空ならextractステップをスキップ。
        validation_rules (dict): output_validatorに渡すルール辞書。
            キー: required_fields / numeric_fields / positive_fields / rules
            空ならvalidateステップをスキップ。
        anomaly_config (dict | None): 異常検知設定。Noneならスキップ。
            キー: fields（対象フィールド名リスト）
                  detect_modes（["range", "digit_error", "zscore", "rules"]）
                  ranges（{フィールド名: [min, max]}）
                  rules（カスタムルールリスト）
        generate_template (str | None): document_generatorのテンプレート名。Noneならスキップ。

    以下のメソッドをオーバーライドして業種固有ロジックを注入できる:
        _step_enrich  - ルールマッチング・DB照合
        _step_calculate - 計算ロジック
    """

    pipeline_name: str = "base"
    extract_schema: dict[str, Any] = {}
    validation_rules: dict[str, Any] = {}
    anomaly_config: Optional[dict[str, Any]] = None
    generate_template: Optional[str] = None

    # -------------------------------------------------------------------
    # メイン実行フロー（オーバーライド不要）
    # -------------------------------------------------------------------

    async def run(self, company_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """7ステップパイプラインを順に実行する。

        各ステップは最大2回リトライ（合計3回試行）する。
        失敗時には compensatable=True のステップのリストを compensation_needed に記録する。

        Args:
            company_id: テナントID（RLSフィルタ用）
            payload: 入力データ。
                file_path (str, optional): OCR対象ファイルパス
                text (str, optional): テキストを直接渡す場合

        Returns:
            dict with keys:
                pipeline (str): パイプライン名
                steps (list[dict]): 各ステップの実行サマリ
                data (dict): 最終出力データ
                success (bool): 全ステップ完了したか
                anomaly_warnings (list[dict]): 異常検知で見つかった警告（あれば）
                total_cost_yen (float): 全ステップのLLMコスト合計
                total_duration_ms (int): 全ステップの処理時間合計（ms）
                failed_step (str | None): 失敗したステップ名
                compensation_needed (list[dict]): 補償が必要なステップ情報。
                    失敗時のみ設定。Phase 1ではログ記録のみ、実際のSaaS取消はPhase 2。
        """
        step_results: list[PipelineStepResult] = []
        result: dict[str, Any] = {
            "pipeline": self.pipeline_name,
            "steps": [],
            "data": {},
            "success": False,
            "anomaly_warnings": [],
            "total_cost_yen": 0.0,
            "total_duration_ms": 0,
            "failed_step": None,
        }

        def _fail(step_name: str) -> dict[str, Any]:
            """失敗時の共通後処理。補償情報を収集して返す。"""
            result["failed_step"] = step_name
            result["steps"] = [s.to_dict() for s in step_results]
            self._accumulate_totals(result, step_results)
            # 成功済みかつ補償可能なステップを記録（Phase 1: ログのみ）
            compensation_needed = [
                {"step": s.name, "compensation_data": s.compensation_data}
                for s in step_results
                if s.success and not s.skipped and s.compensatable
            ]
            if compensation_needed:
                result["compensation_needed"] = compensation_needed
                logger.warning(
                    "[%s] パイプライン失敗。補償が必要なステップ: %s",
                    self.pipeline_name,
                    [c["step"] for c in compensation_needed],
                )
            return result

        # Step 1: OCR
        text, step1 = await self._retry_step(
            lambda: self._step_ocr(company_id, payload),
            step_name="ocr",
        )
        step_results.append(step1)
        if not step1.success and not step1.skipped:
            return _fail("ocr")

        # Step 2: 構造化抽出
        extracted, step2 = await self._retry_step(
            lambda: self._step_extract(company_id, text, payload),
            step_name="extract",
        )
        step_results.append(step2)
        if not step2.success and not step2.skipped:
            return _fail("extract")

        working_data: dict[str, Any] = extracted

        # Step 3: 補完（ルールマッチング等）
        (enriched, step3) = await self._retry_step(
            lambda: self._step_enrich(company_id, working_data),
            step_name="enrich",
        )
        step_results.append(step3)
        working_data = enriched

        # Step 4: 計算
        (calculated, step4) = await self._retry_step(
            lambda: self._step_calculate(company_id, working_data),
            step_name="calculate",
        )
        step_results.append(step4)
        working_data = calculated

        # Step 5: バリデーション
        step5 = await self._retry_step(
            lambda: self._step_validate(company_id, working_data),
            step_name="validate",
        )
        step_results.append(step5)

        # Step 6: 異常検知
        step6 = await self._retry_step(
            lambda: self._step_anomaly_check(company_id, working_data, result),
            step_name="anomaly_check",
        )
        step_results.append(step6)

        # Step 7: ドキュメント生成
        (generated, step7) = await self._retry_step(
            lambda: self._step_generate(company_id, working_data),
            step_name="generate",
        )
        step_results.append(step7)
        if generated is not None:
            working_data = {**working_data, **generated}

        result["data"] = working_data
        result["success"] = True
        result["steps"] = [s.to_dict() for s in step_results]
        self._accumulate_totals(result, step_results)
        return result

    async def _retry_step(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, Any]],
        step_name: str,
        max_retries: int = 2,
    ) -> Any:
        """ステップを最大 max_retries 回リトライするヘルパー。

        Args:
            coro_factory: 引数なしで呼び出すとステップのコルーチンを返すファクトリ。
                          lambdaで包んで渡すこと（毎回新しいコルーチンを生成するため）。
            step_name: ログ出力用のステップ名。
            max_retries: 最大リトライ回数（デフォルト2回 = 合計3回試行）。

        Returns:
            最後の試行結果をそのまま返す。
            - ステップが (data, PipelineStepResult) を返す場合はそのまま。
            - ステップが PipelineStepResult 単体を返す場合はそのまま。

        Retry条件:
            - PipelineStepResult の success=False かつ skipped=False の場合のみリトライ。
            - skipped=True / success=True の場合はリトライしない。
        """
        delays = [1.0, 2.0]  # 指数バックオフ: 1秒 -> 2秒

        result = await coro_factory()

        # PipelineStepResult 単体か (data, PipelineStepResult) タプルかを判別
        if isinstance(result, PipelineStepResult):
            step = result
        else:
            step = result[-1]  # タプルの末尾が PipelineStepResult

        for attempt in range(max_retries):
            if step.success or step.skipped:
                break

            wait = delays[attempt] if attempt < len(delays) else delays[-1]
            logger.warning(
                "[%s/%s] ステップ失敗。%s秒後にリトライ (%d/%d)",
                self.pipeline_name,
                step_name,
                wait,
                attempt + 1,
                max_retries,
            )
            await asyncio.sleep(wait)

            result = await coro_factory()
            if isinstance(result, PipelineStepResult):
                step = result
            else:
                step = result[-1]

        return result

    # -------------------------------------------------------------------
    # 各ステップの実装（業種固有オーバーライド可）
    # -------------------------------------------------------------------

    async def _step_ocr(
        self, company_id: str, payload: dict[str, Any]
    ) -> tuple[str, PipelineStepResult]:
        """Step 1: OCR。file_pathがあればOCR実行、textがあればそのまま返す。"""
        from workers.micro.ocr import run_document_ocr

        if "file_path" in payload:
            out: MicroAgentOutput = await run_document_ocr(MicroAgentInput(
                company_id=company_id,
                agent_name="document_ocr",
                payload={"file_path": payload["file_path"]},
            ))
            step = PipelineStepResult(
                name="ocr",
                success=out.success,
                confidence=out.confidence,
                cost_yen=out.cost_yen,
                duration_ms=out.duration_ms,
            )
            text = out.result.get("text", "") if out.success else ""
            return text, step

        # テキスト直渡し or ファイルなし
        text = payload.get("text", "")
        step = PipelineStepResult(name="ocr", success=True, skipped=True)
        return text, step

    async def _step_extract(
        self, company_id: str, text: str, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], PipelineStepResult]:
        """Step 2: 構造化抽出。extract_schemaが空ならスキップ。"""
        from workers.micro.extractor import run_structured_extractor

        if not self.extract_schema or not text:
            step = PipelineStepResult(name="extract", success=True, skipped=True)
            # スキップ時はpayload自体をパススルー（textキーは除く）
            passthrough = {k: v for k, v in payload.items() if k != "text" and k != "file_path"}
            return passthrough, step

        out: MicroAgentOutput = await run_structured_extractor(MicroAgentInput(
            company_id=company_id,
            agent_name="structured_extractor",
            payload={
                "text": text,
                "schema": self.extract_schema,
                "domain": self.pipeline_name,
            },
        ))
        step = PipelineStepResult(
            name="extract",
            success=out.success,
            confidence=out.confidence,
            cost_yen=out.cost_yen,
            duration_ms=out.duration_ms,
        )
        extracted = out.result.get("extracted", {}) if out.success else {}
        return extracted, step

    async def _step_enrich(
        self, company_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], PipelineStepResult]:
        """Step 3: 補完（ルールマッチング・DB照合）。デフォルトはスキップ。

        業種固有パイプラインでオーバーライドして、知識アイテムDB照合や
        マスタデータ付与を実装する。

        Returns:
            (enriched_data, step_result)
        """
        step = PipelineStepResult(name="enrich", success=True, skipped=True)
        return data, step

    async def _step_calculate(
        self, company_id: str, data: dict[str, Any]
    ) -> tuple[dict[str, Any], PipelineStepResult]:
        """Step 4: 計算。デフォルトはスキップ。

        業種固有パイプラインでオーバーライドして、金額計算・集計・
        レート適用などを実装する。

        Returns:
            (calculated_data, step_result)
        """
        step = PipelineStepResult(name="calculate", success=True, skipped=True)
        return data, step

    async def _step_validate(
        self, company_id: str, data: dict[str, Any]
    ) -> PipelineStepResult:
        """Step 5: バリデーション。validation_rulesが空ならスキップ。"""
        from workers.micro.validator import run_output_validator

        if not self.validation_rules:
            return PipelineStepResult(name="validate", success=True, skipped=True)

        out: MicroAgentOutput = await run_output_validator(MicroAgentInput(
            company_id=company_id,
            agent_name="output_validator",
            payload={
                "document": data,
                **self.validation_rules,
            },
        ))
        return PipelineStepResult(
            name="validate",
            success=out.success,
            confidence=out.confidence,
            cost_yen=out.cost_yen,
            duration_ms=out.duration_ms,
            data=out.result,
        )

    async def _step_anomaly_check(
        self, company_id: str, data: dict[str, Any], result: dict[str, Any]
    ) -> PipelineStepResult:
        """Step 6: 異常検知。anomaly_configがNoneならスキップ。"""
        from workers.micro.anomaly_detector import run_anomaly_detector

        if self.anomaly_config is None:
            return PipelineStepResult(name="anomaly_check", success=True, skipped=True)

        items = self._build_anomaly_items(data)
        if not items:
            return PipelineStepResult(name="anomaly_check", success=True, skipped=True)

        out: MicroAgentOutput = await run_anomaly_detector(MicroAgentInput(
            company_id=company_id,
            agent_name="anomaly_detector",
            payload={
                "items": items,
                "detect_modes": self.anomaly_config.get("detect_modes", ["digit_error"]),
                "rules": self.anomaly_config.get("rules", []),
            },
        ))

        if out.success and out.result.get("anomaly_count", 0) > 0:
            result["anomaly_warnings"] = out.result["anomalies"]

        return PipelineStepResult(
            name="anomaly_check",
            success=out.success,
            confidence=out.confidence,
            cost_yen=out.cost_yen,
            duration_ms=out.duration_ms,
            data=out.result,
        )

    async def _step_generate(
        self, company_id: str, data: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], PipelineStepResult]:
        """Step 7: ドキュメント生成。generate_templateがNoneならスキップ。"""
        from workers.micro.generator import run_document_generator

        if self.generate_template is None:
            return None, PipelineStepResult(name="generate", success=True, skipped=True)

        out: MicroAgentOutput = await run_document_generator(MicroAgentInput(
            company_id=company_id,
            agent_name="document_generator",
            payload={
                "template_name": self.generate_template,
                "data": data,
                "format": "markdown",
            },
        ))
        step = PipelineStepResult(
            name="generate",
            success=out.success,
            confidence=out.confidence,
            cost_yen=out.cost_yen,
            duration_ms=out.duration_ms,
        )
        generated = out.result if out.success else None
        return generated, step

    # -------------------------------------------------------------------
    # ユーティリティ
    # -------------------------------------------------------------------

    def _build_anomaly_items(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """anomaly_configのfieldsに該当する数値をitemsリストに変換する。"""
        if self.anomaly_config is None:
            return []

        fields: list[str] = self.anomaly_config.get("fields", [])
        ranges: dict[str, list] = self.anomaly_config.get("ranges", {})
        items: list[dict[str, Any]] = []

        for field in fields:
            value = data.get(field)
            if value is None or not isinstance(value, (int, float)):
                continue
            item: dict[str, Any] = {"name": field, "value": value}
            if field in ranges:
                item["expected_range"] = ranges[field]
            items.append(item)

        return items

    @staticmethod
    def _accumulate_totals(
        result: dict[str, Any], step_results: list[PipelineStepResult]
    ) -> None:
        """step_resultsからtotal_cost_yen / total_duration_msを集計してresultに書き込む。"""
        result["total_cost_yen"] = round(sum(s.cost_yen for s in step_results), 4)
        result["total_duration_ms"] = sum(s.duration_ms for s in step_results)
