"""Trace & Audit page for ProductInsight Agent.

Provides comprehensive trace logging and debugging capabilities.
"""

from __future__ import annotations

import json
from typing import Optional

import streamlit as st

from frontend.common.api import get_json


def _status_badge(status: str) -> str:
    """Return colored badge text for status."""
    colors = {
        "success": "🟢 success",
        "completed": "🟢 completed",
        "failed": "🔴 failed",
        "running": "🔵 running",
        "paused": "🟠 paused",
        "retry": "🔵 retry",
        "skipped": "⚪ skipped",
        "pending": "⚪ pending",
    }
    status_lower = str(status).lower()
    return colors.get(status_lower, f"❓ {status}")


def _format_latency(ms: Optional[int]) -> str:
    """Format latency in human-readable format."""
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _format_tokens(tokens: Optional[int]) -> str:
    """Format token count."""
    if tokens is None or tokens == 0:
        return "-"
    return f"{tokens:,}"


def _event_type_icon(event_type: str) -> str:
    """Return icon for event type."""
    icons = {
        "llm_call": "🧠",
        "node_execution": "⚙️",
        "agent_step": "🤖",
    }
    return icons.get(event_type, "📋")


def _render_trace_detail(trace: dict) -> None:
    """Render detailed view of a single trace."""
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Node & Agent**")
        st.write(f"- Node: `{trace.get('node_name', '-')}`")
        st.write(f"- Agent: `{trace.get('agent_name', '-')}`")
        st.write(f"- Role: `{trace.get('agent_role', '-')}`")
        
        # Event type with icon
        event_type = trace.get('event_type', '-')
        icon = _event_type_icon(event_type)
        st.write(f"- Event: {icon} `{event_type}`")
        
        st.markdown("**Model & Tokens**")
        model_name = trace.get('model_name', '-')
        # Highlight if it's a real LLM call
        if model_name and model_name not in ('', 'n/a', 'none', 'non_llm', 'rule_based', 'fallback'):
            st.write(f"- Model: `**{model_name}**`")
        else:
            st.write(f"- Model: `{model_name}`")
        st.write(f"- Prompt Version: `{trace.get('prompt_version', '-')}`")
        st.write(f"- Token Input: {_format_tokens(trace.get('token_input'))}")
        st.write(f"- Token Output: {_format_tokens(trace.get('token_output'))}")
    
    with col2:
        st.markdown("**Timing**")
        st.write(f"- Started: `{trace.get('started_at', '-')}`")
        st.write(f"- Completed: `{trace.get('completed_at', '-')}`")
        st.write(f"- Latency: `{_format_latency(trace.get('latency_ms'))}`")
        st.write(f"- Retry Count: `{trace.get('retry_count', 0)}`")
        
        st.markdown("**Status**")
        st.write(_status_badge(trace.get('status', 'pending')))
        
        if trace.get('error_message'):
            st.error(f"**Error:** {trace.get('error_message')}")
    
    # Prompt text
    if trace.get('prompt_text'):
        with st.expander("📝 Prompt Text"):
            st.code(trace.get('prompt_text', ''), language="text", wrap_lines=True)
    
    # Input payload
    input_payload = trace.get('input_payload_json') or trace.get('input_payload')
    if input_payload:
        with st.expander("📥 Input Payload"):
            if isinstance(input_payload, str):
                try:
                    input_payload = json.loads(input_payload)
                except (json.JSONDecodeError, TypeError):
                    pass
            st.json(input_payload)
    
    # Output payload
    output_payload = trace.get('output_payload_json') or trace.get('output_payload')
    if output_payload:
        with st.expander("📤 Output Payload"):
            if isinstance(output_payload, str):
                try:
                    output_payload = json.loads(output_payload)
                except (json.JSONDecodeError, TypeError):
                    pass
            st.json(output_payload)
    
    # Decision summary
    if trace.get('decision_summary'):
        with st.expander("⚖️ Decision Summary"):
            st.write(trace.get('decision_summary'))
    
    # Artifact refs
    artifact_refs = trace.get('artifact_refs_json') or trace.get('artifact_refs')
    if artifact_refs:
        with st.expander("📎 Artifact References"):
            if isinstance(artifact_refs, str):
                try:
                    artifact_refs = json.loads(artifact_refs)
                except (json.JSONDecodeError, TypeError):
                    pass
            if isinstance(artifact_refs, list):
                for ref in artifact_refs:
                    if isinstance(ref, dict):
                        st.write(f"- `{ref.get('type', 'unknown')}`: {ref.get('count', 0)}")
                    else:
                        st.write(f"- {ref}")
            else:
                st.json(artifact_refs)


