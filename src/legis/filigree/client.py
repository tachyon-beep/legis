"""Filigree entity-association client — legis binds governance to issues.

Same transport posture as ``identity/clarion_client.py``: stdlib ``urllib`` with
an injectable ``fetch`` so tests run offline; no new dependency. legis binds the
opaque SEI as ``entity_id`` (Filigree never parses it) and hands the entity's
content hash for Filigree to store verbatim; drift comparison stays legis's job.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, "dict | None"], dict]


class FiligreeError(RuntimeError):
    """A Filigree call failed at the transport or decode layer."""


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
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError) as exc:
        raise FiligreeError(f"{method} {url} failed: {exc}") from exc


class HttpFiligreeClient:
    def __init__(self, base_url: str, *, fetch: Fetch | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._fetch = fetch or _urllib_fetch

    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str) -> dict[str, Any]:
        quoted_issue_id = urllib.parse.quote(issue_id, safe="")
        return self._fetch(
            "POST", f"{self._base}/api/issue/{quoted_issue_id}/entity-associations",
            {"entity_id": entity_id, "content_hash": content_hash, "actor": actor},
        )

    def associations_for_entity(self, entity_id: str) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"entity_id": entity_id})
        body = self._fetch("GET", f"{self._base}/api/entity-associations?{q}", None)
        return list(body.get("associations", []))
