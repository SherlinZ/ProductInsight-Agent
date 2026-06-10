"""Agent trace wrapper for ProductInsight Agent.

Wraps any callable with trace logging. Records prompt, input, output,
token usage, latency, and errors without swallowing exceptions.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.app.storage.repositories import TraceRepository


# Global trace repo instance
_trace_repo = TraceRepository()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_serialize(val: Any) -> str:
    """Serialize a value to JSON string, or return empty string on failure."""
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return ""


def run_with_trace(
    run_id: str,
    node_name: str,
    agent_name: str,
    func: Callable[..., Any],
    *,
    project_id: Optional[str] = None,
    agent_role: Optional[str] = None,
    event_type: str = "agent_step",
    model_name: Optional[str] = None,
    prompt_text: Optional[str] = None,
    prompt_version: Optional[str] = None,
    input_payload: Optional[dict] = None,
    artifact_refs: Optional[list] = None,
    **kwargs: Any,
) -> Any:
    """Wrap a callable with trace logging.

    Always re-raises exceptions from `func` after recording the failure trace.

    Args:
        run_id: The run ID this trace belongs to.
        node_name: Name of the workflow node (e.g. "collect_sources").
        agent_name: Name of the agent (e.g. "CollectorAgent").
        func: The callable to execute.
        project_id: Optional project ID.
        agent_role: Optional role description (e.g. "source_collector").
        event_type: Type of event (default: "agent_step").
        model_name: LLM model name (e.g. "gpt-4o"). Use "non_llm" for non-LLM nodes.
        prompt_text: Full prompt text sent to LLM.
        prompt_version: Version tag for the prompt.
        input_payload: Additional input context (dict).
        artifact_refs: List of artifact references produced by this step.
        **kwargs: Arguments passed to `func`.

    Returns:
        The return value of `func`.

    Raises:
        Re-raises whatever `func` raises.
    """
    trace_id = str(uuid.uuid4())
    started_at = _now_iso()

    # Build initial trace record
    trace = {
        "trace_id": trace_id,
        "run_id": run_id,
        "project_id": project_id,
        "node_name": node_name,
        "agent_name": agent_name,
        "agent_role": agent_role,
        "event_type": event_type,
        "model_name": model_name or "non_llm",
        "prompt_version": prompt_version,
        "prompt_text": prompt_text,
        "input_payload": input_payload,
        "artifact_refs": artifact_refs,
        "status": "running",
        "started_at": started_at,
        "created_at": started_at,
        "retry_count": 0,
        "token_input": 0,
        "token_output": 0,
        "latency_ms": None,
        "error_message": None,
        "completed_at": None,
        "output_payload": None,
        "decision_summary": None,
    }

    # Record started trace
    _trace_repo.add_trace(trace)

    t_start = time.perf_counter()
    result = None
    exception_raised: Optional[Exception] = None

    try:
        result = func(**kwargs)

        # Execution succeeded
        elapsed_ms = int((time.perf_counter() - t_start) * 1000)
        completed_at = _now_iso()

        # Extract token counts from result if available
        token_in = 0
        token_out = 0
        if isinstance(result, dict):
            token_in = result.get("token_input", 0) or 0
            token_out = result.get("token_output", 0) or 0

        completed_trace = {
            **trace,
            "status": "success",
            "completed_at": completed_at,
            "latency_ms": elapsed_ms,
            "token_input": token_in,
            "token_output": token_out,
            "output_payload": result if isinstance(result, dict) else {"result": str(result)[:2000]},
        }

        _trace_repo.add_trace(completed_trace)
        return result

    except Exception as exc:
        # Execution failed — record failure, then re-raise
        elapsed_ms = int((time.perf_counter() - t_start) * 1000)
        completed_at = _now_iso()

        failed_trace = {
            **trace,
            "status": "failed",
            "completed_at": completed_at,
            "latency_ms": elapsed_ms,
            "error_message": f"{type(exc).__name__}: {exc}",
        }

        # Record failure trace (don't let DB error prevent re-raise)
        try:
            _trace_repo.add_trace(failed_trace)
        except Exception:
            pass  # logging failure should not prevent re-raise

        # Re-raise the original exception
        raise


# Convenience helpers for creating traces without wrapping a function
def create_trace_start(
    run_id: str,
    node_name: str,
    agent_name: str,
    *,
    project_id: Optional[str] = None,
    agent_role: Optional[str] = None,
    event_type: str = "agent_step",
    model_name: Optional[str] = None,
    prompt_text: Optional[str] = None,
    prompt_version: Optional[str] = None,
    input_payload: Optional[dict] = None,
    artifact_refs: Optional[list] = None,
) -> dict:
    """Create a 'running' trace entry and return it."""
    trace_id = str(uuid.uuid4())
    started_at = _now_iso()

    trace = {
        "trace_id": trace_id,
        "run_id": run_id,
        "project_id": project_id,
        "node_name": node_name,
        "agent_name": agent_name,
        "agent_role": agent_role,
        "event_type": event_type,
        "model_name": model_name or "non_llm",
        "prompt_version": prompt_version,
        "prompt_text": prompt_text,
        "input_payload": input_payload,
        "artifact_refs": artifact_refs,
        "status": "running",
        "started_at": started_at,
        "created_at": started_at,
        "retry_count": 0,
        "token_input": 0,
        "token_output": 0,
        "latency_ms": None,
        "error_message": None,
        "completed_at": None,
        "output_payload": None,
        "decision_summary": None,
    }

    _trace_repo.add_trace(trace)
    return trace


def complete_trace(
    trace: dict,
    result: Any,
    *,
    token_input: int = 0,
    token_output: int = 0,
    artifact_refs: Optional[list] = None,
) -> None:
    """Mark a trace as successful with output data."""
    from datetime import datetime, timezone

    elapsed_ms = None
    if trace.get("started_at"):
        try:
            start = datetime.fromisoformat(trace["started_at"])
            elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        except Exception:
            elapsed_ms = None

    completed = {
        **trace,
        "status": "success",
        "completed_at": _now_iso(),
        "latency_ms": elapsed_ms,
        "token_input": token_input,
        "token_output": token_output,
        "output_payload": result if isinstance(result, dict) else {"result": str(result)[:2000]},
        "artifact_refs": artifact_refs,
    }

    _trace_repo.add_trace(completed)


def fail_trace(trace: dict, error: Exception) -> None:
    """Mark a trace as failed with error message. Does NOT re-raise."""
    elapsed_ms = None
    if trace.get("started_at"):
        try:
            start = datetime.fromisoformat(trace["started_at"])
            elapsed_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        except Exception:
            elapsed_ms = None

    failed = {
        **trace,
        "status": "failed",
        "completed_at": _now_iso(),
        "latency_ms": elapsed_ms,
        "error_message": f"{type(error).__name__}: {error}",
    }

    try:
        _trace_repo.add_trace(failed)
    except Exception:
        pass
