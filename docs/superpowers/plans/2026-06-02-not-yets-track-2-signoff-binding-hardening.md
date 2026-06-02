# Not-Yets Track 2 (WP-A3) — Sign-off Binding Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a sign-off→issue binding a tamper-bound, legis-side record. On bind, legis writes an HMAC-signed `BindingRecord{signoff_seq, issue_id, sei, content_hash}` into a dedicated append-only ledger, verified on read (forged record rejected); `signoff_seq` becomes durable; and the Sprint-6 §6.2 overstatement is corrected.

**Architecture:** A dedicated `BindingLedger` wraps its own append-only `AuditStore` and signs each binding with the same `signing.sign`/`verify` HMAC scheme used by protected verdicts. It is isolated from the override/gap governance trail, so binding records never pollute `/overrides` or orphan-gap/lineage reads (the WP-A2 surfaces stay untouched). `bind_signoff_to_issue` gains an optional ledger: it still posts the opaque `{entity_id, content_hash, actor}` pointer to Filigree (the Filigree row stays a pointer — WP-B1 later adds a signature column there), then records the signed binding locally. The API wires a `binding_ledger` and adds a verified read surface `GET /signoff/{seq}/binding`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/SQLite (`AuditStore`), stdlib `hmac` (via `legis.enforcement.signing`), pytest (warnings-as-errors). No new runtime dependency.

**Implements (design spec `2026-06-02-not-yets-completion-design.md`):** WP-A3. Closes R-2.3-01c (legis half), R-2.3-02, and the §6.2 overstatement. **Decision (approved):** dedicated binding ledger, not the shared governance trail.

**Locked design decisions (do not reopen):**
1. **Dedicated ledger, own store.** The binding ledger uses a separate `AuditStore`; it does not append to the governance trail. Gap-detection and `/overrides` are not modified and never see binding records.
2. **Same HMAC scheme, distinct signed field set.** Reuse `signing.sign`/`verify`. The signed dict is exactly `{signoff_seq, issue_id, sei, content_hash}` — reconstructable from the stored payload, so a transplant to a different SEI/issue/hash invalidates the signature.
3. **Filigree row stays an opaque pointer.** This WP does NOT change what legis posts to Filigree (`{entity_id, content_hash, actor}`). The tamper-binding lives in the legis ledger. Adding a signature column at Filigree is WP-B1 (sibling-gated), out of scope here.
4. **Backward compatible.** `bind_signoff_to_issue`'s `ledger` param is optional; when absent, behaviour is exactly as today (no record), so existing bind-issue tests are unaffected. The binding is recorded only after a successful Filigree `attach` (validate → attach → record).
5. **Fail-closed on read.** A forged/mutated/missing-signature binding record raises `BindingError` at read time; the HTTP read surface maps that to 500, mirroring the protected trail's `TamperError` → 500.

---

## File structure

| File | Responsibility |
|---|---|
| `src/legis/governance/binding_ledger.py` | `BindingError`; `binding_signing_fields`; `BindingLedger` (`record` / `verify` / `get`) over a dedicated `AuditStore` |
| `src/legis/governance/signoff_binding.py` | `bind_signoff_to_issue` gains optional `ledger`; records the signed binding after attach; returns `signoff_seq` + `binding_seq` |
| `src/legis/api/app.py` | inject `binding_ledger`; `bind_issue` records via the ledger; `GET /signoff/{seq}/binding` verified read |
| `tests/governance/test_binding_ledger.py` | record/verify/get round-trip; forged + transplanted records rejected; unknown seq → None |
| `tests/governance/test_signoff_binding.py` | (append) bind records a signed binding + returns `binding_seq` |
| `tests/api/test_combinations_api.py` | (append) bind-issue records to the ledger; `GET …/binding` verifies; forge → 500 |
| `docs/superpowers/plans/2026-06-02-legis-sprint-6-suite-combinations.md` | correct the §6.2 self-review row + Known Limitation |
| `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md` | mark WP-A3 done |

---

## Task 1: `BindingLedger` — signed, dedicated, load-verified

