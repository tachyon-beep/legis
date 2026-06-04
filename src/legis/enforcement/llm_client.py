"""Deployable OpenRouter-backed LLM client for the coached judge."""

from __future__ import annotations

import json
import os
import ipaddress
import http.client
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

Fetch = Callable[[str, str, dict[str, Any], Mapping[str, str]], dict[str, Any]]

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_JUDGE_MODEL = "anthropic/claude-opus-4.7"
DEFAULT_JUDGE_MAX_TOKENS = 1024
MAX_RESPONSE_BYTES = 1_000_000


class LLMTransportError(RuntimeError):
    """A judge LLM transport or response-shape failure."""


@dataclass(frozen=True)
class LLMClientConfig:
    provider: str
    api_key: str
    model_id: str
    max_tokens: int
    base_url: str = DEFAULT_OPENROUTER_BASE_URL


def llm_client_config_from_env() -> LLMClientConfig | None:
    provider = os.environ.get("LEGIS_JUDGE_PROVIDER")
    if provider is None or provider == "":
        return None
    if provider != "openrouter":
        raise LLMTransportError(f"unsupported LEGIS_JUDGE_PROVIDER: {provider}")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMTransportError("LEGIS_JUDGE_PROVIDER=openrouter requires OPENROUTER_API_KEY")

    raw_max_tokens = os.environ.get("LEGIS_JUDGE_MAX_TOKENS", str(DEFAULT_JUDGE_MAX_TOKENS))
    try:
        max_tokens = int(raw_max_tokens)
    except ValueError as exc:
        raise LLMTransportError("LEGIS_JUDGE_MAX_TOKENS must be an integer") from exc
    if max_tokens <= 0:
        raise LLMTransportError("LEGIS_JUDGE_MAX_TOKENS must be positive")

    return LLMClientConfig(
        provider="openrouter",
        api_key=api_key,
        model_id=os.environ.get("LEGIS_JUDGE_MODEL", DEFAULT_JUDGE_MODEL),
        max_tokens=max_tokens,
        base_url=_validate_base_url(
            os.environ.get("LEGIS_JUDGE_BASE_URL", DEFAULT_OPENROUTER_BASE_URL)
        ),
    )


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LLMTransportError("LLM judge base URL must be an http(s) URL with a host")
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname):
        raise LLMTransportError("LLM judge base URL must use HTTPS unless it is loopback")
    return base_url.rstrip("/")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _open_no_redirect(req: urllib.request.Request) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=30.0)


def _urllib_fetch(
    method: str,
    url: str,
    body: dict[str, Any],
    headers: Mapping[str, str],
) -> dict[str, Any]:
    data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for name, value in headers.items():
        req.add_header(name, value)

    try:
        with _open_no_redirect(req) as resp:  # noqa: S310
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if 300 <= exc.code < 400:
            raise LLMTransportError(
                f"{method} {url} redirect not allowed: {exc.code}"
            ) from exc
        raise LLMTransportError(f"{method} {url} failed: {exc}") from exc
    except (urllib.error.URLError, ValueError, OSError, http.client.HTTPException) as exc:
        raise LLMTransportError(f"{method} {url} failed: {exc}") from exc

    if len(raw) > MAX_RESPONSE_BYTES:
        raise LLMTransportError(f"{method} {url} response too large")

    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMTransportError(f"{method} {url} returned invalid JSON") from exc

    if not isinstance(decoded, dict):
        raise LLMTransportError(f"{method} {url} returned {type(decoded).__name__}, expected object")
    return decoded


class OpenRouterLLMClient:
    def __init__(self, config: LLMClientConfig, *, fetch: Fetch | None = None) -> None:
        if config.provider != "openrouter":
            raise LLMTransportError(f"OpenRouterLLMClient cannot use provider {config.provider!r}")
        self._config = replace(config, base_url=_validate_base_url(config.base_url))
        self._fetch = fetch or _urllib_fetch
        self.model_id = f"openrouter:{config.model_id}"

    def complete(self, prompt: str) -> str:
        body = {
            "model": self._config.model_id,
            "max_tokens": self._config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        response = self._fetch(
            "POST",
            f"{self._config.base_url}/chat/completions",
            body,
            headers,
        )

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMTransportError("LLM response missing choices")
        first = choices[0]
        if not isinstance(first, dict):
            raise LLMTransportError("LLM response choice is not an object")
        message = first.get("message")
        if not isinstance(message, dict):
            raise LLMTransportError("LLM response choice missing message")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMTransportError("LLM response content is not a string")
        if not content.strip():
            raise LLMTransportError("LLM response empty content")
        return content
