# 04 — Final Report

**Target:** Legis `1.0.0rc2` — the git/CI + governance layer of the Weft suite
**Scope:** `src/legis/` (63 files, ~7,353 LOC), cross-referenced against `tests/`, `docs/`, prior audits, and live tooling
**Method:** 6 parallel codebase-explorer passes along architectural seams + synthesis; tooling run live; two prior read-only audits used as a known-issues baseline
**Date:** 2026-06-06

---

## 1. Executive summary

Legis implements a **governance 2×2** — two agent-set dials (structure: simple/complex; judge: off/on)
yielding four enforcement cells (Chill, Coached, Structured, Protected) — over a tamper-evident,
SEI-keyed audit trail. The codebase is small, disciplined, and architecturally coherent: a clean
dependency DAG with no import cycles, pervasive fail-closed defaults, dependency injection at every
seam, and a single canonicalization/signing choke point. mypy is clean across all 63 files and line
coverage is 90%.

The architecture's organizing idea is sound and largely realized: **Wardline analyses, Legis governs;
Loomweave owns identity, Legis consumes it; Filigree owns issue lifecycle, Legis attests to it.** Every
governance decision produces one append-only hash-chained record, and the protected cell layers HMAC
signing bound to the inspected source.

The dominant *architectural* finding is that the **transport-agnostic service layer (WP-M1) is a partial
seam**. It cleanly owns governance decisions for the HTTP and MCP frontends, but three drifts remain: the
HTTP API reaches *past* its own service layer for sign-off, the CLI bypasses the service entirely (hand-rolling
its own trail-verification and override-rate logic), and the MCP server couples to the HTTP module for shared
constants. The prior audits' dominant theme — **adapter drift, where MCP omitted HTTP/CLI server-side
constraints** — has been **substantially remediated**: all six tracked MCP-drift findings (C2, C3, H1, M9,
M10, M11) are RESOLVED in the current tree. The residual drift is now structural (seam discipline), not a
live security bypass.

The remaining *security-relevant* findings cluster around **evidence binding and authentication of inputs**:
protected records for non-`.py` entities sign an `unverified` source binding; check/PR facts are recorded on
the writer's word; the Filigree transport is unsigned; the LLM judge parses model output as gate authority (a
prompt-injection surface in coached/protected); and the writer/operator scope split is enforced only in
`TOKEN_ACTORS` mode, not in single-secret mode (its severity hinges on whether single-secret is a supported
split-promising production mode — see §5/§6). None of these block the rc, but each is a sharp edge an
architect should schedule before GA.

**Overall assessment: a well-built, honest, internally consistent rc.** The bones are good. The work ahead
is seam-tightening and input-authentication hardening, not rearchitecture.

---

## 2. Subsystem map

13 subsystems + a foundations pair, in a 7-layer DAG (full catalog in `02`, diagrams in `03`):

| Layer | Modules | Role |
|---|---|---|
| L0 (leaves) | `canonical`, `clock`, `identity.entity_key`, `identity.loomweave_client`, `filigree.client`, `git.*`, `checks`, `pulls`, `governance.params` | primitives + leaf integration surfaces |
| L1 | `identity.resolver`, `records`, `store`, `policy` | resolution, schema, persistence, grammar |
| L2 | `enforcement` | the 2×2 engine + judge + protected/signoff/lifecycle |
| L3 | `governance`, `wardline` | binding/backfill/gaps; scan-to-cell routing |
| L4 | `service` | transport-agnostic decision layer (WP-M1) |
| L5–L7 | `api`, `mcp`, `cli` | three frontends |

**Largest / hottest modules:** `policy` (1072 LOC) and `enforcement` (1062 LOC) carry the domain weight;
`api/app.py` (830) and `mcp.py` (~1123) are the dense frontends. `identity`, `canonical`, and `clock` are
the most-depended-upon foundations (14 / 9 / many inbound edges respectively).

---

## 3. Cross-subsystem flows (the wiring that *is* the product)

A bottom-up catalog under-serves a system whose value is the *combination* of its parts. These four
end-to-end traces are the load-bearing paths.

### 3.1 Agent override → graded cell → tamper-evident record (the core loop)

```
agent → [frontend: api POST /overrides | mcp override_submit | (cli is gate-only)]
      → service.governance.submit_override / submit_protected_override / request_signoff
      → service.resolve_for_record → identity.resolver.resolve(locator)
            → Loomweave (HMAC/HTTPS): SEI-keyed EntityKey + alive + content_hash + lineage_snapshot,
              or honest locator-keyed degradation
      → policy.cells.cell_for(policy) selects the 2×2 cell
      → cell dispatch:
          chill     → enforcement.engine.submit_override(judge=None)        → record ACCEPTED_SELF
          coached   → enforcement.engine.submit_override(judge=LLMJudge)    → judge BEFORE write
          structured→ enforcement.signoff.SignoffGate.request               → PENDING_SIGNOFF (does not clear)
          protected → enforcement.protected.ProtectedGate.submit            → judge + HMAC sign + source-binding
      → store.audit_store.append → content_hash → chain_hash = sha256(prev + content_hash)
```

