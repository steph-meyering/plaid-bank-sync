"""Transaction sync and query endpoints."""

import json
import logging
from fastapi import APIRouter, HTTPException, Query, Request
from typing import List, Optional
from ..models import TransactionResponse, SyncResult
from ..services.transaction_service import sync_transactions

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["transactions"])


@router.post("/sync/transactions", response_model=List[SyncResult])
async def sync_all_transactions(request: Request):
    """Trigger transaction sync for all linked items."""
    db = request.app.state.db
    client = request.app.state.plaid_client
    results = []

    cursor = await db.execute("SELECT item_id, access_token FROM plaid_items")
    items = await cursor.fetchall()

    for item in items:
        item_id, access_token = item[0], item[1]
        try:
            result = await sync_transactions(client, db, item_id, access_token)
            # Log success
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, added_count, modified_count, removed_count, completed_at) "
                "VALUES (?, 'transactions', 'success', ?, ?, ?, datetime('now'))",
                (item_id, result["added"], result["modified"], result["removed"]),
            )
            await db.commit()
            results.append(SyncResult(
                item_id=item_id, sync_type="transactions", status="success",
                added=result["added"], modified=result["modified"], removed=result["removed"],
            ))
        except Exception as e:
            logger.error(f"Transaction sync failed for {item_id}: {e}")
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
                "VALUES (?, 'transactions', 'error', ?, datetime('now'))",
                (item_id, str(e)),
            )
            await db.commit()
            results.append(SyncResult(
                item_id=item_id, sync_type="transactions", status="error", error=str(e),
            ))

    return results


@router.post("/sync/transactions/{item_id}", response_model=SyncResult)
async def sync_item_transactions(item_id: str, request: Request):
    """Trigger transaction sync for a specific item."""
    db = request.app.state.db
    client = request.app.state.plaid_client

    cursor = await db.execute(
        "SELECT access_token FROM plaid_items WHERE item_id = ?", (item_id,)
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Item not found")

    try:
        result = await sync_transactions(client, db, item_id, row[0])
        await db.execute(
            "INSERT INTO sync_log (item_id, sync_type, status, added_count, modified_count, removed_count, completed_at) "
            "VALUES (?, 'transactions', 'success', ?, ?, ?, datetime('now'))",
            (item_id, result["added"], result["modified"], result["removed"]),
        )
        await db.commit()
        return SyncResult(
            item_id=item_id, sync_type="transactions", status="success",
            added=result["added"], modified=result["modified"], removed=result["removed"],
        )
    except Exception as e:
        await db.execute(
            "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
            "VALUES (?, 'transactions', 'error', ?, datetime('now'))",
            (item_id, str(e)),
        )
        await db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions", response_model=List[TransactionResponse])
async def list_transactions(
    request: Request,
    account_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    pending: Optional[bool] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    search: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
):
    """List transactions with optional filters."""
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
    if pending is not None:
        conditions.append("pending = ?")
        params.append(1 if pending else 0)
    if min_amount is not None:
        conditions.append("amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        conditions.append("amount <= ?")
        params.append(max_amount)
    if search:
        conditions.append("(name LIKE ? OR merchant_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    query = f"SELECT transaction_id, account_id, item_id, amount, iso_currency_code, name, " \
            f"merchant_name, pending, pending_transaction_id, authorized_date, date, " \
            f"category, category_id, personal_finance_category, payment_channel, " \
            f"transaction_type, location " \
            f"FROM transactions{where} ORDER BY date DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor = await db.execute(query, params)
    rows = await cursor.fetchall()

    return [
        TransactionResponse(
            transaction_id=r[0], account_id=r[1], item_id=r[2], amount=r[3],
            iso_currency_code=r[4], name=r[5], merchant_name=r[6],
            pending=bool(r[7]), pending_transaction_id=r[8],
            authorized_date=r[9], date=r[10], category=r[11],
            category_id=r[12], personal_finance_category=r[13],
            payment_channel=r[14], transaction_type=r[15], location=r[16],
        )
        for r in rows
    ]


@router.get("/transactions/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(transaction_id: str, request: Request):
    """Get a single transaction by ID."""
    db = request.app.state.db
    cursor = await db.execute(
        "SELECT transaction_id, account_id, item_id, amount, iso_currency_code, name, "
        "merchant_name, pending, pending_transaction_id, authorized_date, date, "
        "category, category_id, personal_finance_category, payment_channel, "
        "transaction_type, location "
        "FROM transactions WHERE transaction_id = ?",
        (transaction_id,),
    )
    row = await cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return TransactionResponse(
        transaction_id=row[0], account_id=row[1], item_id=row[2], amount=row[3],
        iso_currency_code=row[4], name=row[5], merchant_name=row[6],
        pending=bool(row[7]), pending_transaction_id=row[8],
        authorized_date=row[9], date=row[10], category=row[11],
        category_id=row[12], personal_finance_category=row[13],
        payment_channel=row[14], transaction_type=row[15], location=row[16],
    )
