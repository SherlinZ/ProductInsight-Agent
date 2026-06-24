"""Global styles for ProductInsight Agent frontend."""

import streamlit as st


def apply_global_styles():
    """Apply global CSS styles to the Streamlit app."""
    st.markdown("""
<style>
    .stMainBlockContainer { padding-top: 1rem; }
    section[data-testid="stSidebar"] > div { padding-top: 1rem; }
    div[data-testid="stExpander"] { border: 1px solid #e0e0e0; border-radius: 8px; }
    .stMetric { background: #f8f9fa; border-radius: 8px; padding: 10px; }
    /* Uniform table row heights */
    .stTable tbody tr { height: 36px !important; }
    .stTable thead tr th { height: 36px !important; padding: 4px 12px !important; }
    .stTable tbody tr td { height: 36px !important; padding: 4px 12px !important; }
</style>
""", unsafe_allow_html=True)