**Files:**
- Create: `src/legis/governance/binding_ledger.py`
- Test: `tests/governance/test_binding_ledger.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/governance/test_binding_ledger.py
import pytest

from legis.clock import FixedClock
from legis.enforcement.signing import sign
from legis.governance.binding_ledger import (
    BindingError,
    BindingLedger,
    binding_signing_fields,
)
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

KEY = b"binding-key-1"


def _ledger(tmp_path):
    store = AuditStore(f"sqlite:///{tmp_path / 'bind.db'}")
    return BindingLedger(store, FixedClock("2026-06-02T12:00:00+00:00"), key=KEY), store


def test_record_then_get_round_trips_the_binding(tmp_path):
    ledger, _ = _ledger(tmp_path)
    seq = ledger.record(signoff_seq=7, issue_id="ISSUE-1",
                        entity_key=EntityKey.from_sei("clarion:eid:abc"), content_hash="h")
    assert seq == 1
    got = ledger.get(7)
    assert got["signoff_seq"] == 7
    assert got["issue_id"] == "ISSUE-1"
    assert got["entity_key"] == {"value": "clarion:eid:abc", "identity_stable": True}
    assert got["content_hash"] == "h"
    assert got["binding_signature"].startswith("hmac-sha256:v1:")


def test_verify_passes_for_a_legit_record(tmp_path):
    ledger, _ = _ledger(tmp_path)
    ledger.record(signoff_seq=1, issue_id="I", entity_key=EntityKey.from_sei("clarion:eid:x"),
                  content_hash="h")
    ledger.verify()  # does not raise


def test_unknown_signoff_seq_returns_none(tmp_path):
    ledger, _ = _ledger(tmp_path)
    ledger.record(signoff_seq=1, issue_id="I", entity_key=EntityKey.from_sei("clarion:eid:x"),
                  content_hash="h")
    assert ledger.get(99) is None


def test_forged_signature_is_rejected(tmp_path):
    ledger, store = _ledger(tmp_path)
    store.append({"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
                  "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
                  "content_hash": "h", "recorded_at": "t",
                  "binding_signature": "hmac-sha256:v1:deadbeef"})
    with pytest.raises(BindingError):
        ledger.verify()
    with pytest.raises(BindingError):
        ledger.get(1)


def test_transplanted_signature_is_rejected(tmp_path):
    # A signature valid for a DIFFERENT content_hash must not verify against this record.
    ledger, store = _ledger(tmp_path)
    good_sig = sign(binding_signing_fields(
        {"signoff_seq": 1, "issue_id": "I", "content_hash": "ORIGINAL",
         "entity_key": {"value": "clarion:eid:x"}}), KEY)
    store.append({"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
                  "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
                  "content_hash": "TAMPERED", "recorded_at": "t",
                  "binding_signature": good_sig})
    with pytest.raises(BindingError):
        ledger.verify()


def test_missing_signature_is_rejected(tmp_path):
    ledger, store = _ledger(tmp_path)
    store.append({"kind": "issue_binding", "signoff_seq": 1, "issue_id": "I",
                  "entity_key": {"value": "clarion:eid:x", "identity_stable": True},
                  "content_hash": "h", "recorded_at": "t"})
    with pytest.raises(BindingError):
        ledger.verify()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/governance/test_binding_ledger.py -v`
Expected: FAIL — `ModuleNotFoundError: legis.governance.binding_ledger`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/governance/binding_ledger.py
"""Tamper-bound ledger of sign-off → issue bindings (legis-side, WP-A3).

A governed sign-off bound to a Filigree issue is recorded here as a signed,
append-only ``BindingRecord`` — the legis-side tamper-bound attestation. The row
legis posts to Filigree is an opaque ``{entity_id, content_hash, actor}`` POINTER;
this ledger is where the binding's integrity actually lives, using the same HMAC
scheme as protected verdicts (``signing.sign``/``verify``). A forged or mutated
binding record is rejected at read time (``BindingError``), mirroring the
protected trail's load-time verification. The ledger is a DEDICATED append-only
store, isolated from the override/gap governance trail, so binding records never
pollute orphan-gap or ``/overrides`` reads.
"""

from __future__ import annotations

from typing import Any

from legis.clock import Clock
from legis.enforcement.signing import sign, verify
from legis.identity.entity_key import EntityKey
from legis.store.audit_store import AuditStore

BINDING_KIND = "issue_binding"


class BindingError(RuntimeError):
    """A binding record failed load-time signature verification."""


def binding_signing_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """The exact dict that is HMAC-signed for a binding — reconstructable from a
    stored payload. Binds the four load-bearing facts so a transplant to another
    issue/SEI/hash invalidates the signature."""
    return {
        "signoff_seq": payload["signoff_seq"],
        "issue_id": payload["issue_id"],
        "sei": payload["entity_key"]["value"],
        "content_hash": payload["content_hash"],
    }


