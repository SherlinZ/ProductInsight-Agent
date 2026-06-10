"""Formatting utilities for ProductInsight Agent frontend."""

import streamlit as st


_REASON_CODE_MAP = {
    "MISSING_DIMENSION": "Missing Dimension",
    "INSUFFICIENT_PRODUCT_COVERAGE": "Insufficient Product Coverage",
    "PARTIAL_PRODUCT_COVERAGE": "Partial Product Coverage",
    "LOW_EVIDENCE_QUALITY": "Low Evidence Quality",
    "UNVERIFIED_CLAIM": "Unverified Claim",
    "OUTDATED_INFORMATION": "Outdated Information",
    "INSUFFICIENT_SOURCES": "Insufficient Sources",
    "SELF_REPORTED_ONLY": "Self-Reported Evidence Only",
    "CONFLICTING_EVIDENCE": "Conflicting Evidence",
    "TIMELINE_GAP": "Timeline Gap",
}


def _fmt_rate(v):
    """Format a rate/float as percentage string."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _status_icon(status: str) -> str:
    status_lower = str(status).lower()
    icons = {
        "pending": "⏳",
        "planned": "📋",
        "running": "🔄",
        "completed": "✅",
        "success": "✅",
        "failed": "❌",
        "skipped": "⏭️",
        "approved": "👍",
        "rejected": "👎",
        "rework": "🔧",
        "paused": "⏸️",
        "retry": "🔁",
    }
    return icons.get(status_lower, "❓")


def _status_color(status: str) -> str:
    status_lower = str(status).lower()
    colors = {
        "pending": "gray",
        "planned": "blue",
        "running": "blue",
        "completed": "green",
        "success": "green",
        "failed": "red",
        "skipped": "gray",
        "approved": "green",
        "rejected": "red",
        "rework": "orange",
        "paused": "orange",
        "retry": "blue",
    }
    return colors.get(status_lower, "gray")


def _badge(status: str) -> str:
    """Render a status badge with emoji and color."""
    icon = _status_icon(status)
    color = _status_color(status)
    return f":{color}[{icon} **{status.upper()}**]"


def _workflow_node_icon(status: str) -> str:
    status_lower = str(status).lower()
    icons = {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "success": "✅",
        "failed": "❌",
        "skipped": "⏭️",
        "paused": "⏸️",
    }
    return icons.get(status_lower, "❓")


def _workflow_node_color(status: str) -> str:
    status_lower = str(status).lower()
    colors = {
        "pending": "gray",
        "running": "blue",
        "completed": "green",
        "success": "green",
        "failed": "red",
        "skipped": "gray",
        "paused": "orange",
    }
    return colors.get(status_lower, "gray")


def format_reason_code(reason_code: str) -> str:
    """Format a reason code into a human-readable string."""
    return _REASON_CODE_MAP.get(reason_code, reason_code)
