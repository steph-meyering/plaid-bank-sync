"""Application configuration loaded from environment variables."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

VALID_PLAID_ENVS = ("sandbox", "development", "production")


@dataclass(frozen=True)
class Settings:
    plaid_client_id: str
    plaid_secret: str
    plaid_env: str
    database_url: str
    sync_interval_hours: int
    log_level: str


def get_settings() -> Settings:
    """Load and validate settings from environment variables."""
    plaid_client_id = os.getenv("PLAID_CLIENT_ID", "")
    plaid_secret = os.getenv("PLAID_SECRET", "")
    plaid_env = os.getenv("PLAID_ENV", "sandbox")
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./plaid_data.db")
    sync_interval_hours = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))
    log_level = os.getenv("LOG_LEVEL", "INFO")

    if not plaid_client_id:
        raise ValueError("PLAID_CLIENT_ID is required")
    if not plaid_secret:
        raise ValueError("PLAID_SECRET is required")
    if plaid_env not in VALID_PLAID_ENVS:
        raise ValueError(f"PLAID_ENV must be one of {VALID_PLAID_ENVS}, got '{plaid_env}'")

    return Settings(
        plaid_client_id=plaid_client_id,
        plaid_secret=plaid_secret,
        plaid_env=plaid_env,
        database_url=database_url,
        sync_interval_hours=sync_interval_hours,
        log_level=log_level,
    )


settings = get_settings()
