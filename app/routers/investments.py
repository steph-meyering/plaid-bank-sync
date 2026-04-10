"""Investment sync and query endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Query, Request
from typing import List, Optional
from ..models import HoldingResponse, InvestmentTransactionResponse, SyncResult
from ..services.investment_service import sync_holdings, sync_investment_transactions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["investments"])


@router.post("/sync/investments", response_model=List[SyncResult])
async def sync_all_investments(request: Request):
    """Trigger investment sync (holdings + transactions) for all items."""
    db = request.app.state.db
    client = request.app.state.plaid_client
    results = []

    cursor = await db.execute("SELECT item_id, access_token FROM plaid_items")
    items = await cursor.fetchall()

    for item in items:
        item_id, access_token = item[0], item[1]
        try:
            holdings_result = await sync_holdings(client, db, item_id, access_token)
            txn_result = await sync_investment_transactions(client, db, item_id, access_token)
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, added_count, completed_at) "
                "VALUES (?, 'investments', 'success', ?, datetime('now'))",
                (item_id, holdings_result["holdings_synced"] + txn_result["transactions_synced"]),
            )
            await db.commit()
            results.append(SyncResult(
                item_id=item_id, sync_type="investments", status="success",
                added=holdings_result["holdings_synced"] + txn_result["transactions_synced"],
            ))
        except Exception as e:
            logger.error(f"Investment sync failed for {item_id}: {e}")
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
                "VALUES (?, 'investments', 'error', ?, datetime('now'))",
                (item_id, str(e)),
            )
            await db.commit()
            results.append(SyncResult(
                item_id=item_id, sync_type="investments", status="error", error=str(e),
            ))

    return results


@router.post("/sync/investments/{item_id}", response_model=SyncResult)
async def sync_item_investments(item_id: str, request: Request):
    """Trigger investment sync for a specific item."""
    db = request.app.state.db
    client = request.app.state.plaid_client

    cursor = await db.execute(
        "SELECT access_token FROM plaid_items WHERE item_id = ?", (item_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    try:
        holdings_result = await sync_holdings(client, db, item_id, row[0])
        txn_result = await sync_investment_transactions(client, db, item_id, row[0])
        await db.execute(
            "INSERT INTO sync_log (item_id, sync_type, status, added_count, completed_at) "
            "VALUES (?, 'investments', 'success', ?, datetime('now'))",
            (item_id, holdings_result["holdings_synced"] + txn_result["transactions_synced"]),
        )
        await db.commit()
        return SyncResult(
            item_id=item_id, sync_type="investments", status="success",
            added=holdings_result["holdings_synced"] + txn_result["transactions_synced"],
        )
    except Exception as e:
        await db.execute(
            "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
            "VALUES (?, 'investments', 'error', ?, datetime('now'))",
            (item_id, str(e)),
        )
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/investments/holdings", response_model=List[HoldingResponse])
async def list_holdings(request: Request, account_id: Optional[str] = None):
    """List all investment holdings, optionally filtered by account."""
    db = request.app.state.db
    if account_id:
        cursor = await db.execute(
            "SELECT account_id, item_id, security_id, institution_price, "
            "institution_price_as_of, institution_value, cost_basis, quantity, currency_code "
            "FROM investment_holdings WHERE account_id = ?",
            (account_id,),
        )
    else:
        cursor = await db.execute(
            "SELECT account_id, item_id, security_id, institution_price, "
            "institution_price_as_of, institution_value, cost_basis, quantity, currency_code "
            "FROM investment_holdings"
        )
    rows = await cursor.fetchall()
    return [
        HoldingResponse(
            account_id=r[0], item_id=r[1], security_id=r[2], institution_price=r[3],
            institution_price_as_of=r[4], institution_value=r[5], cost_basis=r[6],
            quantity=r[7], currency_code=r[8],
        )
        for r in rows
    ]


@router.get("/investments/transactions", response_model=List[InvestmentTransactionResponse])
async def list_investment_transactions(
    request: Request,
    account_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    """List investment transactions with optional filters."""
    db = request.app.state.db
    conditions = []
    params = []

    if account_id:
        conditions.append("account_id = ?")
        params.append(account_id)
    if start_date:
        conditions.append("date >= ?")
        params.append(start_date)
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = (
        f"SELECT investment_transaction_id, account_id, item_id, security_id, date, "
        f"name, quantity, amount, price, fees, type, subtype, currency_code "
        f"FROM investment_transactions{where} ORDER BY date DESC LIMIT ? OFFSET ?"
    )
    params.extend([limit, offset])

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()
    return [
        InvestmentTransactionResponse(
            investment_transaction_id=r[0], account_id=r[1], item_id=r[2],
            security_id=r[3], date=r[4], name=r[5], quantity=r[6],
            amount=r[7], price=r[8], fees=r[9], type=r[10],
            subtype=r[11], currency_code=r[12],
        )
        for r in rows
    ]
