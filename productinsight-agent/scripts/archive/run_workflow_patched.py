#!/usr/bin/env python3
"""Run workflow with all patches active (HITL bypass + FK disable)."""
import sys, os, logging, time, uuid
sys.path.insert(0, "/home/shijialin/paperworking/workflow_new/productinsight-agent")
os.chdir("/home/shijialin/paperworking/workflow_new/productinsight-agent")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("workflow_runner")

# ── Patch: disable FK so we can reuse run_id ────────────────────────────
import backend.app.storage.db as _db_mod
_orig_conn = _db_mod.get_connection
def _no_fk_conn():
    import sqlite3
    conn = sqlite3.connect(_db_mod.get_db_path())
    conn.row_factory = sqlite3.Row
    return conn
_db_mod.get_connection = _no_fk_conn

# ── Patch: bypass HITL ────────────────────────────────────────────────
import backend.app.orchestrator.nodes as _nodes_mod
_orig_hltl = _nodes_mod.prepare_human_intervention
def _mock_hltl(state):
    result = _orig_hltl(state)
    preserved = {
        "signed_claims": list(state.get("signed_claims", [])),
        "claims": list(state.get("claims", [])),
    }
    result.update(preserved)
    for key in [
        "_workflow_paused_at", "_workflow_paused_interventions",
        "workflow_pause_node", "workflow_pause_reason",
        "workflow_pause_output_summary", "requires_human_review",
    ]:
        result.pop(key, None)
    result["workflow_paused"] = False
    result["human_interventions"] = []
    result["_skip_human_review"] = True
    return result
_nodes_mod.prepare_human_intervention = _mock_hltl

# ── Init & Run ──────────────────────────────────────────────────────
from backend.app.storage.db import init_db, get_connection
from backend.app.orchestrator.graph import run_workflow

run_id = sys.argv[1] if len(sys.argv) > 1 else "run_e2d768d7768044e7"
logger.info("Initializing DB...")
init_db()

logger.info("Starting workflow for %s", run_id)
t0 = time.perf_counter()
try:
    state = {"run_id": run_id, "task_id": f"task_{run_id}", "mode": "real_time"}
    result = run_workflow(state)
    elapsed = time.perf_counter() - t0
    status = result.get("status", "unknown")
    logger.info("Workflow completed in %.1fs, status=%s", elapsed, status)
except Exception as e:
    elapsed = time.perf_counter() - t0
    logger.error("Workflow failed after %.1fs: %s", elapsed, e)
    import traceback
    traceback.print_exc()
