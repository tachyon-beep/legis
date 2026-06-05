"""Filigree entity-association client — legis binds governance to issues.

Same transport posture as ``identity/loomweave_client.py``: stdlib ``urllib`` with
an injectable ``fetch`` so tests run offline; no new dependency. legis binds the
opaque SEI as ``entity_id`` (Filigree never parses it) and hands the entity's
content hash for Filigree to store verbatim; drift comparison stays legis's job.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import ipaddress
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Protocol, runtime_checkable

Fetch = Callable[[str, str, "dict | None"], dict]


class FiligreeError(RuntimeError):
    """A Filigree call failed at the transport or decode layer."""


MAX_RESPONSE_BYTES = 1_000_000


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


def sign_filigree_request(
    key: bytes,
    method: str,
    url: str,
    body: dict | None,
    *,
    timestamp: int,
    nonce: str,
) -> dict[str, str]:
    """Weft-component HMAC headers for a legis->Filigree request (Q-M4).

    Mirrors ``identity.loomweave_client.sign_loomweave_request`` so the Filigree
    channel has the same transport authentication the Loomweave channel already
    had. The attach ``signature`` is an app-level attestation about WHAT is
    bound; this proves WHO is calling. ``timestamp`` and ``nonce`` are injected
    (not generated here) so the signature is deterministically testable.

    Canonicalization contract: the body hash is taken over ``_json_body_bytes``
    (sorted keys, compact ``(",", ":")`` separators). The wire transport
    (``_urllib_fetch``) sends those exact bytes, and a Filigree verifier MUST
    canonicalize the received body identically before hashing — any spacing or
    key-ordering drift on either side breaks every signature. See ADR-0003.
    """
    body_hash = hashlib.sha256(_json_body_bytes(body)).hexdigest()
    message = (
        f"{method}\n{_path_and_query(url)}\n{body_hash}\n{timestamp}\n{nonce}"
    ).encode("utf-8")
    signature = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-Weft-Component": f"filigree:{signature}",
        "X-Weft-Timestamp": str(timestamp),
        "X-Weft-Nonce": nonce,
    }


def filigree_hmac_key_from_env() -> bytes | None:
    """Resolve the Filigree HMAC key without making it mandatory.

    Absent key -> unsigned (backward compatible with deployments that have not
    provisioned the channel key yet), mirroring ``loomweave_hmac_key_from_env``.
    """
    value = os.environ.get("LEGIS_FILIGREE_HMAC_KEY") or os.environ.get("LEGIS_HMAC_KEY")
    return value.encode("utf-8") if value else None


@runtime_checkable
class FiligreeClient(Protocol):
    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str, signoff_seq: int | None = None,
               signature: str | None = None) -> dict[str, Any]: ...
    def associations_for_entity(self, entity_id: str) -> list[dict[str, Any]]: ...


def _urllib_fetch(
    method: str, url: str, body: dict | None, headers: dict[str, str] | None = None
) -> dict:
    # Send the SAME canonical bytes that sign_filigree_request hashes
    # (_json_body_bytes: sorted keys, compact separators). The Weft signature
    # commits to that body hash, so a verifier checking the hash against the
    # actual request bytes only matches if the wire body is byte-identical to
    # the signed body (Q-M4). Default json.dumps spacing/ordering would diverge
    # and every signed POST would fail verification. Mirrors loomweave_client.
    data = _json_body_bytes(body) if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
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
    def __init__(
        self,
        base_url: str,
        *,
        fetch: Fetch | None = None,
        hmac_key: bytes | None = None,
    ) -> None:
        self._base = _validate_base_url(base_url)
        # An injected fetch (tests) is used verbatim and never signs, so resolve
        # the key only when the real signing transport is in play — otherwise an
        # ambient LEGIS_*_HMAC_KEY would be read but never used. Absent key ->
        # unsigned, backward compatible.
        if fetch is not None:
            self._hmac_key = hmac_key
            self._fetch = fetch
        else:
            self._hmac_key = hmac_key if hmac_key is not None else filigree_hmac_key_from_env()
            self._fetch = self._signing_fetch

    def _signing_fetch(self, method: str, url: str, body: dict | None) -> dict:
        headers: dict[str, str] = {}
        if self._hmac_key is not None:
            headers = sign_filigree_request(
                self._hmac_key,
                method,
                url,
                body,
                timestamp=int(time.time()),
                nonce=secrets.token_hex(16),
            )
        return _urllib_fetch(method, url, body, headers)

    def attach(self, issue_id: str, entity_id: str, content_hash: str,
               *, actor: str, signoff_seq: int | None = None,
               signature: str | None = None) -> dict[str, Any]:
        quoted_issue_id = urllib.parse.quote(issue_id, safe="")
        body: dict[str, Any] = {
            "entity_id": entity_id,
            "content_hash": content_hash,
            "actor": actor,
        }
        if signoff_seq is not None:
            body["signoff_seq"] = signoff_seq
        if signature is not None:
            body["signature"] = signature
        return _require_dict(
            self._fetch(
                "POST", f"{self._base}/api/issue/{quoted_issue_id}/entity-associations",
                body,
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
