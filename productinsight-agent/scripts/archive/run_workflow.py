#!/usr/bin/env python3
"""Run workflow for a specific run_id."""
import sys
import os

sys.path.insert(0, "/home/shijialin/paperworking/workflow_new/productinsight-agent")
os.chdir("/home/shijialin/paperworking/workflow_new/productinsight-agent")

import logging
from backend.app.storage.db import init_db, get_connection
from backend.app.orchestrator.graph import run_workflow
from backend.app.orchestrator.state import WorkflowState

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('workflow_runner')

def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else "run_d56a4f411ba14c08"
    
    logger.info(f"Initializing DB...")
    init_db()
    
    # Verify run exists
    with get_connection() as conn:
        run = conn.execute(
            "SELECT run_id, status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if not run:
            logger.error(f"Run {run_id} not found!")
            return
        logger.info(f"Found run {run_id}, status: {run[1]}")
    
    # Create initial state
    state = WorkflowState(run_id=run_id)
    logger.info(f"Starting workflow for {run_id}...")
    
    try:
        result = run_workflow(state)
        final_status = result.get("status", "unknown")
        logger.info(f"Workflow completed! Status: {final_status}")
        
        # Update run status in DB
        with get_connection() as conn:
            conn.execute(
                "UPDATE runs SET status=? WHERE run_id=?",
                (final_status, run_id)
            )
        logger.info(f"Updated run status to: {final_status}")
        
    except Exception as e:
        logger.error(f"Workflow failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
