"""Database initialization, connection management, and migrations."""

import aiosqlite
import logging
import os

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./plaid_data.db")
# Extract file path from URL format
DB_FILE = DB_PATH.replace("sqlite+aiosqlite:///", "").replace("sqlite:///", "")

MIGRATIONS = {
    1: """
-- Linked Plaid items (one per Link session)
CREATE TABLE IF NOT EXISTS plaid_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT UNIQUE NOT NULL,
    access_token TEXT NOT NULL,  -- TODO: encrypt at rest in production
    institution_id TEXT,
    institution_name TEXT,
    transaction_cursor TEXT,
    investment_last_sync_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Bank accounts under each item
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plaid_account_id TEXT UNIQUE NOT NULL,
    item_id TEXT NOT NULL REFERENCES plaid_items(item_id),
    name TEXT,
    official_name TEXT,
    type TEXT,
    subtype TEXT,
    mask TEXT,
    current_balance REAL,
    available_balance REAL,
    currency_code TEXT DEFAULT 'USD',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Transactions (both pending and settled)
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT UNIQUE NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(plaid_account_id),
    item_id TEXT NOT NULL REFERENCES plaid_items(item_id),
    amount REAL NOT NULL,
    iso_currency_code TEXT DEFAULT 'USD',
    name TEXT,
    merchant_name TEXT,
    pending BOOLEAN NOT NULL DEFAULT 0,
    pending_transaction_id TEXT,
    authorized_date TEXT,
    date TEXT NOT NULL,
    category TEXT,
    category_id TEXT,
    personal_finance_category TEXT,
    payment_channel TEXT,
    transaction_type TEXT,
    location TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Investment holdings (current snapshot per sync)
CREATE TABLE IF NOT EXISTS investment_holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(plaid_account_id),
    item_id TEXT NOT NULL REFERENCES plaid_items(item_id),
    security_id TEXT NOT NULL,
    institution_price REAL,
    institution_price_as_of TEXT,
    institution_value REAL,
    cost_basis REAL,
    quantity REAL,
    currency_code TEXT DEFAULT 'USD',
    synced_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(account_id, security_id)
);

-- Securities reference table
CREATE TABLE IF NOT EXISTS securities (
    security_id TEXT PRIMARY KEY,
    isin TEXT,
    cusip TEXT,
    sedol TEXT,
    ticker_symbol TEXT,
    name TEXT,
    type TEXT,
    close_price REAL,
    close_price_as_of TEXT,
    currency_code TEXT DEFAULT 'USD',
    is_cash_equivalent BOOLEAN DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Investment transactions
CREATE TABLE IF NOT EXISTS investment_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    investment_transaction_id TEXT UNIQUE NOT NULL,
    account_id TEXT NOT NULL REFERENCES accounts(plaid_account_id),
    item_id TEXT NOT NULL REFERENCES plaid_items(item_id),
    security_id TEXT,
    date TEXT NOT NULL,
    name TEXT,
    quantity REAL,
    amount REAL,
    price REAL,
    fees REAL,
    type TEXT,
    subtype TEXT,
    currency_code TEXT DEFAULT 'USD',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sync log for debugging
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES plaid_items(item_id),
    sync_type TEXT NOT NULL,
    status TEXT NOT NULL,
    added_count INTEGER DEFAULT 0,
    modified_count INTEGER DEFAULT 0,
    removed_count INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_pending ON transactions(pending);
CREATE INDEX IF NOT EXISTS idx_transactions_pending_txn_id ON transactions(pending_transaction_id);
CREATE INDEX IF NOT EXISTS idx_investment_holdings_account ON investment_holdings(account_id);
CREATE INDEX IF NOT EXISTS idx_investment_transactions_account ON investment_transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_investment_transactions_date ON investment_transactions(date);
CREATE INDEX IF NOT EXISTS idx_accounts_item ON accounts(item_id);
""",
}


async def get_db(db_path: str = None) -> aiosqlite.Connection:
    """Get a database connection."""
    path = db_path or DB_FILE
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def run_migrations(db: aiosqlite.Connection):
    """Apply pending database migrations."""
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, "
        "applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ")"
    )
    await db.commit()

    cursor = await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    row = await cursor.fetchone()
    current_version = row[0]

    for version in sorted(MIGRATIONS.keys()):
        if version > current_version:
            logger.info(f"Applying migration v{version}")
            await db.executescript(MIGRATIONS[version])
            await db.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (version,)
            )
            await db.commit()
            logger.info(f"Migration v{version} applied")


async def init_db(db_path: str = None) -> aiosqlite.Connection:
    """Initialize the database: connect and run migrations."""
    db = await get_db(db_path)
    await run_migrations(db)
    return db
