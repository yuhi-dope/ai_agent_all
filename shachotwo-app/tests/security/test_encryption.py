"""Tests for security/encryption.py — AES-256-GCM round-trip, randomness, tamper detection."""
import base64
import os
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from security.encryption import encrypt_field, decrypt_field


def _make_connector_module() -> ModuleType:
    """routers.connector を外部依存なしでインポートするためのヘルパー。
    auth / db / fastapi などをモックしてからインポートする。
    """
    stubs = {
        "fastapi": MagicMock(),
        "auth": MagicMock(),
        "auth.middleware": MagicMock(),
        "auth.jwt": MagicMock(),
        "db": MagicMock(),
        "db.supabase": MagicMock(),
        "security.audit": MagicMock(),
    }
    # 既にキャッシュされていれば削除して再 import
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("routers.connector"):
            del sys.modules[mod_name]

    with patch.dict(sys.modules, stubs):
        import importlib
        mod = importlib.import_module("routers.connector")
    return mod


# ---------------------------------------------------------------------------
# ラウンドトリップテスト
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """encrypt_field → decrypt_field で元のデータが復元できることを確認。"""

    def test_dict_roundtrip(self) -> None:
        data = {"api_key": "secret-123", "tenant": "acme", "port": 8080}
        assert decrypt_field(encrypt_field(data)) == data

    def test_string_roundtrip(self) -> None:
        data = "シャチョツー暗号化テスト"
        assert decrypt_field(encrypt_field(data)) == data

    def test_int_roundtrip(self) -> None:
        data = 42
        assert decrypt_field(encrypt_field(data)) == data

    def test_float_roundtrip(self) -> None:
        data = 3.14
        assert decrypt_field(encrypt_field(data)) == data

    def test_list_roundtrip(self) -> None:
        data = [1, "two", {"three": 3}]
        assert decrypt_field(encrypt_field(data)) == data

    def test_none_roundtrip(self) -> None:
        data = None
        assert decrypt_field(encrypt_field(data)) == data

    def test_nested_dict_roundtrip(self) -> None:
        data = {
            "credentials": {"user": "admin", "password": "P@ssw0rd!"},
            "endpoint": "https://api.example.com",
            "options": {"timeout": 30, "retry": True},
        }
        assert decrypt_field(encrypt_field(data)) == data

    def test_unicode_dict_roundtrip(self) -> None:
        data = {"会社名": "株式会社テスト", "担当者": "山田太郎"}
        assert decrypt_field(encrypt_field(data)) == data


# ---------------------------------------------------------------------------
# ランダム性テスト（同じ平文でも毎回異なる暗号文）
# ---------------------------------------------------------------------------

class TestRandomness:
    """nonce がランダムであるため、同一入力から異なる暗号文が生成されることを確認。"""

    def test_different_ciphertext_for_same_plaintext(self) -> None:
        data = {"key": "value"}
        enc1 = encrypt_field(data)
        enc2 = encrypt_field(data)
        # 同一データでも暗号文は異なるべき（nonce がランダムなため）
        assert enc1 != enc2

    def test_both_decrypt_to_same_value(self) -> None:
        data = {"key": "value"}
        enc1 = encrypt_field(data)
        enc2 = encrypt_field(data)
        assert decrypt_field(enc1) == decrypt_field(enc2) == data

    def test_nonce_is_unique_across_many_calls(self) -> None:
        """100回暗号化して全て異なる暗号文になることを確認。"""
        data = "test"
        ciphertexts = {encrypt_field(data) for _ in range(100)}
        assert len(ciphertexts) == 100


# ---------------------------------------------------------------------------
# 改ざん検出テスト
# ---------------------------------------------------------------------------

class TestTamperDetection:
    """GCM 認証タグにより改ざんを検出できることを確認。"""

    def test_tampered_ciphertext_raises(self) -> None:
        data = {"secret": "value"}
        encrypted = encrypt_field(data)
        # base64 デコードして ciphertext 部分を改ざん
        combined = bytearray(base64.b64decode(encrypted))
        combined[-1] ^= 0xFF  # 最終バイトをフリップ
        tampered = base64.b64encode(bytes(combined)).decode()
        with pytest.raises(Exception):
            decrypt_field(tampered)

    def test_tampered_nonce_raises(self) -> None:
        data = {"secret": "value"}
        encrypted = encrypt_field(data)
        combined = bytearray(base64.b64decode(encrypted))
        combined[0] ^= 0xFF  # nonce 先頭バイトをフリップ
        tampered = base64.b64encode(bytes(combined)).decode()
        with pytest.raises(Exception):
            decrypt_field(tampered)

    def test_completely_random_bytes_raises(self) -> None:
        """ランダムなバイト列は復号エラーになる。"""
        random_b64 = base64.b64encode(os.urandom(64)).decode()
        with pytest.raises(Exception):
            decrypt_field(random_b64)

    def test_wrong_key_raises(self) -> None:
        """正しい鍵で暗号化したデータを別の鍵では復号できない。"""
        data = {"secret": "value"}
        # 鍵A で暗号化
        with patch.dict(os.environ, {"ENCRYPTION_KEY": base64.b64encode(b"A" * 32).decode()}):
            encrypted = encrypt_field(data)
        # 鍵B で復号しようとすると失敗
        with patch.dict(os.environ, {"ENCRYPTION_KEY": base64.b64encode(b"B" * 32).decode()}):
            with pytest.raises(Exception):
                decrypt_field(encrypted)


