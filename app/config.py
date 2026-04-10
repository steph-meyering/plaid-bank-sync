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
    plaid_redirect_uri: str


def get_settings() -> Settings:
    """Load and validate settings from environment variables."""
    plaid_client_id = os.getenv("PLAID_CLIENT_ID", "")
    plaid_env = os.getenv("PLAID_ENV", "sandbox")

    # Resolve per-environment secret, falling back to generic PLAID_SECRET
    env_key = f"PLAID_SECRET_{plaid_env.upper()}"
    plaid_secret = os.getenv(env_key, "") or os.getenv("PLAID_SECRET", "")
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./plaid_data.db")
    sync_interval_hours = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))
    log_level = os.getenv("LOG_LEVEL", "INFO")
    plaid_redirect_uri = os.getenv("PLAID_REDIRECT_URI", "")

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
        plaid_redirect_uri=plaid_redirect_uri,
    )


settings = get_settings()
