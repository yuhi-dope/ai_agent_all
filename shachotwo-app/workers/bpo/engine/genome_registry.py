"""GenomeRegistry — brain/genome/data/*.json からパイプラインレジストリを動的生成する。

JSON構造は2種類存在する:
- brain/genome/data/*.json: departments → items 形式（建設/製造/卸売/共通等）
- brain/genome/data/bpo/*.json: pipeline_config 形式（業界別パイプライン設定）

どちらの形式でも柔軟にパースし、task_router.py の PIPELINE_REGISTRY と
マージ可能な形式に統一する。静的レジストリが常に優先される（後方互換保証）。
"""

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# BPO pipeline_config のキー → task_router キーへのデフォルトマッピング
# bpo/{industry}.json の pipeline_config 内のキー名から推測する
_KNOWN_PIPELINE_MODULE_MAP: dict[str, str] = {
    # clinic
    "medical_receipt": "workers.bpo.clinic.pipelines.medical_receipt_pipeline.run_medical_receipt_pipeline",
    # nursing
    "care_billing": "workers.bpo.nursing.pipelines.care_billing_pipeline.run_care_billing_pipeline",
    # realestate
    "rent_collection": "workers.bpo.realestate.pipelines.rent_collection_pipeline.run_rent_collection_pipeline",
    # logistics
    "dispatch": "workers.bpo.logistics.pipelines.dispatch_pipeline.run_dispatch_pipeline",
    # staffing
    "dispatch_contract": "workers.bpo.staffing.pipelines.dispatch_contract_pipeline.run_dispatch_contract_pipeline",
    # construction (departments形式から推測)
    "estimation": "workers.bpo.construction.pipelines.estimation_pipeline.run_estimation_pipeline",
    "billing": "workers.bpo.construction.pipelines.billing_pipeline.run_billing_pipeline",
    "safety_docs": "workers.bpo.construction.pipelines.safety_docs_pipeline.run_safety_docs_pipeline",
    "cost_report": "workers.bpo.construction.pipelines.cost_report_pipeline.run_cost_report_pipeline",
    "photo_organize": "workers.bpo.construction.pipelines.photo_organize_pipeline.run_photo_organize_pipeline",
    "subcontractor": "workers.bpo.construction.pipelines.subcontractor_pipeline.run_subcontractor_pipeline",
    "permit": "workers.bpo.construction.pipelines.permit_pipeline.run_permit_pipeline",
    "construction_plan": "workers.bpo.construction.pipelines.construction_plan_pipeline.run_construction_plan_pipeline",
    # manufacturing
    "quoting": "workers.bpo.manufacturing.pipelines.quoting_pipeline.run_quoting_pipeline",
    # common
    "expense": "workers.bpo.common.pipelines.expense_pipeline.run_expense_pipeline",
    "payroll": "workers.bpo.common.pipelines.payroll_pipeline.run_payroll_pipeline",
    "attendance": "workers.bpo.common.pipelines.attendance_pipeline.run_attendance_pipeline",
    "contract": "workers.bpo.common.pipelines.contract_pipeline.run_contract_pipeline",
    "admin_reminder": "workers.bpo.common.pipelines.admin_reminder_pipeline.run_admin_reminder_pipeline",
    "vendor": "workers.bpo.common.pipelines.vendor_pipeline.run_vendor_pipeline",
    # wholesale/realestate/etc（凍結業種用プレースホルダー）
    "fl_cost": "workers.bpo.restaurant.pipelines.fl_cost_pipeline.run_fl_cost_pipeline",
    "shift": "workers.bpo.restaurant.pipelines.shift_pipeline.run_shift_pipeline",
    "deadline_mgmt": "workers.bpo.professional.pipelines.deadline_mgmt_pipeline.run_deadline_mgmt_pipeline",
    "dispensing_billing": "workers.bpo.pharmacy.pipelines.dispensing_billing_pipeline.run_dispensing_billing_pipeline",
    "booking_recall": "workers.bpo.beauty.pipelines.recall_pipeline.run_recall_pipeline",
    "repair_quoting": "workers.bpo.auto_repair.pipelines.repair_quoting_pipeline.run_repair_quoting_pipeline",
    "revenue_mgmt": "workers.bpo.hotel.pipelines.revenue_mgmt_pipeline.run_revenue_mgmt_pipeline",
    "product_listing": "workers.bpo.ecommerce.pipelines.listing_pipeline.run_listing_pipeline",
    "building_permit": "workers.bpo.architecture.pipelines.building_permit_pipeline.run_building_permit_pipeline",
    "receipt_check": "workers.bpo.dental.pipelines.receipt_check_pipeline.run_receipt_check_pipeline",
}

