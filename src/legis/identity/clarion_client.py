"""Clarion SEI read client — a thin transport seam.

legis consumes Clarion's SEI surfaces as an HTTP client (the same consumer model
as ``GitSurface`` / the read API). The default transport is stdlib ``urllib`` so
legis adds no dependency; a ``fetch`` callable is injectable so tests run offline.
SEI strings are opaque here — this module never parses them, only forwards them.

Wire contracts (pinned in ``clarion/docs/federation/contracts.md`` §SEI identity
resolution): ``GET /api/v1/_capabilities`` → ``{"sei": {"supported", "version"}}``;
``POST /api/v1/identity/resolve`` ``{"locator"}`` → ``{sei, current_locator,
content_hash, alive}`` (or ``{"alive": false}``); ``GET /api/v1/identity/sei/:sei``
→ alive or ``{alive:false, lineage}``; ``GET /api/v1/identity/lineage/:sei`` →
``{sei, lineage}``. On a loopback/trusted Clarion these read routes are
unauthenticated (trust matrix); HMAC provisioning is deferred (see plan).
"""

from __future__ import annotations

import json
import ipaddress
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, "dict | None"], dict]


class ClarionError(RuntimeError):
    """A Clarion identity call failed at the transport or decode layer."""


MAX_RESPONSE_BYTES = 1_000_000


@runtime_checkable
class ClarionIdentity(Protocol):
    def capability(self) -> bool: ...
    def resolve_locator(self, locator: str) -> dict[str, Any]: ...
    def resolve_sei(self, sei: str) -> dict[str, Any]: ...
    def lineage(self, sei: str) -> list[dict[str, Any]]: ...


def _urllib_fetch(method: str, url: str, body: dict | None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310 (trusted Clarion URL)
            decoded = _decode_json_response(resp, f"{method} {url}")
    except (urllib.error.URLError, ValueError) as exc:
        raise ClarionError(f"{method} {url} failed: {exc}") from exc
    return _require_dict(decoded, f"{method} {url}")


def _decode_json_response(resp: Any, context: str) -> Any:
    headers = getattr(resp, "headers", {}) or {}
    content_type = headers.get("Content-Type", "application/json")
    if "json" not in content_type.lower():
        raise ClarionError(f"{context} returned non-JSON content type: {content_type}")
    raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ClarionError(f"{context} response too large")
    return json.loads(raw.decode("utf-8"))


def _require_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ClarionError(f"{context} returned {type(value).__name__}, expected object")
    return value


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
        raise ClarionError("Clarion base URL must be an http(s) URL with a host")
    allow_insecure_remote = os.environ.get("LEGIS_ALLOW_INSECURE_REMOTE_HTTP") == "1"
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname) and not allow_insecure_remote:
        raise ClarionError("Clarion base URL must use HTTPS unless it is loopback")
    return base_url.rstrip("/")


class HttpClarionIdentity:
    def __init__(self, base_url: str, *, fetch: Fetch | None = None) -> None:
        self._base = _validate_base_url(base_url)
        self._fetch = fetch or _urllib_fetch

    def capability(self) -> bool:
        body = _require_dict(
            self._fetch("GET", f"{self._base}/api/v1/_capabilities", None),
            "Clarion capability",
        )
        sei = body.get("sei") if isinstance(body, dict) else None
        return isinstance(sei, dict) and sei.get("supported") is True

    def resolve_locator(self, locator: str) -> dict[str, Any]:
        return _require_dict(
            self._fetch(
                "POST", f"{self._base}/api/v1/identity/resolve", {"locator": locator}
            ),
            "Clarion resolve_locator",
        )

    def resolve_sei(self, sei: str) -> dict[str, Any]:
        quoted_sei = urllib.parse.quote(sei, safe="")
        return _require_dict(
            self._fetch("GET", f"{self._base}/api/v1/identity/sei/{quoted_sei}", None),
            "Clarion resolve_sei",
        )

    def lineage(self, sei: str) -> list[dict[str, Any]]:
        quoted_sei = urllib.parse.quote(sei, safe="")
        body = _require_dict(
            self._fetch("GET", f"{self._base}/api/v1/identity/lineage/{quoted_sei}", None),
            "Clarion lineage",
        )
        lineage = body.get("lineage", [])
        if not isinstance(lineage, list) or not all(isinstance(item, dict) for item in lineage):
            raise ClarionError("Clarion lineage returned malformed lineage list")
        return list(lineage)
