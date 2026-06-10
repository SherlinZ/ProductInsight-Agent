"""
Unit tests for EvidenceEvaluator.
"""
from __future__ import annotations

import unittest
from backend.app.services.evidence_evaluator import (
    EvidenceEvaluator,
    EvidenceQuality,
    evaluate_evidence_items,
    SCHEMA_KEYWORDS,
    DIMENSION_KEYWORDS,
)


class TestEvidenceEvaluator(unittest.TestCase):
    """Test cases for EvidenceEvaluator class."""

    def setUp(self):
        self.evaluator = EvidenceEvaluator()

    def test_empty_evidence(self):
        """Empty evidence should not crash."""
        quality = self.evaluator.evaluate({})
        self.assertIsInstance(quality, EvidenceQuality)
        self.assertGreaterEqual(quality.final_score, 0.0)
        self.assertLessEqual(quality.final_score, 1.0)

    def test_evidence_without_snippet(self):
        """Evidence without snippet should return minimal score."""
        quality = self.evaluator.evaluate({"evidence_id": "test123"})
        self.assertIsInstance(quality, EvidenceQuality)
        self.assertEqual(quality.relevance, 0.0)
        self.assertEqual(quality.schema_fit, 0.0)

    def test_high_quality_evidence(self):
        """High-quality evidence with all positive signals."""
        evidence = {
            "evidence_id": "test1",
            "snippet": "Dify offers workflow orchestration with visual builder, Docker deployment, and enterprise features including RBAC and SSO. Pricing starts at $99/month for professional plan.",
            "source_type": "documentation",
            "trust_tier": "high",
            "url": "https://docs.dify.ai",
            "fetched_at": "2024-06-01T10:00:00Z",
            "product_id": "dify",
            "product_name": "Dify",
            "schema_key": "workflow",
        }
        quality = self.evaluator.evaluate(evidence)
        self.assertGreaterEqual(quality.final_score, 0.6)
        self.assertTrue(quality.usable_for_claim)

    def test_low_quality_evidence(self):
        """Low-quality evidence with minimal content."""
        evidence = {
            "evidence_id": "test2",
            "snippet": "ok",
            "source_type": "social",
            "trust_tier": "low",
            "url": "https://twitter.com/example",
        }
        quality = self.evaluator.evaluate(evidence)
        self.assertLess(quality.final_score, 0.6)
        self.assertFalse(quality.usable_for_claim)

    def test_schema_fit_scoring(self):
        """Evidence with AI Agent schema keywords should score high on schema_fit."""
        evidence = {
            "evidence_id": "test3",
            "snippet": "This platform supports RAG workflow with vector embeddings, Docker deployment, API integration, and enterprise SSO authentication.",
            "source_type": "documentation",
            "trust_tier": "high",
            "product_name": "TestProduct",
        }
        quality = self.evaluator.evaluate(evidence)
        self.assertGreaterEqual(quality.schema_fit, 0.5)

    def test_authority_scoring(self):
        """High-authority sources should score high."""
        high_authority = {
            "evidence_id": "test4",
            "snippet": "Feature documentation",
            "source_type": "documentation",
            "trust_tier": "high",
            "url": "https://docs.example.com",
        }
        low_authority = {
            "evidence_id": "test5",
            "snippet": "Feature discussion",
            "source_type": "forum",
            "trust_tier": "low",
            "url": "https://reddit.com/r/example",
        }
        high_quality = self.evaluator.evaluate(high_authority)
        low_quality = self.evaluator.evaluate(low_authority)
        self.assertGreater(high_quality.authority, low_quality.authority)

    def test_freshness_unknown_date(self):
        """Evidence without date should get medium freshness score."""
        evidence = {
            "evidence_id": "test8",
            "snippet": "Some content",
        }
        quality = self.evaluator.evaluate(evidence)
        self.assertEqual(quality.freshness, 0.5)

    def test_information_density(self):
        """Rich content should score higher than sparse content."""
        rich = {
            "evidence_id": "test9",
            "snippet": "Supported features: API integration, webhook notifications, Docker deployment with Kubernetes scaling. Models supported: GPT-4, Claude 3, Llama 2. Pricing: Free tier, $99 Pro, $499 Enterprise.",
        }
        sparse = {
            "evidence_id": "test10",
            "snippet": "Feature",
        }
        rich_quality = self.evaluator.evaluate(rich)
        sparse_quality = self.evaluator.evaluate(sparse)
        self.assertGreater(rich_quality.information_density, sparse_quality.information_density)


