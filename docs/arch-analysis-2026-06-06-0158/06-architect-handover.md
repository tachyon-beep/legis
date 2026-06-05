# 06 — Architect Handover

Transition document from *analysis* to *improvement planning*. Sequences the findings from
`05-quality-assessment.md` into a risk-ordered roadmap with concrete entry points, and frames the
open architectural decisions an architect must own before GA.

**Starting position:** Legis `1.0.0rc2` — a well-built rc (B+). Clean DAG, mypy-clean, 90% coverage,
governance-aware CI, active fix loop. The work here is **hardening + seam discipline**, not rearchitecture.

---

## 1. The one architectural decision to make first

**Decide what the service layer is *for*, then enforce it.**

Today `service/` (WP-M1) is a *partial* seam: it owns governance decisions for `api` and `mcp`, but
`api` reaches past it (sign-off), `cli` ignores it, and `mcp` couples to `api`. The override-rate gate
exists in **three** implementations (§3.4 of `04`), and that duplication already caused a divergent fix
(`07cf54e`). This is the root cause behind a whole class of future drift.

**The decision:** is the service layer the *single mandatory path* to every governance decision, or just
a convenience library two of three frontends happen to use? The architecture only pays off under the first
reading. Recommend ratifying **"every governance decision flows through `service/`; frontends are thin
adapters that translate transport ↔ `ServiceError`"** as an explicit invariant, then closing the three
drifts to match. Everything in Tier 1 below assumes this choice.

---

## 2. Risk-ordered roadmap

### Tier 1 — Before GA (security + the seam invariant)

| # | Item | Entry point | Effort | Rationale |
|---|---|---|---|---|
| 1 | **Resolve single-secret scope split** (Q-H1) — *decision-gated.* The writer/operator split is tested only in `TOKEN_ACTORS` mode (`tests/api/test_auth.py:100`); single-secret mode does not separate them, and **no test promises it should**. **First decide (checklist item 2): is single-secret a supported split-promising production mode?** If **yes** → make `_verify_secret` consult `required_scope` so a single secret cannot satisfy `operator`; require an explicit operator credential (or opt-in `LEGIS_ALLOW_SINGLE_SECRET_OPERATOR=1` for dev) — **GA-blocking**. If **no** → document the limitation (single-secret = one-credential mode; use `TOKEN_ACTORS` for the split) and consider failing closed on operator routes without an operator-scoped credential — **not GA-blocking**. | `api/app.py:103,108-116` | S | Severity hinges on the product decision, not the code (which the validator confirmed). Don't ship the High framing unconditionally. |
| 2 | **Make `service/` the only path to a governance decision** (Q-H2). Route `api` sign-off through `service.request_signoff`/a new `service.sign_off`; replace the inline trail-verify block with `service.verified_records`; rebuild `cli`'s `_check_override_rate` on `service.compute_override_rate(service.verified_records(...))`. | `api/app.py:588,605-618,680`; `cli.py:170-244` | M | Collapses three trail-read implementations to one; kills the drift class at the source. |
| 3 | **Decide the protected source-binding contract** (Q-M1). Either fail closed unless `source_binding.status == "verified"` for source-code policies, or add server-side entity classification so the caller's locator shape can't choose the verification standard. | `service/source_binding.py:82-89`; `service/governance.py:163` | S–M | A protected record can be signed while not bound to current source bytes — "protected" ≠ "source verified." |
| 4 | **Harden `verify_integrity` to never raise** (Q-M3). Guard the loop-body `content_hash(rec.payload)` (catch `ValueError` → return `False`, or raise a domain `AuditIntegrityError`). Align api/cli/mcp error mapping. Add a non-finite-float tamper regression. | `store/audit_store.py:168` | S | The function can crash on exactly the tamper input it exists to detect; propagates into backfill/binding verify. |
| 5 | **Authenticate or quarantine recorded facts** (Q-M2, Q-M4). Split writer authority from forge-reporter authority; require signed webhook/HMAC envelope over check/PR facts, or mark them `provenance: unauthenticated` so consumers can't mistake them for governance evidence. Sign the Filigree transport (Weft-component HMAC) to match Loomweave. | `api/app.py:448,466`; `filigree/client.py` | M | Closes the "trust the writer's word" surface; removes the signed/unsigned asymmetry across suite seams. |

### Tier 2 — Soon after GA (robustness + correctness)

| # | Item | Entry point | Effort |
|---|---|---|---|
| 6 | **Production-default the policy cell to fail closed** (Q-M7). Make the in-code default `structured` (or a dedicated `unknown` cell), so an absent `cells.toml` can't silently downgrade to self-clear `chill`. | `policy/cells.py:44`; `mcp.py:111` | S |
| 7 | **Atomic Wardline batches** (Q-M5). Wrap `route_findings`' per-finding appends in one transaction, or record a scan-level batch envelope with per-finding status. | `wardline/governor.py:60-65` | M |
| 8 | **Robustness guards** (Q-L1, Q-L2). `isinstance(dict)` guard in `gaps.py`; per-record try/except in `decay_sweep` so one bad row doesn't abort the sweep. | `gaps.py:51,75`; `lifecycle.py:55-62` | S |
| 9 | **Strengthen the honesty gate** (Q-M8). Make the policy-co-occurrence check semantic — the boundary *result* must be the assertion subject, not a substring in a message. | `policy/evidence.py:135-152` | M |
| 10 | **Couple governance to the store protocol** (Q-L3). Type `binding_ledger`/`sei_backfill`/`gaps` against `AppendOnlyStore`, finishing the M12 migration so they're unit-testable against a fake. | `governance/*.py` | S |

