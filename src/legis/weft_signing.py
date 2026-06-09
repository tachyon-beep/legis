"""Shared Weft-component transport-HMAC seam.

The Loomweave SEI client (``identity/loomweave_client.py``) and the Filigree
association client (``filigree/client.py``) authenticate their requests to a
sibling Weft component with the *same* wire scheme: an
``X-Weft-Component: <name>:<hmac>`` header alongside ``X-Weft-Timestamp`` and
``X-Weft-Nonce``, where the HMAC is computed over
``METHOD\\npath?query\\nsha256(body)\\ntimestamp\\nnonce``. This module is the
single definition of that scheme so the two channels cannot silently diverge —
a change to the canonicalization or header shape now happens in one place.

Canonicalization contract: the signed body bytes are
``json.dumps(body, sort_keys=True, separators=(",", ":"))`` with the default
``ensure_ascii=True``. This is deliberately **NOT** ``canonical.canonical_json``,
whose ``ensure_ascii=False`` is the byte-for-byte HMAC contract shared with
Wardline; routing a transport body through it would change every signed
request's bytes. The wire transport MUST send exactly ``weft_body_bytes(body)``
and a verifier MUST recanonicalize identically before hashing.

Verification posture (G11, weft-c7e3486246) — stated plainly so the emitted
headers are never mistaken for an enforced control: legis EMITS these
``X-Weft-*`` headers on every signed Filigree/Loomweave request, but the current
Filigree *classic* route does NOT verify them — it stores the app-level
``binding_signature`` verbatim and ignores the transport HMAC (issue
legis-d5783eacff). So today the bind is **transport-open**: integrity rests on
the loopback transport and on legis's own ``BindingLedger`` (the authoritative,
locally-verifiable record), NOT on a sibling checking this signature. The headers
are kept deliberately — the scheme is shared with the Loomweave channel, the HMAC
is cheap, and the emit is *forward-compatible*: the moment a verifier checks them
they become live with no producer change. Whether to verify, or to formally
declare the route transport-open and stop emitting, is **Filigree's decision to
make** (it owns the verifying end); legis emits honestly-labelled rather than
ripping out a cross-component contract unilaterally. The live evidence behind this
posture is asserted in ``tests/governance/test_signoff_binding_real_filigree.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.parse


def weft_body_bytes(body: dict | None) -> bytes:
    """Serialize a request body to the exact bytes the signature commits to."""
    if body is None:
        return b""
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def weft_path_and_query(url: str) -> str:
    """The path (plus query, if any) the signed message commits to."""
    parsed = urllib.parse.urlsplit(url)
    path_and_query = parsed.path or "/"
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"
    return path_and_query


def sign_weft_request(
    component: str,
    key: bytes,
    method: str,
    url: str,
    body: dict | None,
    *,
    timestamp: int,
    nonce: str,
) -> dict[str, str]:
    """Return the Weft-component HMAC request headers for ``component``.

    ``timestamp`` and ``nonce`` are injected (not generated here) so the
    signature is deterministically testable.
    """
    body_hash = hashlib.sha256(weft_body_bytes(body)).hexdigest()
    message = (
        f"{method}\n{weft_path_and_query(url)}\n{body_hash}\n{timestamp}\n{nonce}"
    ).encode("utf-8")
    signature = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-Weft-Component": f"{component}:{signature}",
        "X-Weft-Timestamp": str(timestamp),
        "X-Weft-Nonce": nonce,
    }


def weft_hmac_key_from_env(component_env_var: str) -> bytes | None:
    """Resolve a channel HMAC key without making it mandatory.

    The channel-specific variable (e.g. ``LEGIS_LOOMWEAVE_HMAC_KEY``) wins; an
    absent channel key falls back to the shared ``LEGIS_HMAC_KEY``; absent both,
    the channel is unsigned (backward compatible with deployments that have not
    provisioned a key yet).
    """
    value = os.environ.get(component_env_var) or os.environ.get("LEGIS_HMAC_KEY")
    return value.encode("utf-8") if value else None
