# Cluster C — Governance & Persistence Foundations

Catalog for the foundational governance + persistence layer of Legis (Weft suite).
Four separate entry blocks: Governance, Store, Records, Foundations.

---

## Governance

**Location:** `src/legis/governance/`

**Responsibility:** Tamper-bound binding of sign-offs to Filigree issues, append-only SEI re-keying/backfill of pre-SEI records, lineage-spine gap/divergence detection, and pure closure-gate decisions — all layered on the record-agnostic audit store.

**Key Components:**
- `binding_ledger.py` (93 lines) — `BindingLedger` records signed (`issue_binding`) bindings to a *dedicated* `AuditStore` and verifies them at read time. `verify()` (L59–76) now checks `store.verify_integrity()` first (hash chain) then HMAC-verifies each record's signing fields. `get`/`get_by_issue_id` (L78–93) are fail-closed: they call `verify()` before returning. `BindingError` raised on tamper/forgery. Signing fields fixed by `binding_signing_fields` (L30–37).
- `signoff_binding.py` (74 lines) — `bind_signoff_to_issue` (L28–74): validate (rejects `identity_stable=False` locator keys, L38) → `filigree.attach` → optional `ledger.record`. Returns `binding_seq`. Documents the non-atomic attach-then-record trade-off (L64–73): no compensating delete; orphaned attach surfaced by ledger `verify()`.
- `sei_backfill.py` (259 lines) — `run_pre_sei_backfill` (L44): scans audit records, finds locator-keyed (`identity_stable=False`, non-SEI) records, resolves via Loomweave batch, and **appends** `SEI_BACKFILL` / `SEI_BACKFILL_UNRESOLVED` events referencing `original_seq` (never rewrites). Idempotent via `_backfilled_original_sequences` (L152). Fails closed on integrity failure (L58). `SeiBackfillReport` dataclass.
- `gaps.py` (115 lines) — `find_orphan_gaps` (L57): SEIs Loomweave reports `alive: false`. `find_lineage_integrity` (L68): REQ-L-01 Option-3 custody — verifies stored `lineage_snapshot` is still a *prefix* of current lineage (`content_hash(current[:n]) == snap["hash"]`, L105); prefix-break = divergence, growth is legitimate. Returns `LineageIntegrity` (divergences + unavailable).
- `filigree_gate.py` (32 lines) — `evaluate_issue_closure` (L14): pure decision; closable only if ledger holds a verified binding. Missing binding → structured `allowed: False`; tampered ledger → `BindingError` propagates.
- `params.py` (11 lines) — Reviewed governance constants (ADR-0002): `OVERRIDE_RATE_THRESHOLD`, `_WINDOW`, `_MIN_SAMPLE`. Policy, read server-side only.
- `__init__.py` (1 line) — package docstring.

**Dependencies:**
- Inbound:
  - `cli.py:9` → `sei_backfill.run_pre_sei_backfill`; `cli.py:173` → `governance.params`
  - `mcp.py:29` → `binding_ledger.BindingError`; `mcp.py:146` → `BindingLedger`; `mcp.py:969` → `filigree_gate.evaluate_issue_closure`
  - `service/governance.py:18` → `governance.params`
  - `api/app.py:37` → `gaps.find_lineage_integrity, find_orphan_gaps`; `api/app.py:39` → `binding_ledger.BindingError, BindingLedger`; `api/app.py:40` → `signoff_binding.bind_signoff_to_issue`; `api/app.py:345` → `BindingLedger`; `api/app.py:664` → `filigree_gate.evaluate_issue_closure`
- Outbound:
  - `binding_ledger.py:18` → `legis.clock.Clock`; `:19` → `legis.enforcement.signing.sign, verify`; `:20` → `legis.identity.entity_key.EntityKey`; `:21` → `legis.store.audit_store.AuditStore`
  - `signoff_binding.py:20` → `enforcement.signing.sign`; `:21` → `filigree.client.FiligreeClient`; `:22` → `governance.binding_ledger.BindingLedger`; `:23` → `identity.entity_key.EntityKey` (intra-cluster edge: signoff_binding → binding_ledger)
  - `sei_backfill.py:14` → `legis.canonical.content_hash`; `:15` → `clock.Clock`; `:16` → `identity.loomweave_client.LoomweaveIdentity`; `:17` → `identity.entity_key.EntityKey`; `:18` → `store.audit_store.AuditRecord, AuditStore`
  - `gaps.py:17` → `legis.canonical.content_hash`; `:18` → `identity.loomweave_client.LoomweaveIdentity`; `:19` → `store.audit_store.AuditRecord`
  - `filigree_gate.py` — none (takes `ledger: Any`, structurally typed)

