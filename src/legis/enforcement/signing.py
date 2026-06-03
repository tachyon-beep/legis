"""Keyed tamper-evidence for protected-cell verdicts.

The Sprint 0 hash chain detects edits by an actor who *cannot* recompute it; an
actor with DB-file access can re-chain a forged record. The HMAC closes that:
without the key, a forged record cannot carry a valid signature. Versioned
(`v2` pins the expanded audit field set and canonical-JSON v1) so future
canonicalisation or field-set upgrades can be introduced without ambiguity.
"""

from __future__ import annotations

import hashlib
import hmac

from legis.canonical import canonical_json

SIG_PREFIX_V1 = "hmac-sha256:v1:"
SIG_PREFIX_V2 = "hmac-sha256:v2:"
SIG_PREFIX = SIG_PREFIX_V2


def _prefix_for(version: str) -> str:
    if version == "v1":
        return SIG_PREFIX_V1
    if version == "v2":
        return SIG_PREFIX_V2
    raise ValueError(f"unsupported signature version: {version}")


def _signed(fields: dict, key: bytes, prefix: str) -> str:
    mac = hmac.new(
        key, canonical_json(fields).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{prefix}{mac}"


def sign(fields: dict, key: bytes, *, version: str = "v2") -> str:
    return _signed(fields, key, _prefix_for(version))


def verify(fields: dict, signature: str, key: bytes) -> bool:
    if signature.startswith(SIG_PREFIX_V2):
        return hmac.compare_digest(_signed(fields, key, SIG_PREFIX_V2), signature)
    if signature.startswith(SIG_PREFIX_V1):
        return hmac.compare_digest(_signed(fields, key, SIG_PREFIX_V1), signature)
    else:
        return False
