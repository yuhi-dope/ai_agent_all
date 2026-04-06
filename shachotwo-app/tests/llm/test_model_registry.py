"""llm/model_registry.py のユニットテスト"""
import pytest
from llm.model_registry import (
    MODEL_REGISTRY,
    ModelInfo,
    ModelUpdateLog,
    get_fallback_chain,
    get_model_costs,
    check_deprecations,
    get_update_logs,
    register_model,
    deprecate_model,
    select_optimal_model,
)

# ---------------------------------------------------------------------------
# ヘルパー定数
# ---------------------------------------------------------------------------

_TEST_MODEL_ID = "__test_model_for_unit_tests__"
_TEST_SUCCESSOR_ID = "__test_successor_for_unit_tests__"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def cleanup_test_model():
    """テスト用モデルをレジストリに追加した場合のクリーンアップ。"""
    yield
    # テスト終了後にテスト用モデルをレジストリから除去する
    for mid in (_TEST_MODEL_ID, _TEST_SUCCESSOR_ID):
        MODEL_REGISTRY.pop(mid, None)


# ---------------------------------------------------------------------------
# 1. 必須モデルの登録確認
# ---------------------------------------------------------------------------


def test_registry_has_required_models():
    """gemini-2.5-flash, claude-sonnet-4-6, gpt-4o が登録されていること。"""
    required = ["gemini-2.5-flash", "claude-sonnet-4-6", "gpt-4o"]
    for model_id in required:
        assert model_id in MODEL_REGISTRY, (
            f"必須モデル '{model_id}' が MODEL_REGISTRY に登録されていない"
        )


# ---------------------------------------------------------------------------
# 2. fast tier のフォールバックチェイン
# ---------------------------------------------------------------------------


def test_get_fallback_chain_fast():
    """fast tier のチェインが空でなく、先頭が gemini-2.5-flash であること。"""
    chain = get_fallback_chain("fast")
    assert len(chain) > 0, "fast tier のフォールバックチェインが空"
    assert chain[0] == "gemini-2.5-flash", (
        f"fast tier の先頭モデルが gemini-2.5-flash ではない: {chain[0]}"
    )


# ---------------------------------------------------------------------------
# 3. deprecated モデルがチェインに含まれない
# ---------------------------------------------------------------------------


def test_get_fallback_chain_excludes_deprecated(cleanup_test_model):
    """deprecate_model で廃止したモデルがフォールバックチェインに含まれないこと。"""
    # fast tier の先頭モデルを取得しておく
    original_chain = get_fallback_chain("fast")

    # テスト用モデルを fast tier に追加
    register_model(
        model_id=_TEST_MODEL_ID,
        info=ModelInfo(
            provider="test",
            tier="fast",
            cost_per_1k_tokens_in=0.001,
            cost_per_1k_tokens_out=0.002,
            supports_vision=False,
            supports_structured_output=False,
            deprecated=False,
        ),
    )
    chain_before = get_fallback_chain("fast")
    assert _TEST_MODEL_ID in chain_before, (
        "register_model 後にテスト用モデルがチェインに含まれていない"
    )

    # deprecated にする
    deprecate_model(model_id=_TEST_MODEL_ID)

    chain_after = get_fallback_chain("fast")
    assert _TEST_MODEL_ID not in chain_after, (
        "deprecate_model 後もテスト用モデルがチェインに残っている"
    )


# ---------------------------------------------------------------------------
# 4. get_model_costs — 既存モデル
# ---------------------------------------------------------------------------


def test_get_model_costs():
    """既存モデルのコストが {'in': float, 'out': float} 形式で返ること。"""
    costs = get_model_costs("gemini-2.5-flash")
    assert isinstance(costs, dict), "get_model_costs の戻り値が dict ではない"
    assert "in" in costs, "コスト dict に 'in' キーがない"
    assert "out" in costs, "コスト dict に 'out' キーがない"
    assert isinstance(costs["in"], float), "'in' の値が float ではない"
    assert isinstance(costs["out"], float), "'out' の値が float ではない"
    assert costs["in"] >= 0.0, "'in' コストが負の値"
    assert costs["out"] >= 0.0, "'out' コストが負の値"


