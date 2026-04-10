"""Plaid client factory."""

import plaid
from plaid.api import plaid_api


PLAID_ENV_URLS = {
    "sandbox": plaid.Environment.Sandbox,
    "development": "https://development.plaid.com",
    "production": plaid.Environment.Production,
}


def create_plaid_client(client_id: str, secret: str, env: str) -> plaid_api.PlaidApi:
    """Create and return a configured Plaid API client."""
    configuration = plaid.Configuration(
        host=PLAID_ENV_URLS[env],
        api_key={
            "clientId": client_id,
            "secret": secret,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)
