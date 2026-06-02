"""Keyed tamper-evidence for protected-cell verdicts.

The Sprint 0 hash chain detects edits by an actor who *cannot* recompute it; an
actor with DB-file access can re-chain a forged record. The HMAC closes that:
without the key, a forged record cannot carry a valid signature. Versioned
(`v1` pins canonical-JSON v1) so an RFC-8785 upgrade is a clean `v2`.
"""

from __future__ import annotations

import hashlib
import hmac

from legis.canonical import canonical_json

SIG_PREFIX = "hmac-sha256:v1:"


def sign(fields: dict, key: bytes) -> str:
    mac = hmac.new(
        key, canonical_json(fields).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{SIG_PREFIX}{mac}"


def verify(fields: dict, signature: str, key: bytes) -> bool:
    if not signature.startswith(SIG_PREFIX):
        return False
    return hmac.compare_digest(sign(fields, key), signature)
