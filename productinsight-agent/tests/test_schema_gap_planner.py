"""
Unit tests for SchemaGapPlanner.
"""
from __future__ import annotations

import unittest
from backend.app.services.schema_gap_planner import (
    SchemaGapPlanner,
    detect_schema_gaps,
    REQUIRED_SCHEMA_KEYS,
    SCHEMA_KEY_NORMALIZATION,
)


class TestSchemaGapPlanner(unittest.TestCase):
    """Test cases for SchemaGapPlanner class."""

    def setUp(self):
        self.planner = SchemaGapPlanner()

    def test_empty_products(self):
        """Empty products list should return empty gaps."""
        gaps, summary = self.planner.plan(
            facts=[], evidence_items=[], products=[], run_id="test_001"
        )
        self.assertEqual(gaps, [])
        self.assertEqual(summary["products_analyzed"], 0)
        self.assertEqual(summary["schema_completion_rate"], 0.0)

    def test_empty_facts_generates_missing_fact_gaps(self):
        """No facts should generate missing_fact gaps for each required schema key."""
        products = [
            {"product_id": "dify", "product_name": "Dify", "product_slug": "dify"},
        ]
        gaps, summary = self.planner.plan(
            facts=[], evidence_items=[], products=products, run_id="test_002"
        )
        # Should have one gap per required schema key (19 total)
        self.assertEqual(len(gaps), len(REQUIRED_SCHEMA_KEYS))
        self.assertEqual(summary["products_analyzed"], 1)
        # All gaps should be missing_fact with high priority
        for gap in gaps:
            self.assertEqual(gap["gap_type"], "missing_fact")
            self.assertEqual(gap["priority"], "high")
            self.assertEqual(gap["product_id"], "dify")

    def test_weak_evidence_detection(self):
        """Fact with low quality evidence should generate weak_evidence gap."""
        products = [
            {"product_id": "coze", "product_name": "Coze", "product_slug": "coze"},
        ]
        facts = [
            {
                "fact_id": "fact_001",
                "run_id": "test_003",
                "product_id": "coze",
                "product_slug": "coze",
                "schema_key": "api_support",  # Use exact required key
                "confidence": 0.7,
                "value_json": "{}",
            }
        ]
        evidence_items = [
            {
                "evidence_id": "ev_001",
                "product_id": "coze",
                "product_slug": "coze",
                "schema_key": "api_support",  # Match the fact's schema_key
                "quality": {
                    "final_score": 0.3,
                    "usable_for_claim": False,
                    "freshness": 0.8,
                },
            }
        ]
        gaps, summary = self.planner.plan(
            facts=facts, evidence_items=evidence_items, products=products, run_id="test_003"
        )
        # Should have weak_evidence gap for api_support
        api_gaps = [g for g in gaps if g["schema_key"] == "api_support"]
        self.assertEqual(len(api_gaps), 1)
        self.assertEqual(api_gaps[0]["gap_type"], "weak_evidence")

    def test_low_confidence_detection(self):
        """Fact with low confidence should generate low_confidence gap."""
        products = [
            {"product_id": "fastgpt", "product_name": "FastGPT", "product_slug": "fastgpt"},
        ]
        facts = [
            {
                "fact_id": "fact_002",
                "run_id": "test_004",
                "product_id": "fastgpt",
                "product_slug": "fastgpt",
                "schema_key": "workflow",
                "confidence": 0.4,  # Below 0.55 threshold
                "value_json": "{}",
            }
        ]
        gaps, summary = self.planner.plan(
            facts=facts, evidence_items=[], products=products, run_id="test_004"
        )
        # Should have low_confidence gap
        workflow_gaps = [g for g in gaps if g["schema_key"] == "workflow_orchestration"]
        self.assertEqual(len(workflow_gaps), 1)
        self.assertEqual(workflow_gaps[0]["gap_type"], "low_confidence")

    def test_stale_source_detection(self):
        """Evidence with low freshness should generate stale_source gap."""
        products = [
            {"product_id": "flowise", "product_name": "Flowise", "product_slug": "flowise"},
        ]
        facts = [
            {
                "fact_id": "fact_003",
                "run_id": "test_005",
                "product_id": "flowise",
                "product_slug": "flowise",
                "schema_key": "rag_support",  # Use exact required key
                "confidence": 0.7,
                "value_json": "{}",
            }
        ]
        evidence_items = [
            {
                "evidence_id": "ev_002",
                "product_id": "flowise",
                "product_slug": "flowise",
                "schema_key": "rag_support",  # Match the fact's schema_key
                "quality": {
                    "final_score": 0.7,
                    "usable_for_claim": True,
                    "freshness": 0.2,  # Below 0.4 threshold
                },
            }
        ]
        gaps, summary = self.planner.plan(
            facts=facts, evidence_items=evidence_items, products=products, run_id="test_005"
        )
        # Should have stale_source gap for rag_support
        rag_gaps = [g for g in gaps if g["schema_key"] == "rag_support"]
        self.assertEqual(len(rag_gaps), 1)
        self.assertEqual(rag_gaps[0]["gap_type"], "stale_source")

    def test_suggested_queries_for_deployment(self):
        """Suggested queries should be generated for deployment gaps."""
        products = [
            {"product_id": "dify", "product_name": "Dify", "product_slug": "dify"},
        ]
        gaps, _ = self.planner.plan(
            facts=[], evidence_items=[], products=products, run_id="test_006"
        )
        deployment_gaps = [g for g in gaps if g["schema_key"] == "self_hosted"]
        self.assertEqual(len(deployment_gaps), 1)
        queries = deployment_gaps[0]["suggested_queries"]
        self.assertGreaterEqual(len(queries), 2)
        self.assertLessEqual(len(queries), 4)
        # Queries should contain product name
        for q in queries:
            self.assertIn("Dify", q)

    def test_suggested_queries_for_pricing(self):
        """Suggested queries should be generated for pricing gaps."""
        products = [
            {"product_id": "coze", "product_name": "Coze", "product_slug": "coze"},
        ]
        gaps, _ = self.planner.plan(
            facts=[], evidence_items=[], products=products, run_id="test_007"
        )
        pricing_gaps = [g for g in gaps if g["schema_key"] == "paid_plans"]
        self.assertEqual(len(pricing_gaps), 1)
        queries = pricing_gaps[0]["suggested_queries"]
        self.assertGreaterEqual(len(queries), 2)
        for q in queries:
            self.assertIn("Coze", q)

    def test_suggested_queries_for_enterprise(self):
        """Suggested queries should be generated for enterprise gaps."""
        products = [
            {"product_id": "fastgpt", "product_name": "FastGPT", "product_slug": "fastgpt"},
        ]
        gaps, _ = self.planner.plan(
            facts=[], evidence_items=[], products=products, run_id="test_008"
        )
        enterprise_gaps = [g for g in gaps if g["schema_key"] == "rbac"]
        self.assertEqual(len(enterprise_gaps), 1)
        queries = enterprise_gaps[0]["suggested_queries"]
        self.assertGreaterEqual(len(queries), 2)
        for q in queries:
            self.assertIn("FastGPT", q)

    def test_schema_completion_rate_calculation(self):
        """Schema completion rate should be calculated correctly."""
        products = [
            {"product_id": "dify", "product_name": "Dify", "product_slug": "dify"},
        ]
        # Provide one fact for workflow
        facts = [
            {
                "fact_id": "fact_004",
                "run_id": "test_009",
                "product_id": "dify",
                "product_slug": "dify",
                "schema_key": "workflow",
                "confidence": 0.8,
                "value_json": "{}",
            }
        ]
        gaps, summary = self.planner.plan(
            facts=facts, evidence_items=[], products=products, run_id="test_009"
        )
        # 1 out of 19 keys filled
        expected_rate = 1 / len(REQUIRED_SCHEMA_KEYS)
        self.assertAlmostEqual(
            summary["schema_completion_rate"], expected_rate, places=3
        )

    def test_multiple_products(self):
        """Multiple products should be analyzed correctly."""
        products = [
            {"product_id": "dify", "product_name": "Dify", "product_slug": "dify"},
            {"product_id": "coze", "product_name": "Coze", "product_slug": "coze"},
        ]
        gaps, summary = self.planner.plan(
            facts=[], evidence_items=[], products=products, run_id="test_010"
        )
        self.assertEqual(len(gaps), len(REQUIRED_SCHEMA_KEYS) * 2)
        self.assertEqual(summary["products_analyzed"], 2)
        # Check coverage by product
        self.assertIn("dify", summary["schema_coverage_by_product"])
        self.assertIn("coze", summary["schema_coverage_by_product"])

    def test_schema_key_normalization(self):
        """Schema keys should be normalized correctly."""
        products = [
            {"product_id": "test", "product_name": "Test", "product_slug": "test"},
        ]
        facts = [
            {
                "fact_id": "fact_005",
                "run_id": "test_011",
                "product_id": "test",
                "product_slug": "test",
                "schema_key": "workflow",  # Should normalize to workflow_orchestration
                "confidence": 0.8,
                "value_json": "{}",
                "evidence_ids": ["ev_workflow"],  # Add evidence_ids
            }
        ]
        evidence_items = [
            {
                "evidence_id": "ev_workflow",
                "product_id": "test",
                "product_slug": "test",
                "schema_key": "workflow",
                "quality": {
                    "final_score": 0.8,
                    "usable_for_claim": True,
                    "freshness": 0.9,
                },
            }
        ]
        gaps, summary = self.planner.plan(
            facts=facts, evidence_items=evidence_items, products=products, run_id="test_011"
        )
        # Should NOT have a missing_fact gap for workflow_orchestration since we provided a fact with good evidence
        workflow_missing_gaps = [g for g in gaps if g["schema_key"] == "workflow_orchestration" and g["gap_type"] == "missing_fact"]
        self.assertEqual(len(workflow_missing_gaps), 0, "Should not have missing_fact gap when fact with evidence exists")

    def test_gap_fields_complete(self):
        """Each gap should have all required fields."""
        products = [
            {"product_id": "test", "product_name": "Test", "product_slug": "test"},
        ]
        gaps, _ = self.planner.plan(
            facts=[], evidence_items=[], products=products, run_id="test_012"
        )
        required_fields = [
            "gap_id", "run_id", "product_id", "product_name", "product_slug",
            "schema_key", "gap_type", "priority", "required_source_types",
            "suggested_queries", "reason", "related_evidence_ids", "created_at",
        ]
        for gap in gaps:
            for field in required_fields:
                self.assertIn(field, gap, f"Missing field: {field} in gap {gap.get('gap_id')}")

    def test_gap_types_have_correct_priorities(self):
        """Gap types should map to correct priorities."""
        expected_priorities = {
            "missing_fact": "high",
            "low_confidence": "medium",
            "weak_evidence": "medium",
            "stale_source": "low",
        }
        for gap_type, expected_priority in expected_priorities.items():
            self.assertEqual(
                self.planner.GAP_TYPE_PRIORITY[gap_type], expected_priority
            )


