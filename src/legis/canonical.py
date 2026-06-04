"""Canonical JSON + content hashing (leaf module — no legis imports).

v1 uses sorted-key, tight-separator JSON for deterministic hashing. RFC 8785 is
a future hardening (elspeth uses RFC 8785); legis should converge there before
the protected cell ships cryptographic guarantees (see ADR-0001).
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