# BPO処理レベルのデフォルト（pipeline キーから推測できない場合）
_DEFAULT_EXECUTION_LEVEL = 2  # DRAFT_CREATE

# BPOタスクのデフォルト影響度
_DEFAULT_ESTIMATED_IMPACT = 0.5


class GenomePipelineEntry:
    """ゲノムJSONから生成したパイプラインエントリ。"""

    def __init__(
        self,
        key: str,                          # "construction/estimation" 形式
        industry_id: str,                  # "construction"
        pipeline_name: str,                # "estimation"
        module_path: str,                  # "workers.bpo...."
        pipeline_config: dict[str, Any],   # JSON由来のパイプライン設定
        execution_level: int = _DEFAULT_EXECUTION_LEVEL,
        estimated_impact: float = _DEFAULT_ESTIMATED_IMPACT,
        source_file: str = "",             # 由来JSONファイルパス（デバッグ用）
        frozen: bool = False,              # 凍結業種フラグ
    ) -> None:
        self.key = key
        self.industry_id = industry_id
        self.pipeline_name = pipeline_name
        self.module_path = module_path
        self.pipeline_config = pipeline_config
        self.execution_level = execution_level
        self.estimated_impact = estimated_impact
        self.source_file = source_file
        self.frozen = frozen

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "industry_id": self.industry_id,
            "pipeline_name": self.pipeline_name,
            "module_path": self.module_path,
            "pipeline_config": self.pipeline_config,
            "execution_level": self.execution_level,
            "estimated_impact": self.estimated_impact,
            "source_file": self.source_file,
            "frozen": self.frozen,
        }


# 凍結業種（task_router.py のコメントアウトリストと対応）
_FROZEN_INDUSTRIES = {
    "dental", "restaurant", "hotel", "beauty", "auto_repair",
    "ecommerce", "staffing", "architecture", "pharmacy", "professional",
}


