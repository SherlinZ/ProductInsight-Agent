from __future__ import annotations

import json
import sqlite3
import time
import uuid
from typing import Any

from backend.app.storage.db import transaction, MAX_RETRIES, RETRY_DELAY


def _execute_with_retry(sql: str, params: tuple) -> None:
    """Execute SQL with retry logic for concurrent access."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            with transaction() as conn:
                conn.execute(sql, params)
            return  # Success
        except sqlite3.OperationalError as e:
            last_error = e
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
                continue
            raise
    if last_error:
        raise last_error


class ReportWriteError(Exception):
    """Raised by ReportRepository.add_report_with_spans when a DB step fails.

    Attributes:
        code: One of DB_WRITE_REPORT_ROW_FAILED, DB_CLEAR_REPORT_SPANS_FAILED,
              DB_WRITE_REPORT_SPAN_FAILED
        message: The raw exception string.
        report_id, run_id, span_id, section_id: Context fields from the failed step.
    """

    def __init__(
        self,
        code: str,
        message: str,
        report_id: str = "",
        run_id: str = "",
        span_id: str | None = None,
        section_id: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.report_id = report_id
        self.run_id = run_id
        self.span_id = span_id or ""
        self.section_id = section_id or ""


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _safe_parse_json(raw: Any, default: Any = None) -> Any:
    """Parse a JSON string into a Python object. Return default on failure."""
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


def _slugify(name: str) -> str:
    """Create a URL-safe slug from a product name."""
    return name.lower().replace(" ", "-").replace("_", "-")


def _ensure_product_in_db(run_id: str, raw_product_id: str, raw_product_name: str = "", now: str = "") -> str:
    """
    Ensure a product exists in the products table, creating a run-scoped product_id
    if it doesn't exist. Returns the run-scoped product_id that should be used
    for all subsequent writes (sources, evidence, facts, claims).

    This handles the case where task_brief.products has canonical product_id
    (e.g., "Coze") but the products table stores run-scoped IDs (e.g., "run_xxx_Coze").
    """
    import logging
    _logger = logging.getLogger(__name__)

    if not raw_product_id and not raw_product_name:
        return raw_product_id
    # P0-Fix: Detect if raw_product_id is already a run-scoped product ID.
    # When collect_sources injects product_id="run_xxx_dify" but the product
    # was already written by ProductRepository.add_product with the same ID,
    # _slugify would convert underscores to hyphens, creating a new different ID
    # "run_xxx_run-xxx-dify" instead of reusing the existing one.
    # Check: if raw_product_id starts with "{run_id}_", the product was already
    # scoped and we should use it as-is.
    if run_id and raw_product_id.startswith(f"{run_id}_"):
        product_id = raw_product_id
    else:
        slug = _slugify(raw_product_id or raw_product_name)
        product_id = f"{run_id}_{slug}" if run_id else (raw_product_id or raw_product_name)
    try:
        with transaction() as conn:
            # P0-Fix: Check if product already exists with this product_id.
            # This prevents double-prefixing when raw_product_id already contains
            # the run scope (e.g. raw_pid="run_xxx_dify" would otherwise become
            # "run_xxx_run-xxx-dify" because _slugify converts underscores to hyphens).
            existing = conn.execute(
                "SELECT product_id FROM products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            if existing:
                return existing[0]  # Return existing ID, don't regenerate
            if not existing:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO products (
                        product_id, run_id, product_name, seed_urls_json, created_at, updated_at
                    ) VALUES (?, ?, ?, '[]', ?, ?)
                    """,
                    (product_id, run_id, raw_product_name or raw_product_id, now, now),
                )
    except Exception as exc:
        _logger.error(
            "_ensure_product_in_db: failed to ensure product %s in DB for run_id=%s: %s",
            product_id, run_id, exc,
        )
    return product_id


class RunRepository:
    def create_run(self, run: dict[str, Any]) -> None:
        import logging
        _logger = logging.getLogger(__name__)
        _logger.critical("!!! CREATE_RUN !!! run_id=%s, task_brief_keys=%s, products_count=%d",
            run["run_id"],
            list(run.get("task_brief", {}).keys()),
            len(run.get("task_brief", {}).get("products", [])),
        )
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, project_id, task_id, task_title, task_brief_json, mode, status,
                    current_node, error_message, created_at, started_at, completed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"], run.get("project_id"), run["task_id"], run["task_title"],
                    json.dumps(run["task_brief"]), run["mode"], run["status"],
                    run.get("current_node"), run.get("error_message"),
                    run["created_at"], run.get("started_at"), run.get("completed_at"),
                    run["updated_at"],
                ),
            )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        import logging
        _logger = logging.getLogger(__name__)
        with transaction() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            return None
        result = _row_to_dict(row)
        _logger.critical("!!! GET_RUN !!! run_id=%s, task_brief_json_len=%d",
            run_id, len(result.get("task_brief_json", "")))
        # vNext-P0: Parse task_brief_json so callers get a dict
        if result.get("task_brief_json"):
            try:
                result["task_brief"] = json.loads(result.pop("task_brief_json"))
                _logger.critical("!!! GET_RUN: parsed task_brief with products_count=%d",
                    len(result.get("task_brief", {}).get("products", [])))
            except Exception:
                result["task_brief"] = {}
        return result

    def list_runs(self) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        results = _rows_to_list(rows)
        # vNext-P0: Parse task_brief_json for each run
        for result in results:
            if result.get("task_brief_json"):
                try:
                    result["task_brief"] = json.loads(result.pop("task_brief_json"))
                except Exception:
                    result["task_brief"] = {}
        return results

    def update_status(
        self,
        run_id: str,
        status: str,
        current_node: str | None = None,
        error_message: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        with transaction() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    current_node = COALESCE(?, current_node),
                    error_message = COALESCE(?, error_message),
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at),
                    updated_at = datetime('now')
                WHERE run_id = ?
                """,
                (status, current_node, error_message, started_at, completed_at, run_id),
            )


class ProductRepository:
    def add_product(self, product: dict[str, Any]) -> None:
        run_id = product.get("run_id", "")
        raw_name = product.get("product_name", "")
        raw_id = product.get("product_id", "")
        slug = product.get("product_slug") or _slugify(raw_name)
        # P0-A Fix: Use the same run-scoped product_id format as _ensure_product_in_db
        # to prevent FOREIGN KEY constraint failures in sources/evidence tables.
        product_id = f"{run_id}_{slug}" if run_id else (raw_id or raw_name)
        try:
            with transaction() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO products (
                        product_id, run_id, product_name, company_name, official_website,
                        region, product_type, product_slug, seed_urls_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        product_id, run_id, product.get("product_name"), product.get("company_name"),
                        product.get("official_website"), product.get("region"), product.get("product_type"),
                        slug, json.dumps(product.get("seed_urls", [])),
                        product["created_at"], product["updated_at"],
                    ),
                )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "ProductRepository.add_product: failed to write product %s for run_id=%s: %s",
                product_id, run_id, exc,
            )

    def list_products(self, run_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute("SELECT * FROM products WHERE run_id = ?", (run_id,)).fetchall()
        return _rows_to_list(rows)


class SourceRepository:
    def add_source(self, source: dict[str, Any]) -> None:
        run_id = source.get("run_id", "")
        raw_pid = source.get("product_id", "")
        resolved_pid = _ensure_product_in_db(
            run_id=run_id,
            raw_product_id=raw_pid,
            raw_product_name=source.get("product_name", raw_pid),
            now=source.get("created_at", ""),
        )
        # Derive product_slug from product_id or product_name if not provided.
        product_slug = source.get("product_slug")
        if not product_slug:
            pname = source.get("product_name", "")
            product_slug = source.get("product_slug") or _slugify(raw_pid) or _slugify(pname)
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sources (
                    source_id, run_id, product_id, product_slug, source_type, title, url, domain,
                    collection_method, robots_status, terms_note, trust_tier, fetched_at,
                    content_hash, status, error_message, created_at, updated_at,
                    fetch_level, fetch_strategy, char_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source["source_id"], run_id, resolved_pid, product_slug,
                    source["source_type"], source.get("title"), source.get("url"), source.get("domain"),
                    source["collection_method"], source["robots_status"], source.get("terms_note"),
                    source.get("trust_tier"), source.get("fetched_at"), source.get("content_hash"),
                    source["status"], source.get("error_message"),
                    source["created_at"], source["updated_at"],
                    source.get("fetch_level", 0),
                    source.get("fetch_strategy", ""),
                    source.get("char_count", 0),
                ),
            )

    def list_sources(self, run_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM sources WHERE run_id = ? ORDER BY product_slug, source_type",
                (run_id,),
            ).fetchall()
        return _rows_to_list(rows)


class EvidenceRepository:
    def add_snapshot(self, snapshot: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO snapshots (
                    snapshot_id, source_id, run_id, raw_text_path, html_path, screenshot_path,
                    metadata_json, content_hash, token_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot["snapshot_id"], snapshot["source_id"], snapshot["run_id"],
                    snapshot.get("raw_text_path"), snapshot.get("html_path"), snapshot.get("screenshot_path"),
                    json.dumps(snapshot.get("metadata", {})), snapshot["content_hash"],
                    snapshot.get("token_count"), snapshot["created_at"],
                ),
            )

    def add_evidence(self, evidence: dict[str, Any]) -> None:
        run_id = evidence.get("run_id", "")
        raw_pid = evidence.get("product_id", "")
        resolved_pid = _ensure_product_in_db(
            run_id=run_id,
            raw_product_id=raw_pid,
            raw_product_name=evidence.get("product_name", raw_pid),
            now=evidence.get("created_at", ""),
        )
        # Derive product_slug from product_id or product_name if not provided.
        product_slug = evidence.get("product_slug")
        if not product_slug:
            product_slug = _slugify(raw_pid) if raw_pid else None
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO evidence_items (
                    evidence_id, run_id, source_id, snapshot_id, product_id, product_slug,
                    schema_key, snippet, start_offset, end_offset, section_title,
                    confidence, quality_score, quality_json, usable_for_claim,
                    pii_masked, evidence_type, created_at,
                    trust_tier, source_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence["evidence_id"], run_id, evidence["source_id"], evidence["snapshot_id"],
                    resolved_pid, product_slug, evidence.get("schema_key"),
                    evidence["snippet"], evidence.get("start_offset"), evidence.get("end_offset"),
                    evidence.get("section_title"), evidence["confidence"],
                    evidence.get("quality_score", 0.0),
                    json.dumps(evidence.get("quality_json", {})) if "quality_json" in evidence else None,
                    1 if evidence.get("usable_for_claim", False) else 0,
                    1 if evidence.get("pii_masked", True) else 0, evidence.get("evidence_type"),
                    evidence["created_at"],
                    evidence.get("trust_tier", "medium"),
                    evidence.get("source_type", "web_page"),
                ),
            )

    def update_evidence_usable(self, evidence_id: str, usable: bool) -> None:
        """Update the usable_for_claim field of an evidence item after the gate check."""
        with transaction() as conn:
            conn.execute(
                "UPDATE evidence_items SET usable_for_claim = ? WHERE evidence_id = ?",
                (1 if usable else 0, evidence_id),
            )

    def list_evidence(self, run_id: str, product_id: str | None = None) -> list[dict[str, Any]]:
        with transaction() as conn:
            base_cols = [
                "e.evidence_id", "e.run_id", "e.source_id", "e.snapshot_id",
                "e.product_id", "e.schema_key", "e.snippet", "e.start_offset",
                "e.end_offset", "e.section_title", "e.confidence", "e.pii_masked",
                "e.evidence_type", "e.created_at", "e.product_slug",
                "e.quality_score", "e.quality_json", "e.usable_for_claim",
                "e.trust_tier",
                # JOINed from sources table
                "s.title AS source_title", "s.url AS source_url",
                "s.source_type AS src_source_type", "s.fetched_at",
            ]
            select = ", ".join(base_cols)
            if product_id:
                rows = conn.execute(
                    f"SELECT {select} FROM evidence_items e "
                    "LEFT JOIN sources s ON e.source_id = s.source_id "
                    "WHERE e.run_id = ? AND e.product_id = ? ORDER BY e.product_slug, e.created_at",
                    (run_id, product_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {select} FROM evidence_items e "
                    "LEFT JOIN sources s ON e.source_id = s.source_id "
                    "WHERE e.run_id = ? ORDER BY e.product_slug, e.created_at",
                    (run_id,),
                ).fetchall()
        return _rows_to_list(rows)

    def get_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with transaction() as conn:
            row = conn.execute(
                """
                SELECT e.*, s.title AS source_title, s.url AS source_url, s.source_type, s.fetched_at
                FROM evidence_items e
                LEFT JOIN sources s ON e.source_id = s.source_id
                WHERE e.evidence_id = ?
                """,
                (evidence_id,),
            ).fetchone()
        return _row_to_dict(row)

    def get_evidence_by_ids(
        self,
        evidence_ids: list[str],
        run_id: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Batch fetch evidence items by ID, indexed by evidence_id.

        Returns a dict: {evidence_id -> evidence_record}
        """
        if not evidence_ids:
            return {}
        placeholders = ",".join(["?"] * len(evidence_ids))
        with transaction() as conn:
            if run_id:
                query = f"""
                    SELECT e.*, s.title AS source_title, s.url AS source_url,
                           s.source_type, s.fetched_at
                    FROM evidence_items e
                    LEFT JOIN sources s ON e.source_id = s.source_id
                    WHERE e.evidence_id IN ({placeholders}) AND e.run_id = ?
                    ORDER BY e.evidence_id
                """
                rows = conn.execute(query, evidence_ids + [run_id]).fetchall()
            else:
                query = f"""
                    SELECT e.*, s.title AS source_title, s.url AS source_url,
                           s.source_type, s.fetched_at
                    FROM evidence_items e
                    LEFT JOIN sources s ON e.source_id = s.source_id
                    WHERE e.evidence_id IN ({placeholders})
                    ORDER BY e.evidence_id
                """
                rows = conn.execute(query, evidence_ids).fetchall()
        return {row["evidence_id"]: _row_to_dict(dict(row)) for row in rows}

    def update_evidence_quality(
        self,
        evidence_id: str,
        quality_json: dict[str, Any],
        usable_for_claim: bool = False,
    ) -> None:
        """Update quality scores for an evidence item.

        Updates quality_json, usable_for_claim, and quality_score (if column exists).
        """
        import json
        final_score = quality_json.get("final_score", 0.0)
        quality_json_str = json.dumps(quality_json, ensure_ascii=False)

        with transaction() as conn:
            # Try to update quality_score if column exists (graceful degradation)
            try:
                conn.execute(
                    """
                    UPDATE evidence_items
                    SET quality_json = ?, usable_for_claim = ?, quality_score = ?
                    WHERE evidence_id = ?
                    """,
                    (quality_json_str, 1 if usable_for_claim else 0, final_score, evidence_id),
                )
            except Exception:
                # Fallback: update without quality_score if column doesn't exist
                conn.execute(
                    """
                    UPDATE evidence_items
                    SET quality_json = ?, usable_for_claim = ?
                    WHERE evidence_id = ?
                    """,
                    (quality_json_str, 1 if usable_for_claim else 0, evidence_id),
                )