**Patterns Observed:**
- Fail-closed throughout: integrity failure raises before any data is returned (`binding_ledger.get*` L79/87, `sei_backfill` L58, `filigree_gate` propagates `BindingError`).
- Append-only migration: SEI re-keying never rewrites history; new events reference `original_seq` (`sei_backfill` L97–127, L195–217).
- Prefix-monotonic custody: lineage growth is legitimate, only a broken prefix is tamper (`gaps` L105).
- Pure decision functions separated from I/O (`filigree_gate`).
- Dedicated isolated ledger store so binding rows never pollute the override/gap trail (`binding_ledger` docstring L9–11).

**Concerns:**
- **H5 — RESOLVED.** `BindingLedger.verify()` now invokes `store.verify_integrity()` (binding_ledger.py:60) before the per-record HMAC pass; the prior hash-chain omission is closed.
- **M12 — residual relocated to governance.** M12-as-flagged (enforcement → concrete `AuditStore`) is addressed: enforcement now imports the `AppendOnlyStore` protocol (engine.py:25, protected.py:23, signoff.py:20). The concrete coupling now lives *here*: `binding_ledger.py:21`, `sei_backfill.py:18`, and `gaps.py:19` type against concrete `AuditStore`/`AuditRecord` rather than the protocol — so these modules cannot be unit-tested against a protocol fake. (Concrete *construction* in api/app.py, cli.py, mcp.py is the composition root, not a violation.)
- **M6 propagation (governance impact).** `sei_backfill.run_pre_sei_backfill` (L58) and `binding_ledger.verify` (L60) both branch on `if not store.verify_integrity()`. Because `verify_integrity` can still *raise* on non-finite-float tampering (see Store block), these callers would receive an unexpected `ValueError`/exception instead of a clean `False`/`BindingError` — turning a tamper signal into an uncaught crash.
- **gaps.py null-entity_key crash.** `_stable_seis` (L51) and `find_lineage_integrity` (L75) do `payload.get("entity_key", {}).get(...)`. If a payload contains `"entity_key": null` (explicit), `.get` returns `None` and `.get` raises `AttributeError`. Inconsistent with `sei_backfill._entity_key` (L144) which guards `isinstance(raw, dict)`. Real robustness inconsistency between sibling modules.
- **signoff_binding non-atomic attach→record.** Acknowledged in-code (L64–73): if `ledger.record()` raises after `filigree.attach()` succeeds, Filigree holds a pointer with no local ledger entry; no compensating delete. Surfaced by `verify()`, but a runtime inconsistency window exists.

**Confidence:** High — read all 7 files in full (binding_ledger.py:1–94, signoff_binding.py:1–75, sei_backfill.py:1–260, gaps.py:1–116, filigree_gate.py:1–33, params.py, __init__.py); cross-checked outbound imports against actual `from`-lines and inbound via repo-wide grep; empirically reproduced the M6 propagation path (`json.loads('{"x": Infinity}')` → `content_hash` raises `ValueError`).

---

## Store (persistence)

**Location:** `src/legis/store/`

**Responsibility:** Record-agnostic, append-only, hash-chained SQLAlchemy audit log with DB-level mutation rejection and a structural integrity verifier; plus the `AppendOnlyStore`/`AuditRecordLike` protocols that consumers depend on.

**Key Components:**
- `audit_store.py` (186 lines) — `AuditStore` over SQLAlchemy + `NullPool` (L57). SQLite PRAGMAs (WAL/NORMAL/busy_timeout) via connect listener (L60–71). Append-only enforced by `BEFORE UPDATE`/`BEFORE DELETE` triggers raising `RAISE(ABORT…)` (L88–104); no mutation method exists. `append` (L106): computes `content_hash`, reads last `chain_hash` (genesis if empty), inserts `chain_hash = sha256(prev_hash + content_hash)` under `BEGIN IMMEDIATE` (L110). `verify_integrity` (L161): re-walks chain checking content_hash, prev_hash linkage, and `_chain`. `AuditRecord` frozen dataclass; `read_all`/`read_by_seq`/`get_latest_sequence_and_hash`.
- `protocol.py` (30 lines) — `AuditRecordLike` and `AppendOnlyStore` `Protocol`s (append/read_all/read_by_seq/verify_integrity). This is the abstraction enforcement modules type against.
- `__init__.py` (1 line) — package docstring.