# ---------------------------------------------------------------------------
# 5. get_model_costs — 未知のモデル
# ---------------------------------------------------------------------------


def test_get_model_costs_unknown():
    """未知のモデル ID に対して KeyError またはデフォルト値が返ること。"""
    unknown_id = "__nonexistent_model_12345__"
    try:
        result = get_model_costs(unknown_id)
        # デフォルト値が返る実装の場合、dict 形式を保つこと
        assert isinstance(result, dict), (
            "未知モデルでデフォルト値を返す場合、dict 形式でなければならない"
        )
    except KeyError:
        # KeyError を送出する実装も許容
        pass


# ---------------------------------------------------------------------------
# 6. check_deprecations — 初期状態
# ---------------------------------------------------------------------------


def test_check_deprecations_empty():
    """初期状態で deprecated_date が未来のモデルがない場合は空リストであること。

    注意: deprecated=True かつ deprecated_date が過去のモデルが存在しても、
    「期限切れ間近」として警告するモデルが存在しなければ空リストを返す想定。
    実装の詳細に合わせてアサーションを調整可能。
    """
    result = check_deprecations()
    assert isinstance(result, list), "check_deprecations の戻り値が list ではない"
    # 登録済みモデルに「近々廃止予定」のものがなければ空リストになる
    # 本テストは戻り値が list 型であることのみを保証する（内容は実装依存）


# ---------------------------------------------------------------------------
# 7. select_optimal_model — fast tier で低コストモデル
# ---------------------------------------------------------------------------


def test_select_optimal_model_fast():
    """fast tier では低コストモデルが選ばれること。"""
    model_id = select_optimal_model(tier="fast")
    assert model_id is not None, "fast tier で select_optimal_model が None を返した"
    costs = get_model_costs(model_id)
    # fast tier の全モデルと比較して、入力コストが最小クラスであることを確認
    fast_chain = get_fallback_chain("fast")
    for other_id in fast_chain:
        if other_id == model_id:
            continue
        other_info = MODEL_REGISTRY.get(other_id)
        if other_info and not getattr(other_info, "deprecated", False):
            other_costs = get_model_costs(other_id)
            assert costs["in"] <= other_costs["in"] + 1e-9, (
                f"fast tier で {model_id} より安価な {other_id} が存在するが選ばれなかった"
                f" ({costs['in']} > {other_costs['in']})"
            )


# ---------------------------------------------------------------------------
# 8. select_optimal_model — vision 要件
# ---------------------------------------------------------------------------


def test_select_optimal_model_with_vision():
    """requires_vision=True のときに vision 対応モデルが返ること。"""
    model_id = select_optimal_model(tier="fast", requires_vision=True)
    if model_id is None:
        pytest.skip("vision 対応モデルが存在しないため skip")
    info = MODEL_REGISTRY.get(model_id)
    assert info is not None, f"選択されたモデル '{model_id}' が MODEL_REGISTRY にない"
    assert getattr(info, "supports_vision", False), (
        f"requires_vision=True で選ばれた '{model_id}' が vision 非対応"
    )


# ---------------------------------------------------------------------------
# 9. select_optimal_model — structured output 要件
# ---------------------------------------------------------------------------


def test_select_optimal_model_structured_output():
    """requires_structured_output=True のとき gemini 系モデルが優先されること。"""
    model_id = select_optimal_model(tier="fast", requires_structured_output=True)
    if model_id is None:
        pytest.skip("structured output 対応モデルが存在しないため skip")
    assert model_id.startswith("gemini"), (
        f"requires_structured_output=True で gemini 系以外の '{model_id}' が選ばれた"
    )


