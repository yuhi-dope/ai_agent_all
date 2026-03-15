"""セキュリティ最終チェック — RLS・PII・認証の検証。

パイロット投入前に全項目PASSすることを確認する。
"""
import pytest
from pathlib import Path


class TestRLSConfiguration:
    """全テーブルにRLSが設定されているか検証"""

    def _get_all_migration_sql(self) -> str:
        migrations_dir = Path(__file__).parent.parent / "db" / "migrations"
        sql = ""
        for f in sorted(migrations_dir.glob("*.sql")):
            sql += f.read_text(encoding="utf-8") + "\n"
        # schema.sql も含める
        schema_path = Path(__file__).parent.parent / "db" / "schema.sql"
        if schema_path.exists():
            sql += schema_path.read_text(encoding="utf-8")
        return sql

    def test_all_tables_have_rls(self):
        """CREATE TABLE されたテーブルに ENABLE ROW LEVEL SECURITY がある"""
        sql = self._get_all_migration_sql()

        # CREATE TABLE を抽出
        import re
        tables = re.findall(r'CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)', sql)
        rls_enabled = re.findall(r'ALTER TABLE\s+(\w+)\s+ENABLE ROW LEVEL SECURITY', sql)

        # 全社共通テーブル（RLS不要）
        exempt_tables = {
            "public_labor_rates",    # 全社共通読取専用
            "estimation_templates",  # 全社共通読取専用
        }

        missing_rls = []
        for table in tables:
            if table in exempt_tables:
                continue
            if table not in rls_enabled:
                missing_rls.append(table)

        assert len(missing_rls) == 0, f"RLS未設定テーブル: {missing_rls}"

    def test_rls_policies_use_company_id(self):
        """RLSポリシーがcompany_idでフィルタしている"""
        sql = self._get_all_migration_sql()
        import re
        policies = re.findall(r'CREATE POLICY.*?;', sql, re.DOTALL)

        missing = []
        for policy in policies:
            # 全社共通・INSERT専用・audit_logs等は除外
            if "USING (true)" in policy or "read_all" in policy:
                continue
            if "FOR INSERT" in policy and "company_id" not in policy:
                # INSERT ポリシーでcompany_idチェック不要な場合がある（audit_logs等）
                continue
            if "company_id" not in policy and "app.company_id" not in policy:
                missing.append(policy[:100])

        assert len(missing) == 0, f"company_idフィルタがないポリシー:\n" + "\n".join(missing)


class TestPIIHandler:
    """PII検出機能の検証"""

    def test_pii_handler_imports(self):
        from security.pii_handler import PIIDetector
        detector = PIIDetector()
        assert detector is not None

    def test_email_detection(self):
        from security.pii_handler import PIIDetector
        detector = PIIDetector()
        report = detector.detect_and_report("連絡先は test@example.com です")
        assert report.has_pii
        assert any(m.pii_type.value == "email" for m in report.matches)

    def test_phone_detection(self):
        from security.pii_handler import PIIDetector
        detector = PIIDetector()
        report = detector.detect_and_report("電話番号は 03-1234-5678 です")
        assert report.has_pii

    def test_masking(self):
        from security.pii_handler import PIIDetector
        detector = PIIDetector()
        report = detector.detect_and_report("連絡先は test@example.com です")
        assert report.has_pii
        assert report.masked_text != "連絡先は test@example.com です"


class TestAuditLog:
    """監査ログの検証"""

    def test_audit_module_imports(self):
        from security.audit import audit_log
        assert callable(audit_log)


class TestAuthMiddleware:
    """認証ミドルウェアの検証"""

    def test_middleware_imports(self):
        from auth.middleware import get_current_user, require_role
        assert callable(get_current_user)
        assert callable(require_role)

    def test_jwt_imports(self):
        from auth.jwt import JWTClaims
        assert JWTClaims is not None


class TestNoSecretsInCode:
    """コードにシークレットがハードコードされていないか"""

    def test_no_hardcoded_keys(self):
        """ソースコードにAPIキーやシークレットがハードコードされていないか"""
        src_dir = Path(__file__).parent.parent
        suspicious_patterns = [
            "sk-",           # OpenAI
            "AIza",          # Google
            "eyJhbGci",      # JWT token
            "AKIA",          # AWS
        ]

        violations = []
        for py_file in src_dir.rglob("*.py"):
            if ".venv" in str(py_file) or "node_modules" in str(py_file) or "__pycache__" in str(py_file):
                continue
            # テストファイル自体は除外（パターン定義を含むため）
            if "test_security_check" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8")
                for pattern in suspicious_patterns:
                    if pattern in content:
                        # .envファイルからの読み取りやコメントは除外
                        lines = content.split("\n")
                        for i, line in enumerate(lines):
                            stripped = line.strip()
                            if pattern in stripped and not stripped.startswith("#") and "os.getenv" not in stripped and "os.environ" not in stripped:
                                violations.append(f"{py_file.relative_to(src_dir)}:{i+1}: {stripped[:80]}")
            except Exception:
                pass

        assert len(violations) == 0, f"ハードコードされたシークレットの疑い:\n" + "\n".join(violations)

    def test_env_file_not_in_git(self):
        """gitignoreに.envが含まれているか"""
        gitignore_path = Path(__file__).parent.parent / ".gitignore"
        if gitignore_path.exists():
            content = gitignore_path.read_text()
            assert ".env" in content, ".gitignoreに.envが含まれていません"
