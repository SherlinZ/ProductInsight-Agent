#!/usr/bin/env python3
"""Minimal test to check if review_claims is ever called."""
import sys, os, uuid
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ['DATABASE_URL'] = f'sqlite:///{PROJECT_ROOT}/data/productinsight.db'

import backend.app.storage.db as _db_mod
import sqlite3
def _nfk():
    conn = sqlite3.connect(_db_mod.get_db_path())
    conn.row_factory = sqlite3.Row
    return conn
_db_mod.get_connection = _nfk
from backend.app.storage.db import init_db
init_db()

from backend.app.orchestrator.graph import run_workflow
import backend.app.orchestrator.nodes as _n

_orig = _n.prepare_human_intervention
def _mock(s):
    r = _orig(s)
    r.update({'signed_claims': list(s.get('signed_claims', [])), 'claims': list(s.get('claims', []))})
    r.pop('_workflow_paused_at', None)
    r.pop('_workflow_paused_interventions', None)
    r['workflow_paused'] = False
    r['human_interventions'] = []
    r['_skip_human_review'] = True
    return r
_n.prepare_human_intervention = _mock

run_id = f't23_{uuid.uuid4().hex[:8]}'
state = {
    'run_id': run_id,
    'task_id': f't_{run_id}',
    'task_brief': {
        'title': 'Test',
        'products': [{'product_id': 'dify', 'product_name': 'Dify', 'seed_urls': ['https://docs.dify.ai']}],
        'schema_type': 'ai_agent_platform'
    },
    'mode': 'real_time'
}

sys.stderr.write(f"[TESTER] Starting workflow...\n")
sys.stderr.flush()

result = run_workflow(state)

sys.stderr.write(f"[TESTER] Done. signed_claims={len(result.get('signed_claims', []))} claim_drafts={len(result.get('claim_drafts', []))}\n")
sys.stderr.flush()
print(f"RESULT: signed_claims={len(result.get('signed_claims', []))}")
