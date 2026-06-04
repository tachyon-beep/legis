"""Clarion SEI read client — a thin transport seam.

legis consumes Clarion's SEI surfaces as an HTTP client (the same consumer model
as ``GitSurface`` / the read API). The default transport is stdlib ``urllib`` so
legis adds no dependency; a ``fetch`` callable is injectable so tests run offline.
SEI strings are opaque here — this module never parses them, only forwards them.

Wire contracts (pinned in ``clarion/docs/federation/contracts.md`` §SEI identity
resolution and §Authentication): ``GET /api/v1/_capabilities`` →
``{"sei": {"supported", "version"}}``;
``POST /api/v1/identity/resolve`` ``{"locator"}`` → ``{sei, current_locator,
content_hash, alive}`` (or ``{"alive": false}``); ``GET /api/v1/identity/sei/:sei``
→ alive or ``{alive:false, lineage}``; ``GET /api/v1/identity/lineage/:sei`` →
``{sei, lineage}``. On a loopback/trusted Clarion these read routes are
unauthenticated (trust matrix). When an HMAC key is provisioned, protected
routes carry ``X-Loom-Component: clarion:<hmac>`` plus freshness headers.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import ipaddress
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Mapping
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, "dict | None", Mapping[str, str]], dict]


class ClarionError(RuntimeError):
    """A Clarion identity call failed at the transport or decode layer."""


MAX_RESPONSE_BYTES = 1_000_000


@runtime_checkable
class ClarionIdentity(Protocol):
    def capability(self) -> bool: ...
    def resolve_locator(self, locator: str) -> dict[str, Any]: ...
    def resolve_batch(self, locators: list[str]) -> dict[str, Any]: ...
    def resolve_sei(self, sei: str) -> dict[str, Any]: ...
    def lineage(self, sei: str) -> list[dict[str, Any]]: ...


def _json_body_bytes(body: dict | None) -> bytes:
    if body is None:
        return b""
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _path_and_query(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    path_and_query = parsed.path or "/"
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"
    return path_and_query


def sign_clarion_request(
    key: bytes,
    method: str,
    url: str,
    body: dict | None,
    *,
    timestamp: int,
    nonce: str,
) -> dict[str, str]:
    """Return Clarion's current Loom-component HMAC request headers."""
    body_bytes = _json_body_bytes(body)
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    message = (
        f"{method}\n{_path_and_query(url)}\n{body_hash}\n{timestamp}\n{nonce}"
    ).encode("utf-8")
    signature = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-Loom-Component": f"clarion:{signature}",
        "X-Loom-Timestamp": str(timestamp),
        "X-Loom-Nonce": nonce,
    }


def clarion_hmac_key_from_env() -> bytes | None:
    """Resolve Clarion HMAC key material from env without making it mandatory."""
    value = os.environ.get("LEGIS_CLARION_HMAC_KEY") or os.environ.get("LEGIS_HMAC_KEY")
    return value.encode("utf-8") if value else None


def _urllib_fetch(
    method: str,
    url: str,
    body: dict | None,
    headers: Mapping[str, str] | None = None,
) -> dict:
    body_bytes = _json_body_bytes(body)
    data = body_bytes if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
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
    def __init__(
        self,
        base_url: str,
        *,
        fetch: Fetch | None = None,
        hmac_key: bytes | str | None = None,
        clock: Callable[[], int] | None = None,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        self._base = _validate_base_url(base_url)
        self._fetch = fetch or _urllib_fetch
        self._hmac_key = hmac_key.encode("utf-8") if isinstance(hmac_key, str) else hmac_key
        self._clock = clock or (lambda: int(time.time()))
        self._nonce_factory = nonce_factory or (lambda: uuid.uuid4().hex)

    def _request(self, method: str, path: str, body: dict | None, *, signed: bool = True) -> dict:
        url = f"{self._base}{path}"
        headers: dict[str, str] = {}
        if signed and self._hmac_key is not None:
            headers = sign_clarion_request(
                self._hmac_key,
                method,
                url,
                body,
                timestamp=self._clock(),
                nonce=self._nonce_factory(),
            )
        return self._fetch(method, url, body, headers)

    def capability(self) -> bool:
        body = _require_dict(
            self._request("GET", "/api/v1/_capabilities", None, signed=False),
            "Clarion capability",
        )
        sei = body.get("sei") if isinstance(body, dict) else None
        return isinstance(sei, dict) and sei.get("supported") is True

    def resolve_locator(self, locator: str) -> dict[str, Any]:
        return _require_dict(
            self._request("POST", "/api/v1/identity/resolve", {"locator": locator}),
            "Clarion resolve_locator",
        )

    def resolve_batch(self, locators: list[str]) -> dict[str, Any]:
        return _require_dict(
            self._request("POST", "/api/v1/identity/resolve:batch", {"locators": locators}),
            "Clarion resolve_batch",
        )

    def resolve_sei(self, sei: str) -> dict[str, Any]:
        quoted_sei = urllib.parse.quote(sei, safe="")
        return _require_dict(
            self._request("GET", f"/api/v1/identity/sei/{quoted_sei}", None),
            "Clarion resolve_sei",
        )

    def lineage(self, sei: str) -> list[dict[str, Any]]:
        quoted_sei = urllib.parse.quote(sei, safe="")
        body = _require_dict(
            self._request("GET", f"/api/v1/identity/lineage/{quoted_sei}", None),
            "Clarion lineage",
        )
        lineage = body.get("lineage", [])
        if not isinstance(lineage, list) or not all(isinstance(item, dict) for item in lineage):
            raise ClarionError("Clarion lineage returned malformed lineage list")
        return list(lineage)
