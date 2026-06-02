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
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, "dict | None"], dict]


class ClarionError(RuntimeError):
    """A Clarion identity call failed at the transport or decode layer."""


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
        with urllib.request.urlopen(req) as resp:  # noqa: S310 (trusted Clarion URL)
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise ClarionError(f"{method} {url} failed: {exc}") from exc


class HttpClarionIdentity:
    def __init__(self, base_url: str, *, fetch: Fetch | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._fetch = fetch or _urllib_fetch

    def capability(self) -> bool:
        body = self._fetch("GET", f"{self._base}/api/v1/_capabilities", None)
        sei = body.get("sei") if isinstance(body, dict) else None
        return isinstance(sei, dict) and sei.get("supported") is True

    def resolve_locator(self, locator: str) -> dict[str, Any]:
        return self._fetch(
            "POST", f"{self._base}/api/v1/identity/resolve", {"locator": locator}
        )

    def resolve_sei(self, sei: str) -> dict[str, Any]:
        return self._fetch("GET", f"{self._base}/api/v1/identity/sei/{sei}", None)

    def lineage(self, sei: str) -> list[dict[str, Any]]:
        body = self._fetch("GET", f"{self._base}/api/v1/identity/lineage/{sei}", None)
        return list(body.get("lineage", []))