**Dependencies:**
- Inbound:
  - Concrete `AuditStore`: `governance/sei_backfill.py:18`, `governance/binding_ledger.py:21`, `governance/gaps.py:19` (AuditRecord), `api/app.py:318`, `api/app.py:373`, `api/app.py:345` (BindingLedger ctor path), `cli.py:12`, `cli.py:174`, `mcp.py:54`
  - Protocol `AppendOnlyStore`: `enforcement/engine.py:25`, `enforcement/protected.py:23`, `enforcement/signoff.py:20`
- Outbound:
  - `audit_store.py:35` → `legis.canonical.canonical_json, content_hash` (intra-cluster: store → foundations)
  - external: `sqlalchemy`, `hashlib`, `json`
  - `protocol.py` — stdlib `typing`/`collections.abc` only

**Patterns Observed:**
- Two complementary integrity layers: DB triggers (reject in-band mutation) + hash chain (detect out-of-band file tampering) — documented L7–12.
- Record-agnostic boundary: store persists opaque `dict` payloads; schema knowledge lives in `records`/`governance`.
- Protocol-first consumption seam (`protocol.py`) — enforcement layer depends on the abstraction, not the concretion.
- `NullPool` + `BEGIN IMMEDIATE` for clean, lock-minimal append semantics.

**Concerns:**
- **M6 — PARTIALLY closed.** `verify_integrity` wraps `read_all()` in `try/except (JSONDecodeError, TypeError, ValueError)` (L163–166), so decode-time malformed JSON now returns `False` cleanly. BUT the loop body `content_hash(rec.payload)` (L168) is **unguarded**, and `read_all` uses default `json.loads`, which accepts `Infinity`/`NaN` literals. A directly-tampered `payload` column containing `{"x": Infinity}` decodes fine, then `content_hash` → `canonical_json(allow_nan=False)` raises `ValueError` *inside the loop* — propagating out of `verify_integrity` instead of returning `False`. Empirically reproduced. This is exactly the tamper case `verify_integrity` is meant to flag, so the function can crash on the input it exists to defend against.
- **HMAC framing correction.** `AuditStore` itself holds **no HMAC** — it is hash-chain only. HMAC tamper-evidence lives in `enforcement/signing.py` and is applied by `BindingLedger`/protected-verdict callers writing *into* the store; the store persists the signature as just another payload field. The cluster brief's "HMAC for protected records [in store]" is slightly off: the store provides chaining + append-only triggers, not keyed signing.
- **Pragma failures silently swallowed.** The PRAGMA block (L64–69) catches and `pass`es all exceptions, so a WAL/busy_timeout misconfiguration is invisible (no log/observability).

**Confidence:** High — read audit_store.py:1–187 and protocol.py:1–30 in full; traced append/verify chain logic line-by-line; empirically confirmed the M6 raise path (`json.loads('{"x": Infinity}')` decodes to `inf`, `content_hash` raises `ValueError`); inbound/outbound verified by grep against actual import lines.

---

## Records

**Location:** `src/legis/records/`

**Responsibility:** Defines the shared core `OverrideRecord` schema (the chill-cell recordable override) that serializes to a flat dict for the record-agnostic audit store, with judge/HMAC fields attaching via `extensions`.

**Key Components:**
- `override_record.py` (39 lines) — `OverrideRecord` frozen dataclass: `policy`, `entity_key: EntityKey`, `rationale`, `agent_id`, `recorded_at`, `extensions`. `identity_stable` property (L26) delegates to `entity_key`. `to_payload` (L30) emits the canonical flat dict (entity_key via `to_dict()`, copies extensions).
- `__init__.py` (1 line) — package docstring.

**Dependencies:**
- Inbound (all in `enforcement/`):
  - `enforcement/protected.py:22`, `judge_factory.py:12`, `lifecycle.py:18`, `engine.py:24`, `judge.py:17`, `signoff.py:19` → `OverrideRecord`
  - (No governance/store module imports records — records is consumed by enforcement, which writes payloads into the store.)
- Outbound:
  - `override_record.py:14` → `legis.identity.entity_key.EntityKey`

**Patterns Observed:**
- Stable-core / extensible-edge: core schema fixed across the 2×2 cell matrix; Sprint-2 judge and Sprint-3 HMAC fields attach via `extensions` (docstring L1–7).
- Frozen dataclass + explicit `to_payload()` serialization boundary; record never touches the store directly (record → dict → store handoff).
- Identity delegation: `identity_stable` derived from `EntityKey`, single source of truth.

