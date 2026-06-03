"""Filigree entity-association client — legis binds governance to issues.

Same transport posture as ``identity/clarion_client.py``: stdlib ``urllib`` with
an injectable ``fetch`` so tests run offline; no new dependency. legis binds the
opaque SEI as ``entity_id`` (Filigree never parses it) and hands the entity's
content hash for Filigree to store verbatim; drift comparison stays legis's job.
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


class FiligreeError(RuntimeError):
    """A Filigree call failed at the transport or decode layer."""


MAX_RESPONSE_BYTES = 1_000_000


@runtime_checkable
class FiligreeClient(Protocol):
    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str) -> dict[str, Any]: ...
    def associations_for_entity(self, entity_id: str) -> list[dict[str, Any]]: ...


def _urllib_fetch(method: str, url: str, body: dict | None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310 (trusted Filigree URL)
            decoded = _decode_json_response(resp, f"{method} {url}")
    except (urllib.error.URLError, ValueError) as exc:
        raise FiligreeError(f"{method} {url} failed: {exc}") from exc
    return _require_dict(decoded, f"{method} {url}")


def _decode_json_response(resp: Any, context: str) -> Any:
    headers = getattr(resp, "headers", {}) or {}
    content_type = headers.get("Content-Type", "application/json")
    if "json" not in content_type.lower():
        raise FiligreeError(f"{context} returned non-JSON content type: {content_type}")
    raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise FiligreeError(f"{context} response too large")
    return json.loads(raw.decode("utf-8"))


def _require_dict(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FiligreeError(f"{context} returned {type(value).__name__}, expected object")
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
        raise FiligreeError("Filigree base URL must be an http(s) URL with a host")
    allow_insecure_remote = os.environ.get("LEGIS_ALLOW_INSECURE_REMOTE_HTTP") == "1"
    if parsed.scheme == "http" and not _is_loopback(parsed.hostname) and not allow_insecure_remote:
        raise FiligreeError("Filigree base URL must use HTTPS unless it is loopback")
    return base_url.rstrip("/")


class HttpFiligreeClient:
    def __init__(self, base_url: str, *, fetch: Fetch | None = None) -> None:
        self._base = _validate_base_url(base_url)
        self._fetch = fetch or _urllib_fetch

    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str) -> dict[str, Any]:
        quoted_issue_id = urllib.parse.quote(issue_id, safe="")
        return _require_dict(
            self._fetch(
                "POST", f"{self._base}/api/issue/{quoted_issue_id}/entity-associations",
                {"entity_id": entity_id, "content_hash": content_hash, "actor": actor},
            ),
            "Filigree attach",
        )

    def associations_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"entity_id": entity_id})
        body = _require_dict(
            self._fetch("GET", f"{self._base}/api/entity-associations?{q}", None),
            "Filigree associations_for_entity",
        )
        associations = body.get("associations", [])
        if not isinstance(associations, list) or not all(
            isinstance(item, dict) for item in associations
        ):
            raise FiligreeError("Filigree returned malformed associations list")
        return list(associations)
