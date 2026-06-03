"""Ingest a Wardline scan result — select the gate population, carry the tiers.

legis does not call Wardline (Wardline has no HTTP); the agent hands legis the
MCP scan response. legis never re-analyzes — it reads findings and governs. The
trust tiers are Wardline's, carried verbatim as the one suite vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from legis.enforcement.signing import verify

# The shared trust vocabulary (Wardline taints.py) — carried, never re-derived.
TRUST_TIERS: frozenset[str] = frozenset({
    "INTEGRAL", "ASSURED", "GUARDED", "EXTERNAL_RAW",
    "UNKNOWN_RAW", "UNKNOWN_GUARDED", "UNKNOWN_ASSURED", "MIXED_RAW",
})
SUPPRESSION_PROOF_KEYS: frozenset[str] = frozenset({
    "suppression_proof",
    "suppression_ticket",
    "suppression_reason",
})
MAX_FINDINGS = 500
ARTIFACT_SIGNATURE_FIELD = "artifact_signature"
ARTIFACT_PROVENANCE_FIELDS: tuple[str, ...] = (
    "scanner_identity",
    "rule_set_version",
    "commit_sha",
    "tree_sha",
)


class WardlineSeverity(str, Enum):
    rank: int

    CRITICAL = ("CRITICAL", 4)
    ERROR = ("ERROR", 3)
    WARN = ("WARN", 2)
    INFO = ("INFO", 1)
    NONE = ("NONE", 0)

    def __new__(cls, value: str, rank: int) -> "WardlineSeverity":
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.rank = rank
        return obj


class WardlinePayloadError(ValueError):
    """A Wardline scan payload is not shaped like the trusted wire contract."""


def wardline_artifact_fields(scan: Mapping[str, Any]) -> dict[str, Any]:
    """The Wardline artifact payload covered by ``artifact_signature``."""
    if not isinstance(scan, Mapping):
        raise WardlinePayloadError("scan must be an object")
    return {
        str(key): value
        for key, value in scan.items()
        if key != ARTIFACT_SIGNATURE_FIELD
    }


def verify_wardline_artifact(
    scan: Mapping[str, Any],
    artifact_key: bytes | None,
) -> dict[str, Any]:
    """Validate optional server-required artifact authentication.

    When ``artifact_key`` is configured, the scan must carry signed scanner,
    rule-set, commit, and tree provenance. Without a configured key we still
    record any supplied metadata, but mark it explicitly unverified.
    """
    fields = wardline_artifact_fields(scan)
    provenance = {
        "artifact_status": "unverified",
    }
    for key in ARTIFACT_PROVENANCE_FIELDS:
        value = scan.get(key)
        if isinstance(value, str) and value:
            provenance[key] = value

    if artifact_key is None:
        return provenance

    missing = [
        key for key in ARTIFACT_PROVENANCE_FIELDS
        if not isinstance(scan.get(key), str) or not scan[key]
    ]
    if missing:
        raise WardlinePayloadError(
            f"Wardline artifact missing required field(s): {', '.join(missing)}"
        )

    signature = scan.get(ARTIFACT_SIGNATURE_FIELD)
    if not isinstance(signature, str) or not signature:
        raise WardlinePayloadError("Wardline artifact signature is required")
    if not verify(fields, signature, artifact_key):
        raise WardlinePayloadError("Wardline artifact signature does not verify")
    return {
        "artifact_status": "verified",
        **{key: scan[key] for key in ARTIFACT_PROVENANCE_FIELDS},
        "artifact_signature": signature,
    }


@dataclass(frozen=True)
class WardlineFinding:
    rule_id: str
    message: str
    severity: WardlineSeverity
    kind: str
    fingerprint: str
    qualname: str | None
    properties: Mapping[str, Any]
    suppressed: str

    @classmethod
    def from_wire(cls, d: Mapping[str, Any]) -> "WardlineFinding":
        missing = [
            key
            for key in ("rule_id", "message", "severity", "kind", "fingerprint")
            if key not in d
        ]
        if missing:
            raise WardlinePayloadError(
                f"finding missing required field(s): {', '.join(missing)}"
            )
        severity_raw = d["severity"]
        if not isinstance(severity_raw, str):
            raise WardlinePayloadError("finding severity must be a string")
        try:
            severity = WardlineSeverity[severity_raw]
        except KeyError as exc:
            raise WardlinePayloadError(f"unknown Wardline severity: {severity_raw}") from exc
        properties = d.get("properties", {})
        if not isinstance(properties, Mapping):
            raise WardlinePayloadError("finding properties must be an object")
        _validate_trust_properties(properties)
        qualname = d.get("qualname")
        if qualname is not None and not isinstance(qualname, str):
            raise WardlinePayloadError("finding qualname must be a string or null")
        suppressed = d.get("suppressed", "active")
        if not isinstance(suppressed, str):
            raise WardlinePayloadError("finding suppressed must be a string")
        for key in ("rule_id", "message", "kind", "fingerprint"):
            if not isinstance(d[key], str) or not d[key]:
                raise WardlinePayloadError(f"finding {key} must be a non-empty string")
        return cls(
            rule_id=d["rule_id"],
            message=d["message"],
            severity=severity,
            kind=d["kind"],
            fingerprint=d["fingerprint"],
            qualname=qualname,
            properties=dict(properties),
            suppressed=suppressed,
        )


def _validate_trust_properties(properties: Mapping[str, Any]) -> None:
    for key, value in properties.items():
        if key in SUPPRESSION_PROOF_KEYS:
            if not isinstance(value, str) or not value.strip():
                raise WardlinePayloadError(
                    f"finding {key} must be a non-empty suppression proof string"
                )
            continue
        if not isinstance(value, str) or value not in TRUST_TIERS:
            raise WardlinePayloadError(
                f"finding property {key} has invalid trust tier: {value!r}"
            )


def _has_suppression_proof(properties: Mapping[str, Any]) -> bool:
    return any(
        isinstance(properties.get(key), str) and bool(properties[key].strip())
        for key in SUPPRESSION_PROOF_KEYS
    )


def active_defects(scan: Mapping[str, Any]) -> list[WardlineFinding]:
    """The gate population: active (non-suppressed) DEFECT findings."""
    if not isinstance(scan, Mapping):
        raise WardlinePayloadError("scan must be an object")
    raw_findings = scan.get("findings", [])
    if not isinstance(raw_findings, list):
        raise WardlinePayloadError("scan findings must be a list")
    if len(raw_findings) > MAX_FINDINGS:
        raise WardlinePayloadError(f"scan findings exceeds maximum batch size {MAX_FINDINGS}")
    out: list[WardlineFinding] = []
    for raw in raw_findings:
        if not isinstance(raw, Mapping):
            raise WardlinePayloadError("each finding must be an object")
        f = WardlineFinding.from_wire(raw)
        if f.kind != "defect":
            continue
        if f.suppressed == "active":
            out.append(f)
            continue
        if f.suppressed in {"waived", "suppressed"}:
            if not _has_suppression_proof(f.properties):
                raise WardlinePayloadError(
                    "suppressed defect must carry suppression proof"
                )
            continue
        raise WardlinePayloadError(
            f"unsupported suppression state for defect: {f.suppressed}"
        )
    return out
