"""Test fixtures: test DB, test client, mock Plaid."""

import asyncio
import os
import pytest
import pytest_asyncio

# Set env vars before importing app modules
os.environ["PLAID_CLIENT_ID"] = "test_client_id"
os.environ["PLAID_SECRET"] = "test_secret"
os.environ["PLAID_ENV"] = "sandbox"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from unittest.mock import MagicMock, AsyncMock, patch
from httpx import AsyncClient, ASGITransport
from app.database import init_db


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Fresh in-memory database for each test."""
    database = await init_db(":memory:")
    yield database
    await database.close()


class FakePlaidClient:
    """Configurable fake Plaid client for tests."""

    def __init__(self):
        self.sandbox_public_token_create = MagicMock()
        self.item_public_token_exchange = MagicMock()
        self.accounts_get = MagicMock()
        self.transactions_sync = MagicMock()
        self.investments_holdings_get = MagicMock()
        self.investments_transactions_get = MagicMock()
        self.link_token_create = MagicMock()
        self.item_remove = MagicMock()


def make_mock_account(account_id="acct_1", name="Checking", acct_type="depository",
                      subtype="checking", mask="0000", balance=1000.0):
    """Create a mock Plaid account object."""
    acct = MagicMock()
    acct.account_id = account_id
    acct.name = name
    acct.official_name = f"Official {name}"
    acct.type = acct_type
    acct.subtype = subtype
    acct.mask = mask
    acct.balances = MagicMock()
    acct.balances.current = balance
    acct.balances.available = balance - 100
    acct.balances.iso_currency_code = "USD"
    return acct


def make_mock_transaction(txn_id="txn_1", account_id="acct_1", amount=50.0,
                          pending=False, pending_transaction_id=None,
                          name="Coffee Shop", date="2024-01-15"):
    """Create a mock Plaid transaction object."""
    txn = MagicMock()
    txn.transaction_id = txn_id
    txn.account_id = account_id
    txn.amount = amount
    txn.iso_currency_code = "USD"
    txn.name = name
    txn.merchant_name = name
    txn.pending = pending
    txn.pending_transaction_id = pending_transaction_id
    txn.authorized_date = None
    txn.date = date
    txn.category = ["Food", "Coffee"]
    txn.category_id = "13005000"
    txn.personal_finance_category = None
    txn.payment_channel = "in store"
    txn.transaction_type = "place"
    txn.location = None
    return txn


def make_mock_holding(account_id="acct_inv", security_id="sec_1",
                      quantity=10.0, price=150.0, value=1500.0, cost_basis=1200.0):
    """Create a mock Plaid holding object."""
    h = MagicMock()
    h.account_id = account_id
    h.security_id = security_id
    h.institution_price = price
    h.institution_price_as_of = "2024-01-15"
    h.institution_value = value
    h.cost_basis = cost_basis
    h.quantity = quantity
    h.iso_currency_code = "USD"
    h.unofficial_currency_code = None
    return h


def make_mock_security(security_id="sec_1", ticker="AAPL", name="Apple Inc",
                       sec_type="equity", price=150.0):
    """Create a mock Plaid security object."""
    s = MagicMock()
    s.security_id = security_id
    s.isin = None
    s.cusip = None
    s.sedol = None
    s.ticker_symbol = ticker
    s.name = name
    s.type = sec_type
    s.close_price = price
    s.close_price_as_of = "2024-01-15"
    s.iso_currency_code = "USD"
    s.unofficial_currency_code = None
    s.is_cash_equivalent = False
    return s


def make_mock_investment_txn(txn_id="inv_txn_1", account_id="acct_inv",
                              security_id="sec_1", amount=1500.0, quantity=10.0,
                              price=150.0, txn_type="buy", date="2024-01-10"):
    """Create a mock Plaid investment transaction object."""
    t = MagicMock()
    t.investment_transaction_id = txn_id
    t.account_id = account_id
    t.security_id = security_id
    t.date = date
    t.name = f"{txn_type.upper()} {security_id}"
    t.quantity = quantity
    t.amount = amount
    t.price = price
    t.fees = 0
    t.type = txn_type
    t.subtype = txn_type
    t.iso_currency_code = "USD"
    t.unofficial_currency_code = None
    return t


@pytest.fixture
def plaid_client():
    """Return a fresh FakePlaidClient."""
    return FakePlaidClient()


@pytest_asyncio.fixture
async def app_client(db, plaid_client):
    """FastAPI test client with mocked dependencies."""
    from app.main import app

    app.state.db = db
    app.state.plaid_client = plaid_client
    app.state.scheduler = MagicMock()
    app.state.scheduler.shutdown = MagicMock()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
