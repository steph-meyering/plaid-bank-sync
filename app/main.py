"""FastAPI application with lifespan, scheduler, and router registration."""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from .config import settings
from .database import init_db
from .plaid_client import create_plaid_client
from .sync.scheduler import create_scheduler
from starlette.requests import Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .routers import accounts, transactions, investments, link
from .models import SyncResult, SyncLogResponse

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown logic."""
    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Initialize database
    app.state.db = await init_db()
    logger.info("Database initialized")

    # Initialize Plaid client
    app.state.plaid_client = create_plaid_client(
        settings.plaid_client_id, settings.plaid_secret, settings.plaid_env,
    )
    logger.info(f"Plaid client initialized (env: {settings.plaid_env})")

    # Start scheduler
    app.state.scheduler = create_scheduler(app, settings.sync_interval_hours)
    app.state.scheduler.start()
    logger.info(f"Scheduler started (interval: {settings.sync_interval_hours}h)")

    yield

    # Shutdown
    app.state.scheduler.shutdown(wait=False)
    await app.state.db.close()
    logger.info("Shutdown complete")


app = FastAPI(title="Plaid Bank Sync", version="1.0.0", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(investments.router)
app.include_router(link.router)


@app.post("/api/sync/all")
async def sync_all(request: Request):
    """Trigger full sync (transactions + investments) for all items."""
    from .sync.scheduler import run_full_sync
    await run_full_sync(request.app)
    return {"status": "sync completed"}


@app.get("/api/sync/status")
async def sync_status(request: Request):
    """Get latest sync log per item, per sync type."""
    db = request.app.state.db
    cursor = await db.execute(
        "SELECT s.item_id, s.sync_type, s.status, s.added_count, s.modified_count, "
        "s.removed_count, s.error_message, s.started_at, s.completed_at "
        "FROM sync_log s "
        "INNER JOIN (SELECT item_id, sync_type, MAX(id) as max_id FROM sync_log GROUP BY item_id, sync_type) latest "
        "ON s.id = latest.max_id "
        "ORDER BY s.started_at DESC"
    )
    rows = await cursor.fetchall()
    return [
        SyncLogResponse(
            item_id=r[0], sync_type=r[1], status=r[2], added_count=r[3] or 0,
            modified_count=r[4] or 0, removed_count=r[5] or 0,
            error_message=r[6], started_at=r[7] or "", completed_at=r[8],
        )
        for r in rows
    ]


# Static files (must be last — catch-all for frontend)
import os
_static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
