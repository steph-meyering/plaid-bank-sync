"""Service for Plaid Link token creation and token exchange."""

import json
import uuid
import logging
from plaid.api import plaid_api
from plaid.model.sandbox_public_token_create_request import SandboxPublicTokenCreateRequest
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.products import Products
import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_INSTITUTION = "ins_109508"  # First Platypus Bank - supports transactions + investments


async def create_sandbox_link(
    client: plaid_api.PlaidApi,
    db: aiosqlite.Connection,
    institution_id: str = DEFAULT_INSTITUTION,
) -> dict:
    """Create a sandbox public token, exchange it, and store the item + accounts."""
    # Step 1: Create sandbox public token
    request = SandboxPublicTokenCreateRequest(
        institution_id=institution_id,
        initial_products=[Products("transactions"), Products("investments")],
    )
    response = client.sandbox_public_token_create(request)
    public_token = response.public_token

    # Step 2: Exchange public token for access token
    exchange_request = ItemPublicTokenExchangeRequest(public_token=public_token)
    exchange_response = client.item_public_token_exchange(exchange_request)
    access_token = exchange_response.access_token
    item_id = exchange_response.item_id

    # Step 3: Store item
    await db.execute(
        "INSERT OR REPLACE INTO plaid_items (item_id, access_token, institution_id, institution_name) "
        "VALUES (?, ?, ?, ?)",
        (item_id, access_token, institution_id, institution_id),
    )
    await db.commit()

    # Step 4: Fetch and store accounts
    accounts_request = AccountsGetRequest(access_token=access_token)
    accounts_response = client.accounts_get(accounts_request)

    for acct in accounts_response.accounts:
        balances = acct.balances
        await db.execute(
            "INSERT OR REPLACE INTO accounts "
            "(plaid_account_id, item_id, name, official_name, type, subtype, mask, "
            "current_balance, available_balance, currency_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                acct.account_id,
                item_id,
                acct.name,
                acct.official_name,
                str(acct.type) if acct.type else None,
                str(acct.subtype) if acct.subtype else None,
                acct.mask,
                balances.current if balances else None,
                balances.available if balances else None,
                balances.iso_currency_code if balances else "USD",
            ),
        )
    await db.commit()

    logger.info(f"Linked item {item_id} with {len(accounts_response.accounts)} accounts")
    return {
        "item_id": item_id,
        "institution_id": institution_id,
        "accounts": len(accounts_response.accounts),
    }
