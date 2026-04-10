"""Background polling service for newly linked items.

After a new item is created, Plaid needs time to fetch transaction and investment
data from the institution. This service polls aggressively (every 3 seconds) to
capture both the initial update (~30 days of transactions, available within minutes)
and the historical update (up to 2 years, available within hours).

Polling schedule:
  - Phase 1 (0-5 min):  every 3 seconds  — catch the initial update quickly
  - Phase 2 (5-30 min): every 30 seconds  — catch stragglers / slow institutions
  - Phase 3 (30m-4h):   every 5 minutes   — wait for historical update
  - After 4 hours:      stop polling (scheduler takes over at SYNC_INTERVAL_HOURS)
"""

import asyncio
import json
import logging
import time
from plaid.exceptions import ApiException

from .transaction_service import sync_transactions
from .investment_service import sync_holdings, sync_investment_transactions

logger = logging.getLogger(__name__)

# Polling phases: (duration_seconds, interval_seconds)
POLL_PHASES = [
    (5 * 60, 3),       # Phase 1: 5 minutes at 3s intervals
    (25 * 60, 30),     # Phase 2: 25 minutes at 30s intervals
    (210 * 60, 5 * 60),  # Phase 3: 3.5 hours at 5min intervals
]


async def _poll_sync(app, item_id: str, access_token: str, products: list):
    """Run a single poll cycle: sync transactions and/or investments."""
    db = app.state.db
    client = app.state.plaid_client
    results = {}

    if "transactions" in products:
        try:
            result = await sync_transactions(client, db, item_id, access_token)
            results["transactions"] = result
            total = result["added"] + result["modified"] + result["removed"]
            if total > 0:
                await db.execute(
                    "INSERT INTO sync_log (item_id, sync_type, status, added_count, modified_count, removed_count, completed_at) "
                    "VALUES (?, 'transactions', 'success', ?, ?, ?, datetime('now'))",
                    (item_id, result["added"], result["modified"], result["removed"]),
                )
                await db.commit()
                logger.info(f"Poll sync {item_id} transactions: +{result['added']} ~{result['modified']} -{result['removed']}")
        except ApiException as e:
            error_body = json.loads(e.body) if e.body else {}
            error_code = error_body.get("error_code", "")
            if error_code == "ITEM_LOGIN_REQUIRED":
                logger.warning(f"Poll: item {item_id} requires re-auth")
                raise
            elif error_code == "PRODUCT_NOT_READY":
                logger.debug(f"Poll: transactions not ready yet for {item_id}")
            else:
                logger.warning(f"Poll: transaction sync error for {item_id}: {error_code} - {e}")
                await db.execute(
                    "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
                    "VALUES (?, 'transactions', 'error', ?, datetime('now'))",
                    (item_id, f"{error_code}: {error_body.get('error_message', str(e))}"),
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Poll: transaction sync failed for {item_id}: {e}")

    if "investments" in products:
        try:
            h_result = await sync_holdings(client, db, item_id, access_token)
            t_result = await sync_investment_transactions(client, db, item_id, access_token)
            results["investments"] = {"holdings": h_result, "transactions": t_result}
            total = h_result["holdings_synced"] + t_result["transactions_synced"]
            if total > 0:
                await db.execute(
                    "INSERT INTO sync_log (item_id, sync_type, status, added_count, completed_at) "
                    "VALUES (?, 'investments', 'success', ?, datetime('now'))",
                    (item_id, total),
                )
                await db.commit()
                logger.info(f"Poll sync {item_id} investments: {h_result['holdings_synced']} holdings, {t_result['transactions_synced']} txns")
        except ApiException as e:
            error_body = json.loads(e.body) if e.body else {}
            error_code = error_body.get("error_code", "")
            if error_code == "ITEM_LOGIN_REQUIRED":
                raise
            elif error_code == "PRODUCT_NOT_READY":
                logger.debug(f"Poll: investments not ready yet for {item_id}")
            else:
                logger.warning(f"Poll: investment sync error for {item_id}: {error_code} - {e}")
                await db.execute(
                    "INSERT INTO sync_log (item_id, sync_type, status, error_message, completed_at) "
                    "VALUES (?, 'investments', 'error', ?, datetime('now'))",
                    (item_id, f"{error_code}: {error_body.get('error_message', str(e))}"),
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Poll: investment sync failed for {item_id}: {e}")

    return results


async def poll_new_item(app, item_id: str, access_token: str, products: list):
    """Background task: aggressively poll a newly linked item for data.

    Investments are snapshot-based — once we successfully fetch holdings, we're done.
    Transactions are cursor-based — Plaid drip-feeds initial (~30 days) then historical
    (up to 2 years), so we poll through all phases waiting for both.
    """
    db = app.state.db
    start_time = time.monotonic()
    got_initial = False
    got_historical = False
    investments_done = "investments" not in products
    consecutive_empty = 0

    # Active products list that shrinks as we finish each one
    active_products = list(products)

    logger.info(f"Starting background poll for new item {item_id} (products: {products})")

    # Get baseline transaction count
    initial_txn_count = 0
    if "transactions" in products:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM transactions WHERE item_id = ?", (item_id,)
        )
        initial_txn_count = (await cursor.fetchone())[0]

    for phase_duration, interval in POLL_PHASES:
        phase_start = time.monotonic()

        while time.monotonic() - phase_start < phase_duration:
            # Check if item still exists (may have been deleted)
            cursor = await db.execute(
                "SELECT item_id FROM plaid_items WHERE item_id = ?", (item_id,)
            )
            if not await cursor.fetchone():
                logger.info(f"Item {item_id} no longer exists, stopping poll")
                return

            try:
                results = await _poll_sync(app, item_id, access_token, active_products)
            except ApiException:
                logger.warning(f"Fatal error polling {item_id}, stopping")
                return

            # --- Investments: done after first successful fetch ---
            if not investments_done and "investments" in results:
                inv = results["investments"]
                if inv["holdings"]["holdings_synced"] > 0 or inv["transactions"]["transactions_synced"] > 0:
                    investments_done = True
                    active_products = [p for p in active_products if p != "investments"]
                    logger.info(f"Investment sync complete for {item_id}, no longer polling investments")

            # --- Transactions: track initial + historical phases ---
            if "transactions" in products:
                if not got_initial:
                    cursor = await db.execute(
                        "SELECT COUNT(*) FROM transactions WHERE item_id = ?", (item_id,)
                    )
                    current_count = (await cursor.fetchone())[0]
                    if current_count > initial_txn_count:
                        got_initial = True
                        await db.execute(
                            "UPDATE plaid_items SET initial_update_complete = 1, updated_at = datetime('now') WHERE item_id = ?",
                            (item_id,),
                        )
                        await db.commit()
                        logger.info(f"Initial update complete for {item_id}: {current_count} transactions")

                if got_initial and not got_historical and "transactions" in results:
                    txn_result = results["transactions"]
                    if txn_result["added"] == 0 and txn_result["modified"] == 0 and txn_result["removed"] == 0:
                        consecutive_empty += 1
                        elapsed = time.monotonic() - start_time
                        if consecutive_empty >= 3 and elapsed > 10 * 60:
                            got_historical = True
                            await db.execute(
                                "UPDATE plaid_items SET historical_update_complete = 1, updated_at = datetime('now') WHERE item_id = ?",
                                (item_id,),
                            )
                            await db.commit()
                            logger.info(f"Historical update complete for {item_id}")
                    else:
                        consecutive_empty = 0
            else:
                # No transactions product — mark both complete once investments are done
                if investments_done and not got_initial:
                    got_initial = True
                    got_historical = True
                    await db.execute(
                        "UPDATE plaid_items SET initial_update_complete = 1, historical_update_complete = 1, "
                        "updated_at = datetime('now') WHERE item_id = ?",
                        (item_id,),
                    )
                    await db.commit()

            # All done — stop early
            if got_historical and investments_done:
                logger.info(f"All updates complete for {item_id}, stopping poll after {time.monotonic() - start_time:.0f}s")
                return

            # If only transactions are left, no need to poll as fast in phase 1
            # (but we still follow the phase schedule)
            await asyncio.sleep(interval)

    elapsed = time.monotonic() - start_time
    logger.info(f"Poll completed for {item_id} after {elapsed:.0f}s (initial={got_initial}, historical={got_historical})")

    # Mark complete at end of polling — scheduler takes over from here
    if not got_historical:
        await db.execute(
            "UPDATE plaid_items SET initial_update_complete = 1, historical_update_complete = 1, "
            "updated_at = datetime('now') WHERE item_id = ?",
            (item_id,),
        )
        await db.commit()
