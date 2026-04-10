"""Tests for transaction sync, reconciliation, and pending->settled transitions."""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from app.services.transaction_service import sync_transactions, upsert_transaction
from tests.conftest import (
    make_mock_transaction, make_mock_account,
)


async def _setup_item(db, item_id="item_1", access_token="access_1"):
    """Helper to insert a plaid_items row."""
    await db.execute(
        "INSERT OR IGNORE INTO plaid_items (item_id, access_token) VALUES (?, ?)",
        (item_id, access_token),
    )
    await db.execute(
        "INSERT OR IGNORE INTO accounts (plaid_account_id, item_id, name, type) VALUES (?, ?, ?, ?)",
        ("acct_1", item_id, "Checking", "depository"),
    )
    await db.commit()


@pytest.mark.asyncio
async def test_first_sync_adds_transactions(db):
    """First sync (no cursor) adds transactions and saves cursor."""
    await _setup_item(db)

    client = MagicMock()
    response = MagicMock()
    response.added = [
        make_mock_transaction("txn_1", amount=50.0),
        make_mock_transaction("txn_2", amount=25.0),
    ]
    response.modified = []
    response.removed = []
    response.next_cursor = "cursor_abc"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    result = await sync_transactions(client, db, "item_1", "access_1")
    assert result["added"] == 2
    assert result["modified"] == 0
    assert result["removed"] == 0

    # Verify cursor saved
    cursor = await db.execute("SELECT transaction_cursor FROM plaid_items WHERE item_id = 'item_1'")
    row = await cursor.fetchone()
    assert row[0] == "cursor_abc"

    # Verify transactions in DB
    cursor = await db.execute("SELECT COUNT(*) FROM transactions")
    assert (await cursor.fetchone())[0] == 2


@pytest.mark.asyncio
async def test_incremental_sync_adds_new(db):
    """Incremental sync with existing cursor adds new transactions."""
    await _setup_item(db)
    await db.execute(
        "UPDATE plaid_items SET transaction_cursor = 'existing_cursor' WHERE item_id = 'item_1'"
    )
    await db.commit()

    client = MagicMock()
    response = MagicMock()
    response.added = [make_mock_transaction("txn_new")]
    response.modified = []
    response.removed = []
    response.next_cursor = "cursor_2"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    result = await sync_transactions(client, db, "item_1", "access_1")
    assert result["added"] == 1


@pytest.mark.asyncio
async def test_modified_transaction_updates(db):
    """Modified transaction updates existing row."""
    await _setup_item(db)

    # Insert initial transaction
    txn = make_mock_transaction("txn_1", amount=50.0, name="Old Name")
    await upsert_transaction(db, txn, "item_1")
    await db.commit()

    # Sync with modification
    client = MagicMock()
    modified_txn = make_mock_transaction("txn_1", amount=55.0, name="Updated Name")
    response = MagicMock()
    response.added = []
    response.modified = [modified_txn]
    response.removed = []
    response.next_cursor = "cursor_mod"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    result = await sync_transactions(client, db, "item_1", "access_1")
    assert result["modified"] == 1

    cursor = await db.execute("SELECT amount, name FROM transactions WHERE transaction_id = 'txn_1'")
    row = await cursor.fetchone()
    assert row[0] == 55.0
    assert row[1] == "Updated Name"


@pytest.mark.asyncio
async def test_removed_transaction_deleted(db):
    """Removed transaction is deleted from DB."""
    await _setup_item(db)

    txn = make_mock_transaction("txn_del")
    await upsert_transaction(db, txn, "item_1")
    await db.commit()

    client = MagicMock()
    removed = MagicMock()
    removed.transaction_id = "txn_del"
    response = MagicMock()
    response.added = []
    response.modified = []
    response.removed = [removed]
    response.next_cursor = "cursor_rem"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    result = await sync_transactions(client, db, "item_1", "access_1")
    assert result["removed"] == 1

    cursor = await db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_id = 'txn_del'")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_pending_to_settled_via_modification(db):
    """Pending transaction settles via modification (same transaction_id)."""
    await _setup_item(db)

    # Insert pending
    txn = make_mock_transaction("txn_p1", pending=True, amount=30.0)
    await upsert_transaction(db, txn, "item_1")
    await db.commit()

    # Settle via modification
    client = MagicMock()
    settled = make_mock_transaction("txn_p1", pending=False, amount=30.0)
    response = MagicMock()
    response.added = []
    response.modified = [settled]
    response.removed = []
    response.next_cursor = "cursor_settle"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    await sync_transactions(client, db, "item_1", "access_1")

    cursor = await db.execute("SELECT pending FROM transactions WHERE transaction_id = 'txn_p1'")
    row = await cursor.fetchone()
    assert row[0] == 0  # Now settled


