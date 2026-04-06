"""llm/model_registry.py — LLMモデルレジストリ。

モデルバージョン管理・コスト情報・廃止検知・後継モデル自動切替を一元管理。
client.py の FALLBACK_CHAINS / MODEL_COSTS をこのレジストリに移行する。

使い方:
    from llm.model_registry import (
        get_fallback_chain,
        get_model_costs,
        check_deprecations,
        select_optimal_model,
        register_model,
        deprecate_model,
        get_update_logs,
        MODEL_REGISTRY,
    )
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelInfo: モデルの仕様・コスト・廃止情報を保持するデータクラス
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """LLMモデルの仕様・コスト・廃止情報を一元管理するデータクラス。

    Attributes:
        id: モデルID（例: "gemini-2.5-flash"）。
        provider: プロバイダ名（"google" | "anthropic" | "openai"）。
        tier: 使用帯域（"fast" | "standard" | "premium"）。
        cost_per_1k_in: 入力トークン1K件あたりのコスト（JPY）。
        cost_per_1k_out: 出力トークン1K件あたりのコスト（JPY）。
        max_context_tokens: 最大コンテキストトークン数。
        max_output_tokens: 最大出力トークン数。
        supports_vision: ビジョン（画像入力）対応フラグ。
        supports_tools: Function Calling / Tool Use 対応フラグ。
        supports_streaming: ストリーミング対応フラグ。
        supports_structured_output: ネイティブ構造化出力（JSON mode）対応フラグ。
        supports_prompt_caching: プロンプトキャッシュ（Anthropic cache_control 等）対応フラグ。
        deprecated: 廃止済みフラグ。True の場合はフォールバックチェインから除外される。
        deprecated_date: 廃止予定日または廃止日（"YYYY-MM-DD"）。
        successor: 廃止後に自動的に切り替える後継モデルID。
        added_date: このレジストリへの追加日（"YYYY-MM-DD"）。
        notes: 変更メモ・備考。
    """

    id: str = ""
    provider: str = ""
    tier: str = ""
    cost_per_1k_in: float = 0.0
    cost_per_1k_out: float = 0.0
    max_context_tokens: int = 0
    max_output_tokens: int = 0
    supports_vision: bool = False
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = False
    supports_prompt_caching: bool = False
    deprecated: bool = False
    deprecated_date: Optional[str] = None
    successor: Optional[str] = None
    added_date: str = ""
    notes: str = ""
    # エイリアスフィールド（テスト互換性のため）
    cost_per_1k_tokens_in: Optional[float] = None
    cost_per_1k_tokens_out: Optional[float] = None

    def __post_init__(self) -> None:
        """エイリアスフィールドを正規フィールドに反映する。"""
        if self.cost_per_1k_tokens_in is not None:
            self.cost_per_1k_in = self.cost_per_1k_tokens_in
        if self.cost_per_1k_tokens_out is not None:
            self.cost_per_1k_out = self.cost_per_1k_tokens_out

    def cost_ratio(self) -> float:
        """出力コストに対するコスト比率（品質/コスト指標の代理値として利用）。

        値が小さいほどコストパフォーマンスが高い。
        """
        return self.cost_per_1k_out


# ---------------------------------------------------------------------------
# MODEL_REGISTRY: 全登録モデルの定義
# ---------------------------------------------------------------------------

MODEL_REGISTRY: dict[str, ModelInfo] = {
    # --- Google: Gemini 2.5 Flash (fast tier) ---
    "gemini-2.5-flash": ModelInfo(
        id="gemini-2.5-flash",
        provider="google",
        tier="fast",
        cost_per_1k_in=0.011,
        cost_per_1k_out=0.044,
        max_context_tokens=1_048_576,
        max_output_tokens=65_536,
        supports_vision=True,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=True,
        supports_prompt_caching=False,
        deprecated=False,
        added_date="2026-01-01",
        notes="プライマリモデル。抽出・Q&A・分類タスクに最適。",
    ),
    # --- Google: Gemini 2.5 Pro (standard tier) ---
    "gemini-2.5-pro": ModelInfo(
        id="gemini-2.5-pro",
        provider="google",
        tier="standard",
        cost_per_1k_in=0.184,
        cost_per_1k_out=0.735,
        max_context_tokens=1_048_576,
        max_output_tokens=65_536,
        supports_vision=True,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=True,
        supports_prompt_caching=False,
        deprecated=False,
        added_date="2026-01-01",
        notes="複雑な推論・分析タスク向け。高精度が求められる場面に。",
    ),
    # --- Anthropic: Claude Sonnet 4.6 (standard tier) ---
    "claude-sonnet-4-6": ModelInfo(
        id="claude-sonnet-4-6",
        provider="anthropic",
        tier="standard",
        cost_per_1k_in=0.45,
        cost_per_1k_out=2.25,
        max_context_tokens=200_000,
        max_output_tokens=8_192,
        supports_vision=True,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=False,
        supports_prompt_caching=True,
        deprecated=False,
        added_date="2026-01-01",
        notes="Gemini Pro のフォールバック。プロンプトキャッシュでコスト削減可能。",
    ),
    # --- Anthropic: Claude Opus 4.6 (premium tier) ---
    "claude-opus-4-6": ModelInfo(
        id="claude-opus-4-6",
        provider="anthropic",
        tier="premium",
        cost_per_1k_in=2.25,
        cost_per_1k_out=11.25,
        max_context_tokens=200_000,
        max_output_tokens=8_192,
        supports_vision=True,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=False,
        supports_prompt_caching=True,
        deprecated=False,
        added_date="2026-01-01",
        notes="重大判断・法令チェック等の最高精度が必要なタスク専用。",
    ),
    # --- Anthropic: Claude Haiku 4.5 (fast tier) ---
    "claude-haiku-4-5": ModelInfo(
        id="claude-haiku-4-5",
        provider="anthropic",
        tier="fast",
        cost_per_1k_in=0.15,
        cost_per_1k_out=0.75,
        max_context_tokens=200_000,
        max_output_tokens=8_192,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=False,
        supports_prompt_caching=True,
        deprecated=False,
        added_date="2026-01-01",
        notes="Anthropic 系の低コストモデル。Gemini Flash の Anthropic フォールバック。",
    ),
    # --- OpenAI: GPT-4o (standard tier) ---
    "gpt-4o": ModelInfo(
        id="gpt-4o",
        provider="openai",
        tier="standard",
        cost_per_1k_in=0.375,
        cost_per_1k_out=1.5,
        max_context_tokens=128_000,
        max_output_tokens=16_384,
        supports_vision=True,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=False,
        supports_prompt_caching=False,
        deprecated=False,
        added_date="2026-01-01",
        notes="OpenAI 系 standard モデル。Gemini/Claude がどちらも失敗した場合の最終フォールバック。",
    ),
    # --- OpenAI: GPT-4o mini (fast tier) ---
    "gpt-4o-mini": ModelInfo(
        id="gpt-4o-mini",
        provider="openai",
        tier="fast",
        cost_per_1k_in=0.0225,
        cost_per_1k_out=0.09,
        max_context_tokens=128_000,
        max_output_tokens=16_384,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        supports_structured_output=False,
        supports_prompt_caching=False,
        deprecated=False,
        added_date="2026-01-01",
        notes="OpenAI 系の低コストモデル。fast tier の最終フォールバック候補。",
    ),
}


# ---------------------------------------------------------------------------
# ModelUpdateLog: レジストリ変更ログ
# ---------------------------------------------------------------------------

@dataclass
class ModelUpdateLog:
    """レジストリへの変更を記録するログエントリ。

    Attributes:
        timestamp: ISO 8601 形式のタイムスタンプ。
        action: 操作種別（"added" | "deprecated" | "replaced" | "cost_updated"）。
        model_id: 操作対象のモデルID。
        details: 操作の詳細説明。
    """

    timestamp: str
    action: str
    model_id: str
    details: str


# インメモリ更新ログ（最大 500 件保持）
_update_logs: list[ModelUpdateLog] = []
_MAX_LOG_ENTRIES = 500


def _append_log(action: str, model_id: str, details: str) -> None:
    """内部ヘルパー: 更新ログを追記する。上限を超えた場合は古いエントリを削除。"""
    global _update_logs
    entry = ModelUpdateLog(
        timestamp=datetime.now(timezone.utc).isoformat(),
        action=action,
        model_id=model_id,
        details=details,
    )
    _update_logs.append(entry)
    if len(_update_logs) > _MAX_LOG_ENTRIES:
        _update_logs = _update_logs[-_MAX_LOG_ENTRIES:]
    logger.info(f"[ModelRegistry] {action} model={model_id} — {details}")


# ---------------------------------------------------------------------------
# 公開関数: レジストリ操作
# ---------------------------------------------------------------------------

def get_update_logs(limit: int = 20) -> list[ModelUpdateLog]:
    """直近の更新ログを新しい順で返す。

    Args:
        limit: 返すログの最大件数（デフォルト: 20）。

    Returns:
        ModelUpdateLog のリスト（新しい順）。
    """
    return list(reversed(_update_logs))[:limit]


def register_model(info: ModelInfo, model_id: Optional[str] = None) -> None:
    """新モデルをレジストリに追加し、更新ログに記録する。

    同一 ID のモデルが既に存在する場合は上書きし、"replaced" アクションを記録する。

    Args:
        info: 登録する ModelInfo インスタンス。
        model_id: モデルIDを明示的に指定する場合（省略時は info.id を使用）。
                  指定した場合は info.id を上書きする。
    """
    if model_id is not None:
        info.id = model_id

    if not info.added_date:
        info.added_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    action = "replaced" if info.id in MODEL_REGISTRY else "added"
    MODEL_REGISTRY[info.id] = info
    _append_log(
        action=action,
        model_id=info.id,
        details=(
            f"provider={info.provider} tier={info.tier} "
            f"cost_in={info.cost_per_1k_in} cost_out={info.cost_per_1k_out}"
        ),
    )


def deprecate_model(
    model_id: str,
    successor: Optional[str] = None,
    date: Optional[str] = None,
) -> None:
    """指定モデルを廃止マークし、successor がある場合は自動切替情報を設定する。

    廃止されたモデルは get_fallback_chain() から自動的に除外される。
    successor を指定すると、そのモデルが代わりにチェインに組み込まれる。

    Args:
        model_id: 廃止するモデルID。
        successor: 後継モデルID（省略可能）。
        date: 廃止予定日または廃止日（"YYYY-MM-DD"、省略時は今日の日付）。

    Raises:
        KeyError: model_id がレジストリに存在しない場合。
    """
    if model_id not in MODEL_REGISTRY:
        raise KeyError(f"モデル '{model_id}' はレジストリに存在しません。")

    effective_date = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    model = MODEL_REGISTRY[model_id]
    model.deprecated = True
    model.deprecated_date = effective_date
    model.successor = successor

    details = f"deprecated_date={effective_date}"
    if successor:
        details += f" successor={successor}"
        logger.warning(
            f"[ModelRegistry] モデル '{model_id}' は廃止されました。"
            f"後継モデル '{successor}' を使用してください。"
        )
    else:
        logger.warning(
            f"[ModelRegistry] モデル '{model_id}' は廃止されました。後継モデルは未定です。"
        )

    _append_log(action="deprecated", model_id=model_id, details=details)


# ---------------------------------------------------------------------------
# 公開関数: フォールバックチェイン
# ---------------------------------------------------------------------------

def get_fallback_chain(tier: str) -> list[str]:
    """tier に対応するフォールバックチェインをコスト順で動的生成する。

    - deprecated=True のモデルは自動除外する。
    - deprecated モデルに successor が設定されている場合、successor が代わりに挿入される。
    - 同一 tier 内のモデルを出力コスト（cost_per_1k_out）の昇順でソートする。
    - フォールバック順: 同 tier のモデル群 → 上位 tier のモデル（緊急時）。

    Args:
        tier: "fast" | "standard" | "premium"

    Returns:
        モデルID のリスト（先頭が最優先）。
    """
    _TIER_ESCALATION: dict[str, list[str]] = {
        "fast":     ["fast", "standard"],
        "standard": ["standard", "premium"],
        "premium":  ["premium"],
    }
    target_tiers = _TIER_ESCALATION.get(tier, [tier])

    seen: set[str] = set()
    chain: list[str] = []

    for target_tier in target_tiers:
        candidates = [
            m for m in MODEL_REGISTRY.values()
            if m.tier == target_tier and not m.deprecated
        ]
        candidates.sort(key=lambda m: m.cost_per_1k_out)

        for model in candidates:
            if model.id not in seen:
                seen.add(model.id)
                chain.append(model.id)

    # deprecated モデルの successor を末尾に追加（未追加の場合のみ）
    for model in MODEL_REGISTRY.values():
        if model.deprecated and model.successor and model.successor not in seen:
            successor_info = MODEL_REGISTRY.get(model.successor)
            if successor_info and not successor_info.deprecated:
                seen.add(model.successor)
                chain.append(model.successor)

    if not chain:
        logger.error(f"[ModelRegistry] tier='{tier}' に有効なモデルが見つかりません。")

    return chain


# ---------------------------------------------------------------------------
# 公開関数: コスト情報取得
# ---------------------------------------------------------------------------

def get_model_costs(model_id: str) -> dict[str, float]:
    """MODEL_COSTS 互換の {"in": float, "out": float} を返す。

    client.py の MODEL_COSTS 辞書を置き換えるドロップイン互換関数。
    モデルが見つからない場合はゼロコストを返し、警告ログを出力する。

    Args:
        model_id: コストを取得するモデルID。

    Returns:
        {"in": <入力コスト JPY/1K>, "out": <出力コスト JPY/1K>}
    """
    info = MODEL_REGISTRY.get(model_id)
    if info is None:
        logger.warning(
            f"[ModelRegistry] モデル '{model_id}' のコスト情報が見つかりません。ゼロを返します。"
        )
        return {"in": 0.0, "out": 0.0}
    return {"in": info.cost_per_1k_in, "out": info.cost_per_1k_out}


# ---------------------------------------------------------------------------
# 公開関数: 廃止検知
# ---------------------------------------------------------------------------

def check_deprecations() -> list[dict]:
    """廃止予定・廃止済みモデルの情報をリストで返す。

    各要素には廃止日・後継モデル・廃止まで残り日数が含まれる。
    残り日数が負の場合は既に廃止済みを示す。

    Returns:
        以下キーを持つ dict のリスト:
        - model_id (str): モデルID
        - deprecated_date (str | None): 廃止日
        - successor (str | None): 後継モデルID
        - days_until (int | None): 廃止まで残り日数（負値 = 既に廃止済み、None = 日付未定）
    """
    today = datetime.now(timezone.utc).date()
    results: list[dict] = []

    for model_id, info in MODEL_REGISTRY.items():
        if not info.deprecated:
            continue

        days_until: Optional[int] = None
        if info.deprecated_date:
            try:
                dep_date = datetime.strptime(info.deprecated_date, "%Y-%m-%d").date()
                days_until = (dep_date - today).days
            except ValueError:
                logger.warning(
                    f"[ModelRegistry] モデル '{model_id}' の deprecated_date 形式が不正です: "
                    f"'{info.deprecated_date}'"
                )

        results.append(
            {
                "model_id": model_id,
                "deprecated_date": info.deprecated_date,
                "successor": info.successor,
                "days_until": days_until,
            }
        )

        # ログ通知
        if days_until is not None:
            if days_until <= 0:
                logger.error(
                    f"[ModelRegistry] モデル '{model_id}' は廃止済みです "
                    f"(deprecated_date={info.deprecated_date})。"
                    + (f" 後継: '{info.successor}'" if info.successor else "")
                )
            elif days_until <= 30:
                logger.warning(
                    f"[ModelRegistry] モデル '{model_id}' は {days_until} 日後に廃止予定です "
                    f"(deprecated_date={info.deprecated_date})。"
                    + (f" 後継: '{info.successor}'" if info.successor else "")
                )

    return results


# ---------------------------------------------------------------------------
# 公開関数: 最適モデル選択
# ---------------------------------------------------------------------------

# タスク種別と推奨 tier のマッピング
_TASK_TIER_MAP: dict[str, str] = {
    "extraction":          "fast",
    "qa":                  "fast",
    "classification":      "fast",
    "translation":         "fast",
    "summarization":       "fast",
    "ocr_post_process":    "fast",
    "table_parsing":       "fast",
    "message_drafting":    "fast",
    "reasoning":           "standard",
    "analysis":            "standard",
    "document_generation": "standard",
    "compliance":          "standard",
    "structured_output":   "fast",     # structured_output フラグで絞り込む
    "general":             "fast",
    "critical_decision":   "premium",
    "legal":               "premium",
    "audit":               "premium",
}


def select_optimal_model(
    tier: str,
    task_type: str = "general",
    requires_vision: bool = False,
    requires_structured_output: bool = False,
    max_cost_per_1k_out: Optional[float] = None,
) -> str:
    """タスク要件に基づいて最適なモデルを選択する。

    選択優先順位:
    1. task_type から推奨 tier を決定（引数 tier より task_type を優先）。
    2. 必須機能（vision / structured_output）でフィルタリング。
    3. max_cost_per_1k_out でコスト上限フィルタリング。
    4. 残ったモデルの中でコスパ（cost_per_1k_out 昇順）が最良のものを選択。
    5. 候補が存在しない場合は引数 tier のフォールバックチェイン先頭を返す。

    タスク種別ごとの推奨 tier:
    - "extraction", "qa", "classification", "translation" → fast
    - "reasoning", "analysis", "compliance", "document_generation" → standard
    - "critical_decision", "legal", "audit" → premium
    - "structured_output" → fast（structured_output 対応モデル優先）

    Args:
        tier: 基本 tier（"fast" | "standard" | "premium"）。
              task_type マッピングが存在する場合は上書きされる。
        task_type: タスク種別。_TASK_TIER_MAP に基づいて tier を自動決定。
        requires_vision: True の場合は vision 対応モデルのみを選択対象とする。
        requires_structured_output: True の場合は structured_output 対応モデルのみ対象。
        max_cost_per_1k_out: 出力コスト上限（JPY/1K トークン）。超えるモデルは除外。

    Returns:
        選択されたモデルID。
    """
    # task_type から推奨 tier を決定
    effective_tier = _TASK_TIER_MAP.get(task_type, tier)
    logger.debug(
        f"[ModelRegistry] select_optimal_model: task={task_type} "
        f"base_tier={tier} effective_tier={effective_tier}"
    )

    # フォールバックチェインを取得（deprecatedを除外済み）
    chain = get_fallback_chain(effective_tier)

    # 条件でフィルタリング
    candidates: list[ModelInfo] = []
    for model_id in chain:
        info = MODEL_REGISTRY.get(model_id)
        if info is None or info.deprecated:
            continue
        if requires_vision and not info.supports_vision:
            logger.debug(f"[ModelRegistry] {model_id} は vision 非対応のためスキップ。")
            continue
        if requires_structured_output and not info.supports_structured_output:
            logger.debug(
                f"[ModelRegistry] {model_id} は structured_output 非対応のためスキップ。"
            )
            continue
        if max_cost_per_1k_out is not None and info.cost_per_1k_out > max_cost_per_1k_out:
            logger.debug(
                f"[ModelRegistry] {model_id} はコスト上限超過 "
                f"({info.cost_per_1k_out} > {max_cost_per_1k_out})。"
            )
            continue
        candidates.append(info)

    if not candidates:
        # フォールバック: 条件を緩和してチェイン先頭を返す
        fallback_chain = get_fallback_chain(tier)
        if fallback_chain:
            logger.warning(
                f"[ModelRegistry] 条件を満たすモデルが見つかりません。"
                f"フォールバック: {fallback_chain[0]} を使用します。"
                f"(task={task_type} vision={requires_vision} "
                f"structured={requires_structured_output} max_cost={max_cost_per_1k_out})"
            )
            return fallback_chain[0]
        raise RuntimeError(
            f"[ModelRegistry] 利用可能なモデルが見つかりません。"
            f"tier={tier} task={task_type}"
        )

    # コスパ最良（cost_per_1k_out 昇順）で選択
    best = min(candidates, key=lambda m: m.cost_per_1k_out)
    logger.debug(
        f"[ModelRegistry] 選択: {best.id} "
        f"(cost_out={best.cost_per_1k_out} JPY/1K, tier={best.tier})"
    )
    return best.id


# ---------------------------------------------------------------------------
# 起動時の廃止チェック
# ---------------------------------------------------------------------------

def _run_startup_deprecation_check() -> None:
    """モジュールインポート時に廃止モデルのチェックを実行し、警告を出力する。"""
    deprecated = check_deprecations()
    if deprecated:
        for entry in deprecated:
            days = entry["days_until"]
            if days is not None and days <= 0:
                logger.error(
                    f"[ModelRegistry] 廃止済みモデルが登録されています: {entry['model_id']}"
                )
            elif days is not None and days <= 30:
                logger.warning(
                    f"[ModelRegistry] 廃止間近のモデルがあります: {entry['model_id']} "
                    f"（残り {days} 日）"
                )


_run_startup_deprecation_check()
