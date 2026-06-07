import json

import pytest

from legis.canonical import canonical_json, content_hash
from legis.wardline.ingest import (
    TRUST_TIERS,
    ArtifactStatus,
    ScanOutcome,
    Suppressed,
    WardlineFinding,
    WardlinePayloadError,
    WardlineSeverity,
    active_defects,
)


def test_str_enum_axes_are_byte_identical_to_bare_strings_on_the_wire():
    # The load-bearing compat contract: a str,Enum serializes EXACTLY like its
    # bare string through json.dumps and canonical_json (so wire payloads and the
    # content-hashed audit chain are unchanged). Pin it directly so a future
    # Python/enum change that alters str,Enum serialization fails here loudly,
    # not silently downstream.
    cases = [
        (ScanOutcome.ROUTED, "ROUTED"),
        (ScanOutcome.SKIPPED_DIRTY_TREE, "SKIPPED_DIRTY_TREE"),
        (ArtifactStatus.VERIFIED, "verified"),
        (ArtifactStatus.DIRTY, "dirty"),
        (ArtifactStatus.UNVERIFIED, "unverified"),
        (Suppressed.ACTIVE, "active"),
        (Suppressed.WAIVED, "waived"),
        (Suppressed.SUPPRESSED, "suppressed"),
        (Suppressed.BASELINED, "baselined"),
        (Suppressed.JUDGED, "judged"),
    ]
    for member, raw in cases:
        assert member == raw
        assert json.dumps({"k": member}) == json.dumps({"k": raw})
        assert canonical_json({"k": member}) == canonical_json({"k": raw})
        assert content_hash({"k": member}) == content_hash({"k": raw})
    # The back-compat alias and the error's reason still equal the bare string
    # that callers/boundaries imported and serialized before the enum existed
    # (both are bound by the module-level import block below).
    assert SKIPPED_DIRTY_TREE == "SKIPPED_DIRTY_TREE"
    assert WardlineDirtyTreeError.reason == "SKIPPED_DIRTY_TREE"


def _finding(**over):
    base = {"rule_id": "PY-WL-101", "message": "m", "severity": "ERROR",
            "kind": "defect", "fingerprint": "fp1", "qualname": "m.f",
            "properties": {"actual_return": "UNKNOWN_RAW", "declared_return": "ASSURED"},
            "suppressed": "active"}
    base.update(over)
    return base


def test_from_wire_carries_trust_properties_verbatim():
    f = WardlineFinding.from_wire(_finding())
    assert f.rule_id == "PY-WL-101"
    assert f.severity is WardlineSeverity.ERROR
    assert f.properties["actual_return"] == "UNKNOWN_RAW"  # tier carried verbatim
    assert f.fingerprint == "fp1"


def test_active_defects_excludes_suppressed_and_non_defects():
    scan = {"findings": [
        _finding(fingerprint="a"),                              # active defect → in
        _finding(
            fingerprint="b",
            suppressed="waived",
            properties={
                "actual_return": "UNKNOWN_RAW",
                "declared_return": "ASSURED",
                "suppression_proof": "ISSUE-1",
            },
        ),                                                      # proved waiver → out
        _finding(fingerprint="c", kind="metric", severity="NONE"),  # not a defect → out
    ]}
    got = [f.fingerprint for f in active_defects(scan)]
    assert got == ["a"]


def test_severity_is_ordered_critical_highest():
    assert WardlineSeverity.CRITICAL.rank > WardlineSeverity.ERROR.rank
    assert WardlineSeverity.ERROR.rank > WardlineSeverity.WARN.rank
    assert WardlineSeverity.WARN.rank > WardlineSeverity.INFO.rank


def test_trust_tiers_is_the_shared_vocabulary():
    # Wardline's tiers, carried as the one suite vocabulary (no tier1/2/3).
    assert {"INTEGRAL", "ASSURED", "GUARDED", "EXTERNAL_RAW"} <= TRUST_TIERS


def test_diagnostic_properties_are_carried_verbatim_not_rejected():
    # Real Wardline findings stash diagnostics (sink, callee, markers) in
    # properties alongside trust tiers. legis carries properties verbatim as
    # evidence and never acts on the values, so a non-tier diagnostic must be
    # accepted, not rejected as an "invalid trust tier".
    scan = {"findings": [_finding(
        fingerprint="a",
        properties={"sink": "os.system", "actual_return": "UNKNOWN_RAW"},
    )]}
    got = active_defects(scan)
    assert [f.fingerprint for f in got] == ["a"]
    assert got[0].properties["sink"] == "os.system"            # carried verbatim
    assert got[0].properties["actual_return"] == "UNKNOWN_RAW"


