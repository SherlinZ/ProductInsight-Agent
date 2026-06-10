"""
Migration 007: Expand reports.report_status CHECK constraint to include
reviewed_with_gaps and reviewed_partial.

Root cause: report_spans has FOREIGN KEY (report_id) REFERENCES reports(report_id).
When reports is recreated, the FK in report_spans still references the old table.
We must:
  1. Back up report_spans to report_spans_old
  2. Recreate reports with the new CHECK
  3. Drop the old reports table
  4. Back up report_spans to report_spans_old (before dropping FK)
  5. Recreate report_spans with updated FK (removing ON DELETE CASCADE for safety)
  6. Restore data from report_spans_old, filtering to only rows whose report_id
     is still in reports (orphaned rows are discarded)
  7. Drop report_spans_old
  8. Validate with PRAGMA foreign_key_check

This migration is safe to re-run (idempotent).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return result is not None


def _view_exists(conn: sqlite3.Connection, view: str) -> bool:
    result = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name=?",
        (view,),
    ).fetchone()
    return result is not None


def migrate(db_path: str | Path) -> None:
    db_path = Path(db_path).resolve()

    if not db_path.exists():
        print("[migration 007] Database does not exist yet — skipping.")
        return

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF;")

    try:
        # ── 0. Check if reports table exists ──────────────────────────────────
        if not _table_exists(conn, "reports"):
            print("[migration 007] 'reports' table not found — skipping (will be created by 001_init.sql).")
            return

        # ── 1. Check if migration already applied ───────────────────────────────
        # Migration is done only when:
        #   a) reports has the new CHECK constraint (reviewed_partial present), AND
        #   b) report_spans FK points to "reports", not "reports_old"
        reports_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='reports'",
        ).fetchone()
        spans_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='report_spans'",
        ).fetchone()

        reports_ok = bool(reports_row and "reviewed_partial" in (reports_row[0] or ""))
        spans_sql = spans_row[0] if spans_row else ""
        spans_ok = bool(
            spans_row and (
                'REFERENCES "reports"(report_id)' in spans_sql
                or 'REFERENCES reports(report_id)' in spans_sql
            )
        )
        if reports_ok and spans_ok:
            print("[migration 007] Already up to date (reports CHECK + report_spans FK both correct) — skipping.")
            return

        # ── 2. Idempotency: drop any leftover backup tables ───────────────────
        for t in ["reports_old", "reports_backup",
                  "report_spans_old", "report_spans_backup"]:
            if _table_exists(conn, t):
                conn.execute(f"DROP TABLE IF EXISTS {t};")
                print(f"[migration 007] Dropped leftover table: {t}")

        # ── 3. Rename reports → reports_old ──────────────────────────────────
        conn.execute("ALTER TABLE reports RENAME TO reports_old;")
        print("[migration 007] Renamed reports → reports_old")

        # ── 4. Recreate reports with expanded CHECK ──────────────────────────
        conn.execute("""
            CREATE TABLE reports (
                report_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                title TEXT NOT NULL,
                report_status TEXT NOT NULL
                    CHECK (report_status IN (
                        'draft', 'reviewed', 'reviewed_with_gaps',
                        'reviewed_partial', 'exported', 'blocked'
                    )),
                content_markdown_path TEXT,
                content_html_path TEXT,
                content_pdf_path TEXT,
                quality_summary_json TEXT NOT NULL,
                created_by_agent TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(run_id)
            )
        """)
        print("[migration 007] Created new reports table with expanded CHECK")

        # ── 5. Copy data from reports_old → reports ──────────────────────────
        conn.execute("""
            INSERT INTO reports (
                report_id, run_id, title, report_status,
                content_markdown_path, content_html_path, content_pdf_path,
                quality_summary_json, created_by_agent, created_at, updated_at
            ) SELECT
                report_id, run_id, title, report_status,
                content_markdown_path, content_html_path, content_pdf_path,
                quality_summary_json, created_by_agent, created_at, updated_at
            FROM reports_old
        """)
        n_reports = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        print(f"[migration 007] Restored {n_reports} rows to reports")

        # ── 6. Drop reports_old (we no longer need it) ───────────────────────
        conn.execute("DROP TABLE reports_old;")
        print("[migration 007] Dropped reports_old")

        # ── 7. Back up report_spans (if it exists) before touching its FK ────
        has_spans = _table_exists(conn, "report_spans")
        if has_spans:
            # Rename before recreating so the FK constraint doesn't block us
            conn.execute("ALTER TABLE report_spans RENAME TO report_spans_old;")
            print("[migration 007] Backed up report_spans → report_spans_old")

        # ── 8. Recreate report_spans with corrected FK ───────────────────────
        # Keep the same schema as 001_init.sql but ensure the FK is explicit.
        # Remove ON DELETE CASCADE to avoid accidentally cascading report deletes
        # from this migration (spans are cleaned by clear_report_spans in code).
        if has_spans:
            conn.execute("""
                CREATE TABLE report_spans (
                    span_id TEXT PRIMARY KEY,
                    report_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    section_id TEXT NOT NULL,
                    section_title TEXT NOT NULL,
                    span_type TEXT NOT NULL
                        CHECK (span_type IN ('paragraph', 'table', 'bullet', 'summary')),
                    text TEXT NOT NULL,
                    claim_ids_json TEXT NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    unsupported_flag INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (report_id) REFERENCES reports(report_id),
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                )
            """)
            print("[migration 007] Created new report_spans table with FK to reports")

            # ── 9. Restore spans data, filtering orphaned rows ────────────────
            conn.execute("""
                INSERT INTO report_spans (
                    span_id, report_id, run_id, section_id, section_title,
                    span_type, text, claim_ids_json, evidence_ids_json,
                    unsupported_flag, created_at
                ) SELECT
                    span_id, report_id, run_id, section_id, section_title,
                    span_type, text, claim_ids_json, evidence_ids_json,
                    unsupported_flag, created_at
                FROM report_spans_old
                WHERE report_id IN (SELECT report_id FROM reports)
                  AND run_id IN (SELECT run_id FROM runs)
            """)
            n_spans = conn.execute("SELECT COUNT(*) FROM report_spans").fetchone()[0]
            n_orphaned = conn.execute("SELECT COUNT(*) FROM report_spans_old").fetchone()[0] - n_spans
            print(f"[migration 007] Restored {n_spans} spans ({n_orphaned} orphaned rows discarded)")

            # ── 10. Drop report_spans_old ────────────────────────────────────
            conn.execute("DROP TABLE report_spans_old;")
            print("[migration 007] Dropped report_spans_old")

            # ── 11. Validate FK integrity ────────────────────────────────────
            fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
            if fk_violations:
                print(f"[migration 007] WARNING: FK violations detected: {fk_violations}")
                raise RuntimeError(
                    f"[migration 007] FK violations after restore: {fk_violations}"
                )
            print("[migration 007] PRAGMA foreign_key_check passed — no violations")
        else:
            print("[migration 007] report_spans table not found — skipping spans migration")

        # ── 12. Recreate index on run_id ────────────────────────────────────
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_run_id ON reports(run_id);")

        conn.commit()
        print("[migration 007] Completed successfully")

    except Exception as exc:
        conn.rollback()
        raise RuntimeError(f"migration 007 failed: {exc}") from exc
    finally:
        conn.close()
