# Legis Sprint 3 — Complex tier (structured → protected) Implementation Plan

> **Status:** ✅ implemented 2026-06-02 — all tasks complete, 86 tests green. The 2×2 is now whole.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans / TDD to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Complete the 2×2 with its right-hand column — **structured** (complex + judge OFF: human sign-off, no LLM in the path) and **protected** (complex + judge ON: tamper-bound HMAC verdicts, operator-override, decay sweep, override-rate gate).

**Architecture:** Four focused modules over the Sprint 0 append-only store and the Sprint 2 judge:
- `enforcement/signing.py` — leaf HMAC sign/verify (`hmac-sha256:v1:<hex>`).
- `enforcement/signoff.py` — `SignoffGate` (block + escalate). An optional `signer` unifies structured (procedural, unsigned) and protected (tamper-bound) sign-off.
- `enforcement/protected.py` — `ProtectedGate` (judge + binding + signature + operator-override) and `TrailVerifier` (load-time signature check).
- `enforcement/lifecycle.py` — `decay_sweep` (re-judge kept suppressions) + `evaluate_override_rate` (CI gate).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, SQLite, stdlib `hmac`/`hashlib`, pytest (warnings-as-errors).

---

## Locked design decisions (advisor-reviewed — do not reopen)

1. **Protected-class designation comes from policy config, NOT the record.** `TrailVerifier(key, protected_policies)`: for any record whose `policy ∈ protected_policies`, a valid signature is *mandatory* — a **missing** signature on a protected policy is tampering (a `TamperError`), not "unsigned, skip." This defeats the signature-stripping downgrade attack (strip sig + flip an in-record "protected" bit → silent downgrade). The protected-policy set is reviewed policy (ADR-0002), reachable only from config.
2. **Verification is on the read path consumers use.** `GET /overrides` runs the verifier (when the app is wired with a key + protected set) before returning. A `TamperError` is surfaced as an honest HTTP 500 audit-integrity failure — fail-closed, never skip-and-continue.
3. **The HMAC earns its place vs. Sprint 0's chain.** Sprint 0's `verify_integrity()` is an *unkeyed* SHA-256 chain: an attacker with DB-file access edits a record, recomputes its `content_hash`/`chain_hash`, and re-chains all successors → `verify_integrity()==True`. The keyed HMAC is what they cannot forge. The discriminating test (Task 7) tampers a protected record, **fully re-chains the log so `verify_integrity()==True`**, and asserts `TrailVerifier` still catches it via HMAC mismatch.
4. **Sign `policy` + `entity_key` in addition to the roadmap's six fields** (`verdict, model, timestamp, rationale, fingerprint, ast_path`). The roadmap list omits entity/policy; since consumers key on `entity_key`, an unsigned entity lets a valid signed verdict be *transplanted* onto a different entity. Binding entity+policy closes that. Deliberate extension; the roadmap field list is a sketch, not a wire-format lock.
5. **Decay sweep targets judge-`ACCEPTED` suppressions only.** An `OVERRIDDEN_BY_OPERATOR` record exists *because* the judge blocked it; re-judging re-blocks it tautologically. Operator-overrides are governed by the rate gate instead. `BLOCKED` records are not suppressions and are excluded too.
6. **Override-rate denominator = final-disposition records** (`ACCEPTED` + `OVERRIDDEN_BY_OPERATOR`) in a rolling window; numerator = `OVERRIDDEN_BY_OPERATOR`. Below a min-sample floor → `PASS_WITH_NOTICE` (don't trip mechanically on tiny corpora). Threshold/window/floor are ADR-0002 policy constants read from code — **not** query params or workflow knobs an agent can tune to pass (the literal exit criterion).
7. **HMAC key + signer key are injected, never written to a payload.** A test asserts no stored payload contains the key bytes. Production provisioning (env var, rotation) is ADR-0002 and deferred-to-app-wiring, like Sprint 2's judge seam.
8. **`hmac-sha256:v1`** pins canonical-JSON-v1. RFC 8785 convergence (flagged in `canonical.py`) is a future `:v2`. Versioned prefix makes that a clean migration.

---

## Known limitations (honest disclosure — record in the doc, not hidden)

- **No authz on `operator_id` / signer identity yet.** The structured/protected sign-off trusts the supplied operator id; binding it to an authenticated principal is deferred, consistent with the no-auth-yet posture (same as the agent_id trust in Sprint 2).
- **Judge seam still unfilled** (Open Decision #3). Decay sweep and `ProtectedGate` both consume an injected judge; tests use scripted judges. No concrete `LLMClient` ships.
- **HMAC key provisioning mechanism** (Open Decision #4) is ADR-recorded but the production env-var/rotation wiring is deferred.
- **`sign_off` does not validate that `request_seq` points at a real PENDING request** — it copies whatever record sits at that index (and `IndexError`s if out of range). Consistent with the no-authz posture; harden when sign-off gets a real workflow state machine.
- **`signing_fields` reads `ext["judge_verdict"]` by bracket** — a record tampered to drop `judge_verdict` while keeping a signature raises `KeyError` (→ unhandled 500) rather than a clean `TamperError`. Low-severity hardening for the protected-tier pass; the integrity failure is still surfaced, just not with the tidy message.
- **Mixed-store composition:** when a simple engine *and* a protected gate are wired to different stores, `GET /overrides` reads only the protected store. A composition wrinkle for the future cell-router, not Sprint 3 (today a deployment wires one governance store).

## Post-implementation hardening (advisor-caught, fixed in-sprint)

The override-rate gate originally read the trail **without** signature
verification while `GET /overrides` verified — meaning the enforcement gate with
teeth trusted the store blind. An attacker flipping `OVERRIDDEN_BY_OPERATOR` →
`ACCEPTED` could lower the apparent rate and slip the gate. Fixed: both the human
read path and the rate gate now route through `verified_governance_records()`,
fail-closed (a tampered protected trail yields HTTP 500, never a PASS/FAIL).
Proven by `test_override_rate_gate_fails_closed_on_a_tampered_trail`.

---

## File structure

| File | Responsibility |
|---|---|
| `src/legis/enforcement/verdict.py` | +`Verdict.OVERRIDDEN_BY_OPERATOR`; +`SignoffState` enum |
| `src/legis/enforcement/signing.py` | `sign`/`verify` (HMAC); `SIG_PREFIX` |
| `src/legis/enforcement/protected.py` | `signing_fields` (shared); `ProtectedGate`; `TrailVerifier`; `TamperError` |
| `src/legis/enforcement/signoff.py` | `SignoffGate` (request → sign_off → is_cleared), optional signer |
| `src/legis/enforcement/lifecycle.py` | `decay_sweep`; `evaluate_override_rate`; `GateResult`; `DecayFlag` |
| `src/legis/governance/params.py` | ADR-0002 policy constants (threshold/window/floor) |
| `src/legis/api/app.py` | protected/signoff routes; verified `GET /overrides`; override-rate endpoint |
| `docs/design/adr/0002-complex-tier-governance-parameters.md` | HMAC key + rate-gate policy |
| tests under `tests/enforcement/` and `tests/api/` | one per behaviour |

---

## Task 1: Extend verdict vocabulary

**Files:** Modify `src/legis/enforcement/verdict.py`; Test `tests/enforcement/test_verdict_complex.py`

- [ ] **Step 1 — failing test**

```python
from legis.enforcement.verdict import SignoffState, Verdict


def test_operator_override_is_a_first_class_verdict():
    assert Verdict.OVERRIDDEN_BY_OPERATOR.value == "OVERRIDDEN_BY_OPERATOR"
    assert Verdict.OVERRIDDEN_BY_OPERATOR is not Verdict.ACCEPTED


def test_signoff_states():
    assert SignoffState.PENDING.value == "PENDING_SIGNOFF"
    assert SignoffState.SIGNED_OFF.value == "SIGNED_OFF"
```

- [ ] **Step 2 — run, expect FAIL** (`ImportError: SignoffState`).
- [ ] **Step 3 — implement:** add to `verdict.py`:

```python
class Verdict(str, Enum):
    ACCEPTED = "ACCEPTED"
    BLOCKED = "BLOCKED"
    OVERRIDDEN_BY_OPERATOR = "OVERRIDDEN_BY_OPERATOR"


class SignoffState(str, Enum):
    PENDING = "PENDING_SIGNOFF"
    SIGNED_OFF = "SIGNED_OFF"
```

(Confirm `parse_verdict` still only emits ACCEPTED/BLOCKED — its `[A-Z]+` token split turns `OVERRIDDEN_BY_OPERATOR` into tokens that match neither, so it is never spuriously emitted. Existing parse tests must stay green.)

- [ ] **Step 4 — run, expect PASS**; also run `tests/enforcement/test_verdict_parse.py` (still green).
- [ ] **Step 5 — commit:** `feat(enforcement): complex-tier verdict vocabulary`

---

## Task 2: HMAC signing (leaf)

**Files:** Create `src/legis/enforcement/signing.py`; Test `tests/enforcement/test_signing.py`

- [ ] **Step 1 — failing test**

```python
from legis.enforcement.signing import SIG_PREFIX, sign, verify


def test_sign_is_prefixed_and_deterministic():
    fields = {"verdict": "ACCEPTED", "policy": "p", "entity": "e"}
    sig = sign(fields, b"key-1")
    assert sig.startswith(SIG_PREFIX)
    assert sign(fields, b"key-1") == sig            # deterministic
    assert sign({"verdict": "ACCEPTED"}, b"key-1") != sig  # field-sensitive


def test_verify_round_trips_and_rejects_wrong_key_or_tamper():
    fields = {"verdict": "ACCEPTED", "policy": "p"}
    sig = sign(fields, b"key-1")
    assert verify(fields, sig, b"key-1") is True
    assert verify(fields, sig, b"key-2") is False           # wrong key
    assert verify({**fields, "policy": "q"}, sig, b"key-1") is False  # tampered field
    assert verify(fields, "not-a-sig", b"key-1") is False   # malformed
    assert verify(fields, "", b"key-1") is False
```

- [ ] **Step 2 — run, expect FAIL** (module missing).
- [ ] **Step 3 — implement:**

```python
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
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(enforcement): HMAC verdict signing (hmac-sha256:v1)`

---

## Task 3: signing_fields + ProtectedGate.submit (bound, signed verdicts)

**Files:** Create `src/legis/enforcement/protected.py`; Test `tests/enforcement/test_protected_submit.py`

`signing_fields(...)` is the SINGLE source of the signed dict — both `submit` and `TrailVerifier` call it, so they cannot drift.

- [ ] **Step 1 — failing test**

```python
from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate
from legis.enforcement.signing import verify
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


KEY = b"protected-key-1"


def gate(tmp_path, opinion):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    g = ProtectedGate(
        store, FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(opinion), key=KEY,
    )
    return g, store


def submit(g):
    return g.submit(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="sandboxed eval of trusted template",
        agent_id="agent-9",
        file_fingerprint="sha256:abc",
        ast_path="Module/FunctionDef[f]/Call[eval]",
    )


def test_accepted_record_is_bound_and_signed(tmp_path):
    g, store = gate(tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    result = submit(g)
    assert result.accepted is True
    assert result.verdict is Verdict.ACCEPTED

    ext = store.read_all()[0].payload["extensions"]
    assert ext["judge_verdict"] == "ACCEPTED"
    assert ext["file_fingerprint"] == "sha256:abc"
    assert ext["ast_path"] == "Module/FunctionDef[f]/Call[eval]"
    sig = ext["judge_metadata_signature"]
    assert sig.startswith("hmac-sha256:v1:")


def test_signature_covers_entity_and_policy(tmp_path):
    # Transplanting the verdict to a different entity must invalidate the sig.
    g, store = gate(tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    submit(g)
    payload = store.read_all()[0].payload
    from legis.enforcement.protected import signing_fields

    fields = signing_fields(payload)
    sig = payload["extensions"]["judge_metadata_signature"]
    assert verify(fields, sig, KEY) is True
    moved = {**fields, "entity": {"value": "src/other.py:g", "identity_stable": False}}
    assert verify(moved, sig, KEY) is False


def test_key_is_never_written_to_the_payload(tmp_path):
    g, store = gate(tmp_path, JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok"))
    submit(g)
    import json
    raw = json.dumps(store.read_all()[0].payload)
    assert "protected-key-1" not in raw
```

- [ ] **Step 2 — run, expect FAIL** (module missing).
- [ ] **Step 3 — implement** `protected.py` (submit + signing_fields + result dataclass + TamperError stub used in Task 7; operator_override added in Task 4):

```python
"""Protected cell — tamper-bound, judge-gated verdicts + load-time verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from legis.clock import Clock
from legis.enforcement.judge import Judge
from legis.enforcement.signing import sign, verify
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


class TamperError(RuntimeError):
    """A protected record failed load-time signature verification."""


@dataclass(frozen=True)
class ProtectedResult:
    accepted: bool
    seq: int
    verdict: Verdict
    judge_model: str | None
    judge_rationale: str | None
    signature: str


def signing_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The exact dict that is HMAC-signed — reconstructable from a stored payload.

    Binds entity + policy (advisor decision 4) in addition to the roadmap's six
    fields, so a signed verdict cannot be transplanted to another entity.
    """
    ext = payload["extensions"]
    return {
        "policy": payload["policy"],
        "entity": payload["entity_key"],
        "verdict": ext["judge_verdict"],
        "model": ext.get("judge_model"),
        "recorded_at": payload["recorded_at"],
        "rationale": payload["rationale"],
        "file_fingerprint": ext.get("file_fingerprint"),
        "ast_path": ext.get("ast_path"),
    }


class ProtectedGate:
    def __init__(
        self, store: AuditStore, clock: Clock, judge: Judge, key: bytes
    ) -> None:
        self._store = store
        self._clock = clock
        self._judge = judge
        self._key = key

    def _record_signed(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        actor_id: str,
        verdict: Verdict,
        model: str | None,
        judge_rationale: str | None,
        file_fingerprint: str,
        ast_path: str,
    ) -> ProtectedResult:
        ext: dict[str, Any] = {
            "judge_verdict": verdict.value,
            "judge_model": model,
            "judge_rationale": judge_rationale,
            "file_fingerprint": file_fingerprint,
            "ast_path": ast_path,
        }
        base = OverrideRecord(
            policy=policy,
            entity_key=entity_key,
            rationale=rationale,
            agent_id=actor_id,
            recorded_at=self._clock.now_iso(),
            extensions=ext,
        )
        payload = base.to_payload()
        signature = sign(signing_fields(payload), self._key)
        payload["extensions"]["judge_metadata_signature"] = signature
        seq = self._store.append(payload)
        return ProtectedResult(
            accepted=verdict in (Verdict.ACCEPTED, Verdict.OVERRIDDEN_BY_OPERATOR),
            seq=seq,
            verdict=verdict,
            judge_model=model,
            judge_rationale=judge_rationale,
            signature=signature,
        )

    def submit(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        agent_id: str,
        file_fingerprint: str,
        ast_path: str,
    ) -> ProtectedResult:
        proposed = OverrideRecord(
            policy=policy, entity_key=entity_key, rationale=rationale,
            agent_id=agent_id, recorded_at=self._clock.now_iso(),
        )
        opinion = self._judge.evaluate(proposed)
        return self._record_signed(
            policy=policy, entity_key=entity_key, rationale=rationale,
            actor_id=agent_id, verdict=opinion.verdict, model=opinion.model,
            judge_rationale=opinion.rationale,
            file_fingerprint=file_fingerprint, ast_path=ast_path,
        )
```

Note: `signing_fields` reads `payload["entity_key"]` (OverrideRecord.to_payload stores the key under `entity_key`). The test reconstructs `moved` with `"entity"` key matching `signing_fields`' output shape — keep them consistent (the signed dict uses `"entity"`; the *stored* payload uses `"entity_key"`; `signing_fields` maps one to the other).

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(enforcement): protected submit — bound, signed verdicts (WP-3.2)`

---

## Task 4: ProtectedGate.operator_override (OVERRIDDEN_BY_OPERATOR)

**Files:** Modify `protected.py`; Test `tests/enforcement/test_protected_override.py`

- [ ] **Step 1 — failing test**

```python
from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


def gate(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    g = ProtectedGate(
        store, FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(JudgeOpinion(Verdict.BLOCKED, "judge@1", "no")), key=b"k",
    )
    return g, store


def test_operator_override_is_distinct_signed_and_accepted(tmp_path):
    g, store = gate(tmp_path)
    result = g.operator_override(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="release exception approved by security lead",
        operator_id="op-sec-lead",
        file_fingerprint="sha256:abc",
        ast_path="Module/Call[eval]",
    )
    assert result.verdict is Verdict.OVERRIDDEN_BY_OPERATOR
    assert result.accepted is True
    ext = store.read_all()[0].payload["extensions"]
    assert ext["judge_verdict"] == "OVERRIDDEN_BY_OPERATOR"   # distinct from ACCEPTED
    assert ext["judge_metadata_signature"].startswith("hmac-sha256:v1:")
    assert store.read_all()[0].payload["agent_id"] == "op-sec-lead"
```

- [ ] **Step 2 — run, expect FAIL** (`operator_override` missing).
- [ ] **Step 3 — implement** on `ProtectedGate`:

```python
    def operator_override(
        self,
        *,
        policy: str,
        entity_key: EntityKey,
        rationale: str,
        operator_id: str,
        file_fingerprint: str,
        ast_path: str,
    ) -> ProtectedResult:
        # A human uses authority to bypass the judge. No model is consulted; the
        # verdict is the distinct OVERRIDDEN_BY_OPERATOR signal, still tamper-bound.
        return self._record_signed(
            policy=policy, entity_key=entity_key, rationale=rationale,
            actor_id=operator_id, verdict=Verdict.OVERRIDDEN_BY_OPERATOR,
            model=None, judge_rationale=None,
            file_fingerprint=file_fingerprint, ast_path=ast_path,
        )
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(enforcement): operator override verdict (WP-3.2)`

---

## Task 5: TrailVerifier + the discriminating tamper test

**Files:** Modify `protected.py`; Test `tests/enforcement/test_trail_verify.py`

- [ ] **Step 1 — failing test** (includes the re-chain discriminator)

```python
import sqlite3

from legis.clock import FixedClock
from legis.canonical import canonical_json, content_hash
from legis.enforcement.protected import ProtectedGate, TamperError, TrailVerifier
from legis.enforcement.signing import _chain_unused  # noqa: F401 (placeholder; see below)
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import GENESIS, AuditStore, _chain
```

> NOTE: import `_chain` and `GENESIS` from `legis.store.audit_store` (already defined there). Remove the placeholder line above. The test:

```python
class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


KEY = b"protected-key-1"
PROTECTED = frozenset({"no-eval"})


def _gate(db):
    store = AuditStore(f"sqlite:///{db}")
    g = ProtectedGate(
        store, FixedClock("2026-06-02T12:00:00+00:00"),
        judge=ScriptedJudge(JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")), key=KEY,
    )
    return g, store


def test_clean_protected_trail_verifies(tmp_path):
    g, store = _gate(tmp_path / "gov.db")
    g.submit(policy="no-eval", entity_key=EntityKey.from_locator("e"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap")
    TrailVerifier(KEY, PROTECTED).verify(store.read_all())   # no raise


def test_missing_signature_on_protected_policy_is_tampering(tmp_path):
    # A non-protected record legitimately lacks a signature: must NOT raise.
    g, store = _gate(tmp_path / "gov.db")
    g.submit(policy="no-eval", entity_key=EntityKey.from_locator("e"),
             rationale="r", agent_id="a", file_fingerprint="fp", ast_path="ap")
    # Forge an unsigned record for the SAME protected policy by stripping the sig
    # and fully re-chaining so the Sprint 0 integrity check still passes.
    _strip_signature_and_rechain(tmp_path / "gov.db")
    assert store.verify_integrity() is True          # Sprint 0 chain fooled
    try:
        TrailVerifier(KEY, PROTECTED).verify(store.read_all())
        assert False, "expected TamperError on missing signature"
    except TamperError:
        pass


def test_hmac_catches_a_fully_rechained_edit(tmp_path):
    # THE discriminating test: edit a protected record's rationale, recompute the
    # content/chain hashes for it and every successor so verify_integrity()==True,
    # then assert the keyed HMAC still rejects it.
    g, store = _gate(tmp_path / "gov.db")
    g.submit(policy="no-eval", entity_key=EntityKey.from_locator("e"),
             rationale="original", agent_id="a", file_fingerprint="fp", ast_path="ap")
    _edit_rationale_and_rechain(tmp_path / "gov.db", "FORGED")
    assert store.verify_integrity() is True           # unkeyed chain fooled
    try:
        TrailVerifier(KEY, PROTECTED).verify(store.read_all())
        assert False, "expected TamperError on forged rationale"
    except TamperError:
        pass


# --- raw-sqlite tamper helpers (out-of-band edits the store API forbids) ---

def _rows(db):
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
    return con


def _rechain(con):
    import json
    cur = con.execute("SELECT seq, payload FROM audit_log ORDER BY seq ASC")
    prev = GENESIS
    for seq, payload in cur.fetchall():
        c = content_hash(json.loads(payload))
        ch = _chain(prev, c)
        con.execute(
            "UPDATE audit_log SET content_hash=?, prev_hash=?, chain_hash=? WHERE seq=?",
            (c, prev, ch, seq),
        )
        prev = ch
    con.commit()


def _edit_rationale_and_rechain(db, new_rationale):
    import json
    con = _rows(db)
    seq, payload = con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    p = json.loads(payload)
    p["rationale"] = new_rationale
    con.execute("UPDATE audit_log SET payload=? WHERE seq=?", (canonical_json(p), seq))
    _rechain(con)
    con.close()


def _strip_signature_and_rechain(db):
    import json
    con = _rows(db)
    seq, payload = con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC LIMIT 1"
    ).fetchone()
    p = json.loads(payload)
    p["extensions"].pop("judge_metadata_signature", None)
    con.execute("UPDATE audit_log SET payload=? WHERE seq=?", (canonical_json(p), seq))
    _rechain(con)
    con.close()
```

- [ ] **Step 2 — run, expect FAIL** (`TrailVerifier` missing). Fix the import placeholder line noted above before running.
- [ ] **Step 3 — implement** `TrailVerifier` in `protected.py`:

```python
class TrailVerifier:
    """Load-time signature check. A record whose policy is protected MUST carry a
    valid signature; a missing or mismatched signature is tampering."""

    def __init__(self, key: bytes, protected_policies: frozenset[str]) -> None:
        self._key = key
        self._protected = protected_policies

    def verify(self, records) -> None:
        for rec in records:
            if rec.payload.get("policy") not in self._protected:
                continue
            ext = rec.payload.get("extensions", {})
            sig = ext.get("judge_metadata_signature")
            if not sig:
                raise TamperError(
                    f"protected record seq={rec.seq} is missing its signature"
                )
            if not verify(signing_fields(rec.payload), sig, self._key):
                raise TamperError(
                    f"protected record seq={rec.seq} signature does not verify"
                )
```

- [ ] **Step 4 — run, expect PASS** (all three).
- [ ] **Step 5 — commit:** `feat(enforcement): load-time HMAC trail verification (WP-3.2)`

---

## Task 6: SignoffGate — structured cell (WP-3.1)

**Files:** Create `src/legis/enforcement/signoff.py`; Test `tests/enforcement/test_signoff.py`

- [ ] **Step 1 — failing test**

```python
from legis.clock import FixedClock
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import SignoffState
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore


def gate(tmp_path, signer=None, key=None):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    return SignoffGate(store, FixedClock("2026-06-02T12:00:00+00:00"),
                       signer=signer, key=key), store


def test_request_does_not_clear_until_signed(tmp_path):
    g, store = gate(tmp_path)
    req = g.request(policy="prod-deploy", entity_key=EntityKey.from_locator("svc/api"),
                    rationale="ship hotfix", agent_id="agent-3")
    assert req.cleared is False
    assert g.is_cleared(req.seq) is False
    # the request is recorded as PENDING
    assert store.read_all()[0].payload["extensions"]["signoff_state"] == "PENDING_SIGNOFF"


def test_operator_signoff_clears_the_gate_and_is_recorded(tmp_path):
    g, store = gate(tmp_path)
    req = g.request(policy="prod-deploy", entity_key=EntityKey.from_locator("svc/api"),
                    rationale="ship hotfix", agent_id="agent-3")
    result = g.sign_off(request_seq=req.seq, operator_id="op-release-mgr",
                        rationale="verified rollback plan")
    assert result.cleared is True
    assert g.is_cleared(req.seq) is True
    signoff = store.read_all()[1].payload
    assert signoff["extensions"]["signoff_state"] == "SIGNED_OFF"
    assert signoff["extensions"]["request_seq"] == req.seq
    assert signoff["agent_id"] == "op-release-mgr"


def test_no_llm_is_invoked_on_the_structured_path(tmp_path):
    # SignoffGate has no judge dependency at all — structurally guaranteed.
    g, _ = gate(tmp_path)
    assert not hasattr(g, "_judge")
```

- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `signoff.py`:

```python
"""Structured / protected sign-off — block + escalate, no LLM in the path.

`request` records a PENDING_SIGNOFF and does NOT clear; a designated operator's
`sign_off` records SIGNED_OFF (referencing the request) and clears. An optional
`signer` makes protected-cell sign-offs tamper-bound; structured sign-offs are
procedural (unsigned). Human-in-the-loop by exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.clock import Clock
from legis.enforcement.signing import sign
from legis.enforcement.verdict import SignoffState
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


@dataclass(frozen=True)
class SignoffResult:
    seq: int
    cleared: bool


class SignoffGate:
    def __init__(
        self, store: AuditStore, clock: Clock,
        signer: bool | None = None, key: bytes | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        # `signer` truthy → protected sign-off (sign the SIGNED_OFF record).
        self._sign = bool(signer)
        self._key = key

    def _append(self, *, policy, entity_key, rationale, actor_id, ext) -> int:
        rec = OverrideRecord(
            policy=policy, entity_key=entity_key, rationale=rationale,
            agent_id=actor_id, recorded_at=self._clock.now_iso(), extensions=ext,
        )
        payload = rec.to_payload()
        if self._sign and self._key is not None:
            from legis.enforcement.protected import signing_fields
            payload["extensions"]["signoff_signature"] = sign(
                {**signing_fields_safe(payload)}, self._key
            )
        return self._store.append(payload)

    def request(self, *, policy, entity_key: EntityKey, rationale, agent_id) -> SignoffResult:
        seq = self._append(
            policy=policy, entity_key=entity_key, rationale=rationale,
            actor_id=agent_id,
            ext={"signoff_state": SignoffState.PENDING.value},
        )
        return SignoffResult(seq=seq, cleared=False)

    def sign_off(self, *, request_seq: int, operator_id: str, rationale: str = "") -> SignoffResult:
        req = self._store.read_all()[request_seq - 1].payload
        seq = self._append(
            policy=req["policy"],
            entity_key=EntityKey.from_dict(req["entity_key"]),
            rationale=rationale, actor_id=operator_id,
            ext={"signoff_state": SignoffState.SIGNED_OFF.value, "request_seq": request_seq},
        )
        return SignoffResult(seq=seq, cleared=True)

    def is_cleared(self, request_seq: int) -> bool:
        for rec in self._store.read_all():
            ext = rec.payload.get("extensions", {})
            if (ext.get("signoff_state") == SignoffState.SIGNED_OFF.value
                    and ext.get("request_seq") == request_seq):
                return True
        return False
```

> SIMPLIFY before running: the `signing_fields_safe`/protected import above is overwrought. For the structured cell `self._sign` is False, so signing is skipped. For the protected sign-off (Task referenced in disclosure), sign over a minimal dict: `{"policy":..., "entity":..., "recorded_at":..., "rationale":..., "operator": actor_id, "signoff_state": "SIGNED_OFF"}`. Replace the `_append` signing branch with that inline dict and delete the protected import. Keep it self-contained.

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(enforcement): structured sign-off gate (WP-3.1)`

---

## Task 7: lifecycle — decay sweep (WP-3.3 part 1)

**Files:** Create `src/legis/enforcement/lifecycle.py`; Test `tests/enforcement/test_decay_sweep.py`

- [ ] **Step 1 — failing test**

```python
from legis.enforcement.lifecycle import decay_sweep
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord
from legis.store.audit_store import AuditStore


class PolicyJudge:
    """Blocks any rationale containing 'stale'; accepts the rest."""

    def evaluate(self, record):
        v = Verdict.BLOCKED if "stale" in record.rationale else Verdict.ACCEPTED
        return JudgeOpinion(v, "judge@2", f"re-judged: {record.rationale}")


def _accepted(policy, entity, rationale):
    rec = OverrideRecord(policy=policy, entity_key=EntityKey.from_locator(entity),
                         rationale=rationale, agent_id="a", recorded_at="t",
                         extensions={"judge_verdict": "ACCEPTED", "judge_model": "judge@1"})
    return rec.to_payload()


def test_decay_flags_kept_suppressions_that_fail_a_fresh_pass(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    store.append(_accepted("p", "e1", "still valid reason"))
    store.append(_accepted("p", "e2", "stale reason no longer holds"))
    # a BLOCKED and an OVERRIDDEN record must be ignored by the sweep
    store.append({**_accepted("p", "e3", "stale"), "extensions": {"judge_verdict": "BLOCKED"}})
    store.append({**_accepted("p", "e4", "stale"), "extensions": {"judge_verdict": "OVERRIDDEN_BY_OPERATOR"}})

    flags = decay_sweep(store.read_all(), PolicyJudge())
    flagged_entities = {f.entity for f in flags}
    assert flagged_entities == {"e2"}    # only the ACCEPTED-but-now-stale one
```

- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** `lifecycle.py` (decay part):

```python
"""Protected-cell lifecycle gates — decay sweep + override-rate gate.

Both consume the append-only trail read-only. The decay sweep re-judges only
judge-ACCEPTED suppressions (an OVERRIDDEN_BY_OPERATOR entry would re-block
tautologically — the rate gate governs those instead).
"""

from __future__ import annotations

from dataclasses import dataclass

from legis.enforcement.judge import Judge
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord


@dataclass(frozen=True)
class DecayFlag:
    seq: int
    policy: str
    entity: str
    fresh_rationale: str


def decay_sweep(records, judge: Judge) -> list[DecayFlag]:
    flags: list[DecayFlag] = []
    for rec in records:
        ext = rec.payload.get("extensions", {})
        if ext.get("judge_verdict") != Verdict.ACCEPTED.value:
            continue
        p = rec.payload
        proposed = OverrideRecord(
            policy=p["policy"],
            entity_key=EntityKey.from_dict(p["entity_key"]),
            rationale=p["rationale"], agent_id=p["agent_id"],
            recorded_at=p["recorded_at"],
        )
        opinion = judge.evaluate(proposed)
        if opinion.verdict is not Verdict.ACCEPTED:
            flags.append(DecayFlag(
                seq=rec.seq, policy=p["policy"],
                entity=p["entity_key"]["value"], fresh_rationale=opinion.rationale,
            ))
    return flags
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(enforcement): decay sweep over kept suppressions (WP-3.3)`

---

## Task 8: lifecycle — override-rate gate (WP-3.3 part 2)

**Files:** Modify `lifecycle.py`; Create `src/legis/governance/params.py`; Test `tests/enforcement/test_override_rate.py`

- [ ] **Step 1 — failing test**

```python
from legis.enforcement.lifecycle import GateStatus, evaluate_override_rate


def _final(verdict):
    return {"extensions": {"judge_verdict": verdict}}


def trail(n_accept, n_override, n_blocked=0):
    rows = []
    rows += [_final("ACCEPTED") for _ in range(n_accept)]
    rows += [_final("OVERRIDDEN_BY_OPERATOR") for _ in range(n_override)]
    rows += [_final("BLOCKED") for _ in range(n_blocked)]

    class R:
        def __init__(self, payload, seq):
            self.payload = payload
            self.seq = seq

    return [R(p, i + 1) for i, p in enumerate(rows)]


def test_below_sample_floor_passes_with_notice():
    res = evaluate_override_rate(trail(2, 1), threshold=0.2, window=50, min_sample=10)
    assert res.status is GateStatus.PASS_WITH_NOTICE


def test_over_threshold_fails():
    # 5 overrides / 15 final = 0.33 > 0.2
    res = evaluate_override_rate(trail(10, 5), threshold=0.2, window=50, min_sample=10)
    assert res.status is GateStatus.FAIL
    assert round(res.rate, 2) == 0.33


def test_under_threshold_passes_and_blocked_not_in_denominator():
    # 2 overrides / 20 final = 0.10; 100 BLOCKED must not dilute the denominator
    res = evaluate_override_rate(trail(18, 2, n_blocked=100), threshold=0.2, window=200, min_sample=10)
    assert res.status is GateStatus.PASS
    assert res.sample_size == 20
```

- [ ] **Step 2 — run, expect FAIL.**
- [ ] **Step 3 — implement** in `lifecycle.py`:

```python
from enum import Enum


class GateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PASS_WITH_NOTICE = "PASS_WITH_NOTICE"


@dataclass(frozen=True)
class GateResult:
    status: GateStatus
    rate: float
    sample_size: int


_FINAL = {Verdict.ACCEPTED.value, Verdict.OVERRIDDEN_BY_OPERATOR.value}


def evaluate_override_rate(records, *, threshold: float, window: int, min_sample: int) -> GateResult:
    finals = [
        r for r in records
        if r.payload.get("extensions", {}).get("judge_verdict") in _FINAL
    ]
    finals = finals[-window:]
    n = len(finals)
    overrides = sum(
        1 for r in finals
        if r.payload["extensions"]["judge_verdict"] == Verdict.OVERRIDDEN_BY_OPERATOR.value
    )
    rate = (overrides / n) if n else 0.0
    if n < min_sample:
        status = GateStatus.PASS_WITH_NOTICE
    elif rate > threshold:
        status = GateStatus.FAIL
    else:
        status = GateStatus.PASS
    return GateResult(status=status, rate=rate, sample_size=n)
```

And `src/legis/governance/__init__.py` (`"""Governance policy parameters (ADR-0002)."""`) + `src/legis/governance/params.py`:

```python
"""Reviewed governance constants (ADR-0002). These are POLICY — changing them is
an ADR amendment, not a workflow-file or env tweak an agent can use to pass a gate.
"""

OVERRIDE_RATE_THRESHOLD = 0.2   # max share of kept suppressions forced past the judge
OVERRIDE_RATE_WINDOW = 100      # rolling window of final-disposition records
OVERRIDE_RATE_MIN_SAMPLE = 20   # below this, pass-with-notice (small-corpus floor)
```

- [ ] **Step 4 — run, expect PASS.**
- [ ] **Step 5 — commit:** `feat(governance): override-rate gate + ADR-0002 policy constants (WP-3.3)`

---

## Task 9: API surface — protected, signoff, verified read, rate endpoint

**Files:** Modify `src/legis/api/app.py`; Test `tests/api/test_complex_api.py`

Wire optional `protected_gate`, `signoff_gate`, and a `trail_verifier` into `create_app`. `GET /overrides` runs the verifier (when present) and returns HTTP 500 on `TamperError`. Add `GET /governance/override-rate` reading the ADR-0002 constants (no query knobs). Routes:
- `POST /protected/overrides` (agent submit) → 201 if accepted else 409
- `POST /protected/operator-override` → 201
- `POST /signoff/request` → 202 (accepted-pending), body has `seq`
- `POST /signoff/{request_seq}/sign` → 200, body `cleared: true`
- `GET /governance/override-rate` → `{status, rate, sample_size}`

- [ ] **Step 1 — failing test** (covers: protected post records+verifies; tampered read → 500; signoff request→sign clears; rate endpoint uses policy constants)

```python
from fastapi.testclient import TestClient

from legis.api.app import create_app
from legis.clock import FixedClock
from legis.enforcement.protected import ProtectedGate, TrailVerifier
from legis.enforcement.signoff import SignoffGate
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.store.audit_store import AuditStore


class ScriptedJudge:
    def __init__(self, opinion):
        self.opinion = opinion

    def evaluate(self, record):
        return self.opinion


KEY = b"k"
PROTECTED = frozenset({"no-eval"})
PBODY = {
    "policy": "no-eval", "entity": "src/x.py:f", "rationale": "sandboxed",
    "agent_id": "agent-9", "file_fingerprint": "fp", "ast_path": "ap",
}


def _app(tmp_path, opinion=JudgeOpinion(Verdict.ACCEPTED, "judge@1", "ok")):
    store = AuditStore(f"sqlite:///{tmp_path / 'gov.db'}")
    clock = FixedClock("2026-06-02T12:00:00+00:00")
    pg = ProtectedGate(store, clock, judge=ScriptedJudge(opinion), key=KEY)
    sg = SignoffGate(store, clock)
    app = create_app(protected_gate=pg, signoff_gate=sg,
                     trail_verifier=TrailVerifier(KEY, PROTECTED))
    return TestClient(app), store


def test_protected_post_records_and_verified_read_succeeds(tmp_path):
    c, _ = _app(tmp_path)
    assert c.post("/protected/overrides", json=PBODY).status_code == 201
    trail = c.get("/overrides")
    assert trail.status_code == 200
    assert trail.json()[0]["extensions"]["judge_metadata_signature"].startswith("hmac-sha256:v1:")


def test_signoff_request_then_sign_clears(tmp_path):
    c, _ = _app(tmp_path)
    req = c.post("/signoff/request", json={
        "policy": "prod-deploy", "entity": "svc/api",
        "rationale": "hotfix", "agent_id": "agent-3"})
    assert req.status_code == 202
    seq = req.json()["seq"]
    signed = c.post(f"/signoff/{seq}/sign", json={"operator_id": "op-1", "rationale": "ok"})
    assert signed.status_code == 200
    assert signed.json()["cleared"] is True


def test_tampered_protected_read_is_a_500(tmp_path):
    import json, sqlite3
    from legis.canonical import canonical_json, content_hash
    from legis.store.audit_store import GENESIS, _chain

    c, store = _app(tmp_path)
    c.post("/protected/overrides", json=PBODY)
    db = str(tmp_path / "gov.db")
    con = sqlite3.connect(db)
    con.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
    seq, payload = con.execute(
        "SELECT seq, payload FROM audit_log ORDER BY seq ASC LIMIT 1").fetchone()
    p = json.loads(payload)
    p["rationale"] = "FORGED"
    con.execute("UPDATE audit_log SET payload=? WHERE seq=?", (canonical_json(p), seq))
    # re-chain so the unkeyed integrity check still passes
    prev = GENESIS
    for s, pl in con.execute("SELECT seq, payload FROM audit_log ORDER BY seq ASC").fetchall():
        ch = content_hash(json.loads(pl))
        con.execute("UPDATE audit_log SET content_hash=?, prev_hash=?, chain_hash=? WHERE seq=?",
                    (ch, prev, _chain(prev, ch), s))
        prev = _chain(prev, ch)
    con.commit(); con.close()
    assert store.verify_integrity() is True
    assert c.get("/overrides").status_code == 500


def test_override_rate_endpoint_uses_policy_constants(tmp_path):
    c, _ = _app(tmp_path)
    r = c.get("/governance/override-rate")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "rate", "sample_size"}
    assert body["status"] == "PASS_WITH_NOTICE"   # empty trail < min_sample
```

- [ ] **Step 2 — run, expect FAIL** (`create_app` rejects new kwargs).
- [ ] **Step 3 — implement** in `app.py`: extend `create_app` signature with `protected_gate=None, signoff_gate=None, trail_verifier=None`; add input models `ProtectedIn`, `OperatorOverrideIn`, `SignoffRequestIn`, `SignoffSignIn`; the routes; and make `GET /overrides`:

```python
    @app.get("/overrides")
    def get_overrides() -> list[dict]:
        records = engine().trail_records() if engine_has_records else engine().trail()
        ...
```

> Implementation guidance: keep `GET /overrides` returning the existing engine trail when only the simple engine is wired. When `trail_verifier` is present, read the *store* the protected gate writes to, run `trail_verifier.verify(records)` inside a `try/except TamperError` → `raise HTTPException(500, ...)`, and return `[r.payload for r in records]`. To avoid coupling, give `ProtectedGate` a `store` accessor (`def records(self): return self._store.read_all()`), and when `protected_gate` is wired, source the trail from it. Add `from legis.enforcement.protected import TamperError, ProtectedGate, TrailVerifier`, `from legis.enforcement.signoff import SignoffGate`, `from legis.governance import params`.

Override-rate route:

```python
    @app.get("/governance/override-rate")
    def override_rate() -> dict:
        from legis.enforcement.lifecycle import evaluate_override_rate
        recs = protected_records()   # the governance trail
        res = evaluate_override_rate(
            recs,
            threshold=params.OVERRIDE_RATE_THRESHOLD,
            window=params.OVERRIDE_RATE_WINDOW,
            min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
        )
        return {"status": res.status.value, "rate": res.rate, "sample_size": res.sample_size}
```

- [ ] **Step 4 — run, expect PASS.** Then full suite `uv run pytest -q`.
- [ ] **Step 5 — commit:** `feat(api): complex-tier surface — protected, signoff, verified read, rate gate`

---

## Task 10: ADR-0002 + docs + scope disclosure

- [ ] **Step 1:** write `docs/design/adr/0002-complex-tier-governance-parameters.md` — records: HMAC key provisioning (injected; env-var + rotation in prod; Open Decision #4), the protected-policy set as config, and the override-rate constants as reviewed policy (not workflow-tunable). Status: Accepted.
- [ ] **Step 2:** add `**Status:** ✅ implemented 2026-06-02` under Sprint 3 in `docs/superpowers/plans/2026-06-01-legis-implementation-sprints.md`, and a "Scope boundary & known limitations" section to this plan (the three disclosures above).
- [ ] **Step 3:** full suite green, zero warnings.
- [ ] **Step 4 — commit:** `docs: ADR-0002 + mark Sprint 3 complex tier complete`

---

## Self-review — WP coverage

| WP | Exit criterion | Proven by |
|---|---|---|
| 3.1 structured | designated policy cannot clear without recorded human sign-off; no model on path | Task 6 (`request` not cleared, `sign_off` clears; `no _judge`) |
| 3.2 tamper-binding | out-of-band edit rejected at load; OVERRIDDEN_BY_OPERATOR distinct; key never beside records | Task 3 (key-not-in-payload, entity binding), Task 4 (distinct verdict), Task 5 (re-chain HMAC catch, missing-sig=tamper), Task 9 (500 on tampered read) |
| 3.3 lifecycle | kept suppression failing fresh judge flagged; rate gate FAILs over threshold, PASS_WITH_NOTICE below floor; threshold is policy | Task 7 (decay), Task 8 (rate + ADR constants), Task 9 (endpoint uses constants, no query knobs) |

Advisor decisions 1–8 are each pinned to a test above; the discriminating HMAC-vs-chain test (decision 3) is Task 5 `test_hmac_catches_a_fully_rechained_edit`.
