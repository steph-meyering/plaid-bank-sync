"""Account management endpoints."""

import json
import logging
from fastapi import APIRouter, HTTPException, Request
from typing import List, Optional
from ..models import AccountResponse, ItemResponse, ErrorResponse
from plaid.model.item_remove_request import ItemRemoveRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/accounts", response_model=List[AccountResponse])
async def list_accounts(request: Request):
    """List all linked accounts with balances."""
    db = request.app.state.db
    cursor = await db.execute(
        "SELECT plaid_account_id, item_id, name, official_name, type, subtype, "
        "mask, current_balance, available_balance, currency_code FROM accounts ORDER BY name"
    )
    rows = await cursor.fetchall()
    return [
        AccountResponse(
            plaid_account_id=r[0], item_id=r[1], name=r[2], official_name=r[3],
            type=r[4], subtype=r[5], mask=r[6], current_balance=r[7],
            available_balance=r[8], currency_code=r[9],
        )
        for r in rows
    ]


@router.get("/accounts/{account_id}", response_model=AccountResponse)
async def get_account(account_id: str, request: Request):
    """Get a single account by Plaid account ID."""
    db = request.app.state.db
    cursor = await db.execute(
        "SELECT plaid_account_id, item_id, name, official_name, type, subtype, "
        "mask, current_balance, available_balance, currency_code "
        "FROM accounts WHERE plaid_account_id = ?",
        (account_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return AccountResponse(
        plaid_account_id=row[0], item_id=row[1], name=row[2], official_name=row[3],
        type=row[4], subtype=row[5], mask=row[6], current_balance=row[7],
        available_balance=row[8], currency_code=row[9],
    )


@router.delete("/items/{item_id}")
async def remove_item(item_id: str, request: Request):
    """Unlink a Plaid item and remove its data."""
    db = request.app.state.db
    client = request.app.state.plaid_client

    cursor = await db.execute(
        "SELECT access_token FROM plaid_items WHERE item_id = ?", (item_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    # Try to remove from Plaid
    try:
        remove_request = ItemRemoveRequest(access_token=row[0])
        client.item_remove(remove_request)
    except Exception as e:
        logger.warning(f"Failed to remove item from Plaid: {e}")

    # Remove local data
    await db.execute("DELETE FROM transactions WHERE item_id = ?", (item_id,))
    await db.execute("DELETE FROM investment_transactions WHERE item_id = ?", (item_id,))
    await db.execute("DELETE FROM investment_holdings WHERE item_id = ?", (item_id,))
    await db.execute("DELETE FROM accounts WHERE item_id = ?", (item_id,))
    await db.execute("DELETE FROM sync_log WHERE item_id = ?", (item_id,))
    await db.execute("DELETE FROM plaid_items WHERE item_id = ?", (item_id,))
    await db.commit()

    return {"status": "removed", "item_id": item_id}
