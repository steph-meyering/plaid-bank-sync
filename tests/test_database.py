"""Tests for database schema and migrations."""

import pytest
import pytest_asyncio
from app.database import init_db, run_migrations, MIGRATIONS


@pytest.mark.asyncio
async def test_schema_creates_all_tables(db):
    """All expected tables are created by migration."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in await cursor.fetchall()]
    expected = [
        "accounts", "investment_holdings", "investment_transactions",
        "plaid_items", "schema_version", "securities", "sync_log", "transactions",
    ]
    for t in expected:
        assert t in tables, f"Missing table: {t}"


@pytest.mark.asyncio
async def test_schema_version_tracked(db):
    """Migration version is recorded."""
    cursor = await db.execute("SELECT MAX(version) FROM schema_version")
    row = await cursor.fetchone()
    assert row is not None
    assert row[0] == max(MIGRATIONS.keys())


@pytest.mark.asyncio
async def test_indexes_exist(db):
    """Key indexes are created."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    indexes = [row[0] for row in await cursor.fetchall()]
    expected = [
        "idx_transactions_account", "idx_transactions_date",
        "idx_transactions_pending", "idx_accounts_item",
        "idx_investment_holdings_account",
    ]
    for idx in expected:
        assert idx in indexes, f"Missing index: {idx}"


@pytest.mark.asyncio
async def test_migrations_idempotent(db):
    """Running migrations again doesn't fail."""
    await run_migrations(db)
    cursor = await db.execute("SELECT COUNT(*) FROM schema_version")
    row = await cursor.fetchone()
    assert row[0] == len(MIGRATIONS)