class TestDetectSchemaGapsFunction(unittest.TestCase):
    """Test cases for detect_schema_gaps convenience function."""

    def test_detect_schema_gaps_function(self):
        """detect_schema_gaps should work as convenience function."""
        products = [
            {"product_id": "dify", "product_name": "Dify", "product_slug": "dify"},
        ]
        gaps, summary = detect_schema_gaps(
            facts=[], evidence_items=[], products=products, run_id="test_013"
        )
        self.assertIsInstance(gaps, list)
        self.assertIsInstance(summary, dict)
        self.assertGreater(len(gaps), 0)


class TestSchemaKeyNormalization(unittest.TestCase):
    """Test schema key normalization mappings."""

    def test_workflow_normalization(self):
        """Workflow-related keys should normalize to workflow_orchestration."""
        for key in ["workflow", "orchestration", "flow"]:
            self.assertEqual(
                SCHEMA_KEY_NORMALIZATION.get(key), "workflow_orchestration"
            )

    def test_pricing_normalization(self):
        """Pricing-related keys should normalize correctly."""
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("pricing"), "paid_plans")
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("price"), "paid_plans")
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("free"), "free_tier")

    def test_deployment_normalization(self):
        """Deployment-related keys should normalize correctly."""
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("docker"), "docker_support")
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("self-hosted"), "self_hosted")
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("k8s"), "docker_support")

    def test_enterprise_normalization(self):
        """Enterprise-related keys should normalize correctly."""
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("sso"), "sso")
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("rbac"), "rbac")
        self.assertEqual(SCHEMA_KEY_NORMALIZATION.get("audit"), "audit_log")


