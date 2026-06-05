"""Current-source binding checks for protected governance submissions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from legis.service.errors import InvalidArgumentError


def _source_path_from_entity(entity: str) -> str | None:
    locator = entity.strip()
    if not locator:
        return None
    candidate = locator.split(":", 1)[0]
    if not candidate.endswith(".py"):
        return None
    if Path(candidate).is_absolute():
        raise InvalidArgumentError("source path must be relative to the configured source root")
    return candidate


def _relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise InvalidArgumentError("source path escapes configured source root") from exc


def verify_current_source_binding(
    *,
    entity: str,
    file_fingerprint: str,
    source_root: str | Path | None,
) -> dict[str, Any]:
    """Return signed provenance, rejecting stale hashes when source is available.

    Entity keys remain opaque to downstream consumers, but protected override
    requests currently use locators such as ``src/x.py:f``. When such a Python
    source locator resolves to an existing file under ``source_root``, the caller
    supplied fingerprint must match the current bytes exactly before the judge is
    consulted or an HMAC signature is produced.
    """
    source_path = _source_path_from_entity(entity)
    if source_path is None:
        return {
            "status": "unverified",
            "reason": "entity is not a Python source locator",
        }
    if source_root is None:
        return {
            "status": "unverified",
            "reason": "source root not configured",
            "source_path": source_path,
        }

    root = Path(source_root).resolve()
    candidate = (root / source_path).resolve()
    rel = _relative_to_root(candidate, root)
    if not candidate.exists():
        return {
            "status": "unverified",
            "reason": "source file not found",
            "source_path": rel,
        }
    if not candidate.is_file():
        raise InvalidArgumentError("source path is not a regular file")

    current_fingerprint = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
    if file_fingerprint != current_fingerprint:
        raise InvalidArgumentError(
            f"fingerprint does not match current source for {rel}"
        )
    return {
        "status": "verified",
        "source_path": rel,
        "current_fingerprint": current_fingerprint,
    }


def require_verified_source_binding(entity: str, source_binding: dict[str, Any]) -> None:
    """Fail closed when a *source-path* protected entity was not verified.

    Q-M1 contract: ``protected`` (HMAC-signed) does NOT mean ``source
    verified``. A Python source-PATH locator (``src/x.py:f``) is fail-closed —
    a missing file, an unconfigured root, or a stale fingerprint is rejected
    (a mismatched fingerprint is rejected by ``verify_current_source_binding``
    before this is even reached). A non-path entity (a ``python:function:...``
    qualname, an opaque SEI, a ``service:`` target) has no local bytes to bind
    against, so it records an HONEST ``unverified`` binding rather than being
    rejected — the qualname/SEI protected tier is a first-class feature.

    Crucially this is not a write-side downgrade hole: dropping the ``.py`` to
    skip this check yields a DIFFERENT ``entity_key`` and the
    ``source_binding_status`` is folded into the signed HMAC fields
    (``binding_signing_fields``), so a consumer can always tell a verified
    record from an unverified one. The standing requirement is read-side:
    consumers MUST read the signed ``source_binding_status`` and never treat
    "protected" as "source verified".
    """
    if _source_path_from_entity(entity) is None:
        return
    if source_binding.get("status") == "verified":
        return
    reason = source_binding.get("reason") or "unknown reason"
    raise InvalidArgumentError(f"source binding could not be verified: {reason}")
