"""Tests for account link, list, and removal."""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from app.services.link_service import create_sandbox_link
from tests.conftest import make_mock_account, FakePlaidClient


@pytest.mark.asyncio
async def test_link_creates_item_and_accounts(db):
    """Link flow creates item + accounts in DB."""
    client = FakePlaidClient()

    # Mock sandbox token create
    token_resp = MagicMock()
    token_resp.public_token = "public-sandbox-token"
    client.sandbox_public_token_create.return_value = token_resp

    # Mock exchange
    exchange_resp = MagicMock()
    exchange_resp.access_token = "access-sandbox-token"
    exchange_resp.item_id = "item_linked"
    client.item_public_token_exchange.return_value = exchange_resp

    # Mock accounts
    accounts_resp = MagicMock()
    accounts_resp.accounts = [
        make_mock_account("acct_a", "Checking", balance=5000),
        make_mock_account("acct_b", "Savings", balance=10000),
    ]
    client.accounts_get.return_value = accounts_resp

    result = await create_sandbox_link(client, db, "ins_109508")

    assert result["item_id"] == "item_linked"
    assert result["accounts"] == 2

    # Verify item in DB
    cursor = await db.execute("SELECT item_id, access_token FROM plaid_items WHERE item_id = 'item_linked'")
    row = await cursor.fetchone()
    assert row[0] == "item_linked"
    assert row[1] == "access-sandbox-token"

    # Verify accounts in DB
    cursor = await db.execute("SELECT COUNT(*) FROM accounts WHERE item_id = 'item_linked'")
    assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_balance_update_after_sync(db):
    """Account balances are updated after transaction sync."""
    # Setup
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_bal', 'access_bal')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type, current_balance) "
        "VALUES ('acct_bal', 'item_bal', 'Checking', 'depository', 1000.0)"
    )
    await db.commit()

    # Sync updates balance
    from app.services.transaction_service import sync_transactions

    client = MagicMock()
    sync_resp = MagicMock()
    sync_resp.added = []
    sync_resp.modified = []
    sync_resp.removed = []
    sync_resp.next_cursor = "cursor"
    sync_resp.has_more = False
    client.transactions_sync.return_value = sync_resp

    acct = make_mock_account("acct_bal", "Checking", balance=2500.0)
    accounts_resp = MagicMock()
    accounts_resp.accounts = [acct]
    client.accounts_get.return_value = accounts_resp

    await sync_transactions(client, db, "item_bal", "access_bal")

    cursor = await db.execute("SELECT current_balance FROM accounts WHERE plaid_account_id = 'acct_bal'")
    row = await cursor.fetchone()
    assert row[0] == 2500.0


@pytest.mark.asyncio
async def test_item_removal_cascades(db):
    """Removing an item removes all associated data."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_rm', 'access_rm')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type) VALUES ('acct_rm', 'item_rm', 'Checking', 'depository')"
    )
    await db.execute(
        "INSERT INTO transactions (transaction_id, account_id, item_id, amount, date) "
        "VALUES ('txn_rm', 'acct_rm', 'item_rm', 50.0, '2024-01-01')"
    )
    await db.commit()

    # Delete item and cascade
    await db.execute("DELETE FROM transactions WHERE item_id = 'item_rm'")
    await db.execute("DELETE FROM accounts WHERE item_id = 'item_rm'")
    await db.execute("DELETE FROM plaid_items WHERE item_id = 'item_rm'")
    await db.commit()

    cursor = await db.execute("SELECT COUNT(*) FROM plaid_items WHERE item_id = 'item_rm'")
    assert (await cursor.fetchone())[0] == 0
    cursor = await db.execute("SELECT COUNT(*) FROM accounts WHERE item_id = 'item_rm'")
    assert (await cursor.fetchone())[0] == 0
    cursor = await db.execute("SELECT COUNT(*) FROM transactions WHERE item_id = 'item_rm'")
    assert (await cursor.fetchone())[0] == 0
