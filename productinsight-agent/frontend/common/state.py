"""Session state management for ProductInsight Agent frontend."""

import streamlit as st


def init_session_state():
    """Initialize all session state variables."""
    # Demo state
    if "demo_loaded" not in st.session_state:
        st.session_state["demo_loaded"] = False

    # Selected IDs
    if "selected_run_id" not in st.session_state:
        st.session_state["selected_run_id"] = None

    if "selected_project_id" not in st.session_state:
        st.session_state["selected_project_id"] = None

    # Intake Draft Session State
    if "intake_user_request" not in st.session_state:
        st.session_state["intake_user_request"] = ""

    if "intake_project_draft" not in st.session_state:
        st.session_state["intake_project_draft"] = None

    if "intake_products_df" not in st.session_state:
        st.session_state["intake_products_df"] = None

    if "intake_selected_dimensions" not in st.session_state:
        st.session_state["intake_selected_dimensions"] = None

    if "intake_generated" not in st.session_state:
        st.session_state["intake_generated"] = False

    # AnalysisFlow Session State
    if "af_stage" not in st.session_state:
        st.session_state["af_stage"] = "intake"

    if "af_intake_draft" not in st.session_state:
        st.session_state["af_intake_draft"] = None

    if "af_intake_products_df" not in st.session_state:
        st.session_state["af_intake_products_df"] = None

    if "af_intake_dims" not in st.session_state:
        st.session_state["af_intake_dims"] = None

    # Research Plan state
    if "rp_plan_id" not in st.session_state:
        st.session_state["rp_plan_id"] = None

    if "rp_plan_data" not in st.session_state:
        st.session_state["rp_plan_data"] = None

    if "rp_dag_data" not in st.session_state:
        st.session_state["rp_dag_data"] = None

    if "rp_confirmed_dag_id" not in st.session_state:
        st.session_state["rp_confirmed_dag_id"] = None

    # Edit state
    if "edit_proj_name" not in st.session_state:
        st.session_state["edit_proj_name"] = ""

    if "edit_task_type" not in st.session_state:
        st.session_state["edit_task_type"] = "competitor_landscape"

    if "edit_region" not in st.session_state:
        st.session_state["edit_region"] = "global"

    if "edit_description" not in st.session_state:
        st.session_state["edit_description"] = ""

    # Research Plan confirmed state
    if "af_plan_confirmed" not in st.session_state:
        st.session_state["af_plan_confirmed"] = False

    # Errors
    if "last_start_error" not in st.session_state:
        st.session_state["last_start_error"] = ""


def reset_analysis_flow_state():
    """Reset all AnalysisFlow session state for a new task.
    
    This is the single source of truth for resetting the analysis flow.
    Keep all keys identical to the original af_reset() implementation.
    """
    # Core flow state
    st.session_state["af_stage"] = "intake"

    # Intake state
    st.session_state["af_intake_draft"] = None
    st.session_state["af_intake_products_df"] = None
    st.session_state["af_intake_dims"] = None
    st.session_state["intake_user_request"] = ""
    st.session_state["intake_project_draft"] = None
    st.session_state["intake_products_df"] = None
    st.session_state["intake_selected_dimensions"] = None
    st.session_state["intake_generated"] = False

    # Edit state
    st.session_state["edit_proj_name"] = ""
    st.session_state["edit_task_type"] = "competitor_landscape"
    st.session_state["edit_region"] = "global"
    st.session_state["edit_description"] = ""

    # Research Plan state
    st.session_state["rp_plan_id"] = None
    st.session_state["rp_plan_data"] = None
    st.session_state["rp_dag_data"] = None
    st.session_state["rp_confirmed_dag_id"] = None
    st.session_state["af_plan_confirmed"] = False

    # Project selection state
    st.session_state["selected_project_id"] = None
    st.session_state["selected_run_id"] = None

    # Clear editor keys
    if "products_editor" in st.session_state:
        del st.session_state["products_editor"]
    for dim in ["function_tree", "pricing_model", "user_persona",
                "customer_voice", "swot", "enterprise_readiness",
                "market_positioning", "integration_capabilities"]:
        key = f"intake_dim_{dim}"
        if key in st.session_state:
            del st.session_state[key]
