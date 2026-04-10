"""Tests for Plaid Link token creation, exchange, and update endpoints."""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from tests.conftest import make_mock_account


@pytest.mark.asyncio
async def test_create_link_token(app_client, plaid_client):
    """POST /api/link/token returns a link token."""
    link_resp = MagicMock()
    link_resp.link_token = "link-sandbox-abc123"
    link_resp.expiration = "2024-01-15T12:00:00Z"
    plaid_client.link_token_create.return_value = link_resp

    resp = await app_client.post("/api/link/token", json={"product": "transactions"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["link_token"] == "link-sandbox-abc123"
    assert "expiration" in data


@pytest.mark.asyncio
async def test_create_link_token_with_optional(app_client, plaid_client):
    """POST /api/link/token with optional product."""
    link_resp = MagicMock()
    link_resp.link_token = "link-sandbox-opt"
    link_resp.expiration = "2024-01-15T12:00:00Z"
    plaid_client.link_token_create.return_value = link_resp

    resp = await app_client.post("/api/link/token", json={"product": "transactions", "optional_product": "investments"})
    assert resp.status_code == 200
    # Verify optional_products was passed to Plaid
    call_args = plaid_client.link_token_create.call_args[0][0]
    assert hasattr(call_args, "optional_products")


@pytest.mark.asyncio
async def test_create_link_token_invalid_product(app_client):
    """POST /api/link/token with invalid product returns 422."""
    resp = await app_client.post("/api/link/token", json={"product": "invalid"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_link_token_missing_product(app_client):
    """POST /api/link/token without product returns 422."""
    resp = await app_client.post("/api/link/token", json={})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_exchange_token_success(app_client, db, plaid_client):
    """POST /api/link/exchange stores item and accounts."""
    exchange_resp = MagicMock()
    exchange_resp.access_token = "access-sandbox-xyz"
    exchange_resp.item_id = "item_new"
    plaid_client.item_public_token_exchange.return_value = exchange_resp

    accounts_resp = MagicMock()
    accounts_resp.accounts = [
        make_mock_account("acct_1", "Checking", balance=5000),
        make_mock_account("acct_2", "Savings", balance=10000),
    ]
    plaid_client.accounts_get.return_value = accounts_resp

    # Mock sync calls to avoid errors
    sync_resp = MagicMock()
    sync_resp.added = []
    sync_resp.modified = []
    sync_resp.removed = []
    sync_resp.next_cursor = "cursor"
    sync_resp.has_more = False
    plaid_client.transactions_sync.return_value = sync_resp

    resp = await app_client.post("/api/link/exchange", json={
        "public_token": "public-sandbox-token",
        "institution_id": "ins_1",
        "institution_name": "Test Bank",
        "products": ["transactions"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["item_id"] == "item_new"
    assert len(data["accounts"]) == 2
    assert data["duplicate"] is False

    # Verify DB
    cursor = await db.execute("SELECT item_id, products FROM plaid_items WHERE item_id = 'item_new'")
    row = await cursor.fetchone()
    assert row[0] == "item_new"
    assert row[1] == "transactions"


@pytest.mark.asyncio
async def test_exchange_token_duplicate_detected(app_client, db, plaid_client):
    """POST /api/link/exchange detects duplicate institution."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, institution_id, institution_name, status, products) "
        "VALUES ('item_existing', 'access_existing', 'ins_dup', 'Existing Bank', 'good', 'transactions')"
    )
    await db.commit()

    resp = await app_client.post("/api/link/exchange", json={
        "public_token": "public-token",
        "institution_id": "ins_dup",
        "institution_name": "Existing Bank",
        "products": ["transactions"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["duplicate"] is True
    assert data["existing_item_id"] == "item_existing"


@pytest.mark.asyncio
async def test_exchange_token_force_duplicate(app_client, db, plaid_client):
    """POST /api/link/exchange with force=true proceeds despite duplicate."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, institution_id, institution_name, status, products) "
        "VALUES ('item_existing', 'access_existing', 'ins_dup2', 'Existing Bank', 'good', 'transactions')"
    )
    await db.commit()

    exchange_resp = MagicMock()
    exchange_resp.access_token = "access-new"
    exchange_resp.item_id = "item_forced"
    plaid_client.item_public_token_exchange.return_value = exchange_resp

    accounts_resp = MagicMock()
    accounts_resp.accounts = [make_mock_account("acct_forced")]
    plaid_client.accounts_get.return_value = accounts_resp

    sync_resp = MagicMock()
    sync_resp.added = []
    sync_resp.modified = []
    sync_resp.removed = []
    sync_resp.next_cursor = "c"
    sync_resp.has_more = False
    plaid_client.transactions_sync.return_value = sync_resp

    resp = await app_client.post("/api/link/exchange", json={
        "public_token": "public-token",
        "institution_id": "ins_dup2",
        "institution_name": "Existing Bank",
        "products": ["transactions"],
        "force": True,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["duplicate"] is False
    assert data["item_id"] == "item_forced"


@pytest.mark.asyncio
async def test_create_update_link_token(app_client, db, plaid_client):
    """POST /api/link/token/update returns a link token for re-auth."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, status, products) "
        "VALUES ('item_reauth', 'access_reauth', 'login_required', 'transactions')"
    )
    await db.commit()

    link_resp = MagicMock()
    link_resp.link_token = "link-update-token"
    link_resp.expiration = "2024-01-15T12:00:00Z"
    plaid_client.link_token_create.return_value = link_resp

    resp = await app_client.post("/api/link/token/update", json={"item_id": "item_reauth"})
    assert resp.status_code == 200
    assert resp.json()["link_token"] == "link-update-token"


@pytest.mark.asyncio
async def test_create_update_link_token_not_found(app_client):
    """POST /api/link/token/update returns 404 for missing item."""
    resp = await app_client.post("/api/link/token/update", json={"item_id": "nonexistent"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_item_status(app_client, db):
    """PATCH /api/items/{id}/status updates the status."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, status, products) "
        "VALUES ('item_status', 'access', 'login_required', 'transactions')"
    )
    await db.commit()

    resp = await app_client.patch("/api/items/item_status/status", json={"status": "good"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "good"

    cursor = await db.execute("SELECT status FROM plaid_items WHERE item_id = 'item_status'")
    row = await cursor.fetchone()
    assert row[0] == "good"


@pytest.mark.asyncio
async def test_update_item_status_invalid(app_client, db):
    """PATCH /api/items/{id}/status rejects invalid status values."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, status, products) "
        "VALUES ('item_inv', 'access', 'good', 'transactions')"
    )
    await db.commit()

    resp = await app_client.patch("/api/items/item_inv/status", json={"status": "invalid"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_list_items_with_status_and_products(app_client, db):
    """GET /api/items returns items with status and products."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, institution_id, institution_name, status, products, created_at) "
        "VALUES ('item_a', 'access_a', 'ins_a', 'Bank A', 'good', 'transactions,investments', datetime('now'))"
    )
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token, institution_id, institution_name, status, products, created_at) "
        "VALUES ('item_b', 'access_b', 'ins_b', 'Bank B', 'login_required', 'transactions', datetime('now'))"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type) VALUES ('acct_a1', 'item_a', 'Checking', 'depository')"
    )
    await db.commit()

    resp = await app_client.get("/api/items")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2

    # Find items by id
    items = {i["item_id"]: i for i in data}
    assert items["item_a"]["status"] == "good"
    assert items["item_a"]["products"] == ["transactions", "investments"]
    assert len(items["item_a"]["accounts"]) == 1
    assert items["item_b"]["status"] == "login_required"
    assert items["item_b"]["products"] == ["transactions"]
