from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "productinsight.db"
MIGRATION_DIR = Path(__file__).resolve().parent / "migrations"

# Retry settings for concurrent access
MAX_RETRIES = 3
RETRY_DELAY = 0.1  # seconds


def get_db_path() -> Path:
    # Allow override via environment variable
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite:///"):
        return Path(database_url.replace("sqlite:///", "", 1)).resolve()
    # Use WORK_DIR if set (set by test_e2e_full.py to ensure consistent DB path)
    work_dir = os.getenv("WORK_DIR", "")
    if work_dir:
        return Path(work_dir) / "data" / "productinsight.db"
    return DEFAULT_DB_PATH


def get_connection() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30)  # Increased timeout for concurrent access
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    # Enable WAL mode for better concurrent write performance
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def run_migrations(conn: sqlite3.Connection) -> None:
    # SQL migrations run first (table creation)
    for migration in sorted(MIGRATION_DIR.glob("*.sql")):
        conn.executescript(migration.read_text(encoding="utf-8"))
    # Python migrations run second (data migration / ALTER TABLE)
    import importlib.util, sys
    for migration in sorted(MIGRATION_DIR.glob("*.py")):
        if migration.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(migration.stem, migration)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            # Only run migrate() function if it exists
            try:
                spec.loader.exec_module(mod)
                if hasattr(mod, "migrate"):
                    # Pass the actual db_path used by the connection
                    db_path = get_db_path()
                    mod.migrate(db_path)
            except Exception as exc:
                # Migration errors should not break startup in dev
                import logging
                logging.getLogger(__name__).warning("Migration %s failed: %s", migration.name, exc)


def init_db() -> None:
    with get_connection() as conn:
        run_migrations(conn)
        conn.commit()


def run_with_retry(fn, max_attempts=MAX_RETRIES, delay=RETRY_DELAY):
    """Run a function with retry logic for database lock errors."""
    import functools
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(max_attempts):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as e:
                last_error = e
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    if attempt < max_attempts - 1:
                        time.sleep(delay * (attempt + 1))
                        continue
                raise
        raise last_error
    return wrapper


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Transaction context manager."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except sqlite3.OperationalError as e:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
