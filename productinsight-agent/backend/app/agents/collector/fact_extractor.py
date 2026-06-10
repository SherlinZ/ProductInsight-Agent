"""Rule-based fact extractor with structured output."""
from __future__ import annotations
import json, logging, re, uuid
from datetime import datetime, timezone
from typing import Any
logger = logging.getLogger(__name__)

POSITIVE = {"supported", "yes", "available", "true", "has ", "provides", "offers", "includes", "enables", "allows"}
NEGATIVE = {"not supported", "not available", "no ", "cannot", "doesn't", "does not", "without", "unsupported", "lacks"}

def _signal(text):
    t = text.lower()
    p = sum(1 for s in POSITIVE if s in t)
    n = sum(1 for s in NEGATIVE if s in t)
    return "positive" if p > n else ("negative" if n > p else "neutral")

def _norm_dim(key):
    k = key.lower()
    for frag, dim in [
        ("pricing","pricing_model"),("price","pricing_model"),
        ("workflow","workflow"),("orchestrat","workflow"),("function_tree","workflow"),
        ("rag","knowledge_base"),("knowledge","knowledge_base"),
        ("deploy","deployment_options"),("docker","deployment_options"),
        ("enterprise","enterprise_readiness"),("sso","enterprise_readiness"),("rbac","enterprise_readiness"),
        ("integration","integration"),("api","integration"),
        ("model","model_support"),("llm","model_support"),("gpt","model_support"),
        ("agent","agent_capabilities"),("bot","agent_capabilities"),
        ("user","user_persona")]:
        if frag in k: return dim
    return key.split(".")[0]

def _clean_summary(text, max_len=280):
    text = text.strip()
    if len(text) <= max_len: return text
    for sep in [". ",".\n","!\n","?\n"]:
        i = text.find(sep)
        if 0 < i < max_len: return text[:i+1].strip()
    return text[:max_len].rsplit(" ",1)[0].strip() + "..."

def _build_value(dim, snippet):
    t = snippet.lower()
    base = {"summary": _clean_summary(snippet), "signal": _signal(snippet)}
    if dim == "pricing_model":
        v = dict(base)
        v["has_free_tier"] = any(kw in t for kw in ["free","free tier","free plan","free-forever"])
        v["pricing_public"] = any(kw in t for kw in ["pricing","price","plan","subscription","paid"])
        mentions = re.findall(r"[$€£¥]?\d+[^ \n]{0,40}", snippet)
        if mentions: v["price_mentions"] = mentions[:3]
        return v
    if dim == "workflow":
        v = dict(base)
        v["workflow_supported"] = not any(s in t for s in ["not support","no workflow","no built-in workflow"])
        v["capability_level"] = "strong" if any(s in t for s in ["orchestrat","pipeline","drag","canvas","node"]) else "basic"
        v["visual_builder"] = any(kw in t for kw in ["drag and drop","drag & drop","visual builder"])
        return v
    if dim == "knowledge_base":
        v = dict(base)
        v["rag_supported"] = any(kw in t for kw in ["rag","retrieval","vector","knowledge base"])
        v["document_ingestion"] = any(kw in t for kw in ["chunk","split","document","ingest"])
        return v
    if dim == "deployment_options":
        methods = [m for m in ["docker","kubernetes","self-hosted","cloud","on-premise"] if m in t]
        v = dict(base); v["deployment_methods"] = methods
        v["self_hosted_available"] = "self-hosted" in methods or "docker" in methods
        return v
    if dim == "enterprise_readiness":
        v = dict(base)
        for k, label in [("rbac","rbac"),("sso","sso"),("audit log","audit_log"),("encrypt","encryption")]:
            v[label] = any(kw in t for kw in [k])
        return v
    if dim == "model_support":
        models = [m for m in ["gpt-4","gpt-3.5","claude","gemini","mistral","llama","openai","anthropic","deepseek"] if m in t]
        v = dict(base); v["supported_models"] = models
        v["open_source_models"] = any(kw in t for kw in ["llama","open source model","self-hosted model"])
        return v
    if dim == "integration":
        v = dict(base)
        for k, label in [("api","api_available"),("webhook","webhook_available"),("sdk","sdk_available"),("plugin","plugin_system")]:
            v[label] = any(kw in t for kw in [k])
        return v
    if dim == "agent_capabilities":
        v = dict(base)
        v["agent_supported"] = any(kw in t for kw in ["agent","bot","assistant","copilot"])
        v["multi_agent"] = any(kw in t for kw in ["multi-agent","multi agent","agent collaboration"])
        return v
    return base

UNIT_MAP = {
    "pricing_model":"currency/text","workflow":"feature",
    "deployment_options":"deployment_mode","model_support":"model_list",
    "integration":"feature","agent_capabilities":"feature",
    "knowledge_base":"feature","enterprise_readiness":"feature",
}

class FactExtractor:
    def extract_facts(self, evidence_items, run_id):
        if not evidence_items: return []
        facts = []; now = datetime.now(timezone.utc).isoformat()
        for ev in evidence_items:
            fid = f"fact_{uuid.uuid4().hex[:16]}"
            raw_sk = ev.get("schema_key","function_tree.general")
            dim = _norm_dim(raw_sk)
            snippet = ev.get("snippet","")[:500]
            q = ev.get("quality_score", ev.get("confidence",0.7))
            conf = round(ev.get("confidence",0.7)*0.6 + q*0.4, 3)
            sv = _build_value(dim, snippet)
            facts.append({
                "fact_id": fid, "run_id": run_id,
                "product_id": ev.get("product_id",""),
                "product_slug": ev.get("product_slug"),
                "schema_key": dim, "raw_schema_key": raw_sk,
                "value_json": json.dumps(sv, ensure_ascii=False),
                "value_type": "object",
                "unit": UNIT_MAP.get(dim,"text"),
                "confidence": conf,
                "evidence_ids": [ev.get("evidence_id","")],
                "extraction_result_id": f"extr_{uuid.uuid4().hex[:12]}",
                "review_status": "pending",
                "created_at": now, "updated_at": now,
            })
        logger.info("FactExtractor: %d facts from %d evidence", len(facts), len(evidence_items))
        return facts
