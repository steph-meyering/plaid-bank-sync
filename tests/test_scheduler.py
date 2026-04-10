"""Tests for the sync scheduler."""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from app.sync.scheduler import run_full_sync, create_scheduler


@pytest.mark.asyncio
async def test_scheduler_skips_when_no_items(db):
    """Scheduler does nothing when no items are linked."""
    app = MagicMock()
    app.state.db = db
    app.state.plaid_client = MagicMock()

    await run_full_sync(app)

    # No errors, no sync log entries
    cursor = await db.execute("SELECT COUNT(*) FROM sync_log")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_scheduler_syncs_items(db):
    """Scheduler runs sync for each linked item."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_sched', 'access_sched')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type) VALUES ('acct_sched', 'item_sched', 'Checking', 'depository')"
    )
    await db.commit()

    app = MagicMock()
    app.state.db = db

    client = MagicMock()
    # Transaction sync response
    sync_resp = MagicMock()
    sync_resp.added = []
    sync_resp.modified = []
    sync_resp.removed = []
    sync_resp.next_cursor = "cursor"
    sync_resp.has_more = False
    client.transactions_sync.return_value = sync_resp

    # Accounts response
    from tests.conftest import make_mock_account
    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account("acct_sched")]
    client.accounts_get.return_value = accounts_resp

    # Holdings response
    holdings_resp = MagicMock()
    holdings_resp.holdings = []
    holdings_resp.securities = []
    holdings_resp.accounts = [make_mock_account("acct_sched")]
    client.investments_holdings_get.return_value = holdings_resp

    # Investment transactions response
    inv_txn_resp = MagicMock()
    inv_txn_resp.investment_transactions = []
    inv_txn_resp.total_investment_transactions = 0
    inv_txn_resp.securities = []
    client.investments_transactions_get.return_value = inv_txn_resp

    app.state.plaid_client = client

    await run_full_sync(app)

    # Should have sync log entries
    cursor = await db.execute("SELECT COUNT(*) FROM sync_log")
    assert (await cursor.fetchone())[0] >= 1


@pytest.mark.asyncio
async def test_per_item_error_isolation(db):
    """If one item fails, others still sync."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_ok', 'access_ok')"
    )
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_fail', 'access_fail')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type) VALUES ('acct_ok', 'item_ok', 'Checking', 'depository')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type) VALUES ('acct_fail', 'item_fail', 'Checking', 'depository')"
    )
    await db.commit()

    app = MagicMock()
    app.state.db = db

    from tests.conftest import make_mock_account
    from plaid.exceptions import ApiException

    client = MagicMock()

    # First item succeeds, second fails
    sync_resp_ok = MagicMock()
    sync_resp_ok.added = []
    sync_resp_ok.modified = []
    sync_resp_ok.removed = []
    sync_resp_ok.next_cursor = "cursor"
    sync_resp_ok.has_more = False

    def txn_sync_side_effect(req):
        if req.access_token == "access_fail":
            raise Exception("Plaid error")
        return sync_resp_ok

    client.transactions_sync.side_effect = txn_sync_side_effect

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account("acct_ok")]
    client.accounts_get.return_value = accounts_resp

    holdings_resp = MagicMock()
    holdings_resp.holdings = []
    holdings_resp.securities = []
    holdings_resp.accounts = []
    client.investments_holdings_get.return_value = holdings_resp

    inv_txn_resp = MagicMock()
    inv_txn_resp.investment_transactions = []
    inv_txn_resp.total_investment_transactions = 0
    inv_txn_resp.securities = []
    client.investments_transactions_get.return_value = inv_txn_resp

    app.state.plaid_client = client

    # Should not raise
    await run_full_sync(app)

    # Both items have log entries
    cursor = await db.execute("SELECT COUNT(*) FROM sync_log")
    count = (await cursor.fetchone())[0]
    assert count >= 2  # At least one success and one error

    # Check we have at least one error log
    cursor = await db.execute("SELECT COUNT(*) FROM sync_log WHERE status = 'error'")
    assert (await cursor.fetchone())[0] >= 1


def test_create_scheduler():
    """Scheduler creates with correct configuration."""
    app = MagicMock()
    scheduler = create_scheduler(app, interval_hours=6)
    jobs = scheduler.get_jobs()
    assert len(jobs) == 2  # interval job + initial delayed job
