"""GenomeRegistry のユニットテスト。

外部依存（DB・LLM）はなし。
実際の brain/genome/data/*.json を読み込んで動作確認する統合的テストと、
モックJSONを使った単体テストを組み合わせる。
"""

import json
import os
import tempfile
import pytest

from workers.bpo.engine.genome_registry import (
    GenomeRegistry,
    GenomePipelineEntry,
    get_genome_registry,
    get_loaded_genome_registry,
    _genome_registry_instance,
)

# ─────────────────────────────────────────
# フィクスチャ
# ─────────────────────────────────────────

REAL_GENOME_DIR = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "..", "brain", "genome", "data",
)


def make_temp_genome_dir(top_jsons: dict, bpo_jsons: dict | None = None) -> str:
    """一時ゲノムディレクトリを作成してパスを返す。テスト終了後は呼び出し元で cleanup。"""
    tmp = tempfile.mkdtemp()
    for filename, data in top_jsons.items():
        with open(os.path.join(tmp, filename), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    if bpo_jsons:
        bpo_dir = os.path.join(tmp, "bpo")
        os.makedirs(bpo_dir, exist_ok=True)
        for filename, data in bpo_jsons.items():
            with open(os.path.join(bpo_dir, filename), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
    return tmp


# ─────────────────────────────────────────
# 1. JSONファイル読み込みテスト（実ファイル）
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_real_genome_files():
    """実際の brain/genome/data/ を読み込んでクラッシュしないことを確認する。"""
    if not os.path.isdir(REAL_GENOME_DIR):
        pytest.skip("brain/genome/data/ が存在しません")

    registry = GenomeRegistry(genome_dir=REAL_GENOME_DIR)
    await registry.load()

    # ロード済みフラグが立つ
    assert registry._loaded is True
    # 少なくとも 1 件以上のパイプラインが登録される（bpo/ 配下のファイルから）
    assert len(registry._registry) >= 1


@pytest.mark.asyncio
async def test_load_idempotent():
    """load() を 2 回呼んでも二重登録されないことを確認する。"""
    if not os.path.isdir(REAL_GENOME_DIR):
        pytest.skip("brain/genome/data/ が存在しません")

    registry = GenomeRegistry(genome_dir=REAL_GENOME_DIR)
    await registry.load()
    count_first = len(registry._registry)

    await registry.load()  # 2回目
    count_second = len(registry._registry)

    assert count_first == count_second


# ─────────────────────────────────────────
# 2. パイプライン一覧取得テスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pipelines_bpo_format():
    """bpo/pipeline_config 形式のJSONからパイプライン一覧が取得できる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "clinic.json": {
                "id": "clinic",
                "name": "医療クリニック",
                "pipeline_config": {
                    "medical_receipt": {"execution_level": 2, "estimated_impact": 0.4},
                    "appointment": {"execution_level": 1},
                },
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        pipelines = registry.list_pipelines()
        assert "clinic/medical_receipt" in pipelines
        assert "clinic/appointment" in pipelines
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_list_pipelines_departments_format():
    """departments/items 形式の JSON から bpo_automatable なアイテムが取得できる。"""
    tmp = make_temp_genome_dir(
        top_jsons={
            "construction.json": {
                "id": "construction",
                "name": "建設業",
                "departments": [
                    {
                        "name": "経理",
                        "items": [
                            {
                                "title": "請求書発行フロー",
                                "bpo_automatable": True,
                                "category": "workflow",
                            },
                            {
                                "title": "作業手順書",
                                "bpo_automatable": False,  # 対象外
                            },
                        ],
                    }
                ],
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        # bpo_automatable=True のもののみ登録される
        # タイトル「請求書発行フロー」→ "billing" にマッピングされる
        pipelines = registry.list_pipelines()
        assert any("construction" in k for k in pipelines)
    finally:
        import shutil
        shutil.rmtree(tmp)


# ─────────────────────────────────────────
# 3. 業種フィルタテスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pipelines_industry_filter():
    """industry フィルタで業種を絞り込めること。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "clinic.json": {
                "id": "clinic",
                "name": "医療クリニック",
                "pipeline_config": {"medical_receipt": {}},
            },
            "logistics.json": {
                "id": "logistics",
                "name": "物流",
                "pipeline_config": {"dispatch": {}},
            },
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        clinic_pipelines = registry.list_pipelines(industry="clinic")
        logistics_pipelines = registry.list_pipelines(industry="logistics")
        all_pipelines = registry.list_pipelines()

        assert all(k.startswith("clinic/") for k in clinic_pipelines)
        assert all(k.startswith("logistics/") for k in logistics_pipelines)
        assert len(all_pipelines) == len(clinic_pipelines) + len(logistics_pipelines)
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_list_pipelines_unknown_industry_returns_empty():
    """存在しない業種でフィルタすると空リストが返る。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "clinic.json": {
                "id": "clinic",
                "pipeline_config": {"medical_receipt": {}},
            },
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        result = registry.list_pipelines(industry="nonexistent_industry")
        assert result == []
    finally:
        import shutil
        shutil.rmtree(tmp)


# ─────────────────────────────────────────
# 4. 静的レジストリとのマージテスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_merge_static_priority():
    """静的レジストリがゲノム由来のエントリより優先されること。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "clinic.json": {
                "id": "clinic",
                "pipeline_config": {
                    "medical_receipt": {
                        "module": "workers.bpo.clinic.genome_version.run_pipeline"
                    }
                },
            }
        },
    )
    static_registry = {
        "clinic/medical_receipt": "workers.bpo.clinic.pipelines.medical_receipt_pipeline.run_medical_receipt_pipeline",
    }

    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        merged = registry.merge_with_static(static_registry)

        # 静的定義が優先されること
        assert merged["clinic/medical_receipt"] == static_registry["clinic/medical_receipt"]
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_merge_genome_only_pipelines_added():
    """静的レジストリに存在しないゲノム由来パイプラインがマージ後に追加されること。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "logistics.json": {
                "id": "logistics",
                "pipeline_config": {
                    "dispatch": {},
                    "new_genome_only_pipeline": {
                        "module": "workers.bpo.logistics.pipelines.new_pipeline.run_new_pipeline"
                    },
                },
            }
        },
    )
    static_registry = {
        "logistics/dispatch": "workers.bpo.logistics.pipelines.dispatch_pipeline.run_dispatch_pipeline",
    }

    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        merged = registry.merge_with_static(static_registry)

        # 静的定義は保持される
        assert merged["logistics/dispatch"] == static_registry["logistics/dispatch"]
        # ゲノムにしかないパイプラインが追加される
        assert "logistics/new_genome_only_pipeline" in merged
        assert merged["logistics/new_genome_only_pipeline"] == (
            "workers.bpo.logistics.pipelines.new_pipeline.run_new_pipeline"
        )
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_merge_empty_genome_returns_static():
    """ゲノムJSONが空の場合、静的レジストリがそのまま返ること。"""
    tmp = make_temp_genome_dir(top_jsons={}, bpo_jsons={})

    static_registry = {
        "construction/estimation": "workers.bpo.construction.pipelines.estimation_pipeline.run_estimation_pipeline",
    }

    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        merged = registry.merge_with_static(static_registry)
        assert merged == static_registry
    finally:
        import shutil
        shutil.rmtree(tmp)


# ─────────────────────────────────────────
# 5. JSONが空・不正な場合のテスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_empty_json_skipped():
    """pipeline_config が空のJSONはクラッシュせずスキップされる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "empty.json": {"id": "empty_industry", "name": "空業種"},
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        # パイプライン登録なし（クラッシュもしない）
        assert len(registry._registry) == 0
        assert registry._loaded is True
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_load_missing_id_skipped():
    """id フィールドがないJSONはスキップされる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "no_id.json": {
                "name": "IDなし",
                "pipeline_config": {"some_pipeline": {}},
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        assert len(registry._registry) == 0
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_load_nonexistent_dir():
    """存在しないディレクトリを渡してもクラッシュしない。"""
    registry = GenomeRegistry(genome_dir="/nonexistent/path/to/genome")
    await registry.load()

    assert registry._loaded is True
    assert len(registry._registry) == 0


# ─────────────────────────────────────────
# 6. get_pipeline / get_pipeline_config テスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_pipeline_returns_entry():
    """登録済みパイプラインのエントリが取得できる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "realestate.json": {
                "id": "realestate",
                "pipeline_config": {
                    "rent_collection": {
                        "amount_tolerance": 1000,
                        "execution_level": 3,
                        "estimated_impact": 0.7,
                    }
                },
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        entry = registry.get_pipeline("realestate/rent_collection")
        assert entry is not None
        assert isinstance(entry, GenomePipelineEntry)
        assert entry.industry_id == "realestate"
        assert entry.pipeline_name == "rent_collection"
        assert entry.execution_level == 3
        assert entry.estimated_impact == 0.7
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_get_pipeline_unknown_returns_none():
    """未登録キーは None を返す。"""
    tmp = make_temp_genome_dir(top_jsons={}, bpo_jsons={})
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        assert registry.get_pipeline("unknown/pipeline") is None
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_get_pipeline_config_returns_dict():
    """get_pipeline_config() が設定辞書を返す。未登録は空dict。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "nursing.json": {
                "id": "nursing",
                "pipeline_config": {
                    "care_billing": {"addition_rate": 0.083}
                },
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        cfg = registry.get_pipeline_config("nursing/care_billing")
        assert cfg["addition_rate"] == 0.083

        empty_cfg = registry.get_pipeline_config("nursing/nonexistent")
        assert empty_cfg == {}
    finally:
        import shutil
        shutil.rmtree(tmp)


# ─────────────────────────────────────────
# 7. 凍結業種フラグテスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_frozen_industry_flag():
    """凍結業種（dental等）のエントリは frozen=True になる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "dental.json": {
                "id": "dental",
                "pipeline_config": {"receipt_check": {}},
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        entry = registry.get_pipeline("dental/receipt_check")
        assert entry is not None
        assert entry.frozen is True
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_active_industry_not_frozen():
    """現役業種（construction等）のエントリは frozen=False になる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "construction.json": {
                "id": "construction",
                "pipeline_config": {"estimation": {}},
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        entry = registry.get_pipeline("construction/estimation")
        assert entry is not None
        assert entry.frozen is False
    finally:
        import shutil
        shutil.rmtree(tmp)


# ─────────────────────────────────────────
# 8. get_triggers テスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_triggers_schedule():
    """pipeline_config に schedule があるパイプラインのトリガーが取得できる。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "common.json": {
                "id": "common",
                "pipeline_config": {
                    "payroll": {
                        "schedule": "0 9 25 * *",  # 毎月25日9時
                    },
                    "attendance": {
                        "events": ["clock_in", "clock_out"],
                    },
                },
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        schedule_triggers = registry.get_triggers(trigger_type="schedule")
        event_triggers = registry.get_triggers(trigger_type="event")
        all_triggers = registry.get_triggers()

        assert any(t["pipeline"] == "common/payroll" for t in schedule_triggers)
        assert any(t["pipeline"] == "common/attendance" for t in event_triggers)
        assert len(all_triggers) >= len(schedule_triggers) + len(event_triggers)
    finally:
        import shutil
        shutil.rmtree(tmp)


# ─────────────────────────────────────────
# 9. シングルトン関数テスト
# ─────────────────────────────────────────

def test_get_genome_registry_returns_same_instance():
    """get_genome_registry() は同一インスタンスを返す。"""
    import workers.bpo.engine.genome_registry as mod
    # テスト間干渉を避けるためシングルトンをリセット
    mod._genome_registry_instance = None

    r1 = get_genome_registry()
    r2 = get_genome_registry()
    assert r1 is r2

    # テスト後にリセット
    mod._genome_registry_instance = None


@pytest.mark.asyncio
async def test_get_loaded_genome_registry():
    """get_loaded_genome_registry() は load() 済みの registry を返す。"""
    import workers.bpo.engine.genome_registry as mod
    mod._genome_registry_instance = None

    tmp = make_temp_genome_dir(top_jsons={}, bpo_jsons={})
    try:
        registry = await get_loaded_genome_registry(genome_dir=tmp)
        assert registry._loaded is True
    finally:
        import shutil
        shutil.rmtree(tmp)
        mod._genome_registry_instance = None


# ─────────────────────────────────────────
# 10. module_path 推測テスト
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_known_pipeline_gets_correct_module_path():
    """_KNOWN_PIPELINE_MODULE_MAP に登録済みのパイプラインは正しいモジュールパスを持つ。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "clinic.json": {
                "id": "clinic",
                "pipeline_config": {"medical_receipt": {}},
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        entry = registry.get_pipeline("clinic/medical_receipt")
        assert entry is not None
        assert "medical_receipt_pipeline" in entry.module_path
    finally:
        import shutil
        shutil.rmtree(tmp)


@pytest.mark.asyncio
async def test_unknown_pipeline_gets_inferred_module_path():
    """_KNOWN_PIPELINE_MODULE_MAP に未登録のパイプラインは命名規則から推測されたパスを持つ。"""
    tmp = make_temp_genome_dir(
        top_jsons={},
        bpo_jsons={
            "wholesale.json": {
                "id": "wholesale",
                "pipeline_config": {"custom_new_pipeline": {}},
            }
        },
    )
    try:
        registry = GenomeRegistry(genome_dir=tmp)
        await registry.load()

        entry = registry.get_pipeline("wholesale/custom_new_pipeline")
        assert entry is not None
        # 命名規則: workers.bpo.{industry}.pipelines.{name}_pipeline.run_{name}_pipeline
        assert entry.module_path == (
            "workers.bpo.wholesale.pipelines"
            ".custom_new_pipeline_pipeline.run_custom_new_pipeline_pipeline"
        )
    finally:
        import shutil
        shutil.rmtree(tmp)