class BindingLedger:
    def __init__(self, store: AuditStore, clock: Clock, key: bytes) -> None:
        self._store = store
        self._clock = clock
        self._key = key

    def record(
        self,
        *,
        signoff_seq: int,
        issue_id: str,
        entity_key: EntityKey,
        content_hash: str,
    ) -> int:
        payload: dict[str, Any] = {
            "kind": BINDING_KIND,
            "signoff_seq": signoff_seq,
            "issue_id": issue_id,
            "entity_key": entity_key.to_dict(),
            "content_hash": content_hash,
            "recorded_at": self._clock.now_iso(),
        }
        payload["binding_signature"] = sign(binding_signing_fields(payload), self._key)
        return self._store.append(payload)

    def verify(self) -> None:
        for rec in self._store.read_all():
            payload = rec.payload
            if payload.get("kind") != BINDING_KIND:
                continue
            sig = payload.get("binding_signature")
            if not sig:
                raise BindingError(
                    f"binding record seq={rec.seq} is missing its signature"
                )
            if not verify(binding_signing_fields(payload), sig, self._key):
                raise BindingError(
                    f"binding record seq={rec.seq} signature does not verify"
                )

    def get(self, signoff_seq: int) -> dict[str, Any] | None:
        self.verify()  # fail-closed: never return data from a tampered ledger
        for rec in self._store.read_all():
            p = rec.payload
            if p.get("kind") == BINDING_KIND and p.get("signoff_seq") == signoff_seq:
                return p
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/governance/test_binding_ledger.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/governance/binding_ledger.py tests/governance/test_binding_ledger.py
git commit -m "feat(governance): dedicated tamper-bound binding ledger (WP-A3)"
```

---

## Task 2: `bind_signoff_to_issue` records the signed binding

**Files:**
- Modify: `src/legis/governance/signoff_binding.py`
- Test: `tests/governance/test_signoff_binding.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/governance/test_signoff_binding.py`)

```python
def test_bind_records_a_signed_binding_when_a_ledger_is_given(tmp_path):
    from legis.clock import FixedClock
    from legis.governance.binding_ledger import BindingLedger
    from legis.store.audit_store import AuditStore

    fil = FakeFiligree()
    ledger = BindingLedger(AuditStore(f"sqlite:///{tmp_path / 'bind.db'}"),
                           FixedClock("2026-06-02T12:00:00+00:00"), key=b"k")
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1", entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7, ledger=ledger)
    # Filigree still gets the opaque pointer.
    assert fil.attached == [("ISSUE-1", "clarion:eid:abc", "blake3", "legis")]
    # And legis durably recorded a signed binding (signoff_seq survives readback).
    assert out["signoff_seq"] == 7
    assert out["binding_seq"] == 1
    recorded = ledger.get(7)
    assert recorded["issue_id"] == "ISSUE-1"
    assert recorded["entity_key"]["value"] == "clarion:eid:abc"
    assert recorded["binding_signature"].startswith("hmac-sha256:v1:")