@pytest.mark.asyncio
async def test_pending_to_settled_via_remove_and_add(db):
    """Pending transaction settles via remove + add (new ID, linked by pending_transaction_id)."""
    await _setup_item(db)

    # Insert pending transaction
    txn = make_mock_transaction("txn_pending", pending=True, amount=40.0)
    await upsert_transaction(db, txn, "item_1")
    await db.commit()

    # Sync: remove pending, add settled with pending_transaction_id pointing back
    client = MagicMock()
    removed = MagicMock()
    removed.transaction_id = "txn_pending"

    settled = make_mock_transaction(
        "txn_settled", pending=False, amount=40.0,
        pending_transaction_id="txn_pending",
    )

    response = MagicMock()
    response.added = [settled]
    response.modified = []
    response.removed = [removed]
    response.next_cursor = "cursor_settle2"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    await sync_transactions(client, db, "item_1", "access_1")

    # Pending should be gone
    cursor = await db.execute("SELECT COUNT(*) FROM transactions WHERE transaction_id = 'txn_pending'")
    assert (await cursor.fetchone())[0] == 0

    # Settled should exist
    cursor = await db.execute("SELECT pending, pending_transaction_id FROM transactions WHERE transaction_id = 'txn_settled'")
    row = await cursor.fetchone()
    assert row[0] == 0
    assert row[1] == "txn_pending"


@pytest.mark.asyncio
async def test_cursor_persists_across_syncs(db):
    """Cursor is saved and loaded correctly across syncs."""
    await _setup_item(db)

    client = MagicMock()
    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    # First sync
    resp1 = MagicMock()
    resp1.added = [make_mock_transaction("txn_a")]
    resp1.modified = []
    resp1.removed = []
    resp1.next_cursor = "cursor_1"
    resp1.has_more = False
    client.transactions_sync.return_value = resp1

    await sync_transactions(client, db, "item_1", "access_1")

    # Second sync - should use cursor_1
    resp2 = MagicMock()
    resp2.added = []
    resp2.modified = []
    resp2.removed = []
    resp2.next_cursor = "cursor_2"
    resp2.has_more = False
    client.transactions_sync.return_value = resp2

    await sync_transactions(client, db, "item_1", "access_1")

    # Verify cursor_1 was passed in the second call
    calls = client.transactions_sync.call_args_list
    assert len(calls) == 2
    assert calls[1][0][0].cursor == "cursor_1"


@pytest.mark.asyncio
async def test_empty_sync_response(db):
    """Empty sync response doesn't error."""
    await _setup_item(db)

    client = MagicMock()
    response = MagicMock()
    response.added = []
    response.modified = []
    response.removed = []
    response.next_cursor = "cursor_empty"
    response.has_more = False
    client.transactions_sync.return_value = response

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    result = await sync_transactions(client, db, "item_1", "access_1")
    assert result["added"] == 0
    assert result["modified"] == 0
    assert result["removed"] == 0


@pytest.mark.asyncio
async def test_pagination_has_more(db):
    """Pagination: has_more=true then false."""
    await _setup_item(db)

    client = MagicMock()
    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account()]
    client.accounts_get.return_value = accounts_resp

    # Page 1
    resp1 = MagicMock()
    resp1.added = [make_mock_transaction("txn_p1")]
    resp1.modified = []
    resp1.removed = []
    resp1.next_cursor = "cursor_page1"
    resp1.has_more = True

    # Page 2
    resp2 = MagicMock()
    resp2.added = [make_mock_transaction("txn_p2")]
    resp2.modified = []
    resp2.removed = []
    resp2.next_cursor = "cursor_page2"
    resp2.has_more = False

    client.transactions_sync.side_effect = [resp1, resp2]

    result = await sync_transactions(client, db, "item_1", "access_1")
    assert result["added"] == 2
    assert client.transactions_sync.call_count == 2
