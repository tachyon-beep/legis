"""Canonical JSON + content hashing (leaf module — no legis imports).

v1 uses sorted-key, tight-separator JSON for deterministic hashing. RFC 8785 is
a future hardening (elspeth uses RFC 8785); legis should converge there before
the protected cell ships cryptographic guarantees (see ADR-0001 / ADR-0002).

Q-L4 deferral (assessed 2026-06-06): RFC-8785 is gated on "when cross-language
verification is needed." No current consumer verifies a legis hash from a
non-Python runtime — every hash is produced and checked in-process, and
``content_hash`` always derives bytes via ``.encode("utf-8")``, so the
``ensure_ascii=False`` byte output is deterministic for legis's single-language
use today. Because this is the single canonicalization choke point, the RFC-8785
upgrade stays a one-file change for the day a cross-language verifier lands. The
companion Q-L5 fingerprint reconciliation (decorator.py / boundary_scan.py) is
independent and is done — those fingerprints are Python ``ast.dump`` output, not
cross-language JSON, so RFC-8785 does not apply to them.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
