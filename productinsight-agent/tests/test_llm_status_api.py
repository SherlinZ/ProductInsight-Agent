"""Tests for LLM Status API (vNext-P0)."""

from __future__ import annotations

import os
import tempfile
import unittest

TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_llm_status.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

class TestLLMStatusAPI(unittest.TestCase):
    """Test GET /api/system/llm-status and /api/system/status."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        # Clear any cached LLM client
        import backend.app.services.llm_client as llm_module
        llm_module._client = None

    def test_llm_status_when_not_configured(self):
        """When MODEL_API_KEY is not set, llm-status returns configured=False."""
        # Ensure no API key
        for key in ["MODEL_API_KEY", "MODEL_NAME"]:
            os.environ.pop(key, None)
        import backend.app.services.llm_client as llm_module
        llm_module._client = None

        from backend.app.api.system import _check_llm_status
        status = _check_llm_status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["provider"], "not_configured")
        self.assertIsNotNone(status["reason"])
        # Error message should indicate API key is not set
        self.assertIn("not set", status["reason"].lower())
        print(f"  OK: LLM not configured → {status}")

    def test_system_status_includes_llm_search_db(self):
        """system/status should include llm, search, and database checks."""
        for key in ["MODEL_API_KEY", "MODEL_NAME", "TAVILY_API_KEY", "SERPAPI_API_KEY"]:
            os.environ.pop(key, None)
        import backend.app.services.llm_client as llm_module
        llm_module._client = None
        from backend.app.api.system import system_status
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()

        status = system_status()
        self.assertIn("llm", status)
        self.assertIn("search", status)
        self.assertIn("database", status)
        self.assertIn("overall", status)
        self.assertFalse(status["llm"]["configured"])
        self.assertIn("overall", status)  # should be "degraded" or "unhealthy"
        print(f"  OK: system status overall={status['overall']}")

if __name__ == "__main__":
    unittest.main()
