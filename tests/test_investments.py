"""Tests for investment holdings and transaction sync."""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from app.services.investment_service import sync_holdings, sync_investment_transactions
from tests.conftest import (
    make_mock_holding, make_mock_security, make_mock_investment_txn, make_mock_account,
)


async def _setup_item(db, item_id="item_1"):
    await db.execute(
        "INSERT OR IGNORE INTO plaid_items (item_id, access_token) VALUES (?, ?)",
        (item_id, "access_1"),
    )
    await db.execute(
        "INSERT OR IGNORE INTO accounts (plaid_account_id, item_id, name, type) VALUES (?, ?, ?, ?)",
        ("acct_inv", item_id, "IRA", "investment"),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_holdings_snapshot_upsert(db):
    """Holdings are upserted correctly on sync."""
    await _setup_item(db)

    client = MagicMock()
    response = MagicMock()
    response.holdings = [
        make_mock_holding(security_id="sec_1", quantity=10, value=1500),
        make_mock_holding(security_id="sec_2", quantity=5, value=500),
    ]
    response.securities = [
        make_mock_security("sec_1", "AAPL"),
        make_mock_security("sec_2", "GOOG"),
    ]
    response.accounts = [make_mock_account("acct_inv", "IRA", "investment")]
    client.investments_holdings_get.return_value = response

    result = await sync_holdings(client, db, "item_1", "access_1")
    assert result["holdings_synced"] == 2
    assert result["securities_synced"] == 2

    cursor = await db.execute("SELECT COUNT(*) FROM investment_holdings")
    assert (await cursor.fetchone())[0] == 2

    cursor = await db.execute("SELECT COUNT(*) FROM securities")
    assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_closed_position_removed(db):
    """Holdings not in response (closed positions) are removed."""
    await _setup_item(db)

    client = MagicMock()

    # First sync: 2 holdings
    resp1 = MagicMock()
    resp1.holdings = [
        make_mock_holding(security_id="sec_1", quantity=10),
        make_mock_holding(security_id="sec_2", quantity=5),
    ]
    resp1.securities = [make_mock_security("sec_1"), make_mock_security("sec_2")]
    resp1.accounts = [make_mock_account("acct_inv", "IRA", "investment")]
    client.investments_holdings_get.return_value = resp1
    await sync_holdings(client, db, "item_1", "access_1")

    # Second sync: only 1 holding (sec_2 closed)
    resp2 = MagicMock()
    resp2.holdings = [make_mock_holding(security_id="sec_1", quantity=10)]
    resp2.securities = [make_mock_security("sec_1")]
    resp2.accounts = [make_mock_account("acct_inv", "IRA", "investment")]
    client.investments_holdings_get.return_value = resp2
    await sync_holdings(client, db, "item_1", "access_1")

    cursor = await db.execute("SELECT COUNT(*) FROM investment_holdings")
    assert (await cursor.fetchone())[0] == 1

    cursor = await db.execute("SELECT security_id FROM investment_holdings")
    row = await cursor.fetchone()
    assert row[0] == "sec_1"


@pytest.mark.asyncio
async def test_securities_populated(db):
    """Securities reference table is populated from sync."""
    await _setup_item(db)

    client = MagicMock()
    response = MagicMock()
    response.holdings = [make_mock_holding(security_id="sec_apple")]
    response.securities = [make_mock_security("sec_apple", "AAPL", "Apple Inc")]
    response.accounts = [make_mock_account("acct_inv", "IRA", "investment")]
    client.investments_holdings_get.return_value = response

    await sync_holdings(client, db, "item_1", "access_1")

    cursor = await db.execute("SELECT ticker_symbol, name FROM securities WHERE security_id = 'sec_apple'")
    row = await cursor.fetchone()
    assert row[0] == "AAPL"
    assert row[1] == "Apple Inc"


@pytest.mark.asyncio
async def test_investment_transaction_pagination(db):
    """Investment transactions are fetched with pagination."""
    await _setup_item(db)

    client = MagicMock()

    # Page 1
    resp1 = MagicMock()
    resp1.investment_transactions = [
        make_mock_investment_txn("inv_1"),
        make_mock_investment_txn("inv_2"),
    ]
    resp1.total_investment_transactions = 3
    resp1.securities = [make_mock_security("sec_1")]

    # Page 2
    resp2 = MagicMock()
    resp2.investment_transactions = [make_mock_investment_txn("inv_3")]
    resp2.total_investment_transactions = 3
    resp2.securities = []

    client.investments_transactions_get.side_effect = [resp1, resp2]

    result = await sync_investment_transactions(client, db, "item_1", "access_1")
    assert result["transactions_synced"] == 3
    assert client.investments_transactions_get.call_count == 2


@pytest.mark.asyncio
async def test_incremental_investment_sync_date_window(db):
    """Incremental sync uses last_sync_date - 7 days as start_date."""
    await _setup_item(db)
    await db.execute(
        "UPDATE plaid_items SET investment_last_sync_date = '2024-01-15' WHERE item_id = 'item_1'"
    )
    await db.commit()

    client = MagicMock()
    response = MagicMock()
    response.investment_transactions = []
    response.total_investment_transactions = 0
    response.securities = []
    client.investments_transactions_get.return_value = response

    await sync_investment_transactions(client, db, "item_1", "access_1")

    # Check the request used start_date = 2024-01-08 (15 - 7)
    call_args = client.investments_transactions_get.call_args
    from datetime import date
    assert call_args[0][0].start_date == date(2024, 1, 8)
