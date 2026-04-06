"""契約AI 設定管理"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- LLM ---
    gemini_api_key: str = ""

    # --- Supabase ---
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # --- CloudSign ---
    cloudsign_client_id: str = ""
    cloudsign_token: str = ""

    # --- Stripe ---
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_growth_price_id: str = ""

    # --- Google APIs ---
    google_credentials_path: str = "credentials.json"

    # --- Sender ---
    sender_name: str = "杉本 祐陽"
    sender_email: str = ""
    company_name: str = "シャチョツー"

    # --- URLs ---
    lp_base_url: str = "http://localhost:8080"
    app_base_url: str = "http://localhost:3000"

    model_config = {"env_file": "../.env", "env_file_encoding": "utf-8"}


settings = Settings()
