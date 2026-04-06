"""マーケAI 設定管理"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- LLM ---
    gemini_api_key: str = ""

    # --- Supabase ---
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # --- Google APIs ---
    google_credentials_path: str = "credentials.json"
    google_sheets_spreadsheet_id: str = ""

    # --- gBizINFO ---
    gbizinfo_api_token: str = ""

    # --- Proxy ---
    proxy_url: str | None = None

    # --- LP ---
    lp_base_url: str = "http://localhost:8080"

    # --- Sender Info ---
    sender_name: str = "杉本 祐陽"
    sender_email: str = ""
    sender_phone: str = ""
    company_name: str = "シャチョツー"
    website_url: str = "https://shachotwo.com"

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8"}


settings = Settings()
