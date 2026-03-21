"""マイグレーション適用状況チェッカー

Supabase上の実テーブル・カラムを確認し、
各マイグレーションが適用済みかどうかを判定する。

使い方:
  cd shachotwo-app
  python -m db.check_migrations
"""
import os
import sys
import json
import logging

# .envの読み込み
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from supabase import create_client

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def get_client():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        print("ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が未設定です")
        print("  .env ファイルを確認してください")
        sys.exit(1)
    return create_client(url, key)


# ─────────────────────────────────────
# チェック定義: マイグレーションごとに「何を確認するか」
# ─────────────────────────────────────

MIGRATION_CHECKS = [
    {
        "id": "001",
        "name": "initial_schema",
        "description": "12コアテーブル（companies, users, knowledge_items等）",
        "checks": [
            ("table", "companies"),
            ("table", "users"),
            ("table", "knowledge_items"),
            ("table", "knowledge_relations"),
            ("table", "knowledge_sessions"),
            ("table", "company_state_snapshots"),
            ("table", "proactive_proposals"),
            ("table", "decision_rules"),
            ("table", "tool_connections"),
            ("table", "execution_logs"),
            ("table", "audit_logs"),
            ("table", "consent_records"),
        ],
    },
    {
        "id": "002",
        "name": "vector_search_function",
        "description": "pgvector RPC: match_knowledge_items()",
        "checks": [
            ("rpc", "match_knowledge_items"),
        ],
    },
    {
        "id": "003",
        "name": "embedding_768",
        "description": "embedding次元を512→768に変更",
        "checks": [
            # embeddingカラムの存在確認（次元数はRPCで確認）
            ("column", "knowledge_items", "embedding"),
        ],
    },
    {
        "id": "004",
        "name": "audit_logs_insert_only",
        "description": "audit_logs INSERT-only RLS",
        "checks": [
            ("table", "audit_logs"),  # テーブルが存在すればOK（RLSポリシーはDB側で確認）
        ],
    },
    {
        "id": "005",
        "name": "invitations",
        "description": "招待テーブル",
        "checks": [
            ("table", "invitations"),
        ],
    },
    {
        "id": "006",
        "name": "bpo_base",
        "description": "共通BPOテーブル（invoices, expenses, vendors）",
        "checks": [
            ("table", "bpo_invoices"),
            ("table", "bpo_expenses"),
            ("table", "bpo_vendors"),
        ],
    },
    {
        "id": "007",
        "name": "bpo_construction",
        "description": "建設業BPOテーブル（12テーブル）",
        "checks": [
            ("table", "estimation_projects"),
            ("table", "estimation_items"),
            ("table", "unit_price_master"),
            ("table", "public_labor_rates"),
            ("table", "construction_sites"),
            ("table", "construction_workers"),
            ("table", "safety_documents"),
            ("table", "cost_records"),
        ],
    },
    {
        "id": "008",
        "name": "qa_sessions",
        "description": "Q&Aセッションテーブル",
        "checks": [
            ("table", "qa_sessions"),
        ],
    },
    {
        "id": "009",
        "name": "knowledge_bpo_fields",
        "description": "knowledge_itemsにBPOフィールド追加",
        "checks": [
            ("column", "knowledge_items", "source_tag"),
            ("column", "knowledge_items", "bpo_automatable"),
        ],
    },
    {
        "id": "010",
        "name": "knowledge_session_files",
        "description": "knowledge_sessionsにファイル関連カラム追加",
        "checks": [
            ("column", "knowledge_sessions", "file_name"),
        ],
    },
    {
        "id": "011",
        "name": "session_cost_tracking",
        "description": "knowledge_sessionsにコスト追跡カラム追加",
        "checks": [
            ("column", "knowledge_sessions", "cost_yen"),
            ("column", "knowledge_sessions", "model_used"),
        ],
    },
    {
        "id": "012",
        "name": "feedback_learning",
        "description": "フィードバック学習テーブル",
        "checks": [
            ("table", "extraction_feedback"),
            ("table", "term_normalization"),
            ("column", "estimation_items", "user_modified"),
            ("column", "unit_price_master", "accuracy_rate"),
            ("column", "unit_price_master", "used_count"),
        ],
    },
    {
        "id": "013",
        "name": "llm_call_logs",
        "description": "LLM呼び出しログテーブル",
        "checks": [
            ("table", "llm_call_logs"),
        ],
    },
    {
        "id": "014",
        "name": "bpo_manufacturing",
        "description": "製造業BPOテーブル（4テーブル）",
        "checks": [
            ("table", "mfg_quotes"),
            ("table", "mfg_quote_items"),
            ("table", "mfg_charge_rates"),
            ("table", "mfg_material_prices"),
        ],
    },
    {
        "id": "015",
        "name": "execution_hitl",
        "description": "HITL承認テーブル",
        "checks": [
            ("table", "bpo_hitl_requirements"),
            ("column", "execution_logs", "approval_status"),
        ],
    },
    {
        "id": "016",
        "name": "knowledge_half_life",
        "description": "ナレッジ鮮度管理カラム",
        "checks": [
            ("column", "knowledge_items", "half_life_days"),
            ("column", "knowledge_items", "qa_usage_count"),
        ],
    },
    {
        "id": "017",
        "name": "qa_usage_rpc",
        "description": "RPC: increment_qa_usage_count()",
        "checks": [
            ("rpc", "increment_qa_usage_count"),
        ],
    },
    {
        "id": "018",
        "name": "bpo_hitl_all_pipelines",
        "description": "29パイプラインHITL登録",
        "checks": [
            ("table", "bpo_hitl_requirements"),  # テーブルがあればOK（行数は別途確認）
        ],
    },
    {
        "id": "019",
        "name": "knowledge_feedback_rpc",
        "description": "RPC: increment_knowledge_feedback()",
        "checks": [
            ("rpc", "increment_knowledge_feedback"),
        ],
    },
    {
        "id": "020",
        "name": "mfg_quoting_engine",
        "description": "製造業3層エンジン（sub_industry, layers_used等）",
        "checks": [
            ("column", "mfg_quotes", "sub_industry"),
            ("column", "mfg_quotes", "layers_used"),
            ("column", "mfg_quotes", "overall_confidence"),
            ("column", "mfg_quote_items", "layer_source"),
            ("column", "mfg_quote_items", "user_modified"),
        ],
    },
]


