"""Investment sync service for holdings and investment transactions."""

import json
import logging
from datetime import date, timedelta
from plaid.api import plaid_api
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
import aiosqlite

logger = logging.getLogger(__name__)


async def _upsert_securities(db: aiosqlite.Connection, securities: list):
    """Upsert securities reference data."""
    for sec in securities:
        await db.execute(
            "INSERT OR REPLACE INTO securities "
            "(security_id, isin, cusip, sedol, ticker_symbol, name, type, "
            "close_price, close_price_as_of, currency_code, is_cash_equivalent, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (
                sec.security_id,
                getattr(sec, "isin", None),
                getattr(sec, "cusip", None),
                getattr(sec, "sedol", None),
                getattr(sec, "ticker_symbol", None),
                getattr(sec, "name", None),
                str(getattr(sec, "type", None)) if getattr(sec, "type", None) else None,
                getattr(sec, "close_price", None),
                str(getattr(sec, "close_price_as_of", None)) if getattr(sec, "close_price_as_of", None) else None,
                getattr(sec, "iso_currency_code", None) or getattr(sec, "unofficial_currency_code", None) or "USD",
                1 if getattr(sec, "is_cash_equivalent", False) else 0,
            ),
        )


async def sync_holdings(
    client: plaid_api.PlaidApi,
    db: aiosqlite.Connection,
    item_id: str,
    access_token: str,
) -> dict:
    """Sync investment holdings for an item. Full snapshot replace."""
    request = InvestmentsHoldingsGetRequest(access_token=access_token)
    response = client.investments_holdings_get(request)

    # Upsert securities
    await _upsert_securities(db, response.securities)

    # Track which (account_id, security_id) pairs are in the response
    seen_keys = set()

    # Get accounts belonging to this item
    item_account_ids = set()
    for acct in response.accounts:
        item_account_ids.add(acct.account_id)

    for holding in response.holdings:
        seen_keys.add((holding.account_id, holding.security_id))
        await db.execute(
            "INSERT INTO investment_holdings "
            "(account_id, item_id, security_id, institution_price, institution_price_as_of, "
            "institution_value, cost_basis, quantity, currency_code, synced_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(account_id, security_id) DO UPDATE SET "
            "institution_price=excluded.institution_price, "
            "institution_price_as_of=excluded.institution_price_as_of, "
            "institution_value=excluded.institution_value, "
            "cost_basis=excluded.cost_basis, "
            "quantity=excluded.quantity, "
            "currency_code=excluded.currency_code, "
            "synced_at=excluded.synced_at",
            (
                holding.account_id,
                item_id,
                holding.security_id,
                getattr(holding, "institution_price", None),
                str(getattr(holding, "institution_price_as_of", None)) if getattr(holding, "institution_price_as_of", None) else None,
                getattr(holding, "institution_value", None),
                getattr(holding, "cost_basis", None),
                holding.quantity,
                getattr(holding, "iso_currency_code", None) or getattr(holding, "unofficial_currency_code", None) or "USD",
            ),
        )

    # Remove holdings for this item's accounts that are no longer in the response (closed positions)
    if item_account_ids:
        placeholders = ",".join("?" for _ in item_account_ids)
        cursor = await db.execute(
            f"SELECT account_id, security_id FROM investment_holdings "
            f"WHERE account_id IN ({placeholders}) AND item_id = ?",
            list(item_account_ids) + [item_id],
        )
        existing = await cursor.fetchall()
        for row in existing:
            if (row[0], row[1]) not in seen_keys:
                await db.execute(
                    "DELETE FROM investment_holdings WHERE account_id = ? AND security_id = ?",
                    (row[0], row[1]),
                )

    await db.commit()

    return {
        "item_id": item_id,
        "holdings_synced": len(response.holdings),
        "securities_synced": len(response.securities),
    }


async def sync_investment_transactions(
    client: plaid_api.PlaidApi,
    db: aiosqlite.Connection,
    item_id: str,
    access_token: str,
) -> dict:
    """Sync investment transactions for an item with pagination."""
    # Determine date window
    cursor = await db.execute(
        "SELECT investment_last_sync_date FROM plaid_items WHERE item_id = ?", (item_id,)
    )
    row = await cursor.fetchone()
    last_sync = row[0] if row and row[0] else None

    if last_sync:
        start_date = date.fromisoformat(last_sync) - timedelta(days=7)
    else:
        start_date = date.today() - timedelta(days=730)  # ~2 years

    end_date = date.today()

    offset = 0
    total = None
    added_count = 0

    while total is None or offset < total:
        request = InvestmentsTransactionsGetRequest(
            access_token=access_token,
            start_date=start_date,
            end_date=end_date,
            options={"offset": offset, "count": 500},
        )
        response = client.investments_transactions_get(request)
        total = response.total_investment_transactions

        # Upsert securities
        await _upsert_securities(db, response.securities)

        for txn in response.investment_transactions:
            await db.execute(
                "INSERT OR REPLACE INTO investment_transactions "
                "(investment_transaction_id, account_id, item_id, security_id, date, "
                "name, quantity, amount, price, fees, type, subtype, currency_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    txn.investment_transaction_id,
                    txn.account_id,
                    item_id,
                    getattr(txn, "security_id", None),
                    str(txn.date),
                    getattr(txn, "name", None),
                    getattr(txn, "quantity", None),
                    getattr(txn, "amount", None),
                    getattr(txn, "price", None),
                    getattr(txn, "fees", None),
                    str(getattr(txn, "type", None)) if getattr(txn, "type", None) else None,
                    str(getattr(txn, "subtype", None)) if getattr(txn, "subtype", None) else None,
                    getattr(txn, "iso_currency_code", None) or getattr(txn, "unofficial_currency_code", None) or "USD",
                ),
            )
            added_count += 1

        offset += len(response.investment_transactions)

    # Update last sync date
    await db.execute(
        "UPDATE plaid_items SET investment_last_sync_date = ?, updated_at = datetime('now') WHERE item_id = ?",
        (str(end_date), item_id),
    )
    await db.commit()

    return {
        "item_id": item_id,
        "transactions_synced": added_count,
    }