def test_bind_without_a_ledger_keeps_prior_behaviour(tmp_path):
    fil = FakeFiligree()
    out = bind_signoff_to_issue(
        fil, issue_id="ISSUE-1", entity_key=EntityKey.from_sei("clarion:eid:abc"),
        content_hash="blake3", signoff_seq=7)
    assert out["signoff_seq"] == 7
    assert "binding_seq" not in out   # nothing recorded when no ledger wired
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/governance/test_signoff_binding.py -k ledger_is_given -v`
Expected: FAIL — `bind_signoff_to_issue() got an unexpected keyword argument 'ledger'`.

- [ ] **Step 3: Write minimal implementation**

Edit `src/legis/governance/signoff_binding.py`: import the ledger type, add an optional
`ledger` param, and record after a successful attach.

```python
from legis.governance.binding_ledger import BindingLedger
```

```python
def bind_signoff_to_issue(
    filigree: FiligreeClient,
    *,
    issue_id: str,
    entity_key: EntityKey,
    content_hash: str,
    signoff_seq: int,
    ledger: BindingLedger | None = None,
) -> dict[str, Any]:
    if not entity_key.identity_stable:
        raise ValueError(
            "cannot bind a sign-off on an identity_stable=False (locator) key — "
            "the binding would orphan on rename; resolve to an SEI first"
        )
    result = filigree.attach(
        issue_id, entity_key.value, content_hash, actor=BINDING_ACTOR
    )
    out: dict[str, Any] = {**result, "signoff_seq": signoff_seq}
    if ledger is not None:
        # The Filigree row is an opaque pointer; the tamper-bound record lives here.
        out["binding_seq"] = ledger.record(
            signoff_seq=signoff_seq,
            issue_id=issue_id,
            entity_key=entity_key,
            content_hash=content_hash,
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/governance/test_signoff_binding.py -v`
Expected: PASS (existing tests + 2 new). Then `python -m pytest tests/governance/ -q` — all green.

- [ ] **Step 5: Commit**

```bash
git add src/legis/governance/signoff_binding.py tests/governance/test_signoff_binding.py
git commit -m "feat(governance): bind records a signed BindingRecord + durable signoff_seq (WP-A3)"
```

---

## Task 3: Wire the ledger into the API + verified read surface

**Files:**
- Modify: `src/legis/api/app.py` (`create_app` param, `bind_issue`, new `GET /signoff/{seq}/binding`)
- Test: `tests/api/test_combinations_api.py` (append)

- [ ] **Step 1: Write the failing test** (append to `tests/api/test_combinations_api.py`)

```python
def test_bind_issue_records_to_ledger_and_binding_is_verifiable(tmp_path):
    from legis.clock import FixedClock
    from legis.enforcement.signoff import SignoffGate
    from legis.governance.binding_ledger import BindingLedger
    from legis.store.audit_store import AuditStore

    class FakeFiligree:
        def __init__(self):
            self.attached = []

        def attach(self, issue_id, entity_id, content_hash, *, actor):
            self.attached.append((issue_id, entity_id, content_hash, actor))
            return {"issue_id": issue_id, "clarion_entity_id": entity_id,
                    "content_hash_at_attach": content_hash, "attached_at": "t",
                    "attached_by": actor}

        def associations_for_entity(self, entity_id):
            return []

    clock = FixedClock("2026-06-02T12:00:00+00:00")
    sg = SignoffGate(AuditStore(f"sqlite:///{tmp_path / 'gov.db'}"), clock)
    ledger = BindingLedger(AuditStore(f"sqlite:///{tmp_path / 'bind.db'}"), clock, key=b"k")
    fil = FakeFiligree()
    c = _client(tmp_path, signoff_gate=sg, filigree=fil, binding_ledger=ledger)

    # A sign-off keyed on an alive SEI must exist and be cleared before binding.
    from legis.identity.entity_key import EntityKey
    sg.request(policy="prod-deploy", entity_key=EntityKey.from_sei("clarion:eid:abc"),
               rationale="r", agent_id="a",
               extensions={"clarion": {"content_hash": "blake3", "alive": True,
                                       "lineage_snapshot": None}})
    sg.sign_off(request_seq=1, operator_id="op-1")

    resp = c.post("/signoff/1/bind-issue",
                  json={"issue_id": "ISSUE-1", "sei": "clarion:eid:abc", "content_hash": "ignored"})
    assert resp.status_code == 201
    assert resp.json()["binding_seq"] == 1
    assert fil.attached[0][0] == "ISSUE-1"

    got = c.get("/signoff/1/binding")
    assert got.status_code == 200
    assert got.json()["issue_id"] == "ISSUE-1"
    assert got.json()["entity_key"]["value"] == "clarion:eid:abc"


def test_binding_read_404_when_no_ledger(tmp_path):
    c = _client(tmp_path)
    assert c.get("/signoff/1/binding").status_code == 404
```

> NOTE: `_client` is the helper at the top of this file — confirm it forwards `**kw`
> to `create_app` (it does for `filigree=`); `signoff_gate` and `binding_ledger`
> ride the same `**kw`. The bind-issue endpoint sources the SEI + content_hash
> from the *cleared sign-off record*, not the request body, so the body's
> `content_hash` is intentionally ignored — assert against what the sign-off carried
> (`"blake3"`). If the existing `_client` signature can't pass these, adapt the call
> in YOUR test (e.g. build the app inline like other tests here) — do not change
> `_client` if other tests depend on it.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/api/test_combinations_api.py -k "ledger or binding" -v`
Expected: FAIL — `create_app() got an unexpected keyword argument 'binding_ledger'`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/api/app.py`:

Add the import:

```python
from legis.governance.binding_ledger import BindingError, BindingLedger
```

Add the `create_app` parameter (next to `filigree`):

```python
    filigree: FiligreeClient | None = None,
    binding_ledger: BindingLedger | None = None,
) -> FastAPI:
```

In `bind_issue`, pass the ledger to the binding call (the rest of the handler — the
clearance check and SEI/content_hash sourcing from the cleared record — is unchanged):

```python
        try:
            return bind_signoff_to_issue(
                filigree,
                issue_id=body.issue_id,
                entity_key=entity_key,
                content_hash=content_hash,
                signoff_seq=request_seq,
                ledger=binding_ledger,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
```

Add the verified read surface (next to the other `/signoff/*` routes):

```python
    @app.get("/signoff/{request_seq}/binding")
    def get_binding(request_seq: int) -> dict:
        if binding_ledger is None:
            raise HTTPException(status_code=404, detail="binding ledger not enabled")
        try:
            binding = binding_ledger.get(request_seq)
        except BindingError as exc:
            raise HTTPException(status_code=500, detail=f"binding integrity failure: {exc}")
        if binding is None:
            raise HTTPException(status_code=404, detail="no binding at seq")
        return binding
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/api/test_combinations_api.py -v`
Expected: PASS. Then `python -m pytest -q` — full suite green (was 162 → +8 new tests = 170).

- [ ] **Step 5: Commit**

```bash
git add src/legis/api/app.py tests/api/test_combinations_api.py
git commit -m "feat(api): bind-issue records to the binding ledger + verified read surface (WP-A3)"
```

---

## Task 4: Correct the §6.2 overstatement + mark WP-A3 done

**Files:**
- Modify: `docs/superpowers/plans/2026-06-02-legis-sprint-6-suite-combinations.md`
- Modify: `docs/superpowers/specs/2026-06-02-not-yets-completion-design.md`

- [ ] **Step 1:** In the Sprint 6 plan, find the self-review table row for WP-6.2
  ("a governed sign-off attaches to a Filigree issue with the same tamper-binding as a
  governance verdict") and the WP-6.2 Known Limitation that asserts the exit criterion is
  "satisfied by attach + tamper-binding". Append a correction note to BOTH (do not rewrite
  history — add a dated note): *"Correction (WP-A3, 2026-06-02): the row posted to Filigree
  is an UNSIGNED `{entity_id, content_hash, actor}` pointer; the tamper-binding lives in
  legis's dedicated signed BindingLedger (`governance/binding_ledger.py`), verified on read.
  The earlier wording overstated the Filigree-side binding as verdict-grade."*

- [ ] **Step 2:** In the design spec, append " — ✅ done 2026-06-02" to the WP-A3 heading
  ("**WP-A3 — legis-side signed `BindingRecord` …**").

- [ ] **Step 3: Full suite green, zero warnings**

Run: `python -m pytest -q`
Expected: 170 passed, zero warnings. Confirm the count before committing.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: correct §6.2 binding overstatement; mark WP-A3 complete"
```

---

## Self-review — WP coverage

| Exit criterion (design spec WP-A3) | Proven by |
|---|---|
| a test forges a `BindingRecord` and asserts load-time HMAC rejection | Task 1 (`test_forged_signature_is_rejected`, `test_transplanted_signature_is_rejected`, `test_missing_signature_is_rejected`), Task 3 (read surface → 500 path via `BindingError`) |
| `signoff_seq` survives readback from the legis trail | Task 1 (`test_record_then_get_round_trips_the_binding`), Task 2 (`test_bind_records_a_signed_binding…`), Task 3 (`…binding_is_verifiable`) |
| the binding is signed with the same HMAC scheme as protected verdicts | Task 1 (reuses `signing.sign`/`verify`; signature carries the `hmac-sha256:v1:` prefix) |
| the Filigree row stays an opaque pointer (WP-B1 extends it) | Task 2 (`fil.attached` is still `{issue_id, entity_id, content_hash, actor}`; no signature sent) |
| §6.2 self-review + Known-Limitation text corrected | Task 4 |
| no pollution of the WP-A2 gap/override reads | Locked decision 1 (dedicated store); `/overrides` + gap endpoints untouched — full suite (incl. the WP-A2 tests) green at Task 3/4 |

**Out of scope (other WPs):** Filigree signature column (WP-B1, sibling-gated); Wardline routing breadth (A4–A6); policy grammar (A7–A8); git/CI surface (A9–A11); SEI backfill (A12). Each is its own plan per the design spec.