class TestEvaluateEvidenceItems(unittest.TestCase):
    """Test cases for evaluate_evidence_items function."""

    def test_empty_list(self):
        """Empty list should return empty result with zero summary."""
        items, summary = evaluate_evidence_items([])
        self.assertEqual(items, [])
        self.assertEqual(summary["total_evidence"], 0)
        self.assertEqual(summary["usable_evidence"], 0)
        self.assertEqual(summary["avg_final_score"], 0.0)

    def test_single_item(self):
        """Single evidence item should be enriched with quality."""
        evidence = [
            {
                "evidence_id": "ev1",
                "snippet": "Dify offers comprehensive workflow automation",
                "source_type": "documentation",
                "product_name": "Dify",
            }
        ]
        items, summary = evaluate_evidence_items(evidence, "run_123")
        self.assertEqual(len(items), 1)
        self.assertIn("quality", items[0])
        self.assertEqual(summary["total_evidence"], 1)
        self.assertEqual(summary["run_id"], "run_123")

    def test_multiple_items(self):
        """Multiple evidence items should be processed correctly."""
        evidence_list = [
            {
                "evidence_id": f"ev{i}",
                "snippet": f"Evidence item {i} with some content about workflow and API integration",
                "source_type": "documentation",
                "product_name": "TestProduct",
            }
            for i in range(5)
        ]
        items, summary = evaluate_evidence_items(evidence_list)
        self.assertEqual(len(items), 5)
        self.assertEqual(summary["total_evidence"], 5)
        self.assertGreaterEqual(summary["avg_final_score"], 0.0)
        self.assertLessEqual(summary["avg_final_score"], 1.0)

    def test_quality_fields_present(self):
        """Each enriched item should have all required quality fields."""
        evidence = [
            {
                "evidence_id": "ev1",
                "snippet": "Comprehensive evidence about the product",
                "source_type": "documentation",
                "product_name": "Product",
            }
        ]
        items, _ = evaluate_evidence_items(evidence)
        quality = items[0]["quality"]
        required_fields = [
            "relevance",
            "authority",
            "freshness",
            "schema_fit",
            "information_density",
            "final_score",
            "usable_for_claim",
            "reasons",
        ]
        for field in required_fields:
            self.assertIn(field, quality, f"Missing field: {field}")


class TestSchemaKeywords(unittest.TestCase):
    """Test that schema keywords are properly defined."""

    def test_schema_keywords_not_empty(self):
        """Schema keywords should not be empty."""
        self.assertGreater(len(SCHEMA_KEYWORDS), 0)

    def test_dimension_keywords_not_empty(self):
        """Dimension keywords should not be empty."""
        self.assertGreater(len(DIMENSION_KEYWORDS), 0)

    def test_common_keywords_present(self):
        """Common AI Agent keywords should be present."""
        common_keywords = ["workflow", "api", "deployment", "pricing", "enterprise"]
        for keyword in common_keywords:
            self.assertIn(keyword, SCHEMA_KEYWORDS, f"Missing keyword: {keyword}")


if __name__ == "__main__":
    unittest.main()


class TestSchemaFitRegex(unittest.TestCase):
    """Test schema_fit regex tokenization capabilities."""

    def test_schema_fit_matches_hyphenated_keywords(self):
        """Schema_fit should match hyphenated keywords like self-hosted."""
        evaluator = EvidenceEvaluator()
        evidence = {
            "evidence_id": "test_hyphen",
            "snippet": "This platform supports self-hosted deployment on Docker and Kubernetes.",
        }
        quality = evaluator.evaluate(evidence)
        # self-hosted, docker, kubernetes should all match
        self.assertGreaterEqual(quality.schema_fit, 0.3)

    def test_schema_fit_matches_rbac_sso(self):
        """Schema_fit should match RBAC, SSO, audit-log with punctuation."""
        evaluator = EvidenceEvaluator()
        evidence = {
            "evidence_id": "test_rbac",
            "snippet": "Enterprise features include RBAC/SSO authentication and audit-log capabilities.",
        }
        quality = evaluator.evaluate(evidence)
        # rbac, sso, audit should match
        self.assertGreaterEqual(quality.schema_fit, 0.5)

    def test_schema_fit_matches_api_with_punctuation(self):
        """Schema_fit should match API with commas and punctuation."""
        evaluator = EvidenceEvaluator()
        evidence = {
            "evidence_id": "test_api_punct",
            "snippet": "Supports REST API, webhook integration, and endpoint configuration.",
        }
        quality = evaluator.evaluate(evidence)
        # api, webhook, endpoint should match
        self.assertGreaterEqual(quality.schema_fit, 0.5)


