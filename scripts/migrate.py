#!/usr/bin/env python3
"""
docs/migrations/ 配下の SQL ファイルを Supabase（PostgreSQL）に順番に適用するスクリプト。

使い方:
  python scripts/migrate.py              # 未適用の全マイグレーションを適用
  python scripts/migrate.py --dry-run    # 適用予定の SQL を表示のみ（実行しない）
  python scripts/migrate.py --list       # 適用済み / 未適用の一覧を表示

環境変数（.env.local から自動読み込み）:
  DATABASE_URL  - Supabase PostgreSQL 接続文字列（必須）
                  形式: postgresql://postgres:[PASSWORD]@db.[PROJECT_REF].supabase.co:5432/postgres
                  Supabase Dashboard > Settings > Database > Connection string (URI) で確認

注意:
  - Supabase Python クライアントは DDL（CREATE TABLE, ALTER TABLE 等）の実行に対応していないため、
    psycopg2 で直接 PostgreSQL に接続します。
  - migration_history テーブルで冪等性を保証します（適用済みファイルはスキップ）。
  - ファイル名のアルファベット順に適用されます。
    新規マイグレーションには "migration_YYYYMMDD_description.sql" 形式の命名を推奨します。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent

# プロジェクトルートの .env.local を読み込む
try:
    from dotenv import load_dotenv
    load_dotenv(_root / ".env.local")
except ImportError:
    pass  # python-dotenv 未インストール時は環境変数から直接読む

MIGRATIONS_DIR = _root / "docs" / "migrations"

_CREATE_HISTORY_SQL = """
CREATE TABLE IF NOT EXISTS migration_history (
  filename    TEXT PRIMARY KEY,
  applied_at  TIMESTAMPTZ DEFAULT now()
);
"""


def _get_connection():
    """psycopg2 で Supabase PostgreSQL に直接接続して返す。"""
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        print(
            "ERROR: DATABASE_URL が未設定です。\n"
            "  Supabase Dashboard > Settings > Database > Connection string (URI) から取得して\n"
            "  .env.local に DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres\n"
            "  として設定してください。",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        import psycopg2  # type: ignore
    except ImportError:
        print(
            "ERROR: psycopg2 が未インストールです。\n"
            "  pip install psycopg2-binary でインストールしてください。",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        print(f"ERROR: データベース接続に失敗しました: {e}", file=sys.stderr)
        sys.exit(1)


def _get_migration_files() -> list[Path]:
    """docs/migrations/ 配下の .sql ファイルをソート順で返す。"""
    if not MIGRATIONS_DIR.exists():
        print(f"ERROR: マイグレーションディレクトリが見つかりません: {MIGRATIONS_DIR}", file=sys.stderr)
        sys.exit(1)
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _get_applied(conn) -> set[str]:
    """migration_history から適用済みファイル名の集合を返す（テーブルがなければ作成）。"""
    with conn.cursor() as cur:
        cur.execute(_CREATE_HISTORY_SQL)
        conn.commit()
        cur.execute("SELECT filename FROM migration_history")
        return {row[0] for row in cur.fetchall()}


def _apply_migration(conn, filepath: Path, dry_run: bool = False) -> bool:
    """単一の SQL ファイルを適用する。dry_run=True の場合は表示のみ。"""
    sql = filepath.read_text(encoding="utf-8")
    if dry_run:
        print(f"\n[DRY RUN] {filepath.name}:")
        preview = sql[:600] + ("\n... (truncated)" if len(sql) > 600 else "")
        for line in preview.splitlines():
            print(f"  {line}")
        return True
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO migration_history (filename) VALUES (%s) ON CONFLICT DO NOTHING",
                (filepath.name,),
            )
            conn.commit()
            print(f"  [OK] {filepath.name}")
            return True
        except Exception as e:
            conn.rollback()
            print(f"  [FAIL] {filepath.name}: {e}", file=sys.stderr)
            return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Supabase マイグレーションランナー",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--dry-run", action="store_true", help="SQL を表示のみ（実行しない）")
    parser.add_argument("--list", action="store_true", help="適用済み / 未適用の一覧を表示")
    args = parser.parse_args()

    conn = _get_connection()
    applied = _get_applied(conn)
    files = _get_migration_files()

    if args.list:
        print("=== マイグレーション一覧 ===")
        for f in files:
            status = "[済]" if f.name in applied else "[未]"
            print(f"  {status} {f.name}")
        if not files:
            print("  (マイグレーションファイルなし)")
        conn.close()
        return

    pending = [f for f in files if f.name not in applied]

    if not pending:
        print("適用すべきマイグレーションはありません。すべて適用済みです。")
        conn.close()
        return

    action = "表示（dry-run）" if args.dry_run else "適用"
    print(f"{len(pending)} 件のマイグレーションを{action}します...")

    failed = 0
    for f in pending:
        ok = _apply_migration(conn, f, dry_run=args.dry_run)
        if not ok:
            failed += 1
            print("  → エラーが発生したため以降の適用を停止しました。", file=sys.stderr)
            break

    conn.close()

    if failed:
        print(f"\n失敗: {failed} 件のマイグレーションが適用されませんでした。", file=sys.stderr)
        sys.exit(1)
    else:
        verb = "表示" if args.dry_run else "適用"
        print(f"\n{verb}完了しました。")


if __name__ == "__main__":
    main()
