import os
import sqlite3
from contextlib import contextmanager

from .config import settings


BASE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    lifecycle TEXT NOT NULL DEFAULT 'DRAFT',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS senders (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    name TEXT,
    profile_id INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(profile_id) REFERENCES profiles(id)
);

CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    provider_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profile_members (
    profile_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    percent TEXT NOT NULL,
    version INTEGER NOT NULL,
    allocation_type TEXT NOT NULL DEFAULT 'PERCENT',
    value TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(profile_id, recipient_id, version),
    FOREIGN KEY(profile_id) REFERENCES profiles(id),
    FOREIGN KEY(recipient_id) REFERENCES recipients(id)
);

CREATE TABLE IF NOT EXISTS profile_fees (
    id INTEGER PRIMARY KEY,
    profile_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount TEXT NOT NULL,
    version INTEGER NOT NULL,
    FOREIGN KEY(profile_id) REFERENCES profiles(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    external_id TEXT UNIQUE NOT NULL,
    sender_email TEXT NOT NULL,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    profile_id INTEGER,
    profile_version INTEGER,
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(profile_id) REFERENCES profiles(id)
);

CREATE TABLE IF NOT EXISTS payouts (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL,
    recipient_id INTEGER,
    recipient_email TEXT NOT NULL,
    gross TEXT NOT NULL,
    fee TEXT NOT NULL DEFAULT '0.00',
    net TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    external_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS fees (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL,
    fee_type TEXT NOT NULL,
    name TEXT NOT NULL,
    amount TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    reference_id INTEGER,
    amount TEXT NOT NULL,
    currency TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(transaction_id) REFERENCES transactions(id)
);

CREATE TABLE IF NOT EXISTS provider_events (
    id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    provider_event_id TEXT,
    provider_operation_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    resource_type TEXT,
    resource_id INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope, key)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    admin_id TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _columns(connection, table):
    rows = connection.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()
    return {row[1] for row in rows}


def _add_column(connection, table, column, definition):
    if column not in _columns(connection, table):
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def migrate(connection):
    # Existing recipients table
    _add_column(
        connection,
        "recipients",
        "provider_id",
        "TEXT",
    )

    # Existing profiles table
    _add_column(
        connection,
        "profiles",
        "lifecycle",
        "TEXT NOT NULL DEFAULT 'DRAFT'",
    )

    # Existing profile_members table
    _add_column(
        connection,
        "profile_members",
        "allocation_type",
        "TEXT NOT NULL DEFAULT 'PERCENT'",
    )

    _add_column(
        connection,
        "profile_members",
        "value",
        "TEXT NOT NULL DEFAULT ''",
    )

    connection.execute(
        "UPDATE profile_members SET value=percent "
        "WHERE value='' OR value IS NULL"
    )

    # Existing transactions table
    _add_column(
        connection,
        "transactions",
        "provider",
        "TEXT NOT NULL DEFAULT 'airtm'",
    )
    _add_column(
        connection,
        "transactions",
        "provider_operation_id",
        "TEXT",
    )
    _add_column(
        connection,
        "transactions",
        "profile_snapshot_json",
        "TEXT",
    )

    # Existing payouts table
    _add_column(
        connection,
        "payouts",
        "provider_recipient_id",
        "TEXT",
    )
    _add_column(
        connection,
        "payouts",
        "configured_fee",
        "TEXT NOT NULL DEFAULT '0.00'",
    )
    _add_column(
        connection,
        "payouts",
        "provider_fee",
        "TEXT NOT NULL DEFAULT '0.00'",
    )
    _add_column(
        connection,
        "payouts",
        "idempotency_key",
        "TEXT",
    )
    _add_column(
        connection,
        "payouts",
        "last_error",
        "TEXT",
    )


def create_indexes(connection):
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_tx_status
            ON transactions(status);

        CREATE INDEX IF NOT EXISTS idx_tx_sender
            ON transactions(sender_email);

        CREATE INDEX IF NOT EXISTS idx_tx_provider_operation
            ON transactions(provider_operation_id);

        CREATE INDEX IF NOT EXISTS idx_payout_tx
            ON payouts(transaction_id);

        CREATE INDEX IF NOT EXISTS idx_payout_status
            ON payouts(status);

        CREATE INDEX IF NOT EXISTS idx_ledger_tx
            ON ledger_entries(transaction_id);

        CREATE INDEX IF NOT EXISTS idx_provider_events_operation
            ON provider_events(provider_operation_id);
        """
    )


def init_db():
    os.makedirs(
        os.path.dirname(settings.database_path) or ".",
        exist_ok=True,
    )

    with sqlite3.connect(
        settings.database_path,
        timeout=30,
    ) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")

        # Create missing tables first.
        connection.executescript(BASE_SCHEMA)

        # Upgrade existing tables without deleting their data.
        migrate(connection)

        # Only create indexes after migrations added their columns.
        create_indexes(connection)

        connection.execute(
            """
            INSERT OR IGNORE INTO system_state(key, value)
            VALUES('automation_enabled', '1')
            """
        )

        connection.commit()


@contextmanager
def db():
    connection = sqlite3.connect(
        settings.database_path,
        timeout=30,
    )
    connection.row_factory = sqlite3.Row

    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