**Concerns:**
- None observed (verified: schema immutability via `frozen=True`; serialization boundary explicit; extensions defensively copied at L38; no I/O, validation, or resource concerns in scope). One note: `to_payload` performs no validation of field types — it trusts construction-time correctness (acceptable for an internal frozen dataclass).

**Confidence:** High — read override_record.py:1–39 and __init__.py in full; all 6 inbound edges confirmed by grep; single outbound (EntityKey) confirmed at L14.

---

## Foundations (canonical + clock)

**Location:** `src/legis/canonical.py`, `src/legis/clock.py`

**Responsibility:** Leaf-level deterministic primitives — canonical JSON + content hashing (the basis of every hash/HMAC in the suite) and an injectable time source for deterministic, test-friendly timestamps.

**Key Components:**
- `canonical.py` (22 lines) — `canonical_json` (L15): `json.dumps` with `sort_keys=True`, tight separators, `ensure_ascii=False`, **`allow_nan=False`**. `content_hash` (L21): sha256 of canonical JSON. Leaf module — no `legis` imports. v1 sorted-key; RFC-8785 convergence explicitly deferred (docstring L1–6, ADR-0001).
- `clock.py` (30 lines) — `Clock` Protocol (`now_iso`), `SystemClock` (UTC ISO via `datetime.now(timezone.utc)`), `FixedClock` (deterministic test injection). Production never calls `datetime.now()` directly.

**Dependencies:**
- Inbound (canonical — foundation layer, many edges):
  - `store/audit_store.py:35` → `canonical_json, content_hash`
  - `enforcement/signing.py:15` → `canonical_json`
  - `governance/sei_backfill.py:14` → `content_hash`
  - `governance/gaps.py:17` → `content_hash`
  - `service/wardline.py:8` → `content_hash`
  - `identity/resolver.py:15` → `content_hash`
  - `mcp.py:19` → `content_hash`
  - `policy/decorator.py:23` → `content_hash`
  - `policy/boundary_scan.py:11` → `content_hash`
- Inbound (clock):
  - `enforcement/protected.py:16`, `enforcement/engine.py:20`, `enforcement/signoff.py:15` → `Clock`
  - `governance/binding_ledger.py:18`, `governance/sei_backfill.py:15` → `Clock`
  - `mcp.py:22`, `cli.py:8`, `api/app.py:317`, `api/app.py:372` → `SystemClock`
- Outbound: none (both are leaf modules; stdlib only — `hashlib`, `json`, `datetime`, `typing`).

**Patterns Observed:**
- Leaf-module discipline: zero intra-`legis` imports, so they sit at the bottom of the dependency DAG (the foundation every hash/HMAC and timestamp resolves to).
- Dependency-injected clock with a deterministic test double (`FixedClock`) — same discipline cited from elspeth.
- Single canonicalization choke point: all content hashing routes through one function, so an RFC-8785 upgrade is a one-file change.

**Concerns:**
- **M13 — PARTIALLY closed.** `canonical_json` already passes `allow_nan=False` (canonical.py:17), so the specific "no `allow_nan=False`" finding is addressed. The broader M13 — full RFC-8785 hardening — remains open and is explicitly deferred (docstring L3–6, ADR-0001). Until then, canonicalization is not interoperable with elspeth's RFC-8785 form and Unicode/number-edge normalization is not guaranteed. Note `ensure_ascii=False` makes byte-output encoding-dependent; the suite consistently `.encode("utf-8")` (audit_store L50, signing L33), so consistent today but a latent footgun if any caller hashes the str differently.
- `clock.py`: no concerns observed (Protocol + two trivial implementations; verified determinism via `FixedClock`).

**Confidence:** High — read canonical.py:1–22 and clock.py:1–30 in full; confirmed `allow_nan=False` present at L17 (refining the prior M13 wording); enumerated all 9 canonical inbound edges and all clock inbound edges by grep against actual import lines.

---

## Cross-cluster note (HMAC location)

The HMAC tamper-evidence layer is **not** in this cluster's store — it lives in `src/legis/enforcement/signing.py` (`sign`/`verify`, versioned `hmac-sha256:v2:`, canonical-JSON v1). `BindingLedger` (governance) and protected-verdict writers apply it and persist the signature as an ordinary payload field. The store provides only hash-chaining + append-only triggers.