Every branch terminates in exactly one append-only record on the same hash chain. The cell is chosen
**server-side** from policy config, never from caller input — the anti-downgrade guarantee. The chill cell's
"recordable override" is what makes *humans-not-in-the-loop* safe: an attributable event, never a silent pass.

### 3.2 Wardline finding → governance cell (the "Wardline + Legis" combination)

```
Wardline scan payload → [api POST /wardline/scan-results | mcp scan_route]
   → service.wardline.route_wardline_scan
   → wardline.ingest.verify_wardline_artifact(scan, artifact_key?)   # HMAC provenance IF key configured
   → wardline.ingest.active_defects        # kind==defect & suppressed==active; agent-suppressed needs proof
   → wardline.governor.route_findings      # exactly one of policy|cell_map; rejects block_escalate∪surface_* batch
        per finding: resolve(qualname) → EntityKey ; build `wardline` ext (fingerprint, properties verbatim)
        dispatch → signoff.request | engine.submit_override | engine.record_event
```

This is the unification of two vocabularies into one: Wardline's trust tiers ride **verbatim** into the
record (`properties` write-only), and Legis decides the cell. **Routing ownership is server-side** on both
frontends now (the C2 fix). The seam's weak spot is **intra-store batch non-atomicity** (M3): a multi-finding
same-cell batch is N sequential appends with no surrounding transaction.

### 3.3 Sign-off → SEI-keyed Filigree binding (the "Filigree + Legis" combination)

```
operator → api POST /signoff/{seq}/sign  (operator scope)  → SignoffGate.sign_off → SIGNED_OFF record
agent    → api POST /signoff/{seq}/bind-issue
   → governance.signoff_binding.bind_signoff_to_issue
        guard: reject identity_stable=False (locator) keys   # avoids rename-orphan
      → filigree.client.attach(entity_id=SEI, content_hash, signature)   # UNSIGNED transport
      → governance.binding_ledger.record (signed, dedicated AuditStore)  # non-atomic vs attach
   later: api GET /filigree/issues/{id}/closure-gate
      → governance.filigree_gate.evaluate_issue_closure(ledger)          # closable only w/ verified binding
```

The binding survives rename because it keys on SEI. The structural consequence (M4): **binding availability
is coupled to Loomweave SEI capability** — when Loomweave is degraded the sign-off can be *recorded* but
cannot be *bound*. And the Filigree HTTP channel itself is unauthenticated (the `signature` is an app-level
attestation, not transport auth).

### 3.4 The override-rate CI gate — same decision, three implementations

```
api  GET /governance/override-rate → service.compute_override_rate(service.verified_records(...))   ✅ via service
mcp  override_rate_get             → service.compute_override_rate(_verified_records(...))           ✅ via service
cli  governance-gate               → AuditStore.read_all() + own TrailVerifier + inline evaluate_override_rate  ❌ bypass
```

This is the cleanest illustration of the partial-seam finding: the *same governance computation* is reached
three ways, and the CLI's hand-rolled copy already required a divergent fix (`07cf54e`, "fail closed on
protected override-rate trails") that the service path got for free.

---

## 4. Architectural strengths

1. **Clean DAG, no cycles.** Enforcement depends on neither governance nor policy; the dependency arrows all point downward to leaves. A genuine layered architecture, not a ball of mud.
2. **Fail-closed as a default discipline.** Unregistered policy → UNKNOWN; no judge provider → `FailClosedJudge` (always BLOCKED); malformed config → error not false-green; ambiguous judge output → BLOCKED. The system's resting state is "deny."
3. **Single-source-of-truth choke points.** One `canonical_json`/`content_hash` underlies every hash and HMAC; `signing_fields()` is shared by signer and verifier so they cannot drift; `evidence.py` is shared by the runtime gate and the static scanner.
4. **Dependency injection everywhere.** Store, clock, judge, LLM transport, identity, forge-PR source — all injected Protocols. The only non-test concretes are the HTTP clients. Highly testable (90% coverage, mypy-clean).
5. **Honest degradation.** Identity resolution distinguishes "not alive" (`False`) from "no capability" (`None`); the rename feed distinguishes "found" from "checked." The system tells the truth about what it doesn't know.
6. **Config-owned trust boundary.** The protected-policy set and override-rate constants live in config (ADR-0002), not in the records they govern — a record cannot declare itself unprotected.

---

## 5. Architectural concerns (consolidated; detail + remediation in `05`/`06`)

