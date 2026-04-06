"""AES-256-GCM encryption for sensitive fields (credentials, PII等)。
MVP: アプリレイヤー暗号化。Enterprise: GCP KMS に移行予定。
"""
import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_key() -> bytes:
    """環境変数 ENCRYPTION_KEY (base64) から32バイトキーを取得。
    未設定時は SUPABASE_SERVICE_ROLE_KEY の先頭32バイトをフォールバックとして使用。

    本番環境では必ず ENCRYPTION_KEY を設定すること。
    フォールバックは開発・テスト専用。
    """
    key_b64 = os.environ.get("ENCRYPTION_KEY")
    if key_b64:
        return base64.b64decode(key_b64)[:32]
    # フォールバック（本番では ENCRYPTION_KEY 必須）
    fallback = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "default-key-for-development-only")
    return fallback.encode()[:32].ljust(32, b"\0")


def encrypt_field(data: Any) -> str:
    """dict/str/any を JSON化してAES-256-GCMで暗号化。base64文字列で返す。

    Args:
        data: 暗号化するデータ（JSON シリアライズ可能な任意の型）

    Returns:
        nonce(12bytes) + ciphertext を base64 エンコードした文字列
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)  # 96-bit nonce (GCM推奨サイズ)
    plaintext = json.dumps(data, ensure_ascii=False).encode()
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # nonce + ciphertext を base64 でまとめる
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode()


def decrypt_field(encrypted: str) -> Any:
    """encrypt_field で暗号化した文字列を復号してデシリアライズ。

    Args:
        encrypted: encrypt_field が返した base64 文字列

    Returns:
        元のデータ（dict / str / int 等）

    Raises:
        cryptography.exceptions.InvalidTag: 改ざん検出または鍵不一致
        json.JSONDecodeError: 復号後データが JSON でない場合（通常は発生しない）
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    combined = base64.b64decode(encrypted)
    nonce = combined[:12]
    ciphertext = combined[12:]
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return json.loads(plaintext.decode())
