"""LLM trace wrapper for ProductInsight Agent.

Provides traced_llm_call that wraps any LLM invocation with full trace logging.
Every LLM call is recorded with:
- event_type = "llm_call"
- model_name = actual model name
- prompt_text = full prompt or messages serialization
- input_payload_json / output_payload_json
- token_input / token_output / latency_ms
- status = "success" | "failed"
- error_message on failure

Does NOT interfere with the main workflow. Trace failures are logged but don't raise.
"""

from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from backend.app.storage.repositories import TraceRepository

logger = logging.getLogger(__name__)

# Module-level trace repo singleton
_trace_repo = TraceRepository()

# Non-LLM model names that should NOT be counted as LLM calls
NON_LLM_MODEL_NAMES: set[str] = {"", "n/a", "none", "non_llm", "rule_based", "template"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_serialize(val: Any) -> str:
    """Serialize a value to JSON string, or return empty string on failure."""
    try:
        return json.dumps(val, ensure_ascii=False)
    except Exception:
        return ""


def _estimate_tokens(text: str) -> int:
    """Estimate token count using simple heuristic: ~4 chars per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _extract_tokens_from_response(response: Any, output_text: str) -> tuple[int, int, int, bool]:
    """
    Extract token usage from LLM response.

    Returns: (token_input, token_output, total_tokens, token_estimated)
    - If usage available: returns actual counts
    - If usage missing: estimates and marks token_estimated=True
    """
    token_input = 0
    token_output = 0
    total_tokens = 0
    token_estimated = False

    # Try to extract from response dict
    if isinstance(response, dict):
        usage = response.get("usage")
        if usage:
            if isinstance(usage, dict):
                prompt_tokens = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                completion_tokens = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                total = usage.get("total_tokens") or (prompt_tokens + completion_tokens)
                return int(prompt_tokens), int(completion_tokens), int(total), False

    # Fallback: estimate from text
    if output_text:
        token_output = _estimate_tokens(output_text)

    # Estimate input tokens from response metadata if available
    if isinstance(response, dict):
        # Some APIs return prompt_tokens in different formats
        model_extra = response.get("model_extra") or {}
        if isinstance(model_extra, dict):
            token_input = model_extra.get("prompt_tokens", 0)

    if token_input == 0:
        # We can't estimate input tokens without knowing the prompt length
        # This is handled by the caller passing input_length hint
        pass

    return token_input, token_output, token_input + token_output, True


def traced_llm_call(
    *,
    run_id: str,
    project_id: str | None = None,
    node_name: str,
    agent_name: str,
    agent_role: str,
    prompt_version: str,
    prompt_text: str,
    input_payload: dict | None = None,
    model_name: str | None = None,
    call_fn: Callable[..., Any],
    parse_fn: Callable[[Any], dict] | None = None,
    input_length_hint: int = 0,
    decision_summary: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Wrap an LLM call with full trace logging.

    This wrapper:
    1. Creates a trace record with event_type="llm_call"
    2. Calls the actual LLM function
    3. Extracts tokens, model name, and output from the response
    4. Writes a complete trace record (success or failure)
    5. Returns the parsed output to the caller

    Does NOT interfere with the main workflow. If trace writing fails,
    only a warning is logged; the actual LLM result is still returned.

    Args:
        run_id: The run ID this trace belongs to.
        project_id: Optional project ID.
        node_name: Workflow node name (e.g. "research_plan", "analyze_dimensions").
        agent_name: Agent name (e.g. "ResearchPlanner", "AnalystAgent").
        agent_role: Agent role description.
        prompt_version: Version tag for the prompt.
        prompt_text: Full prompt text or messages serialization.
        input_payload: Additional input context.
        model_name: Override for model name (auto-detected from response if not provided).
        call_fn: The actual LLM call function to execute.
        parse_fn: Optional function to parse the raw response into structured output.
        input_length_hint: Hint for estimating input tokens if not available from response.
        decision_summary: Short description of what the LLM decided/generated.
        **kwargs: Arguments passed to call_fn.

    Returns:
        dict with keys:
        - output_text: Raw text output from LLM
        - parsed_output: Parsed output (if parse_fn provided)
        - token_input, token_output, total_tokens: Token counts
        - model_name: Actual model used
        - token_estimated: Whether tokens were estimated

    Raises:
        Re-raises any exception from call_fn after recording failure trace.
    """
    trace_id = str(uuid.uuid4())
    started_at = _now_iso()
    t_start = time.perf_counter()

    # Build initial trace record
    trace = {
        "trace_id": trace_id,
        "run_id": run_id,
        "project_id": project_id,
        "node_name": node_name,
        "agent_name": agent_name,
        "agent_role": agent_role,
        "event_type": "llm_call",
        "model_name": model_name or "unknown",
        "prompt_version": prompt_version,
        "prompt_text": prompt_text,
        "input_payload": input_payload,
        "artifact_refs": None,
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
        "decision_summary": decision_summary,
    }

    # Record started trace
    try:
        _trace_repo.add_trace(trace)
    except Exception as exc:
        logger.warning("Failed to write starting trace: %s", exc)

    raw_response = None
    exception_raised: Exception | None = None
    output_text = ""
    parsed_output: dict[str, Any] = {}
    token_input = 0
    token_output = 0
    total_tokens = 0
    token_estimated = True
    detected_model_name = model_name or "unknown"

    try:
        # Execute the actual LLM call
        raw_response = call_fn(**kwargs)
        elapsed_ms = int((time.perf_counter() - t_start) * 1000)

        # Extract output text from response
        if isinstance(raw_response, str):
            output_text = raw_response
        elif isinstance(raw_response, dict):
            # Try common response formats
            output_text = (
                raw_response.get("content")
                or raw_response.get("text")
                or raw_response.get("message", {}).get("content", "")
                or raw_response.get("output")
                or ""
            )
            # Try to get model name from response
            if detected_model_name == "unknown":
                detected_model_name = (
                    raw_response.get("model")
                    or raw_response.get("model_name")
                    or raw_response.get("model_id")
                    or model_name
                    or "unknown"
                )

        # Parse output if parse_fn provided
        if parse_fn and output_text:
            try:
                parsed_output = parse_fn(raw_response)
            except Exception as parse_exc:
                logger.warning("Failed to parse LLM output: %s", parse_exc)
                parsed_output = {"raw_output": output_text, "parse_error": str(parse_exc)}
        elif isinstance(raw_response, dict):
            parsed_output = raw_response

        # Extract tokens from response
        token_input, token_output, total_tokens, token_estimated = _extract_tokens_from_response(
            raw_response, output_text
        )

        # Use input length hint if tokens were estimated
        if token_estimated and input_length_hint > 0:
            token_input = max(1, input_length_hint // 4)
            total_tokens = token_input + token_output

        # Build output payload
        output_payload = {
            "output_text": output_text,
            "parsed_output": parsed_output,
            "token_estimated": token_estimated,
        }
        if isinstance(raw_response, dict) and "usage" in raw_response:
            output_payload["usage"] = raw_response["usage"]

        # Build completed trace
        completed_trace = {
            **trace,
            "model_name": detected_model_name,
            "status": "success",
            "completed_at": _now_iso(),
            "latency_ms": elapsed_ms,
            "token_input": token_input,
            "token_output": token_output,
            "output_payload": output_payload,
        }

        # Record success trace
        try:
            _trace_repo.add_trace(completed_trace)
        except Exception as exc:
            logger.warning("Failed to write success trace: %s", exc)

        # Return result
        return {
            "output_text": output_text,
            "parsed_output": parsed_output,
            "token_input": token_input,
            "token_output": token_output,
            "total_tokens": total_tokens,
            "model_name": detected_model_name,
            "token_estimated": token_estimated,
        }

    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - t_start) * 1000)
        exception_raised = exc
        error_str = f"{type(exc).__name__}: {exc}"
        error_traceback = traceback.format_exc()

        # Estimate tokens on failure
        if token_input == 0 and input_length_hint > 0:
            token_input = max(1, input_length_hint // 4)
        total_tokens = token_input + token_output

        # Build failed trace
        failed_trace = {
            **trace,
            "model_name": detected_model_name,
            "status": "failed",
            "completed_at": _now_iso(),
            "latency_ms": elapsed_ms,
            "token_input": token_input,
            "token_output": token_output,
            "error_message": error_str,
            "output_payload": {
                "error": error_str,
                "traceback": error_traceback,
                "partial_output": output_text,
            },
        }

        # Record failure trace
        try:
            _trace_repo.add_trace(failed_trace)
        except Exception as trace_exc:
            logger.warning("Failed to write failure trace: %s", trace_exc)

        # Re-raise the original exception
        raise


def create_llm_fallback_trace(
    run_id: str,
    project_id: str | None,
    node_name: str,
    agent_name: str,
    agent_role: str,
    prompt_version: str,
    prompt_text: str,
    input_payload: dict | None,
    reason: str,
    decision_summary: str = "",
) -> None:
    """
    Create a trace record for LLM fallback (LLM unavailable or failed).

    Use this when LLM is not available and the system falls back to
    template/deterministic logic. This trace should be visible in
    TraceAudit as a failed llm_call with fallback reason.
    """
    trace_id = str(uuid.uuid4())
    started_at = _now_iso()

    trace = {
        "trace_id": trace_id,
        "run_id": run_id,
        "project_id": project_id,
        "node_name": node_name,
        "agent_name": agent_name,
        "agent_role": agent_role,
        "event_type": "llm_call",
        "model_name": "fallback",
        "prompt_version": prompt_version,
        "prompt_text": prompt_text,
        "input_payload": input_payload,
        "artifact_refs": None,
        "status": "failed",
        "started_at": started_at,
        "created_at": started_at,
        "retry_count": 0,
        "token_input": 0,
        "token_output": 0,
        "latency_ms": 0,
        "error_message": f"FALLBACK: {reason}",
        "completed_at": _now_iso(),
        "output_payload": {
            "fallback_reason": reason,
            "decision_summary": decision_summary,
        },
        "decision_summary": decision_summary,
    }

    try:
        _trace_repo.add_trace(trace)
    except Exception as exc:
        logger.warning("Failed to write fallback trace: %s", exc)