def check_table_exists(client, table_name: str) -> bool:
    """テーブルが存在するか確認（SELECTを試みる）"""
    try:
        result = client.table(table_name).select("*", count="exact").limit(0).execute()
        return True
    except Exception as e:
        if "404" in str(e) or "relation" in str(e).lower() or "does not exist" in str(e).lower():
            return False
        # 権限エラー等はテーブル自体は存在する
        if "permission" in str(e).lower() or "403" in str(e):
            return True
        return False


def check_column_exists(client, table_name: str, column_name: str) -> bool:
    """カラムが存在するか確認（SELECTで指定カラムを取得）"""
    try:
        result = client.table(table_name).select(column_name).limit(1).execute()
        return True
    except Exception as e:
        if "column" in str(e).lower() or "does not exist" in str(e).lower():
            return False
        if "404" in str(e):
            return False
        # 権限エラーはカラム存在と見なす
        if "permission" in str(e).lower():
            return True
        return False


def check_rpc_exists(client, function_name: str) -> bool:
    """RPC関数が存在するか確認"""
    # 関数ごとに最低限の引数を渡して呼び出す
    dummy_args = {
        "match_knowledge_items": {
            "query_embedding": [0.0] * 768,
            "match_company_id": "00000000-0000-0000-0000-000000000000",
        },
        "increment_qa_usage_count": {"item_ids": []},
        "increment_knowledge_feedback": {
            "item_ids": [],
            "count_column": "positive_feedback_count",
        },
    }
    args = dummy_args.get(function_name, {})
    try:
        result = client.rpc(function_name, args).execute()
        return True
    except Exception as e:
        err_str = str(e).lower()
        if "could not find" in err_str or "404" in str(e) or "does not exist" in err_str:
            return False
        # 引数エラー等は関数が存在する証拠
        return True


def main():
    print("=" * 70)
    print("シャチョツー マイグレーション適用状況チェッカー")
    print("=" * 70)
    print()

    client = get_client()

    total = len(MIGRATION_CHECKS)
    applied = 0
    partial = 0
    not_applied = 0

    for mig in MIGRATION_CHECKS:
        checks_passed = 0
        checks_total = len(mig["checks"])
        failed_checks = []

        for check_type, *args in mig["checks"]:
            if check_type == "table":
                ok = check_table_exists(client, args[0])
                if not ok:
                    failed_checks.append(f"テーブル {args[0]} が存在しない")
            elif check_type == "column":
                ok = check_column_exists(client, args[0], args[1])
                if not ok:
                    failed_checks.append(f"カラム {args[0]}.{args[1]} が存在しない")
            elif check_type == "rpc":
                ok = check_rpc_exists(client, args[0])
                if not ok:
                    failed_checks.append(f"RPC {args[0]} が存在しない")
            else:
                ok = False
                failed_checks.append(f"不明なチェック種別: {check_type}")

            if ok:
                checks_passed += 1

        # 判定
        if checks_passed == checks_total:
            status = "✅ 適用済"
            applied += 1
        elif checks_passed > 0:
            status = "⚠️  一部適用"
            partial += 1
        else:
            status = "❌ 未適用"
            not_applied += 1

        # 表示
        print(f"  {mig['id']} {mig['name']:.<40s} {status}")
        if failed_checks:
            for fc in failed_checks:
                print(f"       → {fc}")

    print()
    print("-" * 70)
    print(f"  適用済: {applied}/{total}   一部適用: {partial}/{total}   未適用: {not_applied}/{total}")
    print("-" * 70)

    if not_applied > 0:
        print()
        print("【要対応】未適用のマイグレーションがあります。")
        print("  Supabase SQL Editor で以下のファイルを実行してください:")
        for mig in MIGRATION_CHECKS:
            checks_passed = sum(
                1 for check_type, *args in mig["checks"]
                if (check_type == "table" and check_table_exists(client, args[0]))
                or (check_type == "column" and check_column_exists(client, args[0], args[1]))
                or (check_type == "rpc" and check_rpc_exists(client, args[0]))
            )
            if checks_passed == 0:
                print(f"    db/migrations/{mig['id']}_{mig['name']}.sql")

    if partial > 0:
        print()
        print("【注意】一部適用のマイグレーションがあります。")
        print("  手動で確認してください。")

    print()
    return 0 if (not_applied == 0 and partial == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
