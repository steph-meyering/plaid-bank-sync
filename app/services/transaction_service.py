"""Transaction sync service using Plaid /transactions/sync."""

import json
import logging
import time
from datetime import datetime
from plaid.api import plaid_api
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.exceptions import ApiException
import aiosqlite

logger = logging.getLogger(__name__)


def _txn_to_row(txn, item_id: str) -> tuple:
    """Convert a Plaid transaction object to a database row tuple."""
    category = None
    if hasattr(txn, "category") and txn.category:
        category = json.dumps(txn.category) if isinstance(txn.category, list) else str(txn.category)

    pfc = None
    if hasattr(txn, "personal_finance_category") and txn.personal_finance_category:
        try:
            pfc = json.dumps({
                "primary": getattr(txn.personal_finance_category, "primary", None),
                "detailed": getattr(txn.personal_finance_category, "detailed", None),
            })
        except Exception:
            pfc = str(txn.personal_finance_category)

    location = None
    if hasattr(txn, "location") and txn.location:
        try:
            loc = txn.location
            location = json.dumps({
                "city": getattr(loc, "city", None),
                "region": getattr(loc, "region", None),
                "postal_code": getattr(loc, "postal_code", None),
                "country": getattr(loc, "country", None),
            })
        except Exception:
            location = str(txn.location)

    return (
        txn.transaction_id,
        txn.account_id,
        item_id,
        txn.amount,
        getattr(txn, "iso_currency_code", "USD"),
        getattr(txn, "name", None),
        getattr(txn, "merchant_name", None),
        1 if txn.pending else 0,
        getattr(txn, "pending_transaction_id", None),
        str(txn.authorized_date) if getattr(txn, "authorized_date", None) else None,
        str(txn.date),
        category,
        getattr(txn, "category_id", None),
        pfc,
        getattr(txn, "payment_channel", None),
        getattr(txn, "transaction_type", None),
        location,
    )


async def upsert_transaction(db: aiosqlite.Connection, txn, item_id: str):
    """Insert or update a transaction."""
    row = _txn_to_row(txn, item_id)

    # If this settled transaction references a pending one, remove the pending row
    pending_txn_id = getattr(txn, "pending_transaction_id", None)
    if pending_txn_id and not txn.pending:
        await db.execute(
            "DELETE FROM transactions WHERE transaction_id = ? AND pending = 1",
            (pending_txn_id,),
        )

    await db.execute(
        "INSERT OR REPLACE INTO transactions "
        "(transaction_id, account_id, item_id, amount, iso_currency_code, name, "
        "merchant_name, pending, pending_transaction_id, authorized_date, date, "
        "category, category_id, personal_finance_category, payment_channel, "
        "transaction_type, location, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        row,
    )


async def sync_transactions(
    client: plaid_api.PlaidApi,
    db: aiosqlite.Connection,
    item_id: str,
    access_token: str,
) -> dict:
    """Run the full /transactions/sync loop for an item."""
    # Load cursor
    cursor_row = await db.execute(
        "SELECT transaction_cursor FROM plaid_items WHERE item_id = ?", (item_id,)
    )
    row = await cursor_row.fetchone()
    cursor = row[0] if row and row[0] else ""

    added_count = 0
    modified_count = 0
    removed_count = 0
    has_more = True

    while has_more:
        request = TransactionsSyncRequest(
            access_token=access_token,
            cursor=cursor,
            count=500,
        )

        try:
            response = client.transactions_sync(request)
        except ApiException as e:
            error_body = json.loads(e.body) if e.body else {}
            error_code = error_body.get("error_code", "")

            if error_code == "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION":
                logger.warning(f"Mutation during pagination for {item_id}, restarting from saved cursor")
                cursor_row = await db.execute(
                    "SELECT transaction_cursor FROM plaid_items WHERE item_id = ?", (item_id,)
                )
                row = await cursor_row.fetchone()
                cursor = row[0] if row and row[0] else ""
                continue
            raise

        for txn in response.added:
            await upsert_transaction(db, txn, item_id)
            added_count += 1

        for txn in response.modified:
            await upsert_transaction(db, txn, item_id)
            modified_count += 1

        for txn in response.removed:
            txn_id = txn.transaction_id if hasattr(txn, "transaction_id") else txn.get("transaction_id")
            if txn_id:
                await db.execute(
                    "DELETE FROM transactions WHERE transaction_id = ?", (txn_id,)
                )
                removed_count += 1

        cursor = response.next_cursor
        has_more = response.has_more

    # Save cursor
    await db.execute(
        "UPDATE plaid_items SET transaction_cursor = ?, updated_at = datetime('now') WHERE item_id = ?",
        (cursor, item_id),
    )
    await db.commit()

    # Update account balances
    try:
        accounts_request = AccountsGetRequest(access_token=access_token)
        accounts_response = client.accounts_get(accounts_request)
        for acct in accounts_response.accounts:
            balances = acct.balances
            await db.execute(
                "UPDATE accounts SET current_balance = ?, available_balance = ?, "
                "currency_code = ?, updated_at = datetime('now') WHERE plaid_account_id = ?",
                (
                    balances.current if balances else None,
                    balances.available if balances else None,
                    balances.iso_currency_code if balances else "USD",
                    acct.account_id,
                ),
            )
        await db.commit()
    except Exception as e:
        logger.warning(f"Failed to update balances for {item_id}: {e}")

    return {
        "item_id": item_id,
        "added": added_count,
        "modified": modified_count,
        "removed": removed_count,
    }
