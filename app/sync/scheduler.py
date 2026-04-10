"""APScheduler configuration for periodic syncs."""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from ..services.transaction_service import sync_transactions
from ..services.investment_service import sync_holdings, sync_investment_transactions

logger = logging.getLogger(__name__)


async def run_full_sync(app):
    """Run transaction + investment sync for all linked items."""
    db = app.state.db
    client = app.state.plaid_client

    cursor = await db.execute("SELECT item_id, access_token FROM plaid_items")
    items = await cursor.fetchall()

    if not items:
        logger.info("No linked items, skipping scheduled sync")
        return

    for item in items:
        item_id, access_token = item[0], item[1]

        # Transaction sync
        try:
            result = await sync_transactions(client, db, item_id, access_token)
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, added_count, modified_count, removed_count, completed_at) "
                "VALUES (?, 'transactions', 'success', ?, ?, ?, datetime('now'))",
                (item_id, result["added"], result["modified"], result["removed"]),
            )
            await db.commit()
            logger.info(f"Transaction sync for {item_id}: +{result['added']} ~{result['modified']} -{result['removed']}")
        except Exception as e:
            logger.error(f"Transaction sync failed for {item_id}: {e}")
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
                "VALUES (?, 'transactions', 'error', ?, datetime('now'))",
                (item_id, str(e)),
            )
            await db.commit()

        # Investment sync
        try:
            holdings_result = await sync_holdings(client, db, item_id, access_token)
            txn_result = await sync_investment_transactions(client, db, item_id, access_token)
            total = holdings_result["holdings_synced"] + txn_result["transactions_synced"]
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, added_count, completed_at) "
                "VALUES (?, 'investments', 'success', ?, datetime('now'))",
                (item_id, total),
            )
            await db.commit()
            logger.info(f"Investment sync for {item_id}: {holdings_result['holdings_synced']} holdings, {txn_result['transactions_synced']} transactions")
        except Exception as e:
            logger.error(f"Investment sync failed for {item_id}: {e}")
            await db.execute(
                "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
                "VALUES (?, 'investments', 'error', ?, datetime('now'))",
                (item_id, str(e)),
            )
            await db.commit()


def create_scheduler(app, interval_hours: int) -> AsyncIOScheduler:
    """Create and configure the sync scheduler."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_full_sync,
        "interval",
        hours=interval_hours,
        args=[app],
        id="full_sync",
        name="Full Plaid Sync",
        replace_existing=True,
    )
    # Initial sync after 10 second delay
    from datetime import datetime, timedelta
    scheduler.add_job(
        run_full_sync,
        "date",
        run_date=datetime.now() + timedelta(seconds=10),
        args=[app],
        id="initial_sync",
        name="Initial Plaid Sync",
    )
    return scheduler