### Tier 3 — Maturity (process + maintainability)

| # | Item | Entry point | Effort |
|---|---|---|---|
| 11 | **Raise the CI coverage floor** to ~88% global with per-package floors for `enforcement`/`service`/`governance`/`api`/`mcp`; **add ruff as a gating step**. | `.github/workflows/ci.yml:19`; `pyproject.toml` | S |
| 12 | **Make cross-repo conformance non-optional** for releases — a scheduled/pre-release live Loomweave job so endpoint/header drift can't pass default CI. | `ci.yml:22-28` | S |
| 13 | **Lift `filigree/client.py` coverage** (75% → parity) — the uncovered branches are the transport/error paths (ties to item 5). | `tests/filigree/` | S |
| 14 | **Tame `mcp.py`** — table-driven `call_tool` dispatch; bound the stdin JSON-RPC line size; lift the `DEFAULT_*_DB` constants into a shared config module (removes the `mcp -> api` edge). | `mcp.py` | M |
| 15 | **RFC-8785 canonicalization** (Q-L4) when cross-language verification is needed; reconcile the gate/scanner fingerprint extraction (Q-L5). | `canonical.py`; `decorator.py`/`boundary_scan.py` | M |
| 16 | **Reduce the LLM-judge attack surface** (Q-H3) — require non-LLM validation (or operator sign-off) for `ACCEPTED` in protected policies; treat the model as advisory, never sole gate authority. | `enforcement/judge.py`, `engine.py` | M |

---

## 3. What NOT to do

- **Don't rearchitect.** The DAG is clean, the layering is real, the choke points are correct. Resist the urge to "improve" the structure; the structure is the strength. Every Tier-1/2 item is a local change.
- **Don't add a config knob per finding.** Several findings exist because a dev-affordance (single secret, `chill` default, unsafe routing flag) leaks into production posture. Prefer *fail-closed defaults with an explicit opt-in flag* over new always-on configuration.
- **Don't trust the prior audits' severities verbatim.** Six of their findings are already fixed; this handover reflects the *current* tree. Re-verify before acting on any 2026-06-04 line not reconciled in `04 §6`.
- **Don't let `mcp.py` keep absorbing surface area** without the table-driven refactor (item 14) — it's the one file whose complexity is trending the wrong way.

---

## 4. Suggested sequencing

```
Sprint A (GA-blocking):   items 3, 4 (+ item 1 IF the checklist decision makes it GA-blocking)
Sprint B (GA-blocking):   item 2          (the seam invariant — the structural fix; do after A so it's not entangled)
Sprint C (GA-blocking):   item 5          (fact authentication + Filigree signing)
Sprint D (post-GA):       items 6–10      (robustness + fail-closed defaults; item 1's document-and-gate path lands here if not GA-blocking)
Sprint E (maturity):      items 11–16     (CI floors, mcp refactor, RFC-8785, judge hardening)
```

Items 3, 4 are small, independent security quick wins — a single focused sprint. Item 1's placement is
**decided by checklist item 2** (is single-secret split-promising?): GA-blocking in Sprint A if yes, a
document-and-gate task in Sprint D if no. Item 2 is the structural keystone and should land on its own so the
trail-read consolidation isn't tangled with security edits. Items 5 and 16 both touch suite-seam trust and
benefit from a Wardline/Loomweave/Filigree contract review alongside.

---

## 5. Handover checklist for the receiving architect

- [ ] Ratify (or reject) the **service-layer-is-mandatory** invariant (§1). Everything in Tier 1 assumes it.
- [ ] Confirm the **single-secret deployment** assumption — is single-secret a supported production mode? If yes, item 1 is GA-blocking; if it's dev-only, document that and gate it.
- [ ] Decide the **protected source-binding policy** for non-`.py` entities (item 3) — is a non-source protected policy a valid concept, or should those fail closed?
- [ ] Decide whether **check/PR facts** are governance-authoritative or operational-only (item 5) — this determines whether they need provenance or just a clear "unauthenticated" label.
- [ ] Schedule a **cross-repo contract review** with Loomweave/Wardline/Filigree owners (the wire contracts here are Legis-side only).
- [ ] Set the **CI coverage floor** and add lint (item 11) — cheap, immediate, prevents regression of the quality this analysis measured.

---

*Inputs to this handover: `01`–`05` of this analysis set, the two 2026-06-04 read-only audits
(`temp/AUDIT-*.md`, recovered from HEAD), and live mypy/ruff/coverage runs. All findings carry `file:line`
evidence in `02` and `05`.*
