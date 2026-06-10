"""Configuration constants for ProductInsight Agent frontend."""

import os

# Allow override via environment variable for deployment flexibility
_api_host = os.environ.get("BACKEND_HOST", "localhost")
_api_port = os.environ.get("BACKEND_PORT", "8005")
API_BASE = f"http://{_api_host}:{_api_port}"
