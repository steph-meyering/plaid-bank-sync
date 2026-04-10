# Plaid Bank Sync

Backend-only Python application that connects to multiple bank accounts via Plaid, syncs transaction and investment data, and persists everything to a local SQLite database. Exposes a FastAPI REST API for triggering syncs and querying data, plus a CLI for account linking and manual operations.

## Setup

### 1. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

Copy `.env.example` to `.env` and fill in your Plaid credentials:

```bash
cp .env.example .env
```

#### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PLAID_CLIENT_ID` | Yes | — | Your Plaid client ID |
| `PLAID_SECRET` | Yes | — | Your Plaid secret (sandbox/development/production) |
| `PLAID_ENV` | No | `sandbox` | Plaid environment: `sandbox`, `development`, or `production` |
| `DATABASE_URL` | No | `sqlite+aiosqlite:///./plaid_data.db` | SQLite database path |
| `SYNC_INTERVAL_HOURS` | No | `6` | Hours between automatic syncs |
| `LOG_LEVEL` | No | `INFO` | Logging level |

### 3. Link a bank account (sandbox)

```bash
python cli.py link                              # Default: First Platypus Bank (ins_109508)
python cli.py link --institution ins_109508     # Specify institution
```

### 4. Start the server

```bash
uvicorn app.main:app --port 8000 --reload
```

The server will run an initial sync 10 seconds after startup, then every `SYNC_INTERVAL_HOURS`.

## CLI Usage

```bash
python cli.py link                    # Link a new sandbox account
python cli.py link --institution ID   # Link a specific institution
python cli.py accounts                # List all linked accounts with balances
python cli.py sync                    # Sync all linked items
python cli.py sync --item ITEM_ID     # Sync a specific item
```

## API Reference

### Accounts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/accounts` | List all linked accounts with balances |
| `GET` | `/api/accounts/{account_id}` | Get single account detail |
| `DELETE` | `/api/items/{item_id}` | Unlink item and remove all associated data |

### Transactions

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sync/transactions` | Sync transactions for all items |
| `POST` | `/api/sync/transactions/{item_id}` | Sync transactions for one item |
| `GET` | `/api/transactions` | List transactions (see query params below) |
| `GET` | `/api/transactions/{transaction_id}` | Get single transaction |

**Query parameters for `GET /api/transactions`:**

- `account_id` — filter by account
- `start_date`, `end_date` — date range (YYYY-MM-DD)
- `pending` — `true` or `false`
- `min_amount`, `max_amount` — amount range
- `search` — substring match on name or merchant
- `limit` (default 100, max 500), `offset` (default 0)

### Investments

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sync/investments` | Sync investments for all items |
| `POST` | `/api/sync/investments/{item_id}` | Sync investments for one item |
| `GET` | `/api/investments/holdings` | List holdings (optional `account_id` filter) |
| `GET` | `/api/investments/transactions` | List investment transactions (optional `account_id`, `start_date`, `end_date`, `limit`, `offset`) |

### Sync

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/sync/all` | Full sync (transactions + investments) for all items |
| `GET` | `/api/sync/status` | Latest sync status per item per sync type |

## Testing

```bash
pytest -v
```

All tests use mocked Plaid responses — no sandbox API calls are made during testing.

## Architecture

- **Transactions** use Plaid's cursor-based `/transactions/sync` endpoint with full pending-to-settled reconciliation
- **Investment holdings** are synced as full snapshots — closed positions are automatically removed
- **Investment transactions** use date-windowed incremental syncs with 7-day overlap for safety
- **Scheduler** runs via APScheduler with per-item error isolation (one item failing doesn't block others)
- **Database** uses SQLite with WAL mode and foreign keys enabled

## Future Considerations

- Webhook support (`SYNC_UPDATES_AVAILABLE`, `DEFAULT_UPDATE`)
- Access token encryption at rest (Fernet or similar)
- Multi-user support with auth
- Balance history tracking (snapshot balances over time)
- Export to CSV/JSON
- Categorization rules engine (custom category mapping)
- Recurring transaction detection
