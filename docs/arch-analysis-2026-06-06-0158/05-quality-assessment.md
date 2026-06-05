# 05 — Code Quality Assessment

Quantitative signals run live against the working tree (HEAD `2e69141`), combined with the
finding inventory from the six cluster passes and the two prior read-only audits.

---

## 1. Tooling signals (measured this pass)

| Signal | Result | Notes |
|---|---|---|
| **mypy** (`mypy src/legis`) | ✅ **Clean** — "no issues found in 63 source files" | strict-ish config (`warn_unused_configs`, `show_error_codes`) |
| **ruff** (`ruff check src/`) | ⚠️ **2 errors** — both `F401` unused import (`Hashable` in `policy/grammar.py:15`; one more) | auto-fixable; **ruff is NOT in CI** |
| **Line coverage** | ✅ **90%** (3,453 stmts, 329 missed) | high for a governance codebase |
| **Tests** | **492 test functions across 68 files** | unit + contract + conformance + mcp lanes |
| **pytest warnings** | `filterwarnings = ["error", ...]` | warnings are errors (one scoped Starlette ignore) |

### Coverage by subsystem (security-critical paths are well covered)

| Subsystem | Cov | | Subsystem | Cov |
|---|---|---|---|---|
| `records` | 100% | | `store` | 90% |
| `pulls` | 98% | | `api` | 90% |
| `git` | 97% | | `policy` | 88% |
| `checks` | 97% | | `(root: cli+mcp+canonical+clock)` | 85% |
| `identity` | 95% | | **`filigree`** | **75%** ← lowest |
| `enforcement` | 95% | | | |
| `service` | 94% | | | |
| `governance` | 93% | | | |
| `wardline` | 91% | | | |

The two heaviest single files drag the "root" bucket: `mcp.py` 82%, and `cli.py`'s gate paths.
`filigree/client.py` at 75% is the weakest — and it is also the **unsigned transport** surface, so its
uncovered branches are exactly the error/transport paths a security reviewer cares about.

---

## 2. CI pipeline review (`.github/workflows/ci.yml`)

The pipeline is unusually governance-aware — it runs the project's own gates as CI steps:

| Step | Assessment |
|---|---|
| `pytest --cov=legis --cov-fail-under=70` | ✅ runs tests + coverage… ⚠️ **threshold 70% while actual is 90%** — 20 points of silent-regression headroom (prior **M14**, still live) |
| SEI conformance oracle (`test_sei_oracle.py`) | ✅ always runs |
| Live Loomweave oracle | ⚠️ **gated on `vars.LOOMWEAVE_URL != ''`** — opt-in; absent var = silently skipped (prior **M14**) |
| `mypy src/legis` | ✅ enforced |
| `legis policy-boundary-check` | ✅ the honesty gate runs in CI (good — dogfoods its own grammar) |
| `legis governance-gate --db sqlite:///legis-governance.db` | ✅ override-rate gate; now fails closed under `CI=true`/missing-trail (prior **C1**, mostly closed by `07cf54e`/`8b15320`) |

**Gaps:** (1) **no ruff/lint step** — the 2 F401 errors prove lint isn't gating; (2) **coverage threshold (70%) far below reality (90%)** — should be raised, ideally with per-package floors for `enforcement`/`service`/`governance`/`api`/`mcp`; (3) live cross-repo conformance is opt-in, so Loomweave endpoint/header drift passes default CI.

---

## 3. Finding inventory (current tree)

Severity reflects this pass's re-verification, not the prior audits' original scores. "Status" reconciles
against the 2026-06-04 baseline.

### High

| ID | Finding | Location | Status |
|---|---|---|---|
| **Q-H1** | **Single-secret mode does not enforce the writer/operator scope split** — `_verify_secret` returns the actor on a `LEGIS_API_SECRET` match without consulting `required_scope` (`:116`); operator-only routes (`/protected/operator-override` `:559`, `/signoff/{seq}/sign` `:677`) are satisfied by any holder of the single secret. **Severity is conditional (see calibration note).** | `api/app.py:103,108-116` | Sharpens AUDIT-readonly scope-separation finding (§High, lines 166-188); the specific single-secret mechanism is newly localized |
| **Q-H2** | **Service layer is a partial seam** — `api` reaches past it for sign-off (`SignoffGate` direct, inline trail-verify); `cli` bypasses it entirely (hand-rolled `verified_records` + `compute_override_rate`); `mcp` couples to `api` for `DEFAULT_*_DB` constants | `api/app.py:588,605-618,680`; `cli.py:170-244`; `mcp.py:115,496,505` | Architectural; partly NEW |
| **Q-H3** | **LLM judge parses model output as gate authority** with untrusted rationale embedded as text — prompt-injection surface in coached/protected | `enforcement/judge.py` | Baseline H3, confirmed (mitigated by structured-JSON-first + BLOCKED-wins, but advisory-as-authority remains) |

> **Q-H1 severity calibration.** The writer/operator split is a *promised, tested* contract **only in `LEGIS_API_TOKEN_ACTORS` mode** — `tests/api/test_auth.py:100` (`test_scoped_tokens_separate_writer_and_operator_authority`) asserts a writer token gets 403 on `/protected/operator-override` while an operator token succeeds. **No test asserts single-secret mode denies operator routes**; `test_mutating_routes_require_secret_when_configured` (`:91`) only checks that the secret gates *write access*. So single-secret (`LEGIS_API_SECRET` alone) is, as built, a *one-credential* mode that does not offer the split. **Severity therefore depends on a product decision** (carried to `06`): if single-secret is a supported production mode that *promises* operator separation → **High, GA-blocking**; if single-secret means "solo/one-credential deployment" → this is a **Medium documentation-and-gate** item (label the limitation; require `TOKEN_ACTORS` or an explicit operator credential for any deployment relying on the split). This analysis does **not** assert High unconditionally.