| Theme | Finding | Severity |
|---|---|---|
| Seam discipline | Service layer is a partial seam: api reaches past it (sign-off), cli bypasses it entirely, mcp couples to api for constants | High (architectural) |
| Input authentication | Writer/operator scope split enforced only in `TOKEN_ACTORS` mode; single-secret mode does not separate them | High *if* single-secret is a split-promising prod mode, else Medium (§5 calibration) |
| Evidence binding | Protected records for non-`.py` entities sign `source_binding: unverified` (M1) | Medium |
| Input authentication | Check/PR facts recorded on the writer's word, no fact provenance (M2) | Medium |
| Input authentication | Filigree transport unsigned (asymmetric vs signed Loomweave) | Medium |
| Tamper handling | `verify_integrity` can *raise* on non-finite-float tampering instead of returning `False` (M6) | Medium |
| Prompt injection | LLM judge parses model output as gate authority; untrusted rationale embedded (H3 baseline) | Medium |
| Atomicity | Intra-store Wardline batch non-atomicity (M3); non-atomic Filigree attach→record (M4-adjacent) | Medium |
| Robustness | `gaps.py` null-`entity_key` `AttributeError`; `decay_sweep` aborts whole sweep on one bad row | Low–Med |
| Default-open | In-code default cell is self-clearing `chill` (H6); only `cells.toml` makes it `structured` | Medium |
| Honesty gate | Policy-co-occurrence check is substring-in-assert, not semantic (M7) | Low–Med |
| Coupling | Governance modules type against concrete `AuditStore`, not the protocol (M12 residual) | Low |

---

## 6. Remediation delta since the 2026-06-04 audits

The two prior audits (3 Critical, 7 High, 14 Medium, 5 Low) are a moving baseline. Confirmed deltas:

| Prior finding | Status now | Evidence |
|---|---|---|
| C1 CI gate passes on absent trail | **Mostly closed** | `07cf54e` + `8b15320` — CLI fails closed under `CI=true`/missing-trail unless `LEGIS_ALLOW_MISSING_GOVERNANCE_DB` |
| C2 MCP caller-chosen routing | **RESOLVED** | `mcp.py` server-owned routing guard mirrors HTTP |
| C3 MCP skips HMAC trail verify | **RESOLVED** | `_verified_records` → `service.verified_records` → `TrailVerifier` |
| H1 MCP skips artifact HMAC | **RESOLVED** | `scan_route` passes `artifact_key` |
| H5 BindingLedger skips chain integrity | **RESOLVED** | `verify()` calls `store.verify_integrity()` first |
| H7 unscoped tokens grant operator | **Mitigated** | rejected unless `LEGIS_ALLOW_UNSCOPED_API_TOKENS=1` |
| M9 unknown MCP args accepted | **RESOLVED** | `_validate_argument_keys` |
| M10 poll_handle type mismatch | **RESOLVED** | both integer |
| M11 MCP no idempotency | **RESOLVED** | `b4285dc` request-hash replay |
| M12 enforcement → concrete store | **Partially** | enforcement uses protocol; governance still concrete |
| M13 no `allow_nan` | **Partially** | `allow_nan=False` present; RFC-8785 still deferred |
| M5 EntityKey coerces stability | **Not reproduced** | `from_dict` validates `bool` |
| M1/M2/M3/M4/M7/H3/H6 | **Confirmed live** (M3/M4 refined) | see `05` |

**New findings surfaced this pass (not in prior audits):** `gaps.py` null-`entity_key` `AttributeError`;
unsigned Filigree transport asymmetry; CLI service-layer bypass as the third drift vector. (Two clarifications
from a post-validation cross-check of *both* prior audits: M6 — the unguarded `content_hash` in the verify
loop — is a *prior-audit* finding, re-confirmed here as only partially closed, not new. And **Q-H1**
(single-secret writer/operator split) is a *sharpening/localization* of the readonly audit's scope-separation
finding (AUDIT-readonly §High, lines 166-188), not a net-new discovery; its severity is conditional — see §5.)

---

## 7. Confidence & limitations

**Confidence: High** on structure, edges, and finding locations — every subsystem read at 100% by its cluster
pass, every dependency edge grepped with `file:line`, mypy/coverage run live, and each prior-audit finding
discriminated against current source (several empirically reproduced).

**Limitations:**
- The Loomweave / Wardline / Filigree **wire contracts are taken from docstrings and Legis-side clients**, not the sibling repos. Cross-repo conformance (the live oracle test) is opt-in and not exercised here.
- Runtime behavior of injected concretes defined outside a cluster (e.g. an exotic LLM provider) was not executed.
- No tests were run beyond the existing coverage artifact; this is a static + tooling analysis, not a dynamic audit.
- The two prior audits' *severity* judgments were accepted as framing; this pass re-verified *presence*, not re-scored severity from scratch.

`05-quality-assessment.md` quantifies the quality signals; `06-architect-handover.md` sequences the remediation.
