PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    task_title TEXT NOT NULL,
    task_brief_json TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('real_time', 'cached', 'replay')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    current_node TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    product_name TEXT NOT NULL,
    company_name TEXT,
    official_website TEXT,
    region TEXT,
    product_type TEXT,
    seed_urls_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    product_id TEXT,
    source_type TEXT NOT NULL,
    title TEXT,
    url TEXT,
    domain TEXT,
    collection_method TEXT NOT NULL,
    robots_status TEXT NOT NULL,
    terms_note TEXT,
    trust_tier TEXT,
    fetched_at TEXT,
    content_hash TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    raw_text_path TEXT,
    html_path TEXT,
    screenshot_path TEXT,
    metadata_json TEXT,
    content_hash TEXT NOT NULL,
    token_count INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    product_id TEXT,
    schema_key TEXT,
    snippet TEXT NOT NULL,
    start_offset INTEGER,
    end_offset INTEGER,
    section_title TEXT,
    confidence REAL NOT NULL,
    pii_masked INTEGER NOT NULL DEFAULT 1,
    evidence_type TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE IF NOT EXISTS facts (
    fact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    product_id TEXT NOT NULL,
    schema_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    value_type TEXT NOT NULL,
    unit TEXT,
    confidence REAL NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    extraction_result_id TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    product_id TEXT,
    dimension TEXT NOT NULL,
    claim_text TEXT NOT NULL,
    claim_type TEXT NOT NULL,
    fact_ids_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    support_level TEXT,
    review_status TEXT NOT NULL CHECK (review_status IN ('pending', 'signed', 'rework_required', 'rejected')),
    signed_claim_id TEXT,
    created_by_agent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE IF NOT EXISTS claim_evidence_links (
    link_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    support_type TEXT NOT NULL CHECK (support_type IN ('supports', 'contradicts', 'related')),
    support_score REAL NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (claim_id) REFERENCES claims(claim_id),
    FOREIGN KEY (evidence_id) REFERENCES evidence_items(evidence_id)
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    review_target_type TEXT NOT NULL CHECK (review_target_type IN ('claim', 'report', 'schema', 'source')),
    review_target_id TEXT NOT NULL,
    reviewer_agent TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pass', 'rework_required', 'rejected', 'warning')),
    checks_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    comments TEXT,
    signed_claim_id TEXT,
    reviewed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS rework_requests (
    rework_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    review_id TEXT NOT NULL,
    target_agent TEXT NOT NULL,
    target_node TEXT NOT NULL,
    affected_objects_json TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    required_actions_json TEXT NOT NULL,
    success_criteria_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'skipped')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retry INTEGER NOT NULL DEFAULT 2,
    metrics_before_json TEXT,
    metrics_after_json TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (review_id) REFERENCES reviews(review_id)
);

CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    receiver TEXT NOT NULL,
    message_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    trace_id TEXT,
    parent_message_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS trace_logs (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_name TEXT NOT NULL,
    agent_name TEXT,
    prompt_version TEXT,
    model_name TEXT,
    input_path TEXT,
    output_path TEXT,
    decision TEXT,
    token_input INTEGER,
    token_output INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL CHECK (status IN ('success', 'failed', 'retry', 'skipped')),
    error_message TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    title TEXT NOT NULL,
    report_status TEXT NOT NULL CHECK (report_status IN ('draft', 'reviewed', 'exported', 'blocked')),
    content_markdown_path TEXT,
    content_html_path TEXT,
    content_pdf_path TEXT,
    quality_summary_json TEXT NOT NULL,
    created_by_agent TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS report_spans (
    span_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    section_id TEXT NOT NULL,
    section_title TEXT NOT NULL,
    span_type TEXT NOT NULL CHECK (span_type IN ('paragraph', 'table', 'bullet', 'summary')),
    text TEXT NOT NULL,
    claim_ids_json TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    unsupported_flag INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (report_id) REFERENCES reports(report_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS eval_logs (
    eval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    schema_completion_rate REAL NOT NULL,
    evidence_coverage_rate REAL NOT NULL,
    unsupported_claim_rate REAL NOT NULL,
    review_pass_rate REAL,
    rework_success_rate REAL,
    replay_success_rate REAL,
    manual_correction_rate REAL,
    source_coverage_count INTEGER NOT NULL,
    conflict_count INTEGER NOT NULL,
    analysis_time_minutes REAL,
    metrics_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS pii_logs (
    pii_log_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_id TEXT,
    evidence_id TEXT,
    detected_types_json TEXT NOT NULL,
    masked_text_path TEXT,
    risk_level TEXT NOT NULL CHECK (risk_level IN ('low', 'medium', 'high')),
    status TEXT NOT NULL CHECK (status IN ('passed', 'masked', 'blocked')),
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id),
    FOREIGN KEY (source_id) REFERENCES sources(source_id),
    FOREIGN KEY (evidence_id) REFERENCES evidence_items(evidence_id)
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS idx_products_run_id ON products(run_id);
CREATE INDEX IF NOT EXISTS idx_sources_run_id ON sources(run_id);
CREATE INDEX IF NOT EXISTS idx_sources_product_id ON sources(product_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_run_id ON snapshots(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run_id ON evidence_items(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_product_id ON evidence_items(product_id);
CREATE INDEX IF NOT EXISTS idx_facts_run_id ON facts(run_id);
CREATE INDEX IF NOT EXISTS idx_claims_run_id ON claims(run_id);
CREATE INDEX IF NOT EXISTS idx_claims_review_status ON claims(review_status);
CREATE INDEX IF NOT EXISTS idx_reviews_run_id ON reviews(run_id);
CREATE INDEX IF NOT EXISTS idx_rework_run_id ON rework_requests(run_id);
CREATE INDEX IF NOT EXISTS idx_messages_run_id ON messages(run_id);
CREATE INDEX IF NOT EXISTS idx_trace_logs_run_id ON trace_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_reports_run_id ON reports(run_id);
CREATE INDEX IF NOT EXISTS idx_eval_logs_run_id ON eval_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_pii_logs_run_id ON pii_logs(run_id);
