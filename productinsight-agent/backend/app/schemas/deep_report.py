"""
Deep Report v2 Schemas

vNext-R3-A: Deep Report v2 - Multi-stage, evidence-backed, chapterized competitive analysis.

This module defines Pydantic schemas for:
- ReportSection: Individual report chapters
- SectionResearchPack: Evidence/signed claims binding per section
- SectionDraft: Section writing drafts
- ReportFigure: Chart specifications
- ReportTable: Comparison matrices
- ReportReview: Deep report reviewer traces
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional, Any

from pydantic import BaseModel, Field


class ReportSectionBase(BaseModel):
    """Base schema for report sections."""
    section_title: str
    section_slug: str
    section_type: str = "chapter"
    min_word_count: int = 800
    target_word_count: int = 1200
    writing_requirements: dict[str, Any] = Field(default_factory=dict)


class ReportSection(ReportSectionBase):
    """Report section with full metadata."""
    section_id: str
    report_id: str
    run_id: str
    section_index: int
    status: str = "pending"
    depth_score: Optional[float] = None
    evidence_count: int = 0
    claim_count: int = 0
    word_count: int = 0
    revision_count: int = 0
    review_notes: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        section_id: str,
        report_id: str,
        run_id: str,
        section_index: int,
        section_title: str,
        section_slug: str,
        section_type: str = "chapter",
        min_word_count: int = 800,
        target_word_count: int = 1200,
        writing_requirements: dict[str, Any] | None = None,
    ) -> ReportSection:
        """Factory method to create a new ReportSection."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            section_index=section_index,
            section_title=section_title,
            section_slug=section_slug,
            section_type=section_type,
            min_word_count=min_word_count,
            target_word_count=target_word_count,
            writing_requirements=writing_requirements or {},
            status="pending",
            depth_score=None,
            evidence_count=0,
            claim_count=0,
            word_count=0,
            revision_count=0,
            review_notes=None,
            metadata={},
            created_at=now,
            updated_at=now,
        )


class SectionResearchPackBase(BaseModel):
    """Base schema for section research packs."""
    section_question: str
    required_dimensions: list[str] = Field(default_factory=list)
    evidence_items: list[dict[str, Any]] = Field(default_factory=list)
    facts: list[dict[str, Any]] = Field(default_factory=list)
    candidate_claims: list[dict[str, Any]] = Field(default_factory=list)
    signed_claims: list[dict[str, Any]] = Field(default_factory=list)
    comparison_points: list[dict[str, Any]] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    recommended_tables: list[str] = Field(default_factory=list)


