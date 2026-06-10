"""
LLM Client Module.

Provides a unified interface for calling LLM APIs.
All API keys and endpoints are read from environment variables.
No credentials are hardcoded.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Any

# Auto-load .env file from the project root (productinsight-agent/)
_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), ".env")
if os.path.exists(_env_path):
    from dotenv import load_dotenv
    load_dotenv(_env_path)


class LLMConfig:
    """LLM configuration loaded from environment variables."""

    def __init__(self) -> None:
        self.provider: str = os.getenv("MODEL_PROVIDER", "doubao")
        self.name: str = os.getenv("MODEL_NAME", "")
        self.api_key: str = os.getenv("MODEL_API_KEY", "")
        self.endpoint: str = os.getenv(
            "MODEL_ENDPOINT", "https://ark.cn-beijing.volces.com/api/v3"
        )
        self.timeout: int = int(os.getenv("LLM_TIMEOUT", "30"))
        self.max_retries: int = 2

        if not self.api_key:
            raise ValueError(
                "MODEL_API_KEY environment variable is not set. "
                "Please set it in .env or export it to your shell."
            )
        if not self.name:
            raise ValueError(
                "MODEL_NAME environment variable is not set. "
                "Please set it in .env (e.g. MODEL_NAME=<your-model-or-endpoint-id>)."
            )

    def __repr__(self) -> str:
        return (
            f"LLMConfig(provider={self.provider!r}, "
            f"name={self.name!r}, "
            f"endpoint={self.endpoint!r}, "
            f"timeout={self.timeout})"
        )


def _make_request(url: str, payload: dict[str, Any], api_key: str, timeout: int | None = None) -> dict[str, Any]:
    """Make HTTP POST request to LLM API with Bearer token auth."""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        raise LLMError(f"HTTPError {e.code} for POST {url}: {body[:1000]}")
    except Exception as e:
        raise LLMError(f"POST {url} failed ({type(e).__name__}: {e})")


class LLMError(Exception):
    """Raised when LLM API call fails."""
    pass


class LLMClient:
    """
    Unified LLM client with retry and error handling.
    All configuration is read from environment variables.
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        self._base_url = self.config.endpoint.rstrip("/")
        self._api_key = self.config.api_key
        self._model = self.config.name
        self._timeout = self.config.timeout
        self._max_retries = self.config.max_retries

    @property
    def model_name(self) -> str:
        """Return the configured model name."""
        return self._model

    def chat_text(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: int | None = None,
    ) -> str:
        """
        Send a chat request and return plain text response.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Max tokens to generate
            timeout: Per-call timeout in seconds. Defaults to self._timeout.

        Returns:
            The assistant's message content as string.
        """
        import urllib.parse
        url = urllib.parse.urljoin(self._base_url + "/", "chat/completions")

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error: Exception = LLMError("initial error")
        for attempt in range(self._max_retries + 1):
            try:
                data = _make_request(url, payload, self._api_key, timeout if timeout is not None else self._timeout)

                choices = data.get("choices", []) or []
                msg = (choices[0] or {}).get("message", {}) or {}
                content = msg.get("content", "")
                if not content and data.get("error"):
                    raise LLMError(f"API returned error: {data['error']}")
                return str(content)

            except (LLMError, urllib.error.HTTPError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(1.5 ** attempt)

        raise LLMError(
            f"LLM request failed after {self._max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """
        Send a chat request and parse the response as JSON.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
            temperature: Sampling temperature (set to 0 for deterministic JSON output)
            max_tokens: Max tokens to generate
            timeout: Per-call timeout in seconds. Defaults to self._timeout.

        Returns:
            Parsed JSON response as dict.
        """
        text = self.chat_text(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """
        Parse JSON from LLM output using multiple strategies.
        Raises LLMError if JSON cannot be parsed.
        """
        text = text.strip()

        # Strategy 1: direct parse
        try:
            return json.loads(text)
        except Exception:
            pass

        # Strategy 2: extract from ```json ... ``` blocks
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass

        # Strategy 3: extract first { ... } object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass

        raise LLMError(
            f"Could not parse JSON from LLM output (first 300 chars): {text[:300]}"
        )

    def responses_api(
        self,
        messages: list[dict[str, str]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """
        Call the Doubao Responses API (ARK v3 /responses endpoint).

        This API supports tool calling, including the built-in web_search tool
        which allows the model to search the web in real-time.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
            tools: List of tool definitions, e.g. [{"type": "web_search"}]
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Max tokens to generate
            timeout: Per-call timeout in seconds. Defaults to self._timeout.

        Returns:
            The full JSON response dict from the Responses API.
        """
        import urllib.parse as _urllib_parse

        url = _urllib_parse.urljoin(self._base_url + "/", "responses")

        payload: dict[str, Any] = {
            "model": self._model,
            "input": messages,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        last_error: Exception = LLMError("initial error")
        for attempt in range(self._max_retries + 1):
            try:
                data = _make_request(
                    url, payload, self._api_key,
                    timeout if timeout is not None else self._timeout,
                )
                return data
            except (LLMError, urllib.error.HTTPError) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    time.sleep(1.5 ** attempt)

        raise LLMError(
            f"Responses API request failed after {self._max_retries + 1} attempts. "
            f"Last error: {last_error}"
        )


# Module-level singleton
_client: "LLMClient | None" = None


def get_llm_client() -> LLMClient:
    """Get or create the global LLM client singleton."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