class ClaimRepository:
    def add_claim(self, claim: dict[str, Any]) -> None:
        # Resolve product_id to run-scoped ID, creating a placeholder product row if needed.
        # This prevents FOREIGN KEY failures when a product hasn't been written to DB yet
        # (e.g., intentionally no-URL products, or products added during rework).
        run_id = claim.get("run_id", "")
        raw_pid = claim.get("product_id", "")
        resolved_pid = _ensure_product_in_db(
            run_id=run_id,
            raw_product_id=raw_pid,
            raw_product_name=claim.get("product_name", raw_pid),
            now=claim.get("created_at", ""),
        )
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claims (
                    claim_id, run_id, product_id, dimension, claim_text, claim_type,
                    fact_ids_json, evidence_ids_json, confidence, risk_level, support_level,
                    review_status, signed_claim_id, created_by_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim["claim_id"], claim["run_id"], resolved_pid, claim["dimension"],
                    claim["claim_text"], claim["claim_type"], json.dumps(claim.get("fact_ids", [])),
                    json.dumps(claim.get("evidence_ids", [])), claim["confidence"], claim["risk_level"],
                    claim.get("support_level"), claim["review_status"], claim.get("signed_claim_id"),
                    claim["created_by_agent"], claim["created_at"], claim["updated_at"],
                ),
            )

    def add_claim_evidence_link(self, link: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO claim_evidence_links (
                    link_id, run_id, claim_id, evidence_id, support_type, support_score, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link["link_id"], link["run_id"], link["claim_id"], link["evidence_id"],
                    link["support_type"], link["support_score"], link["created_at"],
                ),
            )

    def list_claims(self, run_id: str, status: str | None = None) -> list[dict[str, Any]]:
        with transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM claims WHERE run_id = ? AND review_status = ?",
                    (run_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM claims WHERE run_id = ? ORDER BY dimension, claim_id",
                    (run_id,),
                ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["fact_ids"] = _safe_parse_json(r.pop("fact_ids_json", None), [])
            r["evidence_ids"] = _safe_parse_json(r.pop("evidence_ids_json", None), [])
            results.append(r)
        return results


class ReviewRepository:
    def add_review(self, review: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reviews (
                    review_id, run_id, review_target_type, review_target_id, reviewer_agent,
                    status, checks_json, reason_codes_json, comments, signed_claim_id,
                    reviewed_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review["review_id"], review["run_id"], review["review_target_type"],
                    review["review_target_id"], review["reviewer_agent"], review["status"],
                    json.dumps(review.get("checks", [])), json.dumps(review.get("reason_codes", [])),
                    review.get("comments"), review.get("signed_claim_id"), review["reviewed_at"],
                    review["created_at"],
                ),
            )

    def add_rework_request(self, rework: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO rework_requests (
                    rework_id, run_id, review_id, target_agent, target_node,
                    affected_objects_json, reason_codes_json, required_actions_json,
                    success_criteria_json, status, retry_count, max_retry,
                    metrics_before_json, metrics_after_json, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rework["rework_id"], rework["run_id"], rework["review_id"], rework["target_agent"],
                    rework["target_node"], json.dumps(rework.get("affected_objects", [])),
                    json.dumps(rework.get("reason_codes", [])), json.dumps(rework.get("required_actions", [])),
                    json.dumps(rework.get("success_criteria", {})), rework["status"],
                    rework.get("retry_count", 0), rework.get("max_retry", 2),
                    json.dumps(rework.get("metrics_before", {})), json.dumps(rework.get("metrics_after", {})),
                    rework["created_at"], rework.get("completed_at"),
                ),
            )

    def list_reviews(self, run_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM reviews WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["checks"] = _safe_parse_json(r.pop("checks_json", None), [])
            r["reason_codes"] = _safe_parse_json(r.pop("reason_codes_json", None), [])
            results.append(r)
        return results

    def list_rework_requests(self, run_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM rework_requests WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["affected_objects"] = _safe_parse_json(r.pop("affected_objects_json", None), [])
            r["reason_codes"] = _safe_parse_json(r.pop("reason_codes_json", None), [])
            r["required_actions"] = _safe_parse_json(r.pop("required_actions_json", None), [])
            r["success_criteria"] = _safe_parse_json(r.pop("success_criteria_json", None), {})
            r["metrics_before"] = _safe_parse_json(r.pop("metrics_before_json", None), {})
            r["metrics_after"] = _safe_parse_json(r.pop("metrics_after_json", None), {})
            results.append(r)
        return results


class TraceRepository:
    def add_trace(self, trace: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO trace_logs (
                    trace_id, run_id, node_name, agent_name, prompt_version, model_name,
                    input_path, output_path, decision, token_input, token_output,
                    latency_ms, status, error_message, started_at, completed_at,
                    project_id, agent_role, event_type, prompt_text,
                    input_payload_json, output_payload_json, decision_summary,
                    retry_count, artifact_refs_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace["trace_id"], trace["run_id"], trace["node_name"], trace.get("agent_name"),
                    trace.get("prompt_version"), trace.get("model_name"), trace.get("input_path"),
                    trace.get("output_path"), trace.get("decision"), trace.get("token_input"),
                    trace.get("token_output"), trace.get("latency_ms"), trace["status"],
                    trace.get("error_message"), trace["started_at"], trace.get("completed_at"),
                    trace.get("project_id"), trace.get("agent_role"), trace.get("event_type"),
                    trace.get("prompt_text"),
                    json.dumps(trace.get("input_payload")) if trace.get("input_payload") else None,
                    json.dumps(trace.get("output_payload")) if trace.get("output_payload") else None,
                    trace.get("decision_summary"),
                    trace.get("retry_count", 0),
                    json.dumps(trace.get("artifact_refs")) if trace.get("artifact_refs") else None,
                    trace.get("created_at", trace.get("started_at", "")),
                ),
            )

    def list_traces(
        self,
        run_id: str,
        node_name: str | None = None,
        agent_name: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM trace_logs WHERE run_id = ?"
        params: list[str] = [run_id]

        if node_name:
            query += " AND node_name = ?"
            params.append(node_name)
        if agent_name:
            query += " AND agent_name = ?"
            params.append(agent_name)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY started_at"

        with transaction() as conn:
            rows = conn.execute(query, params).fetchall()
        return _rows_to_list(rows)

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM trace_logs WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
        if row:
            return dict(row)
        return None

    def summarize_traces(self, run_id: str) -> dict[str, Any]:
        """Return summary stats for all traces of a run.
        
        Counts are based on event_type:
        - llm_call: actual LLM invocation traces
        - node_execution: non-LLM workflow node traces
        
        Falls back to model_name check for legacy traces without event_type.
        
        vNext-R2-C: Added successful_llm_calls, failed_llm_calls, fallback_llm_calls.
        """
        from backend.app.tracing.llm_trace import NON_LLM_MODEL_NAMES
        
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM trace_logs WHERE run_id = ?",
                (run_id,),
            ).fetchall()

        # Convert sqlite3.Row objects to dicts
        rows = [_row_to_dict(r) for r in rows]

        if not rows:
            return {
                "total_traces": 0,
                "failed_traces": 0,
                "total_tokens": 0,
                "total_latency_ms": 0,
                "llm_calls": 0,
                "non_llm_calls": 0,
                # vNext-R2-C: Detailed LLM call stats
                "successful_llm_calls": 0,
                "failed_llm_calls": 0,
                "fallback_llm_calls": 0,
            }

        total_traces = len(rows)
        failed_traces = sum(1 for r in rows if r.get("status") == "failed")
        total_tokens = sum((r.get("token_input") or 0) + (r.get("token_output") or 0) for r in rows)
        total_latency = sum(r.get("latency_ms") or 0 for r in rows)

        # Count based on event_type first, then fallback to model_name
        llm_calls = 0
        non_llm_calls = 0
        
        # vNext-R2-C: Detailed LLM call counts
        successful_llm_calls = 0
        failed_llm_calls = 0
        fallback_llm_calls = 0
        
        for r in rows:
            event_type = r.get("event_type", "")
            model_name = r.get("model_name", "") or ""
            model_name_lower = model_name.lower()
            status = r.get("status", "")
            
            # vNext-R2-C: Classify LLM calls based on model_name and status
            if event_type == "llm_call":
                if model_name_lower == "fallback":
                    fallback_llm_calls += 1
                    llm_calls += 1  # Still counts as LLM call
                elif status == "success":
                    successful_llm_calls += 1
                    llm_calls += 1
                elif status == "failed":
                    failed_llm_calls += 1
                    llm_calls += 1
                else:
                    # Unknown status - count as llm_call but not in success/fail/fallback
                    llm_calls += 1
            # If event_type is "node_execution" or "agent_step", count as non-LLM
            elif event_type in ("node_execution", "agent_step"):
                non_llm_calls += 1
            # Legacy fallback: check model_name
            elif model_name and model_name_lower not in NON_LLM_MODEL_NAMES:
                llm_calls += 1
                # Assume successful if no explicit failure marker
                if status == "success":
                    successful_llm_calls += 1
                elif status == "failed":
                    failed_llm_calls += 1
            else:
                non_llm_calls += 1

        return {
            "total_traces": total_traces,
            "failed_traces": failed_traces,
            "total_tokens": total_tokens,
            "total_latency_ms": total_latency,
            "llm_calls": llm_calls,
            "non_llm_calls": non_llm_calls,
            # vNext-R2-C: Detailed LLM call stats
            "successful_llm_calls": successful_llm_calls,
            "failed_llm_calls": failed_llm_calls,
            "fallback_llm_calls": fallback_llm_calls,
        }

    def get_latest_traces(self, run_id: str, limit: int = 5) -> list[dict[str, Any]]:
        """Return the most recent traces for a run."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM trace_logs WHERE run_id = ? ORDER BY started_at DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        return _rows_to_list(rows)

    def get_node_io_summary(self, run_id: str) -> list[dict[str, Any]]:
        """Return per-node input/output/artifact summary."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT node_name, agent_name, model_name, status, latency_ms, "
                "token_input, token_output, input_payload_json, output_payload_json, "
                "artifact_refs_json FROM trace_logs WHERE run_id = ? ORDER BY started_at",
                (run_id,),
            ).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            result.append({
                "node_name": d.get("node_name"),
                "agent_name": d.get("agent_name"),
                "model_name": d.get("model_name"),
                "status": d.get("status"),
                "latency_ms": d.get("latency_ms"),
                "token_input": d.get("token_input"),
                "token_output": d.get("token_output"),
                "input_payload": _safe_parse_json(d.get("input_payload_json"), None),
                "output_payload": _safe_parse_json(d.get("output_payload_json"), None),
                "artifact_refs": _safe_parse_json(d.get("artifact_refs_json"), []),
            })
        return result


class ReportRepository:
    def add_report(self, report: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO reports (
                    report_id, run_id, title, report_status, content_markdown_path,
                    content_html_path, content_pdf_path, quality_summary_json,
                    created_by_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report["report_id"], report["run_id"], report["title"], report["report_status"],
                    report.get("content_markdown_path"), report.get("content_html_path"),
                    report.get("content_pdf_path"), json.dumps(report.get("quality_summary", {})),
                    report["created_by_agent"], report["created_at"], report["updated_at"],
                ),
            )

    def add_report_with_spans(
        self,
        report: dict[str, Any],
        spans: list[dict[str, Any]],
    ) -> None:
        """Atomically insert/replace report + clear old spans + insert new spans.

        All in a single transaction so that a partial failure rolls back completely.
        Raises with structured error dict if any step fails.
        """
        rid = report.get("report_id", "")
        run_id = report.get("run_id", "")
        with transaction() as conn:
            # 1. Insert / replace report row
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO reports (
                        report_id, run_id, title, report_status, content_markdown_path,
                        content_html_path, content_pdf_path, quality_summary_json,
                        created_by_agent, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rid, run_id, report["title"], report["report_status"],
                        report.get("content_markdown_path"), report.get("content_html_path"),
                        report.get("content_pdf_path"), json.dumps(report.get("quality_summary", {})),
                        report["created_by_agent"], report["created_at"], report["updated_at"],
                    ),
                )
            except Exception as exc:
                raise ReportWriteError(
                    code="DB_WRITE_REPORT_ROW_FAILED",
                    message=str(exc),
                    report_id=rid,
                    run_id=run_id,
                    span_id=None,
                    section_id=None,
                ) from exc

            # 2. Clear old spans
            try:
                conn.execute("DELETE FROM report_spans WHERE report_id = ?", (rid,))
            except Exception as exc:
                raise ReportWriteError(
                    code="DB_CLEAR_REPORT_SPANS_FAILED",
                    message=str(exc),
                    report_id=rid,
                    run_id=run_id,
                    span_id=None,
                    section_id=None,
                ) from exc

            # 3. Insert new spans
            for span in spans:
                sid = span.get("span_id", "")
                sec_id = span.get("section_id", "")
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO report_spans (
                            span_id, report_id, run_id, section_id, section_title, span_type,
                            text, claim_ids_json, evidence_ids_json, unsupported_flag, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sid, rid, run_id, sec_id, span.get("section_title", ""),
                            span.get("span_type", "paragraph"), span.get("text", ""),
                            json.dumps(span.get("claim_ids", [])),
                            json.dumps(span.get("evidence_ids", [])),
                            1 if span.get("unsupported_flag", False) else 0,
                            span.get("created_at", ""),
                        ),
                    )
                except Exception as exc:
                    raise ReportWriteError(
                        code="DB_WRITE_REPORT_SPAN_FAILED",
                        message=str(exc),
                        report_id=rid,
                        run_id=run_id,
                        span_id=sid,
                        section_id=sec_id,
                    ) from exc

    def add_report_span(self, span: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_spans (
                    span_id, report_id, run_id, section_id, section_title, span_type,
                    text, claim_ids_json, evidence_ids_json, unsupported_flag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    span["span_id"], span["report_id"], span["run_id"], span["section_id"],
                    span["section_title"], span["span_type"], span["text"],
                    json.dumps(span.get("claim_ids", [])), json.dumps(span.get("evidence_ids", [])),
                    1 if span.get("unsupported_flag", False) else 0, span["created_at"],
                ),
            )

    def clear_report_spans(self, report_id: str) -> None:
        with transaction() as conn:
            conn.execute("DELETE FROM report_spans WHERE report_id = ?", (report_id,))

    def get_report(self, run_id: str) -> dict[str, Any] | None:
        with transaction() as conn:
            report = conn.execute(
                "SELECT * FROM reports WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not report:
                return None
            spans_rows = conn.execute(
                "SELECT * FROM report_spans WHERE report_id = ? ORDER BY section_id, span_id",
                (report["report_id"],),
            ).fetchall()
        result = dict(report)
        
        # Read actual markdown content from file path
        # Priority: 1) DB path, 2) filesystem search for report_{run_id}_v{N}.md
        md_path = result.get("content_markdown_path", "")
        md_loaded = False
        if md_path:
            try:
                from pathlib import Path
                project_root = Path(__file__).parent.parent.parent.parent
                full_path = project_root / md_path
                if full_path.exists():
                    result["content_markdown"] = full_path.read_text(encoding="utf-8")
                    md_loaded = True
            except Exception:
                pass

        # Fallback: search filesystem when DB path is absent or file not found
        if not md_loaded:
            from pathlib import Path
            project_root = Path(__file__).parent.parent.parent.parent
            reports_dir = project_root / "data" / "reports"
            if reports_dir.exists():
                candidates = []
                for f in reports_dir.iterdir():
                    if f.is_file() and f.name.startswith(f"report_{run_id}_v") and f.suffix == ".md":
                        name = f.name[len(f"report_{run_id}_v"):-3]
                        try:
                            ver = int(name)
                            candidates.append((ver, f))
                        except ValueError:
                            pass
                if candidates:
                    candidates.sort(key=lambda x: x[0], reverse=True)
                    try:
                        result["content_markdown"] = candidates[0][1].read_text(encoding="utf-8")
                    except Exception:
                        pass
        
        result["quality_summary"] = _safe_parse_json(result.pop("quality_summary_json", None), {})
        spans = []
        for row in spans_rows:
            span = dict(row)
            span["claim_ids"] = _safe_parse_json(span.pop("claim_ids_json", None), [])
            span["evidence_ids"] = _safe_parse_json(span.pop("evidence_ids_json", None), [])
            spans.append(span)
        result["spans"] = spans
        return result

    def get_latest_report(self, run_id: str) -> dict[str, Any] | None:
        """Alias for get_report — returns the most recent report for a run."""
        return self.get_report(run_id)


class EvalRepository:
    def add_eval_log(self, eval_log: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_logs (
                    eval_id, run_id, schema_completion_rate, evidence_coverage_rate,
                    unsupported_claim_rate, review_pass_rate, rework_success_rate,
                    replay_success_rate, manual_correction_rate, source_coverage_count,
                    conflict_count, analysis_time_minutes, metrics_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_log["eval_id"], eval_log["run_id"], eval_log["schema_completion_rate"],
                    eval_log["evidence_coverage_rate"], eval_log["unsupported_claim_rate"],
                    eval_log.get("review_pass_rate"), eval_log.get("rework_success_rate"),
                    eval_log.get("replay_success_rate"), eval_log.get("manual_correction_rate"),
                    eval_log["source_coverage_count"], eval_log["conflict_count"],
                    eval_log.get("analysis_time_minutes"), json.dumps(eval_log.get("metrics", {})),
                    eval_log["created_at"],
                ),
            )

    def get_latest_eval(self, run_id: str) -> dict[str, Any] | None:
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM eval_logs WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metrics"] = _safe_parse_json(result.pop("metrics_json", None), {})
        if "metrics_before_json" in result:
            result["metrics_before"] = _safe_parse_json(result.pop("metrics_before_json", None), {})
        if "metrics_after_json" in result:
            result["metrics_after"] = _safe_parse_json(result.pop("metrics_after_json", None), {})
        return result


class MessageRepository:
    def add_message(self, message: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO messages (
                    message_id, run_id, task_id, sender, receiver, message_type,
                    schema_version, payload_json, metadata_json, trace_id,
                    parent_message_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["message_id"], message["run_id"], message.get("task_id", ""),
                    message["sender"], message["receiver"], message["message_type"],
                    message.get("schema_version", "1.0.0"),
                    json.dumps(message.get("payload", {}), ensure_ascii=False),
                    json.dumps(message.get("metadata", {}), ensure_ascii=False),
                    message.get("trace_id"), message.get("parent_message_id"),
                    message["created_at"],
                ),
            )

    def list_messages(self, run_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM messages WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["payload"] = _safe_parse_json(r.pop("payload_json", None), {})
            r["metadata"] = _safe_parse_json(r.pop("metadata_json", None), {})
            results.append(r)
        return results


class PiiLogRepository:
    def add_pii_log(self, log: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pii_logs (
                    pii_log_id, run_id, source_id, evidence_id, detected_types_json,
                    masked_text_path, risk_level, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log["pii_log_id"], log["run_id"], log.get("source_id"), log.get("evidence_id"),
                    json.dumps(log.get("detected_types", []), ensure_ascii=False),
                    log.get("masked_text_path"), log.get("risk_level", "low"),
                    log.get("status", "passed"), log["created_at"],
                ),
            )

    def list_pii_logs(self, run_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM pii_logs WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["detected_types"] = _safe_parse_json(r.pop("detected_types_json", None), [])
            results.append(r)
        return results


class ProjectRepository:
    """Repository for the Project-centric data model."""

    def create_project(self, project: dict[str, Any]) -> None:
        # Try to include metadata_json column (added by vNext-P0 migration).
        # If the column doesn't exist yet (pre-migration DB), fall back to the
        # base 9-column insert.
        base_cols = [
            "project_id", "project_name", "task_type", "target_region",
            "description", "analysis_dimensions_json", "status",
            "created_at", "updated_at",
        ]
        base_vals = [
            project["project_id"],
            project["project_name"],
            project.get("task_type", "competitor_landscape"),
            project.get("target_region", "global"),
            project.get("description", ""),
            json.dumps(project.get("analysis_dimensions", [])),
            project.get("status", "active"),
            project["created_at"],
            project["updated_at"],
        ]
        with transaction() as conn:
            try:
                meta_val = (
                    project.get("metadata_json")
                    or json.dumps(project.get("metadata", {}), ensure_ascii=False)
                )
                placeholders = ", ".join(["?"] * 10)
                col_names = ", ".join(base_cols + ["metadata_json"])
                conn.execute(
                    f"INSERT OR REPLACE INTO projects ({col_names}) VALUES ({placeholders})",
                    tuple(base_vals + [meta_val]),
                )
            except sqlite3.OperationalError:
                # Fallback: column not yet added by migration
                placeholders = ", ".join(["?"] * 9)
                col_names = ", ".join(base_cols)
                conn.execute(
                    f"INSERT OR REPLACE INTO projects ({col_names}) VALUES ({placeholders})",
                    tuple(base_vals),
                )

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["analysis_dimensions"] = _safe_parse_json(
            result.pop("analysis_dimensions_json", None), []
        )
        # vNext-P0: Parse metadata_json
        result["metadata"] = _safe_parse_json(
            result.pop("metadata_json", None), {}
        )
        return result

    def list_projects(self, status: str | None = None) -> list[dict[str, Any]]:
        with transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM projects ORDER BY created_at DESC"
                ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["analysis_dimensions"] = _safe_parse_json(
                r.pop("analysis_dimensions_json", None), []
            )
            results.append(r)
        return results

    def update_status(self, project_id: str, status: str) -> None:
        with transaction() as conn:
            conn.execute(
                "UPDATE projects SET status = ?, updated_at = datetime('now') WHERE project_id = ?",
                (status, project_id),
            )

    def add_project_product(self, product: dict[str, Any]) -> None:
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO project_products (
                    project_product_id, project_id, product_slug, product_name,
                    company_name, official_website, seed_urls_json, product_type,
                    region, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product["project_product_id"],
                    product["project_id"],
                    product["product_slug"],
                    product["product_name"],
                    product.get("company_name"),
                    product.get("official_website"),
                    json.dumps(product.get("seed_urls", [])),
                    product.get("product_type"),
                    product.get("region"),
                    product["created_at"],
                    product["updated_at"],
                ),
            )

    def list_project_products(self, project_id: str) -> list[dict[str, Any]]:
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM project_products WHERE project_id = ? ORDER BY product_name",
                (project_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["seed_urls"] = _safe_parse_json(r.pop("seed_urls_json", None), [])
            results.append(r)
        return results

    def get_project_with_products(
        self, project_id: str
    ) -> dict[str, Any] | None:
        project = self.get_project(project_id)
        if not project:
            return None

        products = self.list_project_products(project_id)

        with transaction() as conn:
            run_rows = conn.execute(
                """
                SELECT * FROM runs
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (project_id,),
            ).fetchall()
        runs = [dict(r) for r in run_rows]

        latest_run = None
        if runs:
            latest = runs[0]
            latest_run = {
                "run_id": latest.get("run_id"),
                "mode": latest.get("mode"),
                "status": latest.get("status"),
                "current_node": latest.get("current_node"),
                "error_message": latest.get("error_message"),
                "started_at": latest.get("started_at"),
                "completed_at": latest.get("completed_at"),
                "created_at": latest.get("created_at"),
            }

        run_ids = [r["run_id"] for r in runs]
        aggregates = {
            "source_count": 0,
            "evidence_count": 0,
            "claim_count": 0,
            "fact_count": 0,
            "report_count": 0,
        }
        if run_ids:
            placeholders = ",".join(["?"] * len(run_ids))
            with transaction() as conn:
                aggregates["source_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM sources WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]
                aggregates["evidence_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM evidence_items WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]
                aggregates["claim_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM claims WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]
                aggregates["fact_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM facts WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]
                aggregates["report_count"] = conn.execute(
                    f"SELECT COUNT(*) FROM reports WHERE run_id IN ({placeholders})",
                    run_ids,
                ).fetchone()[0]

        project["products"] = products
        project["runs"] = runs
        project["latest_run"] = latest_run
        project["aggregates"] = aggregates
        # vNext-P0: Ensure metadata is always present in the returned project dict
        if "metadata" not in project:
            project["metadata"] = project.get("metadata") or {}
        return project


class WorkflowRepository:
    """Repository for workflow_nodes and workflow_edges tables."""

    # Main backbone nodes in order
    BACKBONE_NODES = [
        "build_task_brief",
        "plan_schema",
        "plan_sources",
        "collect_sources",
        "evaluate_evidence",
        "pii_scrub",
        "extract_facts",
        "detect_schema_gaps",
        "analyze_dimensions",
        "review_claims",
        "execute_rework",
        "prepare_human_intervention",
        "write_report_v2",
        "final_review",
        "export_report",
        "compute_metrics",
    ]

    # Main backbone edges
    BACKBONE_EDGES = [
        ("build_task_brief", "plan_schema"),
        ("plan_schema", "plan_sources"),
        ("plan_sources", "collect_sources"),
        ("collect_sources", "evaluate_evidence"),
        ("evaluate_evidence", "pii_scrub"),
        ("pii_scrub", "extract_facts"),
        ("extract_facts", "detect_schema_gaps"),
        ("detect_schema_gaps", "analyze_dimensions"),
        ("analyze_dimensions", "review_claims"),
        ("review_claims", "execute_rework"),
        ("execute_rework", "prepare_human_intervention"),
        # write_report_v2 is the single report node (v1/v2 routing removed; v2 is always used)
        ("prepare_human_intervention", "write_report_v2"),
        ("write_report_v2", "final_review"),
        # final_review routes conditionally:
        #   rework_required -> write_report_v2 (loop)
        #   approved/exported -> export_report
        ("final_review", "write_report_v2"),  # conditional
        ("final_review", "export_report"),  # conditional
        ("export_report", "compute_metrics"),
    ]

    def init_workflow_graph(
        self,
        run_id: str,
        node_names: list[str] | None = None,
        edges: list[tuple[str, str]] | None = None,
    ) -> None:
        """Initialize workflow_nodes and workflow_edges for a run.

        If node_names/edges are provided, use them; otherwise use backbone defaults.
        Uses INSERT OR IGNORE to be idempotent (safe to call multiple times).
        """
        if node_names is None:
            node_names = self.BACKBONE_NODES
        if edges is None:
            edges = self.BACKBONE_EDGES

        now = _utc_now()

        with transaction() as conn:
            # Insert nodes only if they don't exist (INSERT OR IGNORE)
            # This preserves existing node state (completed/failed/running) and metadata
            for node_name in node_names:
                node_id = f"{run_id}_{node_name}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO workflow_nodes (
                        node_id, run_id, node_name, node_type, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node_id,
                        run_id,
                        node_name,
                        "backbone",
                        "pending",
                        now,
                        now,
                    ),
                )

            # Insert edges only if they don't exist (INSERT OR IGNORE)
            # This preserves created_at and avoids unnecessary updates
            for from_node, to_node in edges:
                edge_id = f"{run_id}_{from_node}_{to_node}"
                edge_type = "sequence"
                # Determine edge type for conditional edges
                if from_node == "final_review":
                    edge_type = "conditional"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO workflow_edges (
                        edge_id, run_id, from_node, to_node, edge_type, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        run_id,
                        from_node,
                        to_node,
                        edge_type,
                        now,
                    ),
                )

    def start_node(
        self,
        run_id: str,
        node_name: str,
        input_summary: dict[str, Any] | None = None,
    ) -> None:
        """Mark a node as running and record input summary."""
        node_id = f"{run_id}_{node_name}"
        now = _utc_now()
        input_json = json.dumps(input_summary, ensure_ascii=False) if input_summary else None

        with transaction() as conn:
            conn.execute(
                """
                UPDATE workflow_nodes
                SET status = ?, started_at = ?, updated_at = ?, input_summary_json = ?
                WHERE node_id = ?
                """,
                ("running", now, now, input_json, node_id),
            )

    def complete_node(
        self,
        run_id: str,
        node_name: str,
        output_summary: dict[str, Any] | None = None,
        latency_ms: int = 0,
    ) -> None:
        """Mark a node as completed and record output summary and latency."""
        node_id = f"{run_id}_{node_name}"
        now = _utc_now()
        output_json = json.dumps(output_summary, ensure_ascii=False) if output_summary else None

        with transaction() as conn:
            conn.execute(
                """
                UPDATE workflow_nodes
                SET status = ?, completed_at = ?, updated_at = ?,
                    output_summary_json = ?, latency_ms = ?
                WHERE node_id = ?
                """,
                ("completed", now, now, output_json, latency_ms, node_id),
            )

    def fail_node(
        self,
        run_id: str,
        node_name: str,
        error_message: str,
        output_summary: dict[str, Any] | None = None,
        latency_ms: int = 0,
    ) -> None:
        """Mark a node as failed and record error details."""
        node_id = f"{run_id}_{node_name}"
        now = _utc_now()
        output_json = json.dumps(output_summary, ensure_ascii=False) if output_summary else None

        with transaction() as conn:
            conn.execute(
                """
                UPDATE workflow_nodes
                SET status = ?, completed_at = ?, updated_at = ?,
                    output_summary_json = ?, latency_ms = ?, error_message = ?
                WHERE node_id = ?
                """,
                ("failed", now, now, output_json, latency_ms, error_message, node_id),
            )

    def update_node_status(self, run_id: str, node_name: str, status: str) -> None:
        """Update the status of a workflow node (e.g. reset a stale 'running' to 'pending')."""
        node_id = f"{run_id}_{node_name}"
        now = _utc_now()
        with transaction() as conn:
            conn.execute(
                "UPDATE workflow_nodes SET status = ?, updated_at = ? WHERE node_id = ?",
                (status, now, node_id),
            )

    def list_workflow_nodes(self, run_id: str) -> list[dict[str, Any]]:
        """List all workflow nodes for a run, sorted by backbone order.

        Backbone nodes appear first in DAG order (as defined in BACKBONE_NODES).
        Custom nodes appear after backbone nodes, sorted alphabetically.
        """
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_nodes WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["input_summary"] = _safe_parse_json(r.pop("input_summary_json", None))
            r["output_summary"] = _safe_parse_json(r.pop("output_summary_json", None))
            results.append(r)

        # Build position map for backbone nodes
        backbone_order = {name: idx for idx, name in enumerate(self.BACKBONE_NODES)}

        def sort_key(node: dict[str, Any]) -> tuple[int, str]:
            node_name = node.get("node_name", "")
            backbone_idx = backbone_order.get(node_name)
            if backbone_idx is not None:
                return (0, backbone_idx)
            else:
                # Custom nodes sort alphabetically after backbone
                return (1, node_name)

        results.sort(key=sort_key)
        return results

    def list_workflow_edges(self, run_id: str) -> list[dict[str, Any]]:
        """List all workflow edges for a run."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_edges WHERE run_id = ? ORDER BY from_node, to_node",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            r = dict(row)
            r["condition"] = _safe_parse_json(r.pop("condition_json", None))
            results.append(r)
        return results

    def get_node_status(self, run_id: str, node_name: str) -> dict[str, Any] | None:
        """Get status of a specific node."""
        node_id = f"{run_id}_{node_name}"
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_nodes WHERE node_id = ?", (node_id,)
            ).fetchone()
        if not row:
            return None
        r = dict(row)
        r["input_summary"] = _safe_parse_json(r.pop("input_summary_json", None))
        r["output_summary"] = _safe_parse_json(r.pop("output_summary_json", None))
        return r

    def pause_node(
        self,
        run_id: str,
        node_name: str,
        output_summary: dict[str, Any] | None = None,
        reason: str = "",
    ) -> None:
        """Mark a node as paused for human intervention."""
        node_id = f"{run_id}_{node_name}"
        now = _utc_now()
        output_json = json.dumps(output_summary, ensure_ascii=False) if output_summary else None

        with transaction() as conn:
            conn.execute(
                """
                UPDATE workflow_nodes
                SET status = ?, completed_at = ?, updated_at = ?,
                    output_summary_json = ?, error_message = ?
                WHERE node_id = ?
                """,
                ("paused", now, now, output_json, reason, node_id),
            )


class HumanInterventionRepository:
    """Repository for human_interventions table."""

    def create_intervention(self, intervention: dict[str, Any]) -> None:
        """Create a new human intervention record."""
        intervention_id  = intervention["intervention_id"]
        run_id           = intervention["run_id"]
        node_name        = intervention.get("node_name")
        artifact_type    = intervention.get("artifact_type")
        artifact_id      = intervention.get("artifact_id")
        action           = intervention.get("action", "pending")
        status           = intervention.get("status", "pending")
        before_json      = json.dumps(intervention.get("before_json")) if intervention.get("before_json") else None
        after_json       = json.dumps(intervention.get("after_json")) if intervention.get("after_json") else None
        comment          = intervention.get("comment")
        created_at       = intervention["created_at"]
        resolved_at      = intervention.get("resolved_at")
        created_by       = intervention.get("created_by", "system")
        resolved_by      = intervention.get("resolved_by")

        # Try INSERT with created_by/resolved_by first (new schema)
        try:
            with transaction() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO human_interventions (
                        intervention_id, run_id, node_name, artifact_type, artifact_id,
                        action, status, before_json, after_json, comment,
                        created_at, resolved_at, created_by, resolved_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (intervention_id, run_id, node_name, artifact_type, artifact_id,
                     action, status, before_json, after_json, comment,
                     created_at, resolved_at, created_by, resolved_by),
                )
            return
        except sqlite3.IntegrityError:
            # Already exists — UPDATE instead
            pass
        except sqlite3.OperationalError:
            # Old schema without created_by/resolved_by — try INSERT without them
            pass

        # Fallback: INSERT without created_by/resolved_by (old schema)
        try:
            with transaction() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO human_interventions (
                        intervention_id, run_id, node_name, artifact_type, artifact_id,
                        action, status, before_json, after_json, comment,
                        created_at, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (intervention_id, run_id, node_name, artifact_type, artifact_id,
                     action, status, before_json, after_json, comment,
                     created_at, resolved_at),
                )
            return
        except sqlite3.IntegrityError:
            pass

        # Fallback UPDATE for existing record
        with transaction() as conn:
            conn.execute(
                """
                UPDATE human_interventions
                SET node_name=?, artifact_type=?, artifact_id=?, action=?, status=?,
                    before_json=?, after_json=?, comment=?,
                    resolved_at=?, resolved_by=?
                WHERE intervention_id=?
                """,
                (node_name, artifact_type, artifact_id, action, status,
                 before_json, after_json, comment, resolved_at, resolved_by, intervention_id),
            )

    def list_interventions(
        self,
        run_id: str,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List interventions for a run, optionally filtered by status."""
        with transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM human_interventions WHERE run_id = ? AND status = ? ORDER BY created_at",
                    (run_id, status),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM human_interventions WHERE run_id = ? ORDER BY created_at",
                    (run_id,),
                ).fetchall()
        return self._parse_interventions(rows)

    def get_intervention(self, intervention_id: str) -> dict[str, Any] | None:
        """Get a specific intervention by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM human_interventions WHERE intervention_id = ?",
                (intervention_id,),
            ).fetchone()
        if not row:
            return None
        interventions = self._parse_interventions([row])
        return interventions[0] if interventions else None

    def resolve_intervention(
        self,
        intervention_id: str,
        action: str,
        after_json: dict[str, Any] | None = None,
        comment: str = "",
        resolved_by: str = "system",
    ) -> dict[str, Any] | None:
        """Resolve an intervention with approve/reject/edit/respond action."""
        valid_actions = {"approve", "reject", "edit", "respond"}
        if action not in valid_actions:
            raise ValueError(f"Invalid action: {action}. Must be one of {valid_actions}")

        now = _utc_now()
        after_json_str = json.dumps(after_json, ensure_ascii=False) if after_json else None

        with transaction() as conn:
            conn.execute(
                """
                UPDATE human_interventions
                SET status = 'resolved',
                    action = ?,
                    after_json = ?,
                    comment = ?,
                    resolved_at = ?,
                    resolved_by = ?
                WHERE intervention_id = ?
                """,
                (action, after_json_str, comment, now, resolved_by, intervention_id),
            )
        return self.get_intervention(intervention_id)

    def _update_after_json(
        self,
        intervention_id: str,
        after_json: dict[str, Any],
        comment: str = "",
    ) -> None:
        """Update after_json without changing intervention status."""
        after_str = json.dumps(after_json, ensure_ascii=False) if after_json else None
        with transaction() as conn:
            if comment:
                conn.execute(
                    "UPDATE human_interventions SET after_json = ?, comment = ? WHERE intervention_id = ?",
                    (after_str, comment, intervention_id),
                )
            else:
                conn.execute(
                    "UPDATE human_interventions SET after_json = ? WHERE intervention_id = ?",
                    (after_str, intervention_id),
                )

    def cancel_intervention(
        self,
        intervention_id: str,
        comment: str = "",
    ) -> dict[str, Any] | None:
        """Cancel an intervention."""
        now = _utc_now()

        with transaction() as conn:
            conn.execute(
                """
                UPDATE human_interventions
                SET status = 'cancelled',
                    comment = ?,
                    resolved_at = ?
                WHERE intervention_id = ?
                """,
                (comment, now, intervention_id),
            )
        return self.get_intervention(intervention_id)

    def create_review_interventions_from_rework(
        self,
        run_id: str,
        rework_tasks: list[dict[str, Any]],
        review_issues: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Create pending interventions for failed rework tasks or review issues.

        Returns list of created intervention records.
        """
        import uuid as _uuid
        interventions = []
        now = _utc_now()

        # Create interventions for failed rework tasks
        for task in rework_tasks:
            if task.get("status") == "failed":
                # Use rework_id or task_id as part of the key, fallback to uuid
                task_key = task.get("rework_id") or task.get("task_id") or _uuid.uuid4().hex[:12]
                intervention_id = f"interv_rework_{task_key}"
                intervention = {
                    "intervention_id": intervention_id,
                    "run_id": run_id,
                    "node_name": task.get("target_node", "execute_rework"),
                    "artifact_type": "rework",
                    "artifact_id": task.get("rework_id") or task.get("task_id") or task_key,
                    "action": "pending",
                    "status": "pending",
                    "before_json": {
                        "task_status": task.get("status"),
                        "reason": task.get("error_message", task.get("reason", "")),
                        "metrics_before": task.get("metrics_before", {}),
                    },
                    "comment": f"Rework task failed: {task.get('error_message', task.get('reason', 'Unknown error'))}",
                    "created_at": now,
                    "created_by": "system",
                }
                self.create_intervention(intervention)
                interventions.append(intervention)

        # Create interventions for review issues if provided
        if review_issues:
            for issue in review_issues:
                if issue.get("requires_human_review") or issue.get("priority") in ("high", "critical"):
                    # Use issue_id, rework_request_id, or artifact_id as part of the key, fallback to uuid
                    issue_key = (
                        issue.get("issue_id")
                        or issue.get("rework_request_id")
                        or issue.get("artifact_id")
                        or _uuid.uuid4().hex[:12]
                    )
                    intervention_id = f"interv_review_{issue_key}"
                    intervention = {
                        "intervention_id": intervention_id,
                        "run_id": run_id,
                        "node_name": issue.get("node_name", "review_claims"),
                        "artifact_type": issue.get("artifact_type", "general"),
                        "artifact_id": issue.get("artifact_id") or issue_key,
                        "action": "pending",
                        "status": "pending",
                        "before_json": issue,
                        "comment": issue.get("message", ""),
                        "created_at": now,
                        "created_by": "system",
                    }
                    self.create_intervention(intervention)
                    interventions.append(intervention)

        return interventions

    def _parse_interventions(self, rows: list[Any]) -> list[dict[str, Any]]:
        """Parse intervention rows into dicts with parsed JSON fields."""
        results = []
        for row in rows:
            r = dict(row)
            r["before_json"] = _safe_parse_json(r.pop("before_json", None))
            r["after_json"] = _safe_parse_json(r.pop("after_json", None))
            results.append(r)
        return results


def _utc_now() -> str:
    """Return current UTC time in ISO format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
# Internal helper: actually execute a run's workflow.
# Exposed so both /api/runs/{id}/start and /api/projects/{id}/runs
# can invoke it without duplicating code.
# ----------------------------------------------------------------------
def start_run_execution(run_id: str) -> dict[str, Any]:
    """Execute the workflow for an existing pending/running run.

    Returns a dict with run_id, status, current_node, error_message.
    """
    from datetime import datetime, timezone
    from backend.app.orchestrator.graph import run_workflow

    def utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    repo = RunRepository()
    run = repo.get_run(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    # Load task_brief
    # NOTE: get_run() already parses task_brief_json into run["task_brief"].
    # Use run["task_brief"] directly (same pattern as _start_run_sync in runs.py).
    task_brief = run.get("task_brief") or {}
    if not task_brief:
        # Fallback: try task_brief_json directly (for cases where pop wasn't done)
        if run.get("task_brief_json"):
            try:
                task_brief = json.loads(run["task_brief_json"])
                _logger.info("start_run_execution: parsed task_brief from JSON, products=%d",
                    len(task_brief.get("products", [])))
            except Exception:
                pass
    if not task_brief:
        _logger.error("start_run_execution: task_brief is empty even after fallback! run_id=%s", run_id)
        task_brief = {
            "title": run.get("task_title", "AI Agent analysis"),
            "products": [],
            "analysis_dimensions": [],
        }

    # Mark started
    now = utc_now()
    repo.update_status(run_id, "running", "build_task_brief", started_at=now)

    try:
        state = run_workflow({
            "run_id": run_id,
            "task_id": run["task_id"],
            "task_brief": task_brief,
            "mode": run["mode"],
        })
    except Exception as exc:
        _logger.error("start_run_execution run %s exception: %s", run_id, exc, exc_info=True)
        # Graceful degradation: never mark failed.
        try:
            from backend.app.storage.repositories import ReportRepository
            report_record = {
                "report_id": f"report_{run_id}",
                "run_id": run_id,
                "title": task_brief.get("title", "竞品分析报告") if task_brief else "竞品分析报告",
                "report_status": "reviewed_with_gaps",
                "content_markdown_path": "",
                "content_html_path": "",
                "content_pdf_path": "",
                "quality_summary": {
                    "_workflow_exception": str(exc),
                    "_degraded": True,
                    "products_analyzed": len(task_brief.get("products", []) if task_brief else []),
                    "evidence_count": 0,
                    "signed_claims_count": 0,
                },
                "created_by_agent": "WriterAgent",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            ReportRepository().upsert_report(report_record)
        except Exception as db_exc:
            _logger.error("start_run_execution run %s: degraded DB write failed: %s", run_id, db_exc)
        repo.update_status(run_id, "completed", "workflow_exception", completed_at=utc_now())
        return {
            "run_id": run_id,
            "status": "completed",
            "current_node": "workflow_exception",
        }

    # Graceful degradation: never mark 'failed'. All errors degrade to 'reviewed_with_gaps'.
    report_draft = state.get("report_draft") or {}
    errors = state.get("errors", []) or []
    report_status = report_draft.get("report_status")

    critical_errors = {
        "UNSUPPORTED_REPORT_SPAN",
        "DB_WRITE_REPORT_FAILED",
        "NODE_EXCEPTION",
        "PII_NOT_MASKED",
        # NOTE: BLOCKED_NO_SIGNED_CLAIMS is intentionally NOT here.
        # final_review no longer adds this error code (it degrades gracefully instead).
        # NODE_TIMEOUT is not here because the timeout handler already recovers
        # from checkpoint data, and the node is retried by coverage_critic.
    }
    has_critical_error = any(e.get("reason_code") in critical_errors for e in errors)
    is_blocked = report_status in ("blocked", "blocked_consistency")

    if has_critical_error or is_blocked:
        # Override to 'reviewed_with_gaps' so DB CHECK passes.
        report_draft["report_status"] = "reviewed_with_gaps"
        report_draft["_degraded_from"] = report_status or ""
        report_draft["_degraded_errors"] = [e.get("reason_code", "") for e in errors if e.get("reason_code")]
        _logger.warning(
            "start_run_execution run %s degraded to reviewed_with_gaps: blocked=%s errors=%s",
            run_id, is_blocked, [e.get("reason_code") for e in errors]
        )
        try:
            from backend.app.storage.repositories import ReportRepository
            report_record = {
                "report_id": report_draft.get("report_id") or f"report_{run_id}",
                "run_id": run_id,
                "title": report_draft.get("title", "竞品分析报告"),
                "report_status": "reviewed_with_gaps",
                "content_markdown_path": report_draft.get("content_markdown_path", ""),
                "content_html_path": report_draft.get("content_html_path", ""),
                "content_pdf_path": report_draft.get("content_pdf_path", ""),
                "quality_summary": report_draft.get("quality_summary", {}),
                "created_by_agent": "WriterAgent",
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            ReportRepository().upsert_report(report_record)
        except Exception as db_exc:
            _logger.error("start_run_execution run %s: degraded DB write failed: %s", run_id, db_exc)

    repo.update_status(run_id, "completed", "compute_metrics", completed_at=utc_now())
    return {
        "run_id": run_id,
        "status": "completed",
        "current_node": "compute_metrics",
        "error_message": None,
    }


class ReworkTaskRepository:
    """Repository for human-initiated rework tasks (rework_tasks table).

    Supports two types of rework:
    1. Intervention-based: created from human interventions (existing flow)
    2. Product coverage gap: created from insufficient/partial product coverage
    """

    def create_rework_task(self, task: dict[str, Any]) -> None:
        """Create a new rework task record."""
        rework_id = task["rework_id"]
        intervention_id = task.get("intervention_id")
        run_id = task["run_id"]
        project_id = task.get("project_id")
        source_node = task.get("source_node")
        target_artifact_type = task.get("target_artifact_type")
        target_artifact_id = task.get("target_artifact_id")
        reason_codes = task.get("reason_codes", [])
        status = task.get("status", "pending")
        rework_plan_json = task.get("rework_plan_json")
        before_json = task.get("before_json")
        after_json = task.get("after_json")
        created_at = task.get("created_at", _utc_now())
        updated_at = task.get("updated_at")
        created_by = task.get("created_by", "frontend_user")
        # Product coverage gap fields
        product_id = task.get("product_id")
        product_name = task.get("product_name")
        target_node = task.get("target_node")
        required_action = task.get("required_action")
        seed_urls = task.get("seed_urls", [])
        metrics_before = task.get("metrics_before")
        metrics_after = task.get("metrics_after")

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO rework_tasks (
                    rework_id, intervention_id, run_id, project_id, source_node,
                    target_artifact_type, target_artifact_id, reason_codes_json,
                    status, rework_plan_json, before_json, after_json,
                    created_at, updated_at, created_by,
                    product_id, product_name, target_node, required_action,
                    seed_urls_json, metrics_before_json, metrics_after_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rework_id, intervention_id, run_id, project_id, source_node,
                    target_artifact_type, target_artifact_id,
                    json.dumps(reason_codes, ensure_ascii=False),
                    status,
                    json.dumps(rework_plan_json, ensure_ascii=False) if rework_plan_json else None,
                    json.dumps(before_json, ensure_ascii=False) if before_json else None,
                    json.dumps(after_json, ensure_ascii=False) if after_json else None,
                    created_at, updated_at, created_by,
                    product_id, product_name, target_node, required_action,
                    json.dumps(seed_urls, ensure_ascii=False),
                    json.dumps(metrics_before, ensure_ascii=False) if metrics_before else None,
                    json.dumps(metrics_after, ensure_ascii=False) if metrics_after else None,
                ),
            )

    def get_rework_task(self, rework_id: str) -> dict[str, Any] | None:
        """Get a single rework task by rework_id."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM rework_tasks WHERE rework_id = ?",
                (rework_id,),
            ).fetchone()
        if not row:
            return None
        return self._parse_task(dict(row))

    def list_rework_tasks(self, run_id: str) -> list[dict[str, Any]]:
        """List all rework tasks for a run."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM rework_tasks WHERE run_id = ? ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [self._parse_task(dict(r)) for r in rows]

    def update_rework_task(
        self,
        rework_id: str,
        status: str | None = None,
        rework_plan_json: dict[str, Any] | None = None,
        after_json: dict[str, Any] | None = None,
        seed_urls: list[str] | None = None,
        error_json: dict[str, Any] | None = None,
        metrics_after: dict[str, Any] | None = None,
        completed_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Update rework task fields."""
        now = _utc_now()
        updates = []
        params = []
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if rework_plan_json is not None:
            updates.append("rework_plan_json = ?")
            params.append(json.dumps(rework_plan_json, ensure_ascii=False))
        if after_json is not None:
            updates.append("after_json = ?")
            params.append(json.dumps(after_json, ensure_ascii=False))
        if seed_urls is not None:
            updates.append("seed_urls_json = ?")
            params.append(json.dumps(seed_urls, ensure_ascii=False))
        if error_json is not None:
            updates.append("error_json = ?")
            params.append(json.dumps(error_json, ensure_ascii=False))
        if metrics_after is not None:
            updates.append("metrics_after_json = ?")
            params.append(json.dumps(metrics_after, ensure_ascii=False))
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if updates:
            updates.append("updated_at = ?")
            params.append(now)
            params.append(rework_id)
            with transaction() as conn:
                conn.execute(
                    f"UPDATE rework_tasks SET {', '.join(updates)} WHERE rework_id = ?",
                    params,
                )
        return self.get_rework_task(rework_id)

    def get_rework_task_by_product(self, run_id: str, product_id: str) -> dict[str, Any] | None:
        """Find the first rework task for a given run and product_id."""
        with transaction() as conn:
            rows = conn.execute(
                """
                SELECT * FROM rework_tasks
                WHERE run_id = ? AND (product_id = ? OR product_id = ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (run_id, product_id, product_id.strip().lower()),
            ).fetchall()
        if not rows:
            return None
        row = dict(rows[0])
        row.pop("seed_urls_json", None)
        return self._parse_task(row)

    def _parse_task(self, row: dict[str, Any]) -> dict[str, Any]:
        row["reason_codes"] = _safe_parse_json(row.pop("reason_codes_json", None), [])
        row["rework_plan_json"] = _safe_parse_json(row.pop("rework_plan_json", None), None)
        row["before_json"] = _safe_parse_json(row.pop("before_json", None), None)
        row["after_json"] = _safe_parse_json(row.pop("after_json", None), None)
        row["seed_urls"] = _safe_parse_json(row.get("seed_urls_json"), [])
        row["error_json"] = _safe_parse_json(row.get("error_json", None), None)
        row["metrics_before"] = _safe_parse_json(row.get("metrics_before_json", None), None)
        row["metrics_after"] = _safe_parse_json(row.get("metrics_after_json", None), None)
        return row


# ----------------------------------------------------------------------
# ResearchPlan Repository (vNext-R1)
# ----------------------------------------------------------------------

class ResearchPlanRepository:
    """Repository for research_plans table (vNext-R1).

    Stores research plans with flexible payload_json for schema evolution.
    """

    def create_research_plan(self, plan: dict[str, Any]) -> str:
        """Create a new research plan record. Returns the plan_id."""
        plan_id = plan["research_plan_id"]
        project_id = plan.get("project_id", "")
        status = plan.get("status", "draft")
        user_query = plan.get("user_query", "")
        schema_type = plan.get("schema_type", "ai_agent_platform")
        target_region = plan.get("target_region", "global")
        mode = plan.get("mode", "review")
        generated_by = plan.get("generated_by", "fallback")
        payload_json = plan.get("payload_json", json.dumps(plan))
        dag_id = plan.get("dag_id")
        created_at = plan.get("created_at", _utc_now())
        updated_at = plan.get("updated_at", _utc_now())

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO research_plans (
                    id, project_id, status, user_query, schema_type,
                    target_region, mode, generated_by, payload_json,
                    dag_id, created_at, updated_at, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    plan_id, project_id, status, user_query, schema_type,
                    target_region, mode, generated_by, payload_json,
                    dag_id, created_at, updated_at,
                ),
            )
        return plan_id

    def get_research_plan(self, plan_id: str) -> dict[str, Any] | None:
        """Get a research plan by ID with parsed payload."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM research_plans WHERE id = ?",
                (plan_id,),
            ).fetchone()
        if not row:
            return None
        return self._parse_plan(dict(row))

    def list_research_plans(
        self,
        project_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List research plans, optionally filtered by project_id or status."""
        query = "SELECT * FROM research_plans WHERE 1=1"
        params = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"

        with transaction() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._parse_plan(dict(r)) for r in rows]

    def update_research_plan(
        self,
        plan_id: str,
        status: str | None = None,
        payload_json: str | None = None,
        dag_id: str | None = None,
        confirmed_at: str | None = None,
    ) -> dict[str, Any] | None:
        """Update research plan fields. Returns updated plan or None."""
        now = _utc_now()
        updates = ["updated_at = ?"]
        params = [now]
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if payload_json is not None:
            updates.append("payload_json = ?")
            params.append(payload_json)
        if dag_id is not None:
            updates.append("dag_id = ?")
            params.append(dag_id)
        if confirmed_at is not None:
            updates.append("confirmed_at = ?")
            params.append(confirmed_at)

        params.append(plan_id)
        with transaction() as conn:
            conn.execute(
                f"UPDATE research_plans SET {', '.join(updates)} WHERE id = ?",
                params,
            )
        return self.get_research_plan(plan_id)

    def delete_research_plan(self, plan_id: str) -> bool:
        """Delete a research plan. Returns True if deleted."""
        with transaction() as conn:
            cur = conn.execute(
                "DELETE FROM research_plans WHERE id = ?",
                (plan_id,),
            )
        return cur.rowcount > 0

    def _parse_plan(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a plan row into a dict with parsed payload."""
        # Read payload_json and parse it for full ResearchPlan
        payload_json = row.pop("payload_json", None)
        parsed_payload = _safe_parse_json(payload_json, None)
        
        # Reconstruct full plan from parsed payload
        if parsed_payload:
            # Use full parsed payload
            result = parsed_payload
        else:
            # Fallback: reconstruct from individual fields
            result = {
                "research_plan_id": row.get("id"),
                "project_id": row.get("project_id"),
                "status": row.get("status"),
                "user_query": row.get("user_query"),
                "schema_type": row.get("schema_type"),
                "target_region": row.get("target_region"),
                "mode": row.get("mode"),
                "generated_by": row.get("generated_by"),
                "dag_id": row.get("dag_id"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "confirmed_at": row.get("confirmed_at"),
            }
        
        # Add row metadata
        result["_row_id"] = row.get("id")
        return result


class ExecutionDAGRepository:
    """Repository for execution_dags table (vNext-R1).

    Stores compiled execution DAGs with flexible payload_json.
    """

    def create_dag(self, dag: dict[str, Any]) -> str:
        """Create a new execution DAG. Returns the dag_id."""
        dag_id = dag["dag_id"]
        research_plan_id = dag.get("research_plan_id", "")
        status = dag.get("status", "pending")
        payload_json = dag.get("payload_json", json.dumps(dag))
        created_at = dag.get("created_at", _utc_now())
        updated_at = dag.get("updated_at", _utc_now())

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO execution_dags (
                    id, research_plan_id, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (dag_id, research_plan_id, status, payload_json, created_at, updated_at),
            )
        return dag_id

    def get_dag(self, dag_id: str) -> dict[str, Any] | None:
        """Get an execution DAG by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM execution_dags WHERE id = ?",
                (dag_id,),
            ).fetchone()
        if not row:
            return None
        return self._parse_dag(dict(row))

    def get_dag_by_research_plan(self, research_plan_id: str) -> dict[str, Any] | None:
        """Get the execution DAG for a research plan."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM execution_dags WHERE research_plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (research_plan_id,),
            ).fetchone()
        if not row:
            return None
        return self._parse_dag(dict(row))

    def list_dags(self, research_plan_id: str | None = None) -> list[dict[str, Any]]:
        """List execution DAGs, optionally filtered by research_plan_id."""
        if research_plan_id:
            query = "SELECT * FROM execution_dags WHERE research_plan_id = ? ORDER BY created_at DESC"
            params = [research_plan_id]
        else:
            query = "SELECT * FROM execution_dags ORDER BY created_at DESC"
            params = []

        with transaction() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._parse_dag(dict(r)) for r in rows]

    def update_dag_status(self, dag_id: str, status: str) -> dict[str, Any] | None:
        """Update DAG status."""
        now = _utc_now()
        with transaction() as conn:
            conn.execute(
                "UPDATE execution_dags SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, dag_id),
            )
        return self.get_dag(dag_id)

    def _parse_dag(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a DAG row into a dict with parsed payload."""
        payload_json = row.pop("payload_json", "{}")
        dag_id = row.pop("id")
        
        # Parse the payload_json
        parsed_payload = _safe_parse_json(payload_json, {})
        
        # Build result
        result = {
            "dag_id": dag_id,
            "research_plan_id": row.get("research_plan_id"),
            "status": row.get("status", "pending"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        
        # Merge parsed payload if it exists
        if parsed_payload:
            result.update(parsed_payload)
        
        return result


class ReportSectionRepository:
    """Repository for Deep Report v2 sections (vNext-R3-A)."""

    def create_section(self, section: dict[str, Any]) -> None:
        """Create a new report section."""
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_sections (
                    section_id, report_id, run_id, section_index, section_slug,
                    section_title, section_type, min_word_count, target_word_count,
                    status, depth_score, evidence_count, claim_count, word_count,
                    revision_count, writing_requirements_json, review_notes, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section["section_id"], section["report_id"], section["run_id"],
                    section["section_index"], section["section_slug"], section["section_title"],
                    section.get("section_type", "chapter"), section.get("min_word_count", 800),
                    section.get("target_word_count", 1200), section.get("status", "pending"),
                    section.get("depth_score"), section.get("evidence_count", 0),
                    section.get("claim_count", 0), section.get("word_count", 0),
                    section.get("revision_count", 0),
                    json.dumps(section.get("writing_requirements", {})),
                    section.get("review_notes"),
                    json.dumps(section.get("metadata", {})),
                    section["created_at"], section["updated_at"],
                ),
            )

    def get_section(self, section_id: str) -> dict[str, Any] | None:
        """Get a section by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM report_sections WHERE section_id = ?", (section_id,)
            ).fetchone()
        if not row:
            return None
        return self._parse_section(dict(row))

    def get_sections_by_report(self, report_id: str) -> list[dict[str, Any]]:
        """Get all sections for a report, ordered by section_index."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM report_sections WHERE report_id = ? ORDER BY section_index",
                (report_id,),
            ).fetchall()
        return [self._parse_section(dict(row)) for row in rows]

    def update_section(self, section_id: str, updates: dict[str, Any]) -> None:
        """Update a section with the given updates."""
        now = _utc_now()
        with transaction() as conn:
            # Build dynamic update query
            set_clauses = ["updated_at = ?"]
            params = [now]
            
            for key, value in updates.items():
                if key in ("writing_requirements", "metadata"):
                    set_clauses.append(f"{key}_json = ?")
                    params.append(json.dumps(value))
                elif key in ("depth_score", "evidence_count", "claim_count", "word_count", "revision_count", "section_index"):
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
                elif key == "review_notes":
                    set_clauses.append("review_notes = ?")
                    params.append(value)
                elif key == "status":
                    set_clauses.append("status = ?")
                    params.append(value)
            
            params.append(section_id)
            query = f"UPDATE report_sections SET {', '.join(set_clauses)} WHERE section_id = ?"
            conn.execute(query, params)

    def _parse_section(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a section row into a dict with parsed JSON fields."""
        row["writing_requirements"] = _safe_parse_json(row.pop("writing_requirements_json", "{}"), {})
        row["metadata"] = _safe_parse_json(row.pop("metadata_json", "{}"), {})
        return row


class SectionResearchPackRepository:
    """Repository for Deep Report v2 section research packs (vNext-R3-A)."""

    def create_pack(self, pack: dict[str, Any]) -> None:
        """Create a new section research pack with retry for concurrent access."""
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                with transaction() as conn:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO section_research_packs (
                            pack_id, section_id, report_id, run_id, section_question,
                            required_dimensions_json, evidence_items_json, facts_json,
                            candidate_claims_json, signed_claims_json, comparison_points_json,
                            missing_information_json, risk_notes_json, recommended_tables_json,
                            status, evidence_coverage_rate, confidence_level, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pack["pack_id"], pack["section_id"], pack["report_id"], pack["run_id"],
                            pack["section_question"],
                            json.dumps(pack.get("required_dimensions", [])),
                            json.dumps(pack.get("evidence_items", [])),
                            json.dumps(pack.get("facts", [])),
                            json.dumps(pack.get("candidate_claims", [])),
                            json.dumps(pack.get("signed_claims", [])),
                            json.dumps(pack.get("comparison_points", [])),
                            json.dumps(pack.get("missing_information", [])),
                            json.dumps(pack.get("risk_notes", [])),
                            json.dumps(pack.get("recommended_tables", [])),
                            pack.get("status", "pending"),
                            pack.get("evidence_coverage_rate", 0.0),
                            pack.get("confidence_level", "medium"),
                            pack["created_at"], pack["updated_at"],
                        ),
                    )
                return  # Success
            except sqlite3.OperationalError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                raise
        if last_error:
            raise last_error

    def get_pack(self, pack_id: str) -> dict[str, Any] | None:
        """Get a research pack by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM section_research_packs WHERE pack_id = ?", (pack_id,)
            ).fetchone()
        if not row:
            return None
        return self._parse_pack(dict(row))

    def get_pack_by_section(self, section_id: str) -> dict[str, Any] | None:
        """Get the research pack for a section."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM section_research_packs WHERE section_id = ? ORDER BY created_at DESC LIMIT 1",
                (section_id,),
            ).fetchone()
        if not row:
            return None
        return self._parse_pack(dict(row))

    def update_pack(self, pack_id: str, updates: dict[str, Any]) -> None:
        """Update a research pack with the given updates."""
        now = _utc_now()
        with transaction() as conn:
            set_clauses = ["updated_at = ?"]
            params = [now]
            
            for key, value in updates.items():
                json_fields = (
                    "required_dimensions", "evidence_items", "facts", "candidate_claims",
                    "signed_claims", "comparison_points", "missing_information",
                    "risk_notes", "recommended_tables",
                )
                if key in json_fields:
                    set_clauses.append(f"{key}_json = ?")
                    params.append(json.dumps(value))
                elif key in ("evidence_coverage_rate",):
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
                elif key == "confidence_level":
                    set_clauses.append("confidence_level = ?")
                    params.append(value)
                elif key == "status":
                    set_clauses.append("status = ?")
                    params.append(value)
            
            params.append(pack_id)
            query = f"UPDATE section_research_packs SET {', '.join(set_clauses)} WHERE pack_id = ?"
            conn.execute(query, params)

    def _parse_pack(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a research pack row into a dict with parsed JSON fields."""
        json_fields = (
            "required_dimensions", "evidence_items", "facts", "candidate_claims",
            "signed_claims", "comparison_points", "missing_information",
            "risk_notes", "recommended_tables",
        )
        for field in json_fields:
            row[field] = _safe_parse_json(row.pop(f"{field}_json", "[]"), [])
        return row


class SectionDraftRepository:
    """Repository for Deep Report v2 section drafts (vNext-R3-A)."""

    def create_draft(self, draft: dict[str, Any]) -> None:
        """Create a new section draft."""
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO section_drafts (
                    draft_id, section_id, report_id, run_id, draft_index, draft_type,
                    content_markdown, content_html, trigger_type, rework_issue_id,
                    review_feedback, approved, word_count, quality_score, issues_json,
                    key_judgments_json, cited_evidence_ids_json,
                    created_by_agent, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft["draft_id"], draft["section_id"], draft["report_id"],
                    draft["run_id"], draft.get("draft_index", 1),
                    draft.get("draft_type", "initial"), draft["content_markdown"],
                    draft.get("content_html"),
                    draft.get("trigger_type", "automatic"),
                    draft.get("rework_issue_id"),
                    draft.get("review_feedback"),
                    1 if draft.get("approved", False) else 0,
                    draft.get("word_count", 0),
                    draft.get("quality_score"),
                    json.dumps(draft.get("issues", [])),
                    json.dumps(draft.get("key_judgments", [])),
                    json.dumps(draft.get("cited_evidence_ids", [])),
                    draft.get("created_by_agent", "section_writer"),
                    draft["created_at"], draft["updated_at"],
                ),
            )

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        """Get a draft by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM section_drafts WHERE draft_id = ?", (draft_id,)
            ).fetchone()
        if not row:
            return None
        return self._parse_draft(dict(row))

    def get_drafts_by_section(self, section_id: str) -> list[dict[str, Any]]:
        """Get all drafts for a section, ordered by draft_index."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM section_drafts WHERE section_id = ? ORDER BY draft_index",
                (section_id,),
            ).fetchall()
        return [self._parse_draft(dict(row)) for row in rows]

    def get_latest_draft(self, section_id: str) -> dict[str, Any] | None:
        """Get the latest draft for a section.

        DEPRECATED: Use get_best_draft() instead, which filters by approval.
        This method is kept for backwards compatibility.
        """
        return self.get_best_draft(section_id)

    def get_best_draft(self, section_id: str) -> dict[str, Any] | None:
        """Get the best available draft for a section.

        Selection priority:
          1. Latest approved draft (approved=1)
          2. If no approved drafts: latest non-rejected draft (approved=0 or NULL)
          3. If the candidate content is a placeholder (< MIN_WORDS or only '内容待补充'):
             try the next-best candidate
          4. If nothing useful found: return None

        vNext-R3-B: Fixes report assembly using rejected/placeholder drafts.
        """
        MIN_WORDS = 20  # sections shorter than this are treated as placeholders

        def _word_count(text: str) -> int:
            # Chinese text has no spaces, so use character count instead of word count
            if " " not in text.strip():
                return len(text.strip())
            return len(text.split())

        def _is_placeholder(draft: dict | None) -> bool:
            if not draft:
                return True
            content = draft.get("content_markdown", "")
            # Treat '内容待补充' or very short content as placeholder
            if not content.strip():
                return True
            if "内容待补充" in content:
                return True
            # Chinese has no spaces; use newline as the split indicator instead.
            # If no newline present → single-line (English word count); otherwise char count.
            if len(content.strip()) < MIN_WORDS:
                return True
            return False

        with transaction() as conn:
            # Priority 1: latest approved draft
            row = conn.execute(
                """SELECT * FROM section_drafts
                   WHERE section_id = ? AND approved = 1
                   ORDER BY draft_index DESC LIMIT 1""",
                (section_id,),
            ).fetchone()
            if row:
                draft = self._parse_draft(dict(row))
                if not _is_placeholder(draft):
                    return draft
                # approved but placeholder — fall through to try non-approved

            # Priority 2: latest non-approved draft (approved=0 or NULL)
            row = conn.execute(
                """SELECT * FROM section_drafts
                   WHERE section_id = ? AND (approved = 0 OR approved IS NULL)
                   ORDER BY draft_index DESC LIMIT 1""",
                (section_id,),
            ).fetchone()
            if row:
                draft = self._parse_draft(dict(row))
                if not _is_placeholder(draft):
                    return draft

            # Priority 3: latest draft regardless of approval (any leftover)
            row = conn.execute(
                """SELECT * FROM section_drafts
                   WHERE section_id = ?
                   ORDER BY draft_index DESC LIMIT 1""",
                (section_id,),
            ).fetchone()
            if row:
                draft = self._parse_draft(dict(row))
                if not _is_placeholder(draft):
                    return draft

            return None

    def update_draft(self, draft_id: str, updates: dict[str, Any]) -> None:
        """Update a draft with the given updates."""
        now = _utc_now()
        with transaction() as conn:
            set_clauses = ["updated_at = ?"]
            params = [now]
            
            for key, value in updates.items():
                if key == "issues":
                    set_clauses.append("issues_json = ?")
                    params.append(json.dumps(value))
                elif key in ("content_markdown", "content_html", "review_feedback"):
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
                elif key in ("approved", "word_count", "draft_index"):
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
                elif key == "quality_score":
                    set_clauses.append("quality_score = ?")
                    params.append(value if value is not None else None)
            
            params.append(draft_id)
            query = f"UPDATE section_drafts SET {', '.join(set_clauses)} WHERE draft_id = ?"
            conn.execute(query, params)

    def _parse_draft(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a draft row into a dict."""
        row["issues"] = _safe_parse_json(row.pop("issues_json", "[]"), [])
        row["key_judgments"] = _safe_parse_json(row.get("key_judgments_json", "[]"), [])
        row["cited_evidence_ids"] = _safe_parse_json(row.get("cited_evidence_ids_json", "[]"), [])
        row["approved"] = bool(row.get("approved", 0))

        # DB sometimes stores content_markdown as a JSON string like {"content_markdown": "..."}
        # Unwrap it so callers get plain text; also fix word_count to reflect real content length
        cm = row.get("content_markdown", "")
        if isinstance(cm, str) and cm.strip().startswith("{"):
            try:
                inner = json.loads(cm)
                if isinstance(inner, dict):
                    real_content = inner.get("content_markdown", "")
                    row["content_markdown"] = real_content
                    # Correct word_count: the DB field stored JSON-string length, not real word count
                    # Use char count for Chinese text (no spaces), word count for English
                    if row.get("word_count") is not None:
                        if " " not in real_content.strip():
                            row["word_count"] = len(real_content.strip())
                        else:
                            row["word_count"] = len(real_content.split())
            except (json.JSONDecodeError, ValueError):
                pass  # keep as-is
        else:
            # Plain text: correct word_count to char count for Chinese
            wc = row.get("word_count")
            if wc is not None and " " not in (cm or "").strip():
                # DB stores word_count but it's wrong for Chinese — recalculate
                row["word_count"] = len(cm.strip())

        return row


class ReportFigureRepository:
    """Repository for Deep Report v2 figures (vNext-R3-A)."""

    def create_figure(self, figure: dict[str, Any]) -> None:
        """Create a new report figure."""
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_figures (
                    figure_id, report_id, run_id, figure_type, figure_title,
                    figure_description, chart_spec_json, chart_data_json, section_id,
                    target_position, width, height, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    figure["figure_id"], figure["report_id"], figure["run_id"],
                    figure["figure_type"], figure["figure_title"],
                    figure.get("figure_description"),
                    json.dumps(figure.get("chart_spec", {})),
                    json.dumps(figure.get("chart_data", {})),
                    figure.get("section_id"),
                    figure.get("target_position"),
                    figure.get("width", 800), figure.get("height", 600),
                    json.dumps(figure.get("metadata", {})),
                    figure["created_at"], figure["updated_at"],
                ),
            )

    def get_figure(self, figure_id: str) -> dict[str, Any] | None:
        """Get a figure by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM report_figures WHERE figure_id = ?", (figure_id,)
            ).fetchone()
        if not row:
            return None
        return self._parse_figure(dict(row))

    def get_figures_by_report(self, report_id: str) -> list[dict[str, Any]]:
        """Get all figures for a report."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM report_figures WHERE report_id = ? ORDER BY created_at",
                (report_id,),
            ).fetchall()
        return [self._parse_figure(dict(row)) for row in rows]

    def _parse_figure(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a figure row into a dict with parsed JSON fields."""
        row["chart_spec"] = _safe_parse_json(row.pop("chart_spec_json", "{}"), {})
        row["chart_data"] = _safe_parse_json(row.pop("chart_data_json", "{}"), {})
        row["metadata"] = _safe_parse_json(row.pop("metadata_json", "{}"), {})
        return row


class ReportTableRepository:
    """Repository for Deep Report v2 tables (vNext-R3-A)."""

    def create_table(self, table: dict[str, Any]) -> None:
        """Create a new report table."""
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_tables (
                    table_id, report_id, run_id, table_type, table_title,
                    table_description, headers_json, rows_json, cells_json,
                    section_id, target_position, evidence_binding_json, interpretation,
                    metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    table["table_id"], table["report_id"], table["run_id"],
                    table["table_type"], table["table_title"],
                    table.get("table_description"),
                    json.dumps(table.get("headers", [])),
                    json.dumps(table.get("rows", [])),
                    json.dumps(table.get("cells", {})),
                    table.get("section_id"),
                    table.get("target_position"),
                    json.dumps(table.get("evidence_binding", {})),
                    table.get("interpretation"),
                    json.dumps(table.get("metadata", {})),
                    table["created_at"], table["updated_at"],
                ),
            )

    def get_table(self, table_id: str) -> dict[str, Any] | None:
        """Get a table by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM report_tables WHERE table_id = ?", (table_id,)
            ).fetchone()
        if not row:
            return None
        return self._parse_table(dict(row))

    def get_tables_by_report(self, report_id: str) -> list[dict[str, Any]]:
        """Get all tables for a report."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM report_tables WHERE report_id = ? ORDER BY created_at",
                (report_id,),
            ).fetchall()
        return [self._parse_table(dict(row)) for row in rows]

    def _parse_table(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a table row into a dict with parsed JSON fields."""
        row["headers"] = _safe_parse_json(row.pop("headers_json", "[]"), [])
        row["rows"] = _safe_parse_json(row.pop("rows_json", "[]"), [])
        row["cells"] = _safe_parse_json(row.pop("cells_json", "{}"), {})
        row["evidence_binding"] = _safe_parse_json(row.pop("evidence_binding_json", "{}"), {})
        row["metadata"] = _safe_parse_json(row.pop("metadata_json", "{}"), {})
        return row


class ReportReviewV2Repository:
    """Repository for Deep Report v2 reviews (vNext-R3-A)."""

    def create_review(self, review: dict[str, Any]) -> None:
        """Create a new report review."""
        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_reviews (
                    review_id, report_id, run_id, review_type, target_id, target_type,
                    reviewer_agent, overall_score, depth_score, evidence_score,
                    business_value_score, status, issues_json, suggestions_json,
                    rework_instruction, approved, reviewer_notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review["review_id"], review["report_id"], review["run_id"],
                    review.get("review_type", "final"),
                    review.get("target_id"),
                    review.get("target_type"),
                    review.get("reviewer_agent", "report_reviewer"),
                    review.get("overall_score"),
                    review.get("depth_score"),
                    review.get("evidence_score"),
                    review.get("business_value_score"),
                    review.get("status", "pending"),
                    json.dumps(review.get("issues", [])),
                    json.dumps(review.get("suggestions", [])),
                    review.get("rework_instruction"),
                    1 if review.get("approved", False) else 0,
                    review.get("reviewer_notes"),
                    review["created_at"], review["updated_at"],
                ),
            )

    def get_review(self, review_id: str) -> dict[str, Any] | None:
        """Get a review by ID."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM report_reviews WHERE review_id = ?", (review_id,)
            ).fetchone()
        if not row:
            return None
        return self._parse_review(dict(row))

    def get_reviews_by_report(self, report_id: str) -> list[dict[str, Any]]:
        """Get all reviews for a report."""
        with transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM report_reviews WHERE report_id = ? ORDER BY created_at",
                (report_id,),
            ).fetchall()
        return [self._parse_review(dict(row)) for row in rows]

    def get_latest_review(self, report_id: str) -> dict[str, Any] | None:
        """Get the latest review for a report."""
        with transaction() as conn:
            row = conn.execute(
                "SELECT * FROM report_reviews WHERE report_id = ? ORDER BY created_at DESC LIMIT 1",
                (report_id,),
            ).fetchone()
        if not row:
            return None
        return self._parse_review(dict(row))

    def update_review(self, review_id: str, updates: dict[str, Any]) -> None:
        """Update a review with the given updates."""
        now = _utc_now()
        with transaction() as conn:
            set_clauses = ["updated_at = ?"]
            params = [now]
            
            for key, value in updates.items():
                if key in ("issues", "suggestions"):
                    set_clauses.append(f"{key}_json = ?")
                    params.append(json.dumps(value))
                elif key in ("overall_score", "depth_score", "evidence_score", "business_value_score"):
                    set_clauses.append(f"{key} = ?")
                    params.append(value if value is not None else None)
                elif key == "status":
                    set_clauses.append("status = ?")
                    params.append(value)
                elif key == "approved":
                    set_clauses.append("approved = ?")
                    params.append(1 if value else 0)
                elif key == "rework_instruction":
                    set_clauses.append("rework_instruction = ?")
                    params.append(value)
                elif key == "reviewer_notes":
                    set_clauses.append("reviewer_notes = ?")
                    params.append(value)
            
            params.append(review_id)
            query = f"UPDATE report_reviews SET {', '.join(set_clauses)} WHERE review_id = ?"
            conn.execute(query, params)

    def _parse_review(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse a review row into a dict with parsed JSON fields."""
        row["issues"] = _safe_parse_json(row.pop("issues_json", "[]"), [])
        row["suggestions"] = _safe_parse_json(row.pop("suggestions_json", "[]"), [])
        row["approved"] = bool(row.get("approved", 0))
        return row