def test_baselined_and_judged_defects_are_non_active_without_proof():
    # baselined/judged are non-agent-initiated suppression states: not in the
    # active gate population, and (unlike an agent waiver) they carry no proof.
    scan = {"findings": [
        _finding(fingerprint="a"),                              # active → in
        _finding(fingerprint="b", suppressed="baselined"),      # non-active → out
        _finding(fingerprint="c", suppressed="judged"),         # non-active → out
    ]}
    assert [f.fingerprint for f in active_defects(scan)] == ["a"]


def test_waived_defect_accepts_top_level_suppression_proof():
    # Wardline keeps suppression_reason at the finding's top level, not inside
    # properties; legis must accept proof in either location.
    scan = {"findings": [_finding(
        fingerprint="b",
        suppressed="waived",
        suppression_reason="ISSUE-9",
        properties={"actual_return": "UNKNOWN_RAW"},            # no proof key here
    )]}
    assert active_defects(scan) == []                           # accepted + excluded


def test_waived_defect_without_any_proof_is_still_rejected():
    # The proof control is preserved: an agent waiver with no proof anywhere
    # (neither top-level nor in properties) is rejected.
    scan = {"findings": [_finding(
        fingerprint="b",
        suppressed="waived",
        properties={"actual_return": "UNKNOWN_RAW"},
    )]}
    with pytest.raises(WardlinePayloadError, match="suppression proof"):
        active_defects(scan)


def test_unknown_suppression_state_is_still_rejected():
    scan = {"findings": [_finding(fingerprint="x", suppressed="haunted")]}
    with pytest.raises(WardlinePayloadError, match="unsupported suppression state"):
        active_defects(scan)


# --- dirty-tree dev artifact (P0 dev path + P1 typed amber SKIPPED_DIRTY_TREE) ---
#
# wardline `scan --format legis --allow-dirty` emits an UNSIGNED dev artifact
# marked `dirty: true` (signing stays clean-tree-only). legis must:
#   - keyless dev: govern it, but record the dirty marker honestly;
#   - CI posture (key configured): NOT conflate "dirty dev tree" with a
#     tampered/malformed payload (a generic red). Default to a typed amber
#     SKIPPED_DIRTY_TREE; govern unsigned only under an explicit dev-mode opt-in.
# The relaxation is scoped to exactly `dirty is True AND signature absent` — a
# signed (or clean) payload still verifies normally, so a real tamper stays red.

from legis.enforcement.signing import sign  # noqa: E402
from legis.wardline.ingest import (  # noqa: E402
    SKIPPED_DIRTY_TREE,
    WardlineDirtyTreeError,
    verify_wardline_artifact,
    wardline_artifact_fields,
)

_KEY = b"wardline-artifact-key"


def _artifact(*, dirty=None, signed=False, key=_KEY, **over):
    scan = {
        "scanner_identity": "wardline@1.0.0rc1",
        "rule_set_version": "rules@abc123",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "findings": [],
    }
    if dirty is not None:
        scan["dirty"] = dirty
    scan.update(over)
    if signed:
        scan["artifact_signature"] = sign(wardline_artifact_fields(scan), key)
    return scan


def test_dirty_error_is_not_a_generic_payload_error():
    # The amber skip must be DISTINGUISHABLE from the generic red at the
    # boundary — so it is not a WardlinePayloadError (which maps to 422 /
    # INVALID_ARGUMENT). It carries a typed reason instead.
    assert not issubclass(WardlineDirtyTreeError, WardlinePayloadError)
    assert WardlineDirtyTreeError.reason == SKIPPED_DIRTY_TREE


def test_keyless_dirty_artifact_governs_with_honest_dirty_status():
    # Keyless local dev is already permissive; the only change is that the
    # dirty marker is recorded honestly so a dirty dev scan is distinguishable
    # from a clean unsigned one.
    prov = verify_wardline_artifact(_artifact(dirty=True), None)
    assert prov["artifact_status"] == "dirty"
    assert prov["commit_sha"] == "a" * 40


def test_keyless_clean_unsigned_artifact_stays_unverified():
    prov = verify_wardline_artifact(_artifact(), None)
    assert prov["artifact_status"] == "unverified"


def test_ci_dirty_without_devmode_is_typed_amber_skip_not_red():
    # P1: key configured, dirty + unsigned, dev-mode OFF -> typed amber skip,
    # NOT a generic WardlinePayloadError red.
    with pytest.raises(WardlineDirtyTreeError) as exc:
        verify_wardline_artifact(_artifact(dirty=True), _KEY, allow_dirty=False)
    assert exc.value.reason == SKIPPED_DIRTY_TREE