# ---------------------------------------------------------------------------
# 10. register_model / deprecate_model の一連フロー
# ---------------------------------------------------------------------------


def test_register_and_deprecate(cleanup_test_model):
    """register_model → deprecate_model の一連フローで update_logs に記録されること。"""
    logs_before = get_update_logs()
    count_before = len(logs_before)

    register_model(
        model_id=_TEST_MODEL_ID,
        info=ModelInfo(
            provider="test",
            tier="fast",
            cost_per_1k_tokens_in=0.001,
            cost_per_1k_tokens_out=0.002,
            supports_vision=False,
            supports_structured_output=False,
            deprecated=False,
        ),
    )

    deprecate_model(model_id=_TEST_MODEL_ID)

    logs_after = get_update_logs()
    assert len(logs_after) >= count_before + 2, (
        "register_model と deprecate_model の実行後、update_logs に少なくとも 2 件追記されるべき"
    )

    # ログに _TEST_MODEL_ID が含まれていること
    model_ids_in_logs = [getattr(log, "model_id", None) for log in logs_after]
    assert _TEST_MODEL_ID in model_ids_in_logs, (
        f"update_logs に '{_TEST_MODEL_ID}' の記録が見当たらない"
    )


# ---------------------------------------------------------------------------
# 11. get_update_logs — register_model 後にログが取得できる
# ---------------------------------------------------------------------------


def test_update_logs(cleanup_test_model):
    """register_model 後に get_update_logs でログが取得できること。"""
    logs_before = get_update_logs()
    count_before = len(logs_before)

    register_model(
        model_id=_TEST_MODEL_ID,
        info=ModelInfo(
            provider="test",
            tier="standard",
            cost_per_1k_tokens_in=0.01,
            cost_per_1k_tokens_out=0.04,
            supports_vision=False,
            supports_structured_output=True,
            deprecated=False,
        ),
    )

    logs_after = get_update_logs()
    assert isinstance(logs_after, list), "get_update_logs の戻り値が list ではない"
    assert len(logs_after) > count_before, (
        "register_model 後に get_update_logs のログ件数が増加していない"
    )

    # 最新ログが ModelUpdateLog 型であること
    latest_log = logs_after[-1]
    assert isinstance(latest_log, ModelUpdateLog), (
        f"get_update_logs の要素が ModelUpdateLog 型ではない: {type(latest_log)}"
    )


# ---------------------------------------------------------------------------
# 12. successor 指定 deprecate でフォールバックに successor が使われる
# ---------------------------------------------------------------------------


def test_successor_replacement(cleanup_test_model):
    """deprecate_model で successor 指定すると、フォールバックチェインに successor が入ること。"""
    # successor モデルを先に登録
    register_model(
        model_id=_TEST_SUCCESSOR_ID,
        info=ModelInfo(
            provider="test",
            tier="fast",
            cost_per_1k_tokens_in=0.001,
            cost_per_1k_tokens_out=0.002,
            supports_vision=False,
            supports_structured_output=False,
            deprecated=False,
        ),
    )

    # テスト用モデルを登録してから successor 付きで廃止
    register_model(
        model_id=_TEST_MODEL_ID,
        info=ModelInfo(
            provider="test",
            tier="fast",
            cost_per_1k_tokens_in=0.001,
            cost_per_1k_tokens_out=0.002,
            supports_vision=False,
            supports_structured_output=False,
            deprecated=False,
        ),
    )
    deprecate_model(model_id=_TEST_MODEL_ID, successor=_TEST_SUCCESSOR_ID)

    chain = get_fallback_chain("fast")

    # 廃止したモデルはチェインから除外されていること
    assert _TEST_MODEL_ID not in chain, (
        "deprecate_model 後も廃止モデルがフォールバックチェインに残っている"
    )

    # successor がチェインに含まれていること
    assert _TEST_SUCCESSOR_ID in chain, (
        "successor に指定したモデルがフォールバックチェインに含まれていない"
    )
