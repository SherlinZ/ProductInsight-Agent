-- Add quality_json column to evidence_items for storing evidence quality scores
-- This migration adds the column only if it doesn't exist (for forward compatibility)

-- Note: This migration uses a Python-side check for SQLite's lack of IF NOT EXISTS for ADD COLUMN
-- The actual ALTER TABLE is executed in the Python migration file

-- Evidence quality fields explanation:
-- quality_json contains:
-- {
--   "relevance": 0.0-1.0,        -- Match with product name, schema keywords
--   "authority": 0.0-1.0,        -- Source type, trust tier, domain
--   "freshness": 0.0-1.0,       -- Recency of evidence
--   "schema_fit": 0.0-1.0,       -- Alignment with AI Agent schema
--   "information_density": 0.0-1.0, -- Content richness
--   "final_score": 0.0-1.0,     -- Weighted average
--   "usable_for_claim": true/false, -- Threshold-based decision
--   "reasons": []                 -- Human-readable quality reasons
-- }
