"""Plaid Link token creation, exchange, and update endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Request
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
from plaid.model.link_token_transactions import LinkTokenTransactions

import asyncio

from ..config import settings
from ..models import (
    LinkTokenRequest, LinkTokenResponse, ExchangeRequest, ExchangeResponse,
    UpdateLinkTokenRequest, StatusUpdate, AccountResponse,
)
from ..services.poll_service import poll_new_item

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["link"])

VALID_PRODUCTS = {"transactions", "investments"}


@router.post("/link/token", response_model=LinkTokenResponse)
async def create_link_token(body: LinkTokenRequest, request: Request):
    """Create a Plaid Link token for initializing Link in the browser."""
    client = request.app.state.plaid_client

    if body.product not in VALID_PRODUCTS:
        raise HTTPException(
            status_code=422,
            detail=f"product must be one of {VALID_PRODUCTS}, got '{body.product}'",
        )
    if body.optional_product and body.optional_product not in VALID_PRODUCTS:
        raise HTTPException(
            status_code=422,
            detail=f"optional_product must be one of {VALID_PRODUCTS}, got '{body.optional_product}'",
        )

    kwargs = dict(
        user=LinkTokenCreateRequestUser(client_user_id="user-1"),
        client_name="Plaid Bank Sync",
        products=[Products(body.product)],
        country_codes=[CountryCode("US")],
        language="en",
        transactions=LinkTokenTransactions(days_requested=730),
    )
    if body.optional_product:
        kwargs["optional_products"] = [Products(body.optional_product)]
    if settings.plaid_redirect_uri:
        kwargs["redirect_uri"] = settings.plaid_redirect_uri

    link_request = LinkTokenCreateRequest(**kwargs)
    response = client.link_token_create(link_request)

    return LinkTokenResponse(
        link_token=response.link_token,
        expiration=str(response.expiration),
    )


@router.post("/link/exchange", response_model=ExchangeResponse)
async def exchange_public_token(body: ExchangeRequest, request: Request):
    """Exchange a public token from Plaid Link for an access token and store the item."""
    db = request.app.state.db
    client = request.app.state.plaid_client

    # Deduplication check
    if not body.force:
        cursor = await db.execute(
            "SELECT item_id, institution_name FROM plaid_items WHERE institution_id = ?",
            (body.institution_id,),
        )
        existing = await cursor.fetchone()
        if existing:
            return ExchangeResponse(
                item_id="",
                institution_id=body.institution_id,
                institution_name=existing[1] or body.institution_name,
                products=body.products,
                duplicate=True,
                existing_item_id=existing[0],
            )

    # Exchange public token
    exchange_request = ItemPublicTokenExchangeRequest(public_token=body.public_token)
    exchange_response = client.item_public_token_exchange(exchange_request)
    access_token = exchange_response.access_token
    item_id = exchange_response.item_id

    # Store item
    products_str = ",".join(body.products)
    await db.execute(
        "INSERT OR REPLACE INTO plaid_items "
        "(item_id, access_token, institution_id, institution_name, status, products) "
        "VALUES (?, ?, ?, ?, 'good', ?)",
        (item_id, access_token, body.institution_id, body.institution_name, products_str),
    )
    await db.commit()

    # Fetch and store accounts
    accounts_request = AccountsGetRequest(access_token=access_token)
    accounts_response = client.accounts_get(accounts_request)

    account_models = []
    for acct in accounts_response.accounts:
        balances = acct.balances
        await db.execute(
            "INSERT OR REPLACE INTO accounts "
            "(plaid_account_id, item_id, name, official_name, type, subtype, mask, "
            "current_balance, available_balance, currency_code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                acct.account_id, item_id, acct.name, acct.official_name,
                str(acct.type) if acct.type else None,
                str(acct.subtype) if acct.subtype else None,
                acct.mask,
                balances.current if balances else None,
                balances.available if balances else None,
                balances.iso_currency_code if balances else "USD",
            ),
        )
        account_models.append(AccountResponse(
            plaid_account_id=acct.account_id, item_id=item_id,
            name=acct.name, official_name=acct.official_name,
            type=str(acct.type) if acct.type else None,
            subtype=str(acct.subtype) if acct.subtype else None,
            mask=acct.mask,
            current_balance=balances.current if balances else None,
            available_balance=balances.available if balances else None,
            currency_code=balances.iso_currency_code if balances else "USD",
        ))
    await db.commit()

    # Start background polling for data (Plaid needs time to fetch from institution)
    asyncio.create_task(poll_new_item(request.app, item_id, access_token, body.products))
    logger.info(f"Started background poll for {item_id}")

    return ExchangeResponse(
        item_id=item_id,
        institution_id=body.institution_id,
        institution_name=body.institution_name,
        products=body.products,
        accounts=account_models,
    )


@router.post("/link/token/update", response_model=LinkTokenResponse)
async def create_update_link_token(body: UpdateLinkTokenRequest, request: Request):
    """Create a Link token in update mode for re-authentication."""
    db = request.app.state.db
    client = request.app.state.plaid_client

    cursor = await db.execute(
        "SELECT access_token FROM plaid_items WHERE item_id = ?", (body.item_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    kwargs = dict(
        user=LinkTokenCreateRequestUser(client_user_id="user-1"),
        client_name="Plaid Bank Sync",
        country_codes=[CountryCode("US")],
        language="en",
        access_token=row[0],
    )
    if settings.plaid_redirect_uri:
        kwargs["redirect_uri"] = settings.plaid_redirect_uri

    link_request = LinkTokenCreateRequest(**kwargs)
    response = client.link_token_create(link_request)

    return LinkTokenResponse(
        link_token=response.link_token,
        expiration=str(response.expiration),
    )


@router.patch("/items/{item_id}/status")
async def update_item_status(item_id: str, body: StatusUpdate, request: Request):
    """Update an item's status (e.g., after successful re-authentication)."""
    db = request.app.state.db

    valid_statuses = {"good", "login_required", "error"}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {valid_statuses}",
        )

    cursor = await db.execute(
        "SELECT item_id FROM plaid_items WHERE item_id = ?", (item_id,)
    )
    if not await cursor.fetchone():
        raise HTTPException(status_code=404, detail="Item not found")

    await db.execute(
        "UPDATE plaid_items SET status = ?, updated_at = datetime('now') WHERE item_id = ?",
        (body.status, item_id),
    )
    await db.commit()

    return {"item_id": item_id, "status": body.status}