class SectionResearchPack(SectionResearchPackBase):
    """Section research pack with full metadata."""
    pack_id: str
    section_id: str
    report_id: str
    run_id: str
    status: str = "pending"
    evidence_coverage_rate: float = 0.0
    confidence_level: str = "medium"
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        pack_id: str,
        section_id: str,
        report_id: str,
        run_id: str,
        section_question: str,
        required_dimensions: list[str] | None = None,
        evidence_items: list[dict[str, Any]] | None = None,
        facts: list[dict[str, Any]] | None = None,
        candidate_claims: list[dict[str, Any]] | None = None,
        signed_claims: list[dict[str, Any]] | None = None,
        comparison_points: list[dict[str, Any]] | None = None,
        missing_information: list[str] | None = None,
        risk_notes: list[str] | None = None,
        recommended_tables: list[str] | None = None,
    ) -> SectionResearchPack:
        """Factory method to create a new SectionResearchPack."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            pack_id=pack_id,
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            section_question=section_question,
            required_dimensions=required_dimensions or [],
            evidence_items=evidence_items or [],
            facts=facts or [],
            candidate_claims=candidate_claims or [],
            signed_claims=signed_claims or [],
            comparison_points=comparison_points or [],
            missing_information=missing_information or [],
            risk_notes=risk_notes or [],
            recommended_tables=recommended_tables or [],
            status="pending",
            evidence_coverage_rate=0.0,
            confidence_level="medium",
            created_at=now,
            updated_at=now,
        )


class SectionDraftBase(BaseModel):
    """Base schema for section drafts."""
    content_markdown: str
    draft_type: str = "initial"
    trigger_type: str = "automatic"
    rework_issue_id: Optional[str] = None
    review_feedback: Optional[str] = None


class SectionDraft(SectionDraftBase):
    """Section draft with full metadata."""
    draft_id: str
    section_id: str
    report_id: str
    run_id: str
    draft_index: int = 1
    content_html: Optional[str] = None
    approved: bool = False
    word_count: int = 0
    quality_score: Optional[float] = None
    issues: list[dict[str, Any]] = Field(default_factory=list)
    key_judgments: list[str] = Field(default_factory=list)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    created_by_agent: str = "section_writer"
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        draft_id: str,
        section_id: str,
        report_id: str,
        run_id: str,
        content_markdown: str,
        draft_type: str = "initial",
        draft_index: int = 1,
        trigger_type: str = "automatic",
        rework_issue_id: Optional[str] = None,
        review_feedback: Optional[str] = None,
        key_judgments: Optional[list[str]] = None,
        cited_evidence_ids: Optional[list[str]] = None,
        created_by_agent: str = "section_writer",
    ) -> SectionDraft:
        """Factory method to create a new SectionDraft."""
        now = datetime.now(timezone.utc).isoformat()
        # Count words (supports both Chinese and English)
        # Chinese characters: each character counts as 1 word
        # English words: each word (separated by space) counts as 1
        import re
        chinese_count = len(re.findall(r'[\u4e00-\u9fff]', content_markdown))
        english_count = len(re.findall(r'[a-zA-Z]+', content_markdown))
        word_count = chinese_count + english_count
        return cls(
            draft_id=draft_id,
            section_id=section_id,
            report_id=report_id,
            run_id=run_id,
            draft_index=draft_index,
            content_markdown=content_markdown,
            content_html=None,
            draft_type=draft_type,
            trigger_type=trigger_type,
            rework_issue_id=rework_issue_id,
            review_feedback=review_feedback,
            approved=False,
            word_count=word_count,
            quality_score=None,
            issues=[],
            key_judgments=key_judgments or [],
            cited_evidence_ids=cited_evidence_ids or [],
            created_by_agent=created_by_agent,
            created_at=now,
            updated_at=now,
        )


class ReportFigureBase(BaseModel):
    """Base schema for report figures/charts."""
    figure_type: str
    figure_title: str
    figure_description: Optional[str] = None
    chart_spec: dict[str, Any] = Field(default_factory=dict)
    chart_data: dict[str, Any] = Field(default_factory=dict)
    section_id: Optional[str] = None
    target_position: Optional[str] = None
    width: int = 800
    height: int = 600


class ReportFigure(ReportFigureBase):
    """Report figure with full metadata."""
    figure_id: str
    report_id: str
    run_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        figure_id: str,
        report_id: str,
        run_id: str,
        figure_type: str,
        figure_title: str,
        figure_description: Optional[str] = None,
        chart_spec: dict[str, Any] | None = None,
        chart_data: dict[str, Any] | None = None,
        section_id: Optional[str] = None,
        target_position: Optional[str] = None,
        width: int = 800,
        height: int = 600,
    ) -> ReportFigure:
        """Factory method to create a new ReportFigure."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            figure_id=figure_id,
            report_id=report_id,
            run_id=run_id,
            figure_type=figure_type,
            figure_title=figure_title,
            figure_description=figure_description,
            chart_spec=chart_spec or {},
            chart_data=chart_data or {},
            section_id=section_id,
            target_position=target_position,
            width=width,
            height=height,
            metadata={},
            created_at=now,
            updated_at=now,
        )


class ReportTableBase(BaseModel):
    """Base schema for report tables/matrices."""
    table_type: str
    table_title: str
    table_description: Optional[str] = None
    headers: list[str] = Field(default_factory=list)
    rows: list[str] = Field(default_factory=list)
    cells: dict[str, Any] = Field(default_factory=dict)
    section_id: Optional[str] = None
    target_position: Optional[str] = None
    evidence_binding: dict[str, Any] = Field(default_factory=dict)
    interpretation: Optional[str] = None


