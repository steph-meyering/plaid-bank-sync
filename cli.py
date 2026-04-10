"""CLI for linking accounts and manual operations in Plaid sandbox."""

import argparse
import asyncio
import sys
import logging

from app.config import settings
from app.database import init_db
from app.plaid_client import create_plaid_client
from app.services.link_service import create_sandbox_link, DEFAULT_INSTITUTION
from app.services.transaction_service import sync_transactions
from app.services.investment_service import sync_holdings, sync_investment_transactions

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def cmd_link(args):
    """Link a new sandbox account."""
    db = await init_db()
    client = create_plaid_client(settings.plaid_client_id, settings.plaid_secret, settings.plaid_env)

    institution_id = args.institution or DEFAULT_INSTITUTION
    print(f"Linking institution {institution_id}...")

    result = await create_sandbox_link(client, db, institution_id)
    print(f"Linked item {result['item_id']} with {result['accounts']} accounts")
    await db.close()


async def cmd_accounts(args):
    """List all linked accounts."""
    db = await init_db()
    cursor = await db.execute(
        "SELECT a.plaid_account_id, a.name, a.type, a.subtype, a.mask, "
        "a.current_balance, a.available_balance, p.institution_name, p.item_id "
        "FROM accounts a JOIN plaid_items p ON a.item_id = p.item_id ORDER BY a.name"
    )
    rows = await cursor.fetchall()

    if not rows:
        print("No linked accounts.")
        await db.close()
        return

    print(f"\n{'Name':<30} {'Type':<12} {'Mask':<6} {'Balance':>12} {'Item ID':<40}")
    print("-" * 105)
    for r in rows:
        name = r[1] or "Unknown"
        acct_type = f"{r[2]}/{r[3]}" if r[3] else (r[2] or "")
        mask = r[4] or ""
        balance = f"${r[5]:,.2f}" if r[5] is not None else "N/A"
        print(f"{name:<30} {acct_type:<12} {mask:<6} {balance:>12} {r[8]:<40}")

    print(f"\nTotal: {len(rows)} accounts")
    await db.close()


async def cmd_sync(args):
    """Trigger a manual sync."""
    db = await init_db()
    client = create_plaid_client(settings.plaid_client_id, settings.plaid_secret, settings.plaid_env)

    if args.item:
        cursor = await db.execute(
            "SELECT item_id, access_token FROM plaid_items WHERE item_id = ?", (args.item,)
        )
        items = await cursor.fetchall()
        if not items:
            print(f"Item {args.item} not found")
            await db.close()
            return
    else:
        cursor = await db.execute("SELECT item_id, access_token FROM plaid_items")
        items = await cursor.fetchall()

    if not items:
        print("No linked items to sync.")
        await db.close()
        return

    for item in items:
        item_id, access_token = item[0], item[1]
        print(f"\nSyncing {item_id}...")

        # Transactions
        try:
            result = await sync_transactions(client, db, item_id, access_token)
            print(f"  Transactions: +{result['added']} modified:{result['modified']} removed:{result['removed']}")
        except Exception as e:
            print(f"  Transaction sync failed: {e}")

        # Investments
        try:
            h_result = await sync_holdings(client, db, item_id, access_token)
            print(f"  Holdings: {h_result['holdings_synced']} synced, {h_result['securities_synced']} securities")
        except Exception as e:
            print(f"  Holdings sync failed: {e}")

        try:
            t_result = await sync_investment_transactions(client, db, item_id, access_token)
            print(f"  Investment transactions: {t_result['transactions_synced']} synced")
        except Exception as e:
            print(f"  Investment transaction sync failed: {e}")

    print("\nSync complete.")
    await db.close()


async def cmd_reset(args):
    """Remove all items from Plaid and wipe the local database."""
    db = await init_db()
    client = create_plaid_client(settings.plaid_client_id, settings.plaid_secret, settings.plaid_env)

    cursor = await db.execute("SELECT item_id, access_token, institution_name FROM plaid_items")
    items = await cursor.fetchall()

    if not items:
        print("No items to remove.")
        await db.close()
        return

    print(f"This will remove {len(items)} item(s) from Plaid and delete ALL local data.")
    for item in items:
        print(f"  - {item[2] or item[0]}")

    if not args.yes:
        confirm = input("\nType 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            await db.close()
            return

    # Remove items from Plaid first
    from plaid.model.item_remove_request import ItemRemoveRequest
    for item in items:
        item_id, access_token, name = item[0], item[1], item[2] or item[0]
        try:
            client.item_remove(ItemRemoveRequest(access_token=access_token))
            print(f"Removed from Plaid: {name}")
        except Exception as e:
            print(f"Warning: Failed to remove {name} from Plaid: {e}")

    # Wipe local data
    for table in ["transactions", "investment_transactions", "investment_holdings",
                   "securities", "accounts", "sync_log", "plaid_items"]:
        await db.execute(f"DELETE FROM {table}")
    await db.commit()
    print("\nLocal database wiped.")
    await db.close()


def main():
    parser = argparse.ArgumentParser(description="Plaid Bank Sync CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Link command
    link_parser = subparsers.add_parser("link", help="Link a new sandbox account")
    link_parser.add_argument("--institution", default=None, help="Institution ID (default: ins_109508)")

    # Accounts command
    subparsers.add_parser("accounts", help="List all linked accounts")

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Trigger a manual sync")
    sync_parser.add_argument("--item", default=None, help="Sync a specific item ID")

    # Reset command
    reset_parser = subparsers.add_parser("reset", help="Remove all items from Plaid and wipe local DB")
    reset_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "link":
        asyncio.run(cmd_link(args))
    elif args.command == "accounts":
        asyncio.run(cmd_accounts(args))
    elif args.command == "reset":
        asyncio.run(cmd_reset(args))
    elif args.command == "sync":
        asyncio.run(cmd_sync(args))


if __name__ == "__main__":
    main()
