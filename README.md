# Plaid Bank Sync

Python application that connects to multiple bank accounts via Plaid, syncs transaction and investment data, and persists everything to a local SQLite database. Includes a web UI for managing linked accounts via Plaid Link, a FastAPI REST API for triggering syncs and querying data, and a CLI for sandbox operations.

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
| `PLAID_REDIRECT_URI` | No | — | OAuth redirect URI (required for OAuth-based institutions in production) |

### 3. Start the server

```bash
uvicorn app.main:app --port 8000 --reload
```

Open `http://localhost:8000` in your browser to access the web UI. The server runs an initial sync 10 seconds after startup, then every `SYNC_INTERVAL_HOURS`.

### 4. Connect a bank account

**Via the web UI** (recommended): Click "Connect Account", select products, and complete the Plaid Link flow in your browser.

**Via the CLI** (sandbox only):

```bash
python cli.py link                              # Default: First Platypus Bank (ins_109508)
python cli.py link --institution ins_109508     # Specify institution
```

## CLI Usage

```bash
python cli.py link                    # Link a new sandbox account
python cli.py link --institution ID   # Link a specific institution
python cli.py accounts                # List all linked accounts with balances
python cli.py sync                    # Sync all linked items
python cli.py sync --item ITEM_ID     # Sync a specific item
```

## Web UI

The frontend is a single HTML page served at `/` with no build step. It provides:

- **Item list** showing connected institutions with status badges, product tags, and nested accounts with balances
- **Connect Account** button with product selector (transactions, investments, or both) that launches Plaid Link
- **Delete** button per item (removes from Plaid and local DB)
- **Re-link** button for items needing re-authentication (when Plaid reports `ITEM_LOGIN_REQUIRED`)
- **Duplicate detection** warns if you try to connect an institution that's already linked
- **OAuth redirect handling** for institutions that use OAuth flows

## API Reference

### Items & Accounts

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/items` | List all linked items with nested accounts, status, and products |
| `GET` | `/api/accounts` | List all linked accounts with balances |
| `GET` | `/api/accounts/{account_id}` | Get single account detail |
| `DELETE` | `/api/items/{item_id}` | Unlink item and remove all associated data |
| `PATCH` | `/api/items/{item_id}/status` | Update item status (`good`, `login_required`, `error`) |

### Plaid Link

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/link/token` | Create a Plaid Link token (body: `{"products": ["transactions", "investments"]}`) |
| `POST` | `/api/link/exchange` | Exchange public token, store item/accounts, trigger initial sync |
| `POST` | `/api/link/token/update` | Create a Link token in update mode for re-authentication |

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
- **Product-aware sync** only syncs the products each item was linked with (transactions, investments, or both)
- **Item health tracking** detects `ITEM_LOGIN_REQUIRED` during sync and marks items for re-authentication
- **Duplicate detection** warns before linking the same institution twice

## Future Considerations

- Webhook support (`SYNC_UPDATES_AVAILABLE`, `DEFAULT_UPDATE`)
- Access token encryption at rest (Fernet or similar)
- Multi-user support with auth
- Balance history tracking (snapshot balances over time)
- Export to CSV/JSON
- Categorization rules engine (custom category mapping)
- Recurring transaction detection
