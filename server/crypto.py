"""
トークン暗号化ユーティリティ。
Fernet 対称鍵暗号でインフラトークン（Supabase / Vercel）を暗号化・復号する。
TOKEN_ENCRYPTION_KEY が未設定の場合は平文フォールバック（ローカル開発用）。
"""

import logging
import os

logger = logging.getLogger(__name__)

_fernet = None
_initialized = False


def _get_fernet():
    global _fernet, _initialized
    if _initialized:
        return _fernet
    _initialized = True
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        logger.warning("TOKEN_ENCRYPTION_KEY is not set – tokens will be stored in plaintext")
        return None
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(key.encode())
    except Exception as e:
        logger.error("Invalid TOKEN_ENCRYPTION_KEY: %s", e)
        _fernet = None
    return _fernet


def encrypt(plaintext: str) -> str:
    """平文を暗号化して返す。キー未設定時は平文をそのまま返す。"""
    f = _get_fernet()
    if not f:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """暗号文を復号して返す。キー未設定時はそのまま返す。"""
    f = _get_fernet()
    if not f:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except Exception:
        # 平文で保存されていた場合のフォールバック
        return ciphertext