class ReportTable(ReportTableBase):
    """Report table with full metadata."""
    table_id: str
    report_id: str
    run_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        table_id: str,
        report_id: str,
        run_id: str,
        table_type: str,
        table_title: str,
        table_description: Optional[str] = None,
        headers: list[str] | None = None,
        rows: list[str] | None = None,
        cells: dict[str, Any] | None = None,
        section_id: Optional[str] = None,
        target_position: Optional[str] = None,
        evidence_binding: dict[str, Any] | None = None,
        interpretation: Optional[str] = None,
    ) -> ReportTable:
        """Factory method to create a new ReportTable."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            table_id=table_id,
            report_id=report_id,
            run_id=run_id,
            table_type=table_type,
            table_title=table_title,
            table_description=table_description,
            headers=headers or [],
            rows=rows or [],
            cells=cells or {},
            section_id=section_id,
            target_position=target_position,
            evidence_binding=evidence_binding or {},
            interpretation=interpretation,
            metadata={},
            created_at=now,
            updated_at=now,
        )


class ReportReviewIssue(BaseModel):
    """Issue found during report review."""
    issue_type: str
    severity: str  # high, medium, low
    section_id: Optional[str] = None
    description: str
    suggested_action: str
    target_agent: Optional[str] = None


class ReportReviewBase(BaseModel):
    """Base schema for report reviews."""
    review_type: str = "final"
    target_id: Optional[str] = None
    target_type: Optional[str] = None
    overall_score: Optional[float] = None
    depth_score: Optional[float] = None
    evidence_score: Optional[float] = None
    business_value_score: Optional[float] = None
    issues: list[ReportReviewIssue] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    rework_instruction: Optional[str] = None
    reviewer_notes: Optional[str] = None


class ReportReview(ReportReviewBase):
    """Report review with full metadata."""
    review_id: str
    report_id: str
    run_id: str
    reviewer_agent: str = "report_reviewer"
    status: str = "pending"
    approved: bool = False
    created_at: str
    updated_at: str

    @classmethod
    def create(
        cls,
        review_id: str,
        report_id: str,
        run_id: str,
        review_type: str = "final",
        target_id: Optional[str] = None,
        target_type: Optional[str] = None,
        reviewer_agent: str = "report_reviewer",
    ) -> ReportReview:
        """Factory method to create a new ReportReview."""
        now = datetime.now(timezone.utc).isoformat()
        return cls(
            review_id=review_id,
            report_id=report_id,
            run_id=run_id,
            review_type=review_type,
            target_id=target_id,
            target_type=target_type,
            reviewer_agent=reviewer_agent,
            status="pending",
            overall_score=None,
            depth_score=None,
            evidence_score=None,
            business_value_score=None,
            issues=[],
            suggestions=[],
            rework_instruction=None,
            approved=False,
            created_at=now,
            updated_at=now,
        )


# P0-1 Schema Alignment: Only 3 Schema keys — function_tree / pricing_model / user_persona
# Removed: enterprise_readiness, ecosystem_analysis, customer_voice, evidence_tiers
DEEP_REPORT_OUTLINE = [
    # ── Cover ──────────────────────────────────────────────────────────────────
    {
        "slug": "cover",
        "title": "封面与可信度摘要",
        "type": "cover",
        "min_words": 200,
        "target_words": 300,
        "is_structured": True,
    },
    # ── Schema 1: function_tree ───────────────────────────────────────────────
    {
        "slug": "executive_summary",
        "title": "管理层摘要",
        "type": "chapter",
        "min_words": 500,
        "target_words": 800,
        "key_judgments": 5,
        "purpose": "基于 function_tree、pricing_model、user_persona 三个 Schema 的核心结论",
    },
    {
        "slug": "analysis_scope",
        "title": "分析范围与方法",
        "type": "chapter",
        "min_words": 300,
        "target_words": 500,
    },
    {
        "slug": "function_tree_overview",
        "title": "核心功能树概览",
        "type": "chapter",
        "min_words": 600,
        "target_words": 900,
        "schema_keys": ["function_tree"],
        "purpose": "展示各产品核心功能能力矩阵（function_tree Schema）",
    },
    {
        "slug": "workflow_orchestration",
        "title": "Workflow 编排能力",
        "type": "chapter",
        "min_words": 600,
        "target_words": 900,
        "schema_keys": ["function_tree"],
        "requires_matrix": "feature_matrix",
    },
    {
        "slug": "rag_knowledge_base",
        "title": "RAG / 知识库能力",
        "type": "chapter",
        "min_words": 500,
        "target_words": 800,
        "schema_keys": ["function_tree"],
    },
    {
        "slug": "model_support",
        "title": "模型兼容与扩展性",
        "type": "chapter",
        "min_words": 400,
        "target_words": 700,
        "schema_keys": ["function_tree"],
    },
    # ── Schema 2: pricing_model ───────────────────────────────────────────────
    {
        "slug": "pricing_model",
        "title": "商业模式与定价",
        "type": "chapter",
        "min_words": 600,
        "target_words": 900,
        "schema_keys": ["pricing_model"],
        "requires_matrix": "pricing_matrix",
    },
    {
        "slug": "tco_model",
        "title": "TCO 成本分析",
        "type": "chapter",
        "min_words": 400,
        "target_words": 700,
        "schema_keys": ["pricing_model"],
        "is_structured": True,
        "purpose": "基于 pricing_model Schema 提供成本评估框架",
    },
    # ── Schema 3: user_persona ────────────────────────────────────────────────
    {
        "slug": "user_persona",
        "title": "用户场景与适用团队",
        "type": "chapter",
        "min_words": 600,
        "target_words": 900,
        "schema_keys": ["user_persona"],
        "requires_matrix": "user_scenario_matrix",
    },
    {
        "slug": "competitor_overview",
        "title": "竞品对比总览",
        "type": "chapter",
        "min_words": 500,
        "target_words": 800,
        "product_cards": True,
        "schema_keys": ["function_tree", "pricing_model", "user_persona"],
    },
    # ── P0-Fix: Add mandatory decision-oriented sections to default outline ─────────
    # These are also added by _extend_outline_with_schema_sections, but putting them
    # here ensures they're present even in the fallback path (LLM failed, schema missing).
    {
        "slug": "competitor_selection_logic",
        "title": "竞品选择逻辑",
        "type": "chapter",
        "min_words": 200,
        "target_words": 400,
        "purpose": "分析本报告纳入/排除各产品的依据与标准",
    },
    {
        "slug": "market_positioning",
        "title": "市场定位图",
        "type": "chapter",
        "min_words": 300,
        "target_words": 500,
        "purpose": "各产品在功能定位、目标用户、定价层次上的二维定位对比",
    },
    {
        "slug": "competitor_profiles",
        "title": "竞品画像",
        "type": "chapter",
        "min_words": 200,
        "target_words": 400,
        "purpose": "每个主要竞品的发展历程、核心定位、目标用户与差异化优势",
    },
    # ── Cross-Schema ─────────────────────────────────────────────────────────
    {
        "slug": "swot_analysis",
        "title": "SWOT 分析",
        "type": "chapter",
        "min_words": 500,
        "target_words": 800,
        "requires_figures": ["swot_cards"],
        "schema_keys": ["function_tree", "pricing_model", "user_persona"],
    },
    {
        "slug": "selection_scorecard",
        "title": "选型评分卡",
        "type": "chapter",
        "min_words": 400,
        "target_words": 700,
        "is_structured": True,
        "schema_keys": ["function_tree", "pricing_model", "user_persona"],
    },
    # ── Professional Enhancement ───────────────────────────────────────────────
    {
        "slug": "poc_checklist",
        "title": "POC 验证计划",
        "type": "chapter",
        "min_words": 400,
        "target_words": 700,
        "is_structured": True,
        "purpose": "基于 function_tree、pricing_model、user_persona Schema 的验证清单",
    },
    {
        "slug": "risks_gaps",
        "title": "风险与证据缺口",
        "type": "chapter",
        "min_words": 300,
        "target_words": 500,
        "schema_keys": ["function_tree", "pricing_model", "user_persona"],
    },
    {
        "slug": "product_risks",
        "title": "选这个产品有什么风险",
        "type": "chapter",
        "is_structured": True,
        "min_words": 500,
        "target_words": 800,
        "purpose": "分析各产品实际使用中可能遇到的风险",
    },
    {
        "slug": "report_confidence",
        "title": "本报告底气有多足",
        "type": "chapter",
        "is_structured": True,
        "min_words": 400,
        "target_words": 600,
        "purpose": "评估报告结论可信度，帮助决策者正确使用报告",
    },
    {
        "slug": "evidence_appendix",
        "title": "证据附录",
        "type": "appendix",
        "min_words": 300,
        "target_words": 600,
        "is_structured": True,
    },
]


def get_default_outline() -> list[dict[str, Any]]:
    """Return the default Deep Report v2 outline."""
    return DEEP_REPORT_OUTLINE.copy()
