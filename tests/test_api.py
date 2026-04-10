"""API endpoint integration tests."""

import pytest
import pytest_asyncio
from unittest.mock import MagicMock
from tests.conftest import make_mock_account, make_mock_transaction


@pytest.mark.asyncio
async def test_list_accounts_empty(app_client):
    """GET /api/accounts returns empty list when no accounts."""
    response = await app_client.get("/api/accounts")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_accounts(app_client, db):
    """GET /api/accounts returns linked accounts."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_1', 'access_1')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type, current_balance) "
        "VALUES ('acct_1', 'item_1', 'Checking', 'depository', 1000.0)"
    )
    await db.commit()

    response = await app_client.get("/api/accounts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["plaid_account_id"] == "acct_1"
    assert data[0]["current_balance"] == 1000.0


@pytest.mark.asyncio
async def test_get_account_not_found(app_client):
    """GET /api/accounts/{id} returns 404 for missing account."""
    response = await app_client.get("/api/accounts/nonexistent")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_transactions_with_filters(app_client, db):
    """GET /api/transactions supports query param filtering."""
    await db.execute(
        "INSERT INTO plaid_items (item_id, access_token) VALUES ('item_1', 'access_1')"
    )
    await db.execute(
        "INSERT INTO accounts (plaid_account_id, item_id, name, type) VALUES ('acct_1', 'item_1', 'Checking', 'depository')"
    )
    await db.execute(
        "INSERT INTO transactions (transaction_id, account_id, item_id, amount, date, name, pending) "
        "VALUES ('txn_1', 'acct_1', 'item_1', 50.0, '2024-01-15', 'Coffee', 0)"
    )
    await db.execute(
        "INSERT INTO transactions (transaction_id, account_id, item_id, amount, date, name, pending) "
        "VALUES ('txn_2', 'acct_1', 'item_1', 200.0, '2024-02-01', 'Groceries', 1)"
    )
    await db.commit()

    # Filter by date range
    resp = await app_client.get("/api/transactions?start_date=2024-01-01&end_date=2024-01-31")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["transaction_id"] == "txn_1"

    # Filter by pending
    resp = await app_client.get("/api/transactions?pending=true")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["transaction_id"] == "txn_2"

    # Filter by search
    resp = await app_client.get("/api/transactions?search=Coffee")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Filter by amount
    resp = await app_client.get("/api/transactions?min_amount=100")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["amount"] == 200.0


@pytest.mark.asyncio
async def test_get_transaction_not_found(app_client):
    """GET /api/transactions/{id} returns 404."""
    resp = await app_client.get("/api/transactions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_sync_status_empty(app_client):
    """GET /api/sync/status returns empty when no syncs."""
    resp = await app_client.get("/api/sync/status")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_sync_transactions_no_items(app_client):
    """POST /api/sync/transactions returns empty when no items."""
    resp = await app_client.post("/api/sync/transactions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_delete_item_not_found(app_client):
    """DELETE /api/items/{id} returns 404 for missing item."""
    resp = await app_client.delete("/api/items/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_holdings_empty(app_client):
    """GET /api/investments/holdings returns empty list."""
    resp = await app_client.get("/api/investments/holdings")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_investment_transactions_empty(app_client):
    """GET /api/investments/transactions returns empty list."""
    resp = await app_client.get("/api/investments/transactions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_error_response_structured(app_client):
    """Error responses have structured format."""
    resp = await app_client.get("/api/accounts/nonexistent")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data
