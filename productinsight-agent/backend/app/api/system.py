"""System status endpoints (vNext-P0).

Provides health checks for LLM provider, search provider, and database.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter

from backend.app.services.search_provider import get_search_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"])


def _check_llm_status() -> dict[str, Any]:
    """
    Check LLM provider configuration status.

    Returns a dict with:
      - configured: bool
      - provider: str (provider name or "not_configured")
      - model_name: str or None
      - reason: str or None (error code if not configured)
    """
    try:
        from backend.app.services.llm_client import LLMConfig, get_llm_client
        # Try to instantiate config - this raises ValueError if not set
        config = LLMConfig()
        return {
            "configured": True,
            "provider": config.provider,
            "model_name": config.name,  # LLMConfig uses 'name', not 'model_name'
            "endpoint": config.endpoint,
            "reason": None,
        }
    except ValueError as exc:
        # LLM not configured
        reason = str(exc) if str(exc) else "LLM_CLIENT_NOT_CONFIGURED"
        return {
            "configured": False,
            "provider": "not_configured",
            "model_name": None,
            "endpoint": None,
            "reason": reason if "configured" not in reason.lower() else "LLM_CLIENT_NOT_CONFIGURED",
        }
    except Exception as exc:
        logger.warning("LLM status check failed: %s", exc)
        return {
            "configured": False,
            "provider": "unknown",
            "model_name": None,
            "endpoint": None,
            "reason": f"LLM_CHECK_FAILED: {type(exc).__name__}: {exc}",
        }


def _check_search_status() -> dict[str, Any]:
    """Check search provider configuration status."""
    try:
        provider = get_search_provider()
        return {
            "configured": provider.is_configured,
            "provider": provider.provider_name,
            "reason": None if provider.is_configured else "SEARCH_PROVIDER_NOT_CONFIGURED",
        }
    except Exception as exc:
        logger.warning("Search status check failed: %s", exc)
        return {
            "configured": False,
            "provider": "unknown",
            "reason": f"SEARCH_CHECK_FAILED: {type(exc).__name__}: {exc}",
        }


def _check_database_status() -> dict[str, Any]:
    """Check database connectivity."""
    try:
        from backend.app.storage.db import get_connection
        with get_connection() as conn:
            conn.execute("SELECT 1").fetchone()
        return {"connected": True, "reason": None}
    except Exception as exc:
        logger.warning("Database status check failed: %s", exc)
        return {
            "connected": False,
            "reason": f"DB_CONNECTION_FAILED: {type(exc).__name__}: {exc}",
        }


@router.get("/status")
def system_status() -> dict[str, Any]:
    """
    Returns comprehensive system health and configuration status.

    Includes LLM provider, search provider, and database checks.
    Use this before starting a run to understand system readiness.
    """
    llm = _check_llm_status()
    search = _check_search_status()
    db = _check_database_status()

    return {
        # vNext-P0-Real-Frontend-Integration: build_tag for version confirmation
        "build_tag": "vNext-P0-real-frontend-integration",
        "loaded_modules": {
            "nodes_has_plan_schema_llm": True,
            "projects_accepts_research_plan": True,
        },
        "llm": llm,
        "search": search,
        "database": db,
        "overall": (
            "healthy" if (llm["configured"] and search["configured"] and db["connected"])
            else "degraded" if db["connected"]
            else "unhealthy"
        ),
    }


@router.get("/llm-status")
def llm_status() -> dict[str, Any]:
    """
    Returns LLM provider configuration status.

    Use this before starting a run to check if LLM is available.
    If not configured, the frontend should show a clear warning.

    Response:
      - configured: true if LLM API key and model name are set
      - provider: e.g. "doubao", "openai", "not_configured"
      - model_name: e.g. "doubao-pro-32k" or null
      - reason: null if configured, error code string if not
    """
    return _check_llm_status()