class TestRequiredSchemaKeys(unittest.TestCase):
    """Test that required schema keys are properly defined."""

    def test_required_keys_not_empty(self):
        """Required schema keys should not be empty."""
        self.assertGreater(len(REQUIRED_SCHEMA_KEYS), 0)

    def test_required_keys_contain_core_fields(self):
        """Core AI Agent fields should be present."""
        core_keys = [
            "workflow_orchestration", "agent_builder", "knowledge_base",
            "rag_support", "free_tier", "enterprise_plan", "rbac", "api_support",
        ]
        for key in core_keys:
            self.assertIn(key, REQUIRED_SCHEMA_KEYS, f"Missing core key: {key}")


if __name__ == "__main__":
    unittest.main()


class TestHierarchicalSchemaKeys(unittest.TestCase):
    """Test hierarchical schema key normalization."""

    def test_deployment_options_private_deployment(self):
        """deployment_options.private_deployment should normalize to private_deployment."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("deployment_options.private_deployment")
        self.assertEqual(result, "private_deployment")

    def test_deployment_options_self_hosted(self):
        """deployment_options.self_hosted should normalize to self_hosted."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("deployment_options.self_hosted")
        self.assertEqual(result, "self_hosted")

    def test_deployment_options_docker_support(self):
        """deployment_options.docker_support should normalize to docker_support."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("deployment_options.docker_support")
        self.assertEqual(result, "docker_support")

    def test_pricing_model_free_tier(self):
        """pricing_model.free_tier should normalize to free_tier."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("pricing_model.free_tier")
        self.assertEqual(result, "free_tier")

    def test_pricing_model_paid_plans(self):
        """pricing_model.paid_plans should normalize to paid_plans."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("pricing_model.paid_plans")
        self.assertEqual(result, "paid_plans")

    def test_pricing_model_enterprise_plan(self):
        """pricing_model.enterprise_plan should normalize to enterprise_plan."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("pricing_model.enterprise_plan")
        self.assertEqual(result, "enterprise_plan")

    def test_enterprise_readiness_sso(self):
        """enterprise_readiness.sso should normalize to sso."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("enterprise_readiness.sso")
        self.assertEqual(result, "sso")

    def test_enterprise_readiness_rbac(self):
        """enterprise_readiness.rbac should normalize to rbac."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("enterprise_readiness.rbac")
        self.assertEqual(result, "rbac")

    def test_enterprise_readiness_audit_log(self):
        """enterprise_readiness.audit_log should normalize to audit_log."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("enterprise_readiness.audit_log")
        self.assertEqual(result, "audit_log")

    def test_integration_api_support(self):
        """integration.api_support should normalize to api_support."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("integration.api_support")
        self.assertEqual(result, "api_support")

    def test_integration_webhook(self):
        """integration.webhook should normalize to webhook."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("integration.webhook")
        self.assertEqual(result, "webhook")

    def test_model_support_provider(self):
        """model_support.provider should normalize to model_provider_support."""
        planner = SchemaGapPlanner()
        result = planner._normalize_schema_key("model_support.provider")
        self.assertEqual(result, "model_provider_support")


class TestFactEvidenceIdsMatching(unittest.TestCase):
    """Test that fact.evidence_ids is used for evidence matching."""

    def test_fact_evidence_ids_used_for_matching(self):
        """Fact with evidence_ids should use those for quality check."""
        planner = SchemaGapPlanner()
        fact = {
            "fact_id": "f1",
            "product_id": "test",
            "product_slug": "test",
            "schema_key": "workflow",
            "confidence": 0.8,
            "evidence_ids": ["ev_good", "ev_bad"],
        }
        evidence_by_id = {
            "ev_good": {
                "evidence_id": "ev_good",
                "product_id": "test",
                "product_slug": "test",
                "schema_key": "workflow",
                "quality": {"final_score": 0.8, "usable_for_claim": True, "freshness": 0.9},
            },
            "ev_bad": {
                "evidence_id": "ev_bad",
                "product_id": "test",
                "product_slug": "test",
                "schema_key": "workflow",
                "quality": {"final_score": 0.2, "usable_for_claim": False, "freshness": 0.2},
            },
        }
        gap_type, reason, related_ids = planner._check_quality_issues(fact, [], evidence_by_id)
        # related_ids should contain both evidence IDs from fact.evidence_ids
        self.assertIn("ev_good", related_ids)
        self.assertIn("ev_bad", related_ids)

    def test_fact_evidence_ids_json_string(self):
        """Fact with evidence_ids as JSON string should be parsed."""
        planner = SchemaGapPlanner()
        fact = {
            "fact_id": "f1",
            "product_id": "test",
            "product_slug": "test",
            "schema_key": "workflow",
            "confidence": 0.8,
            "evidence_ids": '["ev1", "ev2"]',
        }
        evidence_by_id = {
            "ev1": {
                "evidence_id": "ev1",
                "quality": {"final_score": 0.8, "usable_for_claim": True, "freshness": 0.9},
            },
            "ev2": {
                "evidence_id": "ev2",
                "quality": {"final_score": 0.7, "usable_for_claim": True, "freshness": 0.8},
            },
        }
        gap_type, reason, related_ids = planner._check_quality_issues(fact, [], evidence_by_id)
        # Should have no gap since evidence is good
        self.assertIsNone(gap_type)

    def test_fact_has_evidence_ids_but_not_found(self):
        """Fact with evidence_ids but evidence not found should generate weak_evidence."""
        planner = SchemaGapPlanner()
        fact = {
            "fact_id": "f1",
            "product_id": "test",
            "product_slug": "test",
            "schema_key": "workflow",
            "confidence": 0.8,
            "evidence_ids": ["ev_missing"],
        }
        evidence_by_id = {}  # Evidence not found
        gap_type, reason, related_ids = planner._check_quality_issues(fact, [], evidence_by_id)
        self.assertEqual(gap_type, "weak_evidence")
        self.assertIn("evidence_ids", reason)
        self.assertIn("not found", reason)

    def test_fact_has_no_supporting_evidence(self):
        """Fact without evidence_ids and no matched evidence should generate weak_evidence."""
        planner = SchemaGapPlanner()
        fact = {
            "fact_id": "f1",
            "product_id": "test",
            "product_slug": "test",
            "schema_key": "workflow",
            "confidence": 0.8,
            "evidence_ids": [],
        }
        # No evidence_by_id and empty evidence list
        gap_type, reason, related_ids = planner._check_quality_issues(fact, [], {})
        self.assertEqual(gap_type, "weak_evidence")
        self.assertIn("no supporting evidence", reason)


class TestComputeMetrics(unittest.TestCase):
    """Test compute_metrics includes schema gap examples."""

    def test_metrics_contains_schema_gap_examples(self):
        """compute_metrics should include schema_gap_examples."""
        from backend.app.orchestrator.nodes import compute_metrics

        state = {
            "run_id": "test_run",
            "mode": "real_time",
            "claim_drafts": [],
            "signed_claims": [],
            "evidence_items": [],
            "rework_requests": [],
            "sources": [],
            "errors": [],
            "schema_gaps": [
                {
                    "product_name": "Dify",
                    "schema_key": "rbac",
                    "gap_type": "missing_fact",
                    "priority": "high",
                    "suggested_queries": [
                        "Dify RBAC documentation",
                        "Dify enterprise features",
                    ],
                    "reason": "No fact or evidence found",
                },
                {
                    "product_name": "Coze",
                    "schema_key": "docker",
                    "gap_type": "weak_evidence",
                    "priority": "medium",
                    "suggested_queries": [
                        "Coze docker deployment",
                    ],
                    "reason": "Evidence quality low",
                },
            ],
            "schema_coverage": {
                "schema_completion_rate": 0.5,
                "high_priority_gaps": 1,
                "schema_coverage_by_product": {},
                "missing_schema_keys_by_product": {},
            },
        }

        result = compute_metrics(state)
        metrics = result.get("metrics", {})

        self.assertIn("schema_gap_examples", metrics)
        self.assertIn("schema_gap_suggested_queries_by_product", metrics)
        self.assertEqual(len(metrics["schema_gap_examples"]), 2)
        self.assertIn("Dify", metrics["schema_gap_suggested_queries_by_product"])
        self.assertIn("Coze", metrics["schema_gap_suggested_queries_by_product"])

    def test_schema_gap_examples_limited_to_10(self):
        """schema_gap_examples should be limited to 10."""
        from backend.app.orchestrator.nodes import compute_metrics

        # Create 15 gaps
        gaps = [
            {
                "product_name": f"Product{i}",
                "schema_key": "rbac",
                "gap_type": "missing_fact",
                "priority": "high" if i < 10 else "medium",
                "suggested_queries": [f"Query {i}"],
                "reason": "No fact",
            }
            for i in range(15)
        ]

        state = {
            "run_id": "test_run",
            "mode": "real_time",
            "claim_drafts": [],
            "signed_claims": [],
            "evidence_items": [],
            "rework_requests": [],
            "sources": [],
            "errors": [],
            "schema_gaps": gaps,
            "schema_coverage": {
                "schema_completion_rate": 0.3,
                "high_priority_gaps": 10,
            },
        }

        result = compute_metrics(state)
        metrics = result.get("metrics", {})

        self.assertLessEqual(len(metrics["schema_gap_examples"]), 10)


if __name__ == "__main__":
    unittest.main()