def _render_node_io_table(traces: list[dict]) -> None:
    """Render per-node input/output summary table."""
    if not traces:
        st.info("No node IO data available.")
        return
    
    node_data = []
    for t in traces:
        input_payload = t.get('input_payload_json') or t.get('input_payload', {})
        output_payload = t.get('output_payload_json') or t.get('output_payload', {})
        
        # Parse JSON strings if needed
        if isinstance(input_payload, str):
            try:
                input_payload = json.loads(input_payload)
            except (json.JSONDecodeError, TypeError):
                pass
        
        if isinstance(output_payload, str):
            try:
                output_payload = json.loads(output_payload)
            except (json.JSONDecodeError, TypeError):
                pass
        
        event_type = t.get('event_type', 'unknown')
        icon = _event_type_icon(event_type)
        
        model_name = t.get('model_name', '-') or '-'
        # Highlight if it's a real LLM call
        is_llm = model_name and model_name not in ('', 'n/a', 'none', 'non_llm', 'rule_based', 'fallback')
        
        node_data.append({
            "event": icon,
            "node": t.get('node_name', '-'),
            "agent": t.get('agent_name', '-'),
            "model": f"**{model_name}**" if is_llm else model_name,
            "status": _status_badge(t.get('status', 'pending')),
            "latency": _format_latency(t.get('latency_ms')),
            "tokens_in": _format_tokens(t.get('token_input')),
            "tokens_out": _format_tokens(t.get('token_output')),
            "has_input": bool(input_payload),
            "has_output": bool(output_payload),
            "has_artifacts": bool(t.get('artifact_refs_json') or t.get('artifact_refs')),
        })
    
    # Display as table
    st.dataframe(
        node_data,
        column_config={
            "event": st.column_config.TextColumn("Type", width="small"),
            "node": st.column_config.TextColumn("Node", width="medium"),
            "agent": st.column_config.TextColumn("Agent", width="medium"),
            "model": st.column_config.TextColumn("Model", width="small"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "latency": st.column_config.TextColumn("Latency", width="small"),
            "tokens_in": st.column_config.TextColumn("Tokens In", width="small"),
            "tokens_out": st.column_config.TextColumn("Tokens Out", width="small"),
            "has_input": st.column_config.CheckboxColumn("Has Input", width="small"),
            "has_output": st.column_config.CheckboxColumn("Has Output", width="small"),
            "has_artifacts": st.column_config.CheckboxColumn("Has Artifacts", width="small"),
        },
        hide_index=True,
        use_container_width=True,
    )


def _render_trace_timeline(traces: list[dict]) -> None:
    """Render trace timeline as expandable table."""
    if not traces:
        st.info("No trace records yet. Start or refresh a run.")
        return
    
    timeline_data = []
    for i, t in enumerate(traces):
        event_type = t.get('event_type', 'unknown')
        icon = _event_type_icon(event_type)
        
        timeline_data.append({
            "#": i + 1,
            "trace_id": t.get('trace_id', '')[:16] + "..." if t.get('trace_id') else '-',
            "event": icon,
            "node": t.get('node_name', '-'),
            "agent": t.get('agent_name', '-'),
            "model": t.get('model_name', '-') or '-',
            "status": _status_badge(t.get('status', 'pending')),
            "latency": _format_latency(t.get('latency_ms')),
            "tokens_in": _format_tokens(t.get('token_input')),
            "tokens_out": _format_tokens(t.get('token_output')),
            "error": "⚠️" if t.get('error_message') else "",
        })
    
    st.dataframe(
        timeline_data,
        column_config={
            "#": st.column_config.NumberColumn("#", width="tiny"),
            "trace_id": st.column_config.TextColumn("Trace ID", width="small"),
            "event": st.column_config.TextColumn("Type", width="small"),
            "node": st.column_config.TextColumn("Node", width="medium"),
            "agent": st.column_config.TextColumn("Agent", width="medium"),
            "model": st.column_config.TextColumn("Model", width="small"),
            "status": st.column_config.TextColumn("Status", width="small"),
            "latency": st.column_config.TextColumn("Latency", width="small"),
            "tokens_in": st.column_config.TextColumn("Tokens In", width="small"),
            "tokens_out": st.column_config.TextColumn("Tokens Out", width="small"),
            "error": st.column_config.TextColumn("⚠️", width="tiny"),
        },
        hide_index=True,
        use_container_width=True,
    )


def render_trace_audit_page(run_id: Optional[str] = None) -> None:
    """Render the Trace & Audit page."""
    st.header("🔍 Trace & Audit")
    
    # Use provided run_id or from session state
    if run_id is None:
        run_id = st.session_state.get("selected_run_id")
    
    # Run ID input
    col1, col2 = st.columns([3, 1])
    with col1:
        run_id_input = st.text_input(
            "Run ID",
            value=run_id or "",
            placeholder="Enter Run ID to inspect...",
            help="Enter a run ID to view its trace logs",
        )
    with col2:
        st.write("")  # Spacer
        refresh = st.button("🔄 Refresh", use_container_width=True)
    
    # Also check session state
    if not run_id_input and st.session_state.get("selected_run_id"):
        run_id_input = st.session_state["selected_run_id"]
    
    if not run_id_input:
        st.info("👆 Enter a Run ID above to view trace logs, or start a run to see live traces.")
        st.markdown("""
        ### Trace & Audit Guide
        
        This page provides comprehensive debugging capabilities:
        
        - **Trace Summary**: Overview metrics of all traces for a run
        - **Trace Timeline**: Chronological list of all agent executions
        - **Node IO Table**: Per-node input/output summary
        - **Trace Details**: Expand any trace to see full prompt, input, output
        
        #### Getting Started
        1. Start a new analysis or select an existing run
        2. Navigate here using the sidebar
        3. View live traces as your run progresses
        """)
        return
    
    # Update session state
    st.session_state["selected_run_id"] = run_id_input
    current_run_id = run_id_input
    
    # Fetch data
    traces = get_json(f"/api/runs/{current_run_id}/traces", default=[])
    summary = get_json(f"/api/runs/{current_run_id}/trace-summary", default={})
    node_io = get_json(f"/api/runs/{current_run_id}/node-io", default=[])
    
    # Handle empty data gracefully
    if traces is None:
        traces = []
    if summary is None:
        summary = {}
    if node_io is None:
        node_io = []
    
    # Title with run info
    st.subheader(f"Run: `{current_run_id}`")
    
    # --- Trace Summary ---
    if summary:
        with st.expander("📊 Trace Summary", expanded=True):
            # vNext-R2-C: Extended metrics with successful/failed/fallback LLM calls
            col1, col2, col3, col4, col5, col6 = st.columns(6)
            
            total = summary.get('total_traces', 0)
            failed = summary.get('failed_traces', 0)
            tokens = summary.get('total_tokens', 0)
            latency = summary.get('total_latency_ms', 0)
            llm_calls = summary.get('llm_calls', 0)
            non_llm = summary.get('non_llm_calls', 0)
            
            with col1:
                st.metric("Total Traces", total)
            with col2:
                st.metric("Failed", failed)
            with col3:
                st.metric("LLM Calls", llm_calls)
            with col4:
                st.metric("Non-LLM", non_llm)
            with col5:
                st.metric("Total Tokens", f"{tokens:,}" if tokens else "-")
            with col6:
                st.metric("Total Latency", _format_latency(latency))
            
            # vNext-R2-C: Detailed LLM call breakdown
            successful_llm = summary.get('successful_llm_calls', 0)
            failed_llm = summary.get('failed_llm_calls', 0)
            fallback_llm = summary.get('fallback_llm_calls', 0)
            
            if successful_llm > 0 or failed_llm > 0 or fallback_llm > 0:
                st.divider()
                llm_col1, llm_col2, llm_col3, llm_col4 = st.columns(4)
                
                with llm_col1:
                    st.metric("LLM Attempts", llm_calls)
                with llm_col2:
                    if successful_llm > 0:
                        st.metric("✅ Successful LLM", successful_llm)
                    else:
                        st.metric("✅ Successful LLM", "-")
                with llm_col3:
                    if failed_llm > 0:
                        st.metric("❌ Failed LLM", failed_llm)
                    else:
                        st.metric("❌ Failed LLM", "-")
                with llm_col4:
                    if fallback_llm > 0:
                        st.metric("⚡ Fallback", fallback_llm)
                    else:
                        st.metric("⚡ Fallback", "-")
            
            if failed > 0:
                st.warning(f"⚠️ {failed} trace(s) failed. Check the timeline below for details.")
    else:
        st.info("No trace summary available. Start a run to generate traces.")
    
    # --- Trace Timeline ---
    st.subheader("📋 Trace Timeline")
    _render_trace_timeline(traces)
    
    # --- Node IO Table ---
    with st.expander("🔗 Node Input/Output Summary"):
        _render_node_io_table(node_io)
    
    # --- Individual Trace Details ---
    if traces:
        st.subheader("🔬 Trace Details")
        for i, trace in enumerate(traces):
            node_name = trace.get('node_name', f'Trace {i+1}')
            status = trace.get('status', 'pending')
            agent = trace.get('agent_name', 'Unknown')
            event_type = trace.get('event_type', 'unknown')
            icon = _event_type_icon(event_type)
            
            with st.expander(f"{_status_badge(status)} {icon} {node_name} - {agent}"):
                _render_trace_detail(trace)
    else:
        st.info("No trace records yet. Start or refresh a run.")
    
    # --- Raw JSON ---
    with st.expander("📄 Raw Trace JSON"):
        st.json({
            "run_id": current_run_id,
            "summary": summary,
            "trace_count": len(traces),
            "traces": traces[:50] if traces else [],  # Limit for display
        })
    
    # --- Full trace audit via API ---
    st.divider()
    st.caption(f"Data source: GET /api/runs/{current_run_id}/traces")