### Medium

| ID | Finding | Location | Status |
|---|---|---|---|
| **Q-M1** | Protected records for **non-`.py` entities sign `source_binding: unverified`** | unverified-return `service/source_binding.py:46-53`; fail-closed guard skips non-`.py` `:82-89`; signed at `service/governance.py:170` | Baseline M1, confirmed |
| **Q-M2** | **Check/PR facts recorded on the writer's word** — no fact provenance/signature | `api/app.py:448,466`; `checks/surface.py`; `pulls/surface.py` | Baseline M2, confirmed |
| **Q-M3** | **`verify_integrity` can raise** (`ValueError`) on non-finite-float tampering instead of returning `False` — unguarded `content_hash(rec.payload)` in the verify loop; propagates into `sei_backfill`/`binding_ledger.verify` | `store/audit_store.py:168` | Baseline M6, PARTIALLY closed |
| **Q-M4** | **Filigree transport unsigned** (asymmetric vs HMAC-signed Loomweave); `attach` `signature` is app-level only | `filigree/client.py` | NEW (audit noted binding non-atomicity, not transport) |
| **Q-M5** | **Intra-store Wardline batch non-atomicity** — N sequential appends, no transaction; mid-loop failure persists earlier findings | `wardline/governor.py:60-65` | Baseline M3, refined |
| **Q-M6** | **Filigree binding availability coupled to Loomweave SEI capability** — degraded seam silently removes the binding surface for locator-keyed sign-offs | `governance/signoff_binding.py:38-42` | Baseline M4, confirmed |
| **Q-M7** | **In-code default cell is self-clearing `chill`** — fails open if `cells.toml` (`structured`) is absent | `policy/cells.py:44`; `mcp.py:111` | Baseline H6, confirmed |
| **Q-M8** | **Honesty-gate policy-co-occurrence is a substring-in-assert match**, not a semantic check that the boundary *result* is asserted | `policy/evidence.py:46-53,135-152` | Baseline M7, confirmed |

### Low

| ID | Finding | Location | Status |
|---|---|---|---|
| **Q-L1** | `gaps.py` raises `AttributeError` on explicit `"entity_key": null` (no `isinstance(dict)` guard; inconsistent with `sei_backfill`) | `governance/gaps.py:51,75` | NEW |
| **Q-L2** | `decay_sweep` has no per-record try/except — one malformed `entity_key` row aborts the whole sweep | `enforcement/lifecycle.py:55-62` | NEW |
| **Q-L3** | Governance modules type against **concrete `AuditStore`**, not the protocol (can't fake in unit tests) | `governance/{binding_ledger,sei_backfill,gaps}.py` | Baseline M12, residual relocated |
| **Q-L4** | Canonicalization not RFC-8785 hardened (cross-language verify); `ensure_ascii=False` byte-encoding footgun | `canonical.py` | Baseline M13, partially closed |
| **Q-L5** | Fingerprint extraction diverges between runtime gate and static scanner for class-method/decorated test_refs | `decorator.py:125-135` vs `boundary_scan.py:156-159` | Baseline L4, confirmed |
| **Q-L6** | Identity capability cache per-instance, never invalidated once `True` | `identity/resolver.py:42-48` | NEW |
| **Q-L7** | 2× `F401` unused imports; lint not in CI | `policy/grammar.py:15` + 1 | NEW (tooling) |
| **Q-L8** | `mcp.py` `call_tool` is a 464-stmt single if/elif; hand-rolled JSON-RPC has no stdin line-size bound | `mcp.py` | NEW (maintainability) |

---

## 4. Maintainability & design-quality observations

**Strengths (these are real and worth preserving):**
- **Testability is designed-in.** DI at every seam + Protocol-typed dependencies → 90% coverage and clean mypy are *consequences* of the architecture, not bolt-ons.
- **The fail-closed default** is consistent enough to be a property of the system, not a per-site choice.
- **Single choke points** (`canonical`, `signing_fields`, `evidence`) mean security-relevant changes touch one place.
- **Honest naming and docstrings.** Modules document their own trade-offs (e.g. the non-atomic attach→record window is admitted in-code, not hidden).

**Debt / friction:**
- **Seam erosion** (Q-H2) is the highest-leverage maintainability debt: three implementations of "read the verified trail," already proven to diverge under fixes.
- **`mcp.py` size** (~1123 lines, 464-stmt dispatch) is the single-file complexity hotspot.
- **Concrete-store coupling in governance** (Q-L3) is the residual of an otherwise-completed protocol migration.
- **Lint not gating** lets trivial debt (unused imports) accumulate.

---

## 5. Quality verdict

**Grade: B+ / strong rc.** The codebase is well-engineered for its stage: clean types, high coverage,
governance-aware CI, disciplined fail-closed defaults, and a real layered architecture. The recent fix
velocity (six adapter-drift findings closed, C1/H5/M11 closed) shows an active, responsive maintenance loop.

What separates it from an A is **input-authentication hardening** (Q-M1, Q-M2, Q-M4 — the system trusts
several inputs it records as governance evidence; plus Q-H1's single-secret split *if* that mode is meant to
promise it) and **seam discipline** (Q-H2 — the service layer must become the *only* way to reach a governance
decision). Neither is a rearchitecture; both are scheduling decisions for the path to GA. See
`06-architect-handover.md`.
