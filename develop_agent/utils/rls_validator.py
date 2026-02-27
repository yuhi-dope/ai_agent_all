"""RLS (Row Level Security) バリデータ.

Coder Agent が生成した SQL に RLS ポリシーが正しく含まれているかを検証する。
company_id カラムを持つ全テーブルに対して ALTER TABLE ... ENABLE ROW LEVEL SECURITY と
SELECT/INSERT/UPDATE ポリシーの存在を必須とする。

マスタテーブル（company_id なし）は RLS 対象外として許容する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class RLSValidationResult:
    """RLS バリデーション結果。"""
    passed: bool = True
    findings: list[str] = field(default_factory=list)
    tables_checked: int = 0
    tables_with_rls: int = 0
    tables_exempt: int = 0  # company_id なしのマスタテーブル


# company_id を持たなくてよいテーブル名パターン（マスタ系）
EXEMPT_TABLE_PATTERNS = re.compile(
    r"(master_|mst_|_masters?$|_types?$|_categories$|_statuses$|_config$)",
    re.IGNORECASE,
)


def validate_rls(generated_code: dict[str, str]) -> RLSValidationResult:
    """生成コード内の SQL ファイルを検証し、RLS ポリシーの有無をチェックする。"""
    result = RLSValidationResult()

    sql_files = {
        path: content
        for path, content in generated_code.items()
        if path.endswith(".sql")
    }

    if not sql_files:
        return result  # SQL ファイルなし → チェック不要

    for path, content in sql_files.items():
        tables = _extract_create_tables(content)
        for table_name, table_sql in tables:
            result.tables_checked += 1
            has_company_id = _has_company_id_column(table_sql)

            if not has_company_id:
                # マスタテーブル扱い（RLS 不要）
                result.tables_exempt += 1
                continue

            # company_id があるテーブルには RLS 必須
            rls_enabled = _has_rls_enabled(content, table_name)
            has_select_policy = _has_policy(content, table_name, "SELECT")
            has_insert_policy = _has_policy(content, table_name, "INSERT")
            has_update_policy = _has_policy(content, table_name, "UPDATE")
            has_all_policy = _has_policy(content, table_name, "ALL")

            if not rls_enabled:
                result.passed = False
                result.findings.append(
                    f"[{path}] テーブル `{table_name}` に ENABLE ROW LEVEL SECURITY がありません"
                )
            elif has_all_policy:
                # FOR ALL ポリシーがあれば SELECT/INSERT/UPDATE 個別は不要
                result.tables_with_rls += 1
            elif not (has_select_policy and has_insert_policy and has_update_policy):
                missing = []
                if not has_select_policy:
                    missing.append("SELECT")
                if not has_insert_policy:
                    missing.append("INSERT")
                if not has_update_policy:
                    missing.append("UPDATE")
                result.passed = False
                result.findings.append(
                    f"[{path}] テーブル `{table_name}` に {', '.join(missing)} ポリシーがありません"
                )
            else:
                result.tables_with_rls += 1

    return result


def inject_rls_policies(sql_content: str) -> str:
    """SQL 内の CREATE TABLE に対して不足している RLS ポリシーを自動注入する。

    Returns:
        RLS ポリシーが追加された SQL 文字列。
    """
    tables = _extract_create_tables(sql_content)
    additions = []

    for table_name, table_sql in tables:
        if not _has_company_id_column(table_sql):
            continue
        if _has_rls_enabled(sql_content, table_name):
            continue

        rls_block = f"""
-- RLS（自動注入: {table_name}）
ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;

CREATE POLICY "{table_name}_select" ON {table_name}
  FOR SELECT USING (company_id = current_setting('app.company_id', true));

CREATE POLICY "{table_name}_insert" ON {table_name}
  FOR INSERT WITH CHECK (company_id = current_setting('app.company_id', true));

CREATE POLICY "{table_name}_update" ON {table_name}
  FOR UPDATE USING (company_id = current_setting('app.company_id', true));
"""
        additions.append(rls_block)

    if additions:
        sql_content = sql_content.rstrip() + "\n" + "\n".join(additions)

    return sql_content


# ────────────────────────────────────────────────────────
# 内部ヘルパー
# ────────────────────────────────────────────────────────

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\);",
    re.IGNORECASE | re.DOTALL,
)


def _extract_create_tables(sql: str) -> list[tuple[str, str]]:
    """SQL から CREATE TABLE 文を抽出。(テーブル名, テーブル定義) のリスト。"""
    return [(m.group(1), m.group(2)) for m in _CREATE_TABLE_RE.finditer(sql)]


def _has_company_id_column(table_sql: str) -> bool:
    """テーブル定義に company_id カラムが含まれるかチェック。"""
    return bool(re.search(r"\bcompany_id\b", table_sql, re.IGNORECASE))


def _has_rls_enabled(full_sql: str, table_name: str) -> bool:
    """ALTER TABLE ... ENABLE ROW LEVEL SECURITY が存在するかチェック。"""
    pattern = rf"ALTER\s+TABLE\s+{re.escape(table_name)}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY"
    return bool(re.search(pattern, full_sql, re.IGNORECASE))


def _has_policy(full_sql: str, table_name: str, operation: str) -> bool:
    """指定テーブル・操作のポリシーが存在するかチェック。"""
    pattern = rf"CREATE\s+POLICY\s+\S+\s+ON\s+{re.escape(table_name)}\s+.*?FOR\s+{operation}"
    return bool(re.search(pattern, full_sql, re.IGNORECASE | re.DOTALL))