# ---------------------------------------------------------------------------
# 鍵取得ロジックのテスト
# ---------------------------------------------------------------------------

class TestKeyDerivation:
    """環境変数による鍵切り替えが正しく動作することを確認。"""

    def test_encryption_key_env_var_is_used(self) -> None:
        """ENCRYPTION_KEY が設定されていればそれが使われる。"""
        key = base64.b64encode(os.urandom(32)).decode()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            encrypted = encrypt_field("test")
        with patch.dict(os.environ, {"ENCRYPTION_KEY": key}):
            assert decrypt_field(encrypted) == "test"

    def test_fallback_to_supabase_key(self) -> None:
        """ENCRYPTION_KEY が未設定でも SUPABASE_SERVICE_ROLE_KEY でフォールバックできる。"""
        # ENCRYPTION_KEY を除去し、SUPABASE_SERVICE_ROLE_KEY のみ残した環境を構築
        env_without_enc_key = {
            k: v for k, v in os.environ.items() if k != "ENCRYPTION_KEY"
        }
        env_without_enc_key["SUPABASE_SERVICE_ROLE_KEY"] = "fallback-key-32-chars-exactly!!!"
        with patch.dict(os.environ, env_without_enc_key, clear=True):
            encrypted = encrypt_field({"hello": "world"})
            result = decrypt_field(encrypted)
        assert result == {"hello": "world"}

    def test_default_fallback_key(self) -> None:
        """両方の環境変数が未設定でもデフォルト鍵でフォールバックできる。"""
        with patch.dict(os.environ, {}, clear=True):
            encrypted = encrypt_field(42)
            assert decrypt_field(encrypted) == 42


# ---------------------------------------------------------------------------
# connector.py の暗号化統合テスト（モック）
# ---------------------------------------------------------------------------

class TestConnectorEncryption:
    """routers/connector.py の _encrypt_credentials / _decrypt_credentials が
    encrypt_field / decrypt_field を使っていることを確認。"""

    def test_encrypt_credentials_uses_encrypt_field(self) -> None:
        """_encrypt_credentials は AES-GCM 暗号化済み base64 文字列を返す。"""
        connector = _make_connector_module()

        credentials = {"api_key": "test-api-key-value", "domain": "example.kintone.com"}
        encrypted = connector._encrypt_credentials(credentials)

        # 暗号文は文字列（base64）
        assert isinstance(encrypted, str)
        # base64 デコードできること
        decoded = base64.b64decode(encrypted)
        # nonce(12) + ciphertext(最低16バイトのタグ) = 最低28バイト
        assert len(decoded) >= 28

    def test_decrypt_credentials_uses_decrypt_field(self) -> None:
        """_encrypt_credentials → _decrypt_credentials でラウンドトリップ。"""
        connector = _make_connector_module()

        credentials = {"api_key": "test-api-key-value", "domain": "example.kintone.com"}
        encrypted = connector._encrypt_credentials(credentials)
        decrypted = connector._decrypt_credentials(encrypted)
        assert decrypted == credentials

    def test_credentials_not_base64_plain(self) -> None:
        """旧実装（base64 平文）ではなく AES-GCM であることを確認。
        平文を base64 エンコードしただけなら api_key が復号文に見えてしまう。
        """
        connector = _make_connector_module()

        credentials = {"api_key": "SENSITIVE_VALUE_MUST_NOT_APPEAR"}
        encrypted = connector._encrypt_credentials(credentials)

        # base64 デコードしても平文の JSON が見えないこと
        decoded_bytes = base64.b64decode(encrypted)
        assert b"SENSITIVE_VALUE_MUST_NOT_APPEAR" not in decoded_bytes

    def test_encrypt_field_called_via_connector(self) -> None:
        """connector の _encrypt_credentials が encrypt_field を呼ぶことをモックで確認。"""
        connector = _make_connector_module()

        with patch.object(connector, "encrypt_field", return_value="mocked_encrypted") as mock_enc:
            result = connector._encrypt_credentials({"key": "val"})
            mock_enc.assert_called_once_with({"key": "val"})
            assert result == "mocked_encrypted"

    def test_decrypt_field_called_via_connector(self) -> None:
        """connector の _decrypt_credentials が decrypt_field を呼ぶことをモックで確認。"""
        connector = _make_connector_module()

        with patch.object(connector, "decrypt_field", return_value={"key": "val"}) as mock_dec:
            result = connector._decrypt_credentials("some_encrypted_string")
            mock_dec.assert_called_once_with("some_encrypted_string")
            assert result == {"key": "val"}
