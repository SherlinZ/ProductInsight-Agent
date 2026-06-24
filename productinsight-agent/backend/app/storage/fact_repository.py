"""
FactRepository — handles fact CRUD operations.
"""
from __future__ import annotations

import json
from typing import Any

from backend.app.storage.db import transaction
from backend.app.storage.repositories import _ensure_product_in_db


def _safe_parse_json(raw: Any, default: Any = None) -> Any:
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


class FactRepository:
    def add_fact(self, fact: dict[str, Any]) -> None:
        evidence_ids = fact.get("evidence_ids", [])
        if isinstance(evidence_ids, list):
            evidence_ids_json = json.dumps(evidence_ids, ensure_ascii=False)
        else:
            evidence_ids_json = evidence_ids if isinstance(evidence_ids, str) else json.dumps(evidence_ids)

        # Resolve product_id to run-scoped ID, creating a placeholder product row if needed.
        # This prevents FOREIGN KEY failures.
        run_id = fact.get("run_id", "")
        raw_pid = fact.get("product_id", "")
        resolved_pid = _ensure_product_in_db(
            run_id=run_id,
            raw_product_id=raw_pid,
            raw_product_name=fact.get("product_name", raw_pid),
            now=fact.get("created_at", ""),
        )

        # Derive product_slug from product_id if not provided.
        product_slug = fact.get("product_slug")
        if not product_slug:
            product_slug = raw_pid.lower().replace(" ", "-").replace("_", "-")

        # Serialize value_json: support dict/list input from structured extractors.
        raw_value = fact.get("value_json", "")
        if isinstance(raw_value, (dict, list)):
            value_json_str = json.dumps(raw_value, ensure_ascii=False)
        else:
            value_json_str = raw_value if isinstance(raw_value, str) else json.dumps(raw_value)

        with transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO facts (
                    fact_id, run_id, product_id, product_slug, schema_key, raw_schema_key,
                    value_json, value_type, unit, confidence,
                    evidence_ids_json, extraction_result_id,
                    review_status, created_at, updated_at,
                    rework_iteration
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fact["fact_id"], run_id, resolved_pid, product_slug,
                    fact["schema_key"], fact.get("raw_schema_key"),
                    value_json_str, fact.get("value_type", "string"), fact.get("unit", ""),
                    fact.get("confidence", 0.5), evidence_ids_json,
                    fact.get("extraction_result_id", ""), fact.get("review_status", "pending"),
                    fact["created_at"], fact.get("updated_at", fact["created_at"]),
                    # P1-Redesign (2026-06-18): rework attribution
                    int(fact.get("rework_iteration", 0) or 0),
                ),
            )

    def list_facts(self, run_id: str, product_id: str | None = None) -> list[dict[str, Any]]:
        with transaction() as conn:
            if product_id:
                rows = conn.execute(
                    "SELECT * FROM facts WHERE run_id = ? AND product_id = ? ORDER BY schema_key",
                    (run_id, product_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM facts WHERE run_id = ? ORDER BY schema_key",
                    (run_id,),
                ).fetchall()

        results = []
        for row in rows:
            r = dict(row)
            r["evidence_ids"] = _safe_parse_json(r.pop("evidence_ids_json", None), [])
            results.append(r)
        return results

    def update_review_status(self, fact_id: str, review_status: str) -> None:
        with transaction() as conn:
            conn.execute(
                "UPDATE facts SET review_status = ?, updated_at = datetime('now') WHERE fact_id = ?",
                (review_status, fact_id),
            )