def test_dirty_skip_payload_is_structured_and_actionable():
    # N4 (weft-a7a92a40dd) / C-10(d): the skip must not be a prose-only blob.
    # to_payload() is the single source both transports serialize, so the MCP
    # structuredContent and the HTTP body cannot drift.
    with pytest.raises(WardlineDirtyTreeError) as exc:
        verify_wardline_artifact(_artifact(dirty=True), _KEY, allow_dirty=False)
    payload = exc.value.to_payload()
    assert payload["outcome"] == "SKIPPED_DIRTY_TREE"
    assert payload["reason"] == "SKIPPED_DIRTY_TREE"
    assert payload["routed"] == []
    assert payload["posture"] == "ci_artifact_key_configured"
    assert payload["cause"] == "dirty_unsigned_artifact"
    remediation = payload["remediation"]
    assert isinstance(remediation, list) and remediation
    joined = " ".join(remediation)
    # Names BOTH the clean-tree path and the operator opt-in (out-of-band).
    assert "commit" in joined.lower()
    assert "LEGIS_WARDLINE_ALLOW_DIRTY" in joined
    # The instance still resolves reason as the bare-string ScanOutcome, and the
    # class attribute access used by existing tests/boundaries keeps working.
    assert exc.value.reason == SKIPPED_DIRTY_TREE
    assert WardlineDirtyTreeError.reason == SKIPPED_DIRTY_TREE


def test_ci_dirty_with_devmode_governs_unsigned_as_dirty():
    # P0: key configured, dirty + unsigned, dev-mode ON -> govern unsigned,
    # recorded honestly as dirty (never "verified").
    prov = verify_wardline_artifact(_artifact(dirty=True), _KEY, allow_dirty=True)
    assert prov["artifact_status"] == "dirty"
    assert "artifact_signature" not in prov
    assert prov["scanner_identity"] == "wardline@1.0.0rc1"


def test_devmode_does_not_relax_a_tampered_signature():
    # Security row: dirty + a PRESENT-but-invalid signature is tampering, not a
    # dev tree. Relaxation is scoped to UNSIGNED only, so this stays red even
    # with dev-mode on.
    scan = _artifact(dirty=True)
    scan["artifact_signature"] = "hmac-sha256:v2:" + "0" * 64  # forged
    with pytest.raises(WardlinePayloadError, match="does not verify"):
        verify_wardline_artifact(scan, _KEY, allow_dirty=True)


def test_devmode_does_not_relax_a_clean_unsigned_artifact():
    # Security row: dev-mode relaxes ONLY dirty+unsigned, never "any unsigned".
    # A clean (dirty absent/false) unsigned artifact still requires a signature.
    with pytest.raises(WardlinePayloadError, match="signature is required"):
        verify_wardline_artifact(_artifact(dirty=False), _KEY, allow_dirty=True)
    with pytest.raises(WardlinePayloadError, match="signature is required"):
        verify_wardline_artifact(_artifact(), _KEY, allow_dirty=True)


def test_dirty_marker_must_be_strict_boolean_true():
    # The scan dict is attacker-controlled. A truthy non-True dirty value
    # (string "true", 1) must NOT trip the dev relaxation — it falls through to
    # normal verification (red when a key is configured and it is unsigned).
    for bogus in ("true", "True", 1, [1]):
        with pytest.raises(WardlinePayloadError, match="signature is required"):
            verify_wardline_artifact(_artifact(dirty=bogus), _KEY, allow_dirty=True)


def test_signed_dirty_artifact_verifies_normally():
    # A validly-signed payload that also carries dirty:true is trusted via its
    # signature (only the key-holder can produce it); the dirty marker does not
    # hijack the signed path into a skip.
    scan = _artifact(dirty=True, signed=True)
    prov = verify_wardline_artifact(scan, _KEY, allow_dirty=False)
    assert prov["artifact_status"] == "verified"


def test_ci_posture_missing_provenance_field_is_red():
    # Key configured, clean (not dirty), but a required provenance field is
    # absent -> generic red BEFORE signature verification is even attempted. This
    # is the non-dirty CI branch that demands signed scanner/rule-set/commit/tree
    # provenance; a scan missing any of them is malformed, not an amber skip.
    scan = _artifact()  # all four provenance fields present, unsigned
    del scan["tree_sha"]
    with pytest.raises(WardlinePayloadError, match="missing required field"):
        verify_wardline_artifact(scan, _KEY)