class GenomeRegistry:
    """ゲノムJSONからパイプラインレジストリを動的生成する。

    brain/genome/data/ 配下の全JSONを走査し、BPOパイプラインとして
    実行可能なエントリを収集する。JSON構造が形式ごとに異なるため、
    pipeline_config 形式（bpo/配下）と departments/items 形式（トップレベル）
    の両方を柔軟にパースする。

    使い方:
        registry = GenomeRegistry()
        await registry.load()

        # パイプライン情報取得
        entry = registry.get_pipeline("construction/estimation")

        # 静的レジストリとのマージ（静的が優先）
        merged = registry.merge_with_static(PIPELINE_REGISTRY)
    """

    def __init__(self, genome_dir: Optional[str] = None) -> None:
        self.genome_dir = genome_dir or os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "brain", "genome", "data"
        )
        # key → GenomePipelineEntry
        self._registry: dict[str, GenomePipelineEntry] = {}
        self._loaded = False

    # ──────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────

    async def load(self) -> None:
        """全ゲノムJSONを読み込んでレジストリを構築する。"""
        if self._loaded:
            return

        self._registry.clear()
        genome_dir = os.path.abspath(self.genome_dir)

        if not os.path.isdir(genome_dir):
            logger.warning(f"ゲノムディレクトリが存在しません: {genome_dir}")
            self._loaded = True
            return

        # トップレベルの *.json を処理
        for filename in os.listdir(genome_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(genome_dir, filename)
            if not os.path.isfile(filepath):
                continue
            try:
                data = self._read_json(filepath)
                self._parse_genome_json(data, filepath)
            except Exception as e:
                logger.warning(f"ゲノムJSON読み込みスキップ: {filepath} — {e}")

        # bpo/ サブディレクトリの *.json を処理
        bpo_dir = os.path.join(genome_dir, "bpo")
        if os.path.isdir(bpo_dir):
            for filename in os.listdir(bpo_dir):
                if not filename.endswith(".json"):
                    continue
                filepath = os.path.join(bpo_dir, filename)
                if not os.path.isfile(filepath):
                    continue
                try:
                    data = self._read_json(filepath)
                    self._parse_bpo_genome_json(data, filepath)
                except Exception as e:
                    logger.warning(f"BPOゲノムJSON読み込みスキップ: {filepath} — {e}")

        logger.info(
            f"GenomeRegistry.load() 完了: {len(self._registry)} パイプラインを登録"
        )
        self._loaded = True

    def get_pipeline(self, key: str) -> Optional[GenomePipelineEntry]:
        """パイプライン情報を取得する。未登録の場合は None を返す。"""
        return self._registry.get(key)

    def list_pipelines(self, industry: Optional[str] = None) -> list[str]:
        """パイプラインキー一覧を返す。industry を指定すると業界フィルタをかける。"""
        if industry is None:
            return sorted(self._registry.keys())
        return sorted(
            key for key, entry in self._registry.items()
            if entry.industry_id == industry
        )

    def list_entries(self, industry: Optional[str] = None) -> list[GenomePipelineEntry]:
        """GenomePipelineEntry 一覧を返す。industry を指定すると業界フィルタをかける。"""
        if industry is None:
            return list(self._registry.values())
        return [e for e in self._registry.values() if e.industry_id == industry]

    def get_triggers(self, trigger_type: Optional[str] = None) -> list[dict[str, Any]]:
        """パイプラインに紐付くトリガー情報の一覧を返す。

        現時点のゲノムJSONにはトリガーの明示的なセクションがないため、
        pipeline_config からスケジュール情報などを抽出して返す。
        trigger_type が指定された場合は "schedule" / "event" / "condition" でフィルタ。
        """
        triggers: list[dict[str, Any]] = []
        for entry in self._registry.values():
            cfg = entry.pipeline_config
            # スケジュールトリガーの抽出
            schedule = cfg.get("schedule") or cfg.get("cron") or cfg.get("trigger_schedule")
            if schedule:
                t = {
                    "pipeline": entry.key,
                    "trigger_type": "schedule",
                    "schedule": schedule,
                }
                if trigger_type is None or trigger_type == "schedule":
                    triggers.append(t)
            # イベントトリガーの抽出
            events = cfg.get("events") or cfg.get("trigger_events") or []
            for ev in events:
                t = {
                    "pipeline": entry.key,
                    "trigger_type": "event",
                    "event": ev,
                }
                if trigger_type is None or trigger_type == "event":
                    triggers.append(t)
            # 条件トリガーの抽出
            conditions = cfg.get("conditions") or cfg.get("trigger_conditions") or []
            for cond in conditions:
                t = {
                    "pipeline": entry.key,
                    "trigger_type": "condition",
                    "condition": cond,
                }
                if trigger_type is None or trigger_type == "condition":
                    triggers.append(t)
        return triggers

    def merge_with_static(self, static_registry: dict[str, str]) -> dict[str, str]:
        """既存の PIPELINE_REGISTRY とゲノム由来のレジストリをマージする。

        静的レジストリが優先される（後方互換保証）。
        ゲノムにしかないパイプラインは末尾に追加される。

        Returns:
            マージ済みの { pipeline_key: module_path } dict
        """
        merged: dict[str, str] = dict(static_registry)  # 静的をコピー（優先）

        for key, entry in self._registry.items():
            if key not in merged:
                # 静的に未登録 → ゲノムから追加
                merged[key] = entry.module_path
                logger.debug(f"ゲノムからパイプライン追加: {key} → {entry.module_path}")

        return merged

    def get_pipeline_config(self, key: str) -> dict[str, Any]:
        """パイプライン設定（genome由来パラメータ）を返す。未登録の場合は空dict。"""
        entry = self._registry.get(key)
        return entry.pipeline_config if entry else {}

    # ──────────────────────────────────────────────────────
    # Internal parsers
    # ──────────────────────────────────────────────────────

    def _read_json(self, filepath: str) -> dict[str, Any]:
        with open(filepath, encoding="utf-8") as f:
            return json.load(f)

    def _parse_genome_json(self, data: dict[str, Any], filepath: str) -> None:
        """トップレベルJSON（departments/items形式）をパースしてレジストリに追加する。

        このファイルには BPO パイプラインの直接定義はないが、業界IDと
        _KNOWN_PIPELINE_MODULE_MAP を用いてパイプラインエントリを生成する。
        """
        industry_id: str = data.get("id", "")
        if not industry_id:
            return

        frozen = industry_id in _FROZEN_INDUSTRIES

        # departments.items から bpo_automatable なアイテムを抽出
        departments: list[dict] = data.get("departments", [])
        found_pipelines: set[str] = set()

        for dept in departments:
            items: list[dict] = dept.get("items", [])
            for item in items:
                if not item.get("bpo_automatable", False):
                    continue
                # item タイトルからパイプライン名を推測
                title: str = item.get("title", "")
                pipeline_name = self._title_to_pipeline_name(title, industry_id)
                if not pipeline_name:
                    continue

                key = f"{industry_id}/{pipeline_name}"
                if key in found_pipelines:
                    continue
                found_pipelines.add(key)

                module_path = _KNOWN_PIPELINE_MODULE_MAP.get(pipeline_name, "")
                if not module_path:
                    # モジュールパスが不明な場合は命名規則から推測
                    module_path = (
                        f"workers.bpo.{industry_id}.pipelines"
                        f".{pipeline_name}_pipeline.run_{pipeline_name}_pipeline"
                    )

                entry = GenomePipelineEntry(
                    key=key,
                    industry_id=industry_id,
                    pipeline_name=pipeline_name,
                    module_path=module_path,
                    pipeline_config={},
                    source_file=filepath,
                    frozen=frozen,
                )
                self._registry[key] = entry

        # トップレベルに pipelines / bpo_tasks キーがある場合も処理
        for alt_key in ("pipelines", "bpo_tasks", "bpo_pipelines"):
            pipelines_section = data.get(alt_key)
            if isinstance(pipelines_section, dict):
                self._parse_pipelines_section(
                    industry_id, pipelines_section, filepath, frozen
                )
            elif isinstance(pipelines_section, list):
                for p in pipelines_section:
                    if isinstance(p, dict):
                        pname = p.get("name") or p.get("id") or ""
                        if pname:
                            self._register_pipeline_from_dict(
                                industry_id, pname, p, filepath, frozen
                            )

    def _parse_bpo_genome_json(self, data: dict[str, Any], filepath: str) -> None:
        """bpo/*.json（pipeline_config形式）をパースしてレジストリに追加する。

        pipeline_config のキーがパイプライン名になる。
        例: { "pipeline_config": { "medical_receipt": {...} } }
          → key = "clinic/medical_receipt"
        """
        industry_id: str = data.get("id", "")
        if not industry_id:
            return

        frozen = industry_id in _FROZEN_INDUSTRIES

        pipeline_config: dict[str, Any] = data.get("pipeline_config", {})
        if not pipeline_config:
            # pipeline_config がない場合は pipelines / bpo_tasks も探す
            for alt_key in ("pipelines", "bpo_tasks", "bpo_pipelines"):
                alt = data.get(alt_key)
                if isinstance(alt, dict):
                    pipeline_config = alt
                    break

        if not isinstance(pipeline_config, dict):
            logger.debug(f"pipeline_config が辞書形式ではありません: {filepath}")
            return

        self._parse_pipelines_section(industry_id, pipeline_config, filepath, frozen)

    def _parse_pipelines_section(
        self,
        industry_id: str,
        pipelines: dict[str, Any],
        filepath: str,
        frozen: bool,
    ) -> None:
        """{ pipeline_name: config_dict } 形式のセクションをレジストリに登録する。"""
        for pipeline_name, config in pipelines.items():
            config_dict: dict[str, Any] = config if isinstance(config, dict) else {}
            self._register_pipeline_from_dict(
                industry_id, pipeline_name, config_dict, filepath, frozen
            )

    def _register_pipeline_from_dict(
        self,
        industry_id: str,
        pipeline_name: str,
        config: dict[str, Any],
        filepath: str,
        frozen: bool,
    ) -> None:
        """パイプライン1件をレジストリに追加する（重複時は既存を保持）。"""
        key = f"{industry_id}/{pipeline_name}"

        if key in self._registry:
            return  # 既登録は上書きしない

        module_path = (
            config.get("module")
            or _KNOWN_PIPELINE_MODULE_MAP.get(pipeline_name)
            or (
                f"workers.bpo.{industry_id}.pipelines"
                f".{pipeline_name}_pipeline.run_{pipeline_name}_pipeline"
            )
        )

        execution_level = int(
            config.get("execution_level", _DEFAULT_EXECUTION_LEVEL)
        )
        estimated_impact = float(
            config.get("estimated_impact", _DEFAULT_ESTIMATED_IMPACT)
        )

        entry = GenomePipelineEntry(
            key=key,
            industry_id=industry_id,
            pipeline_name=pipeline_name,
            module_path=module_path,
            pipeline_config=config,
            execution_level=execution_level,
            estimated_impact=estimated_impact,
            source_file=filepath,
            frozen=frozen,
        )
        self._registry[key] = entry
        logger.debug(f"ゲノムパイプライン登録: {key} (frozen={frozen})")

    def _title_to_pipeline_name(self, title: str, industry_id: str) -> str:
        """knowledge_itemのタイトルからパイプライン名を推測する。

        タイトルとキーワードのマッピングで候補を返す。
        対応するものがなければ空文字を返す（登録しない）。
        """
        title_lower = title.lower()

        # 業界固有のマッピング
        industry_mappings: dict[str, dict[str, str]] = {
            "construction": {
                "見積": "estimation",
                "請求": "billing",
                "安全": "safety_docs",
                "原価": "cost_report",
                "写真": "photo_organize",
                "下請": "subcontractor",
                "許可": "permit",
                "施工計画": "construction_plan",
            },
            "manufacturing": {
                "見積": "quoting",
                "発注": "quoting",
            },
            "common": {
                "経費": "expense",
                "給与": "payroll",
                "勤怠": "attendance",
                "契約": "contract",
                "リマインド": "admin_reminder",
                "取引先": "vendor",
            },
        }

        mappings = industry_mappings.get(industry_id, {})
        for keyword, pipeline_name in mappings.items():
            if keyword in title:
                return pipeline_name

        # タイトルにパイプライン名キーワードが直接含まれる場合
        for known_name in _KNOWN_PIPELINE_MODULE_MAP:
            if known_name.replace("_", "") in title_lower.replace(" ", ""):
                return known_name

        return ""


# ──────────────────────────────────────────────────────────────────────────────
# シングルトン（task_router.py から参照する）
# ──────────────────────────────────────────────────────────────────────────────

_genome_registry_instance: Optional[GenomeRegistry] = None


def get_genome_registry(genome_dir: Optional[str] = None) -> GenomeRegistry:
    """GenomeRegistry のシングルトンを返す。初回のみインスタンスを生成する。"""
    global _genome_registry_instance
    if _genome_registry_instance is None:
        _genome_registry_instance = GenomeRegistry(genome_dir=genome_dir)
    return _genome_registry_instance


async def get_loaded_genome_registry(genome_dir: Optional[str] = None) -> GenomeRegistry:
    """load() 済みの GenomeRegistry を返す。未ロードなら load() を呼ぶ。"""
    reg = get_genome_registry(genome_dir)
    if not reg._loaded:
        await reg.load()
    return reg