class TestSourceMetadataEnrichment(unittest.TestCase):
    """Test that source metadata affects authority scoring."""

    def test_official_documentation_has_higher_authority(self):
        """Evidence from official documentation should score higher than unknown sources."""
        evaluator = EvidenceEvaluator()

        # Evidence from official documentation
        doc_evidence = {
            "evidence_id": "test_doc",
            "snippet": "This platform supports API integration and workflow automation.",
            "source_type": "documentation",
            "trust_tier": "high",
            "url": "https://docs.example.com/features",
        }

        # Evidence from unknown source
        unknown_evidence = {
            "evidence_id": "test_unknown",
            "snippet": "This platform supports API integration and workflow automation.",
            "source_type": "unknown",
            "trust_tier": "unknown",
            "url": "https://example.blogspot.com",
        }

        doc_quality = evaluator.evaluate(doc_evidence)
        unknown_quality = evaluator.evaluate(unknown_evidence)

        # Documentation should have significantly higher authority
        self.assertGreater(
            doc_quality.authority,
            unknown_quality.authority,
            "Documentation source should have higher authority than unknown source",
        )

    def test_github_source_has_high_authority(self):
        """Evidence from GitHub should have high authority."""
        evaluator = EvidenceEvaluator()
        evidence = {
            "evidence_id": "test_github",
            "snippet": "This tool provides plugin architecture and webhook support.",
            "source_type": "github",
            "trust_tier": "high",
            "url": "https://github.com/example/project",
        }
        quality = evaluator.evaluate(evidence)
        self.assertGreaterEqual(quality.authority, 0.6)

    def test_social_source_has_low_authority(self):
        """Evidence from social media should have low authority."""
        evaluator = EvidenceEvaluator()
        evidence = {
            "evidence_id": "test_social",
            "snippet": "Great product for AI workflows!",
            "source_type": "social",
            "trust_tier": "low",
            "url": "https://twitter.com/user/status/123",
        }
        quality = evaluator.evaluate(evidence)
        self.assertLess(quality.authority, 0.3)


class TestNoisePatterns(unittest.TestCase):
    """Test that noise patterns reduce information density."""

    def test_navigation_noise_reduces_density(self):
        """Navigation noise should reduce information density score."""
        evaluator = EvidenceEvaluator()

        # Content without noise
        clean_evidence = {
            "evidence_id": "test_clean",
            "snippet": "This platform supports API integration, workflow automation, and enterprise features including RBAC and SSO authentication.",
        }

        # Same content with navigation noise
        noisy_evidence = {
            "evidence_id": "test_noisy",
            "snippet": "Home Menu Navigation Login Sign Up Contact Us Copyright Privacy Policy. This platform supports API integration, workflow automation, and enterprise features including RBAC and SSO authentication.",
        }

        clean_quality = evaluator.evaluate(clean_evidence)
        noisy_quality = evaluator.evaluate(noisy_evidence)

        # Clean content should have higher or equal density
        self.assertGreaterEqual(
            clean_quality.information_density,
            noisy_quality.information_density,
            "Clean content should have higher information density than noisy content",
        )


class TestEvidenceEvaluatorIntegration(unittest.TestCase):
    """Integration tests for the complete evaluation flow."""

    def test_evaluate_evidence_items_with_source_metadata(self):
        """Test that evidence items with source metadata evaluate correctly."""
        evidence_list = [
            {
                "evidence_id": "ev_doc",
                "snippet": "Dify provides comprehensive documentation for workflow orchestration with Docker deployment and API integration.",
                "source_type": "documentation",
                "trust_tier": "high",
                "url": "https://docs.dify.ai",
                "fetched_at": "2024-06-01T10:00:00Z",
                "product_name": "Dify",
                "schema_key": "workflow",
            },
            {
                "evidence_id": "ev_social",
                "snippet": "ok",
                "source_type": "social",
                "trust_tier": "low",
                "url": "https://twitter.com/user",
            },
        ]

        items, summary = evaluate_evidence_items(evidence_list, "test_run")

        # Verify summary statistics
        self.assertEqual(summary["total_evidence"], 2)
        self.assertGreaterEqual(summary["usable_evidence"], 0)

        # Verify first evidence has higher quality
        doc_quality = items[0]["quality"]
        social_quality = items[1]["quality"]
        self.assertGreater(
            doc_quality["final_score"],
            social_quality["final_score"],
        )

    def test_empty_evidence_list_returns_valid_summary(self):
        """Empty evidence list should return valid summary with zero values."""
        items, summary = evaluate_evidence_items([], "test_run")

        self.assertEqual(items, [])
        self.assertEqual(summary["total_evidence"], 0)
        self.assertEqual(summary["usable_evidence"], 0)
        self.assertEqual(summary["avg_final_score"], 0.0)
        self.assertEqual(summary["low_quality_count"], 0)
        self.assertEqual(summary["run_id"], "test_run")


if __name__ == "__main__":
    unittest.main()
