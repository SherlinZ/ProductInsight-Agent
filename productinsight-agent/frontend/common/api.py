"""API client utilities for ProductInsight Agent frontend."""

import os
import requests
import streamlit as st

_api_host = os.environ.get("BACKEND_HOST", "localhost")
_api_port = os.environ.get("BACKEND_PORT", "8005")
API_BASE = f"http://{_api_host}:{_api_port}"


def get_json(path: str, default=None, timeout: float = 10.0):
    """GET JSON from API and handle errors."""
    try:
        resp = requests.get(f"{API_BASE}{path}", timeout=timeout)
        if resp.status_code == 404:
            return default
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to backend API ({API_BASE}). Make sure the backend is running.")
        return default
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        return default


def post_json(path: str, data: dict = None, default=None, timeout: float = 15.0):
    """POST JSON to API and handle errors."""
    try:
        resp = requests.post(f"{API_BASE}{path}", json=data or {}, timeout=timeout)
        if resp.status_code == 404:
            return default
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to backend API ({API_BASE}). Make sure the backend is running.")
        return default
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        return default


def put_json(path: str, data: dict = None, default=None, timeout: float = 15.0):
    """PUT JSON to API and handle errors."""
    try:
        resp = requests.put(f"{API_BASE}{path}", json=data or {}, timeout=timeout)
        if resp.status_code == 404:
            return default
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error(f"Cannot connect to backend API ({API_BASE}). Make sure the backend is running.")
        return default
    except requests.exceptions.RequestException as e:
        st.error(f"API request failed: {e}")
        return default
