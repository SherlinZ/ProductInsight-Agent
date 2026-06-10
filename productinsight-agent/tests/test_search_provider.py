"""Tests for SearchProvider abstraction (vNext-R2-D)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone

# Set test database path before importing anything
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test_search.db")
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH}"

# Shared timestamp for test runs
NOW = datetime.now(timezone.utc).isoformat()


class TestDisabledSearchProvider(unittest.TestCase):
    """Test DisabledSearchProvider returns appropriate reason code."""

    def setUp(self):
        # Clear any cached provider
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()
        # Ensure no API keys are set
        for key in ["TAVILY_API_KEY", "SERPAPI_API_KEY", "SEARCH_API_ENDPOINT", "SEARCH_API_KEY"]:
            os.environ.pop(key, None)

    def test_disabled_provider_not_configured(self):
        """Disabled provider should not be configured."""
        from backend.app.services.search_provider import DisabledSearchProvider
        provider = DisabledSearchProvider()
        self.assertFalse(provider.is_configured)
        self.assertEqual(provider.provider_name, "disabled")

    def test_disabled_provider_search_returns_empty_with_reason(self):
        """Disabled provider should return empty results with correct reason."""
        from backend.app.services.search_provider import DisabledSearchProvider, SEARCH_PROVIDER_NOT_CONFIGURED
        provider = DisabledSearchProvider()
        results, reason = provider.search("test query", limit=5)
        self.assertEqual(results, [])
        self.assertEqual(reason, SEARCH_PROVIDER_NOT_CONFIGURED)


class TestGetSearchProvider(unittest.TestCase):
    """Test the factory function for search providers."""

    def setUp(self):
        from backend.app.services.search_provider import reset_search_provider
        reset_search_provider()
        for key in ["TAVILY_API_KEY", "SERPAPI_API_KEY", "SEARCH_API_ENDPOINT", "SEARCH_API_KEY"]:
            os.environ.pop(key, None)

    def test_no_api_keys_returns_disabled(self):
        """When no API keys are set, should return disabled provider."""
        from backend.app.services.search_provider import get_search_provider
        provider = get_search_provider()
        # ConfiguredWebSearchProvider with no API keys has is_configured=False
        self.assertFalse(provider.is_configured)
        self.assertEqual(provider.provider_name, "disabled")

    def test_tavily_key_configures_tavily(self):
        """Setting TAVILY_API_KEY should configure Tavily provider."""
        os.environ["TAVILY_API_KEY"] = "test_key_123"
        from backend.app.services.search_provider import get_search_provider, reset_search_provider
        reset_search_provider()
        provider = get_search_provider()
        self.assertTrue(provider.is_configured)
        self.assertEqual(provider.provider_name, "tavily")
        os.environ.pop("TAVILY_API_KEY", None)

    def test_custom_endpoint_configures_custom(self):
        """Setting SEARCH_API_ENDPOINT should configure custom provider."""
        os.environ["SEARCH_API_ENDPOINT"] = "https://api.example.com/search"
        os.environ["SEARCH_API_KEY"] = "test_key"
        from backend.app.services.search_provider import get_search_provider, reset_search_provider
        reset_search_provider()
        provider = get_search_provider()
        self.assertTrue(provider.is_configured)
        self.assertEqual(provider.provider_name, "custom")
        os.environ.pop("SEARCH_API_ENDPOINT", None)
        os.environ.pop("SEARCH_API_KEY", None)

    def test_search_provider_reports_disabled_status(self):
        """Search provider should report is_configured=False when no API keys."""
        from backend.app.services.search_provider import get_search_provider, reset_search_provider
        reset_search_provider()
        provider = get_search_provider()
        self.assertFalse(provider.is_configured)
        self.assertEqual(provider.provider_name, "disabled")


class TestSearchProviderIntegration(unittest.TestCase):
    """Test SearchProvider integration with trace recording."""

    def setUp(self):
        from backend.app.storage.db import init_db
        init_db()
        from backend.app.storage.repositories import RunRepository
        RunRepository().create_run({
            "run_id": "search_test_run",
            "task_id": "search_test_task",
            "task_title": "Search Test",
            "task_brief": {},
            "mode": "real_time",
            "status": "pending",
            "created_at": NOW,
            "updated_at": NOW,
        })

    def test_disabled_search_traces_not_configured(self):
        """When search provider is disabled, should record skipped trace."""
        from backend.app.services.search_provider import get_search_provider, reset_search_provider
        from backend.app.storage.repositories import TraceRepository

        # Ensure disabled
        for key in ["TAVILY_API_KEY", "SERPAPI_API_KEY", "SEARCH_API_ENDPOINT"]:
            os.environ.pop(key, None)
        reset_search_provider()

        provider = get_search_provider()
        self.assertFalse(provider.is_configured)

        # Search should return appropriate reason
        results, reason = provider.search("test query", limit=5)
        self.assertEqual(reason, "SEARCH_PROVIDER_NOT_CONFIGURED")
        self.assertEqual(results, [])


class TestSearchProviderSchema(unittest.TestCase):
    """Test SearchResult schema."""

    def test_search_result_to_dict(self):
        """SearchResult should convert to dict correctly."""
        from backend.app.services.search_provider import SearchResult
        result = SearchResult(
            title="Test Page",
            url="https://example.com",
            snippet="Test snippet",
            source="test",
        )
        d = result.to_dict()
        self.assertEqual(d["title"], "Test Page")
        self.assertEqual(d["url"], "https://example.com")
        self.assertEqual(d["snippet"], "Test snippet")
        self.assertEqual(d["source"], "test")


if __name__ == "__main__":
    unittest.main()
