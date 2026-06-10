"""Legacy pages for ProductInsight Agent.

These pages are preserved for backward compatibility but are not actively developed.
"""

import streamlit as st


def render_legacy_page(page: str, run_id: str = None):
    """Render a legacy page by name.
    
    Note: Most pages have been migrated to app.py PAGE_RENDERERS.
    If you see this message, the page mapping may be incorrect.
    """
    # Known legacy pages that have no modern replacement
    LEGACY_ONLY = {"DAG", "Agents", "Evidence", "Review", "Sources", "EvidenceHub", 
                   "KnowledgeTable", "Compliance", "Replay", "Metrics", "NewAnalysis"}
    
    if page in LEGACY_ONLY:
        st.warning(f"Legacy page '{page}' - this page is not actively maintained.")
        st.info("Please use the main navigation pages for active development.")
    else:
        st.error(f"Page '{page}' not found. Please use the sidebar navigation.")
