## Enforcement Engine
**Location:** `src/legis/enforcement/`
**Responsibility:** Grades a policy firing through the governance 2×2 (simple/complex × judge off/on), writing exactly one append-only, hash-chained audit record per submission and — in the protected cell — binding each verdict to its inspected source with an HMAC signature plus lifecycle gates (decay re-judge + override-rate).

**Key Components:**
- `engine.py` (115 LOC) — `EnforcementEngine.submit_override`: the simple-tier chill/coached cells. `judge=None` → chill (record accepted as-is); `judge` present → coached (judge evaluates *before* write; verdict + model + rationale stamped into `extensions`, `accepted = verdict is ACCEPTED`). Also `trail()`, `records()`, `record_event()` (raw governance events e.g. UNKNOWN_POLICY). `EnforcementResult` dataclass.
- `verdict.py` (28 LOC) — shared value types: `Verdict` str-enum (ACCEPTED / BLOCKED / OVERRIDDEN_BY_OPERATOR), `SignoffState` str-enum (PENDING_SIGNOFF / SIGNED_OFF), `JudgeOpinion` dataclass (verdict, model, rationale).
- `judge.py` (111 LOC) — `Judge`/`LLMClient` Protocols; `LLMJudge` (structured-JSON-first, fail-closed). `build_prompt` frames request data as untrusted input. `parse_verdict` / `_parse_structured_response`: BLOCKED wins on any ambiguity; legacy free-text parse only behind `allow_legacy_text`.
- `judge_factory.py` (31 LOC) — `build_judge_from_env`: wires `OpenRouterLLMClient` from env, else returns `FailClosedJudge` (always BLOCKED) when no provider configured. Surface-scoped fallback rationale.
- `llm_client.py` (168 LOC) — deployable `OpenRouterLLMClient` + `llm_client_config_from_env`. SSRF/transport hardening: HTTPS-or-loopback-only base URL, no-redirect opener, 1 MB response cap, strict response-shape validation, `LLMTransportError` on any malformed reply. Injectable `Fetch` seam for tests.
- `protected.py` (288 LOC) — the protected cell. `ProtectedGate.submit` (judge-gated) / `operator_override` (human bypass → OVERRIDDEN_BY_OPERATOR, no model). Every record HMAC-signed via `signing_fields()` (single source of the signed dict, binds entity+policy+source fingerprint+ast_path+loomweave lineage). `TrailVerifier.verify`: load-time signature check; protected-policy set comes from config (ADR-0002) not the record, so a flag-flip can't downgrade. `legacy_signing_fields` for v1 records. `TamperError`.
- `signoff.py` (151 LOC) — `SignoffGate`: structured/protected block+escalate, **no LLM in path**. `request` records PENDING_SIGNOFF (does NOT clear); `sign_off` records SIGNED_OFF referencing `request_seq` + `request_payload_hash` and clears. Optional `signer`+`key` → tamper-bound signed sign-off via `signoff_signing_fields`. `is_cleared` / `request_record` scan the trail.
- `lifecycle.py` (122 LOC) — protected-cell lifecycle gates over the read-only trail. `decay_sweep`: re-judges only judge-ACCEPTED suppressions (strips prior decision fields before re-judging), flags any that no longer pass. `evaluate_override_rate`: `OVERRIDDEN_BY_OPERATOR / (ACCEPTED+OVERRIDDEN_BY_OPERATOR)` over recent `window`; `PASS`/`FAIL`/`PASS_WITH_NOTICE` (small-sample). `GateStatus`, `GateResult`, `DecayFlag`.
- `signing.py` (47 LOC) — keyed HMAC-SHA256 tamper-evidence over `canonical_json(fields)`. Versioned prefixes (`v2` default, `v1` legacy). `sign` / `verify` (verify accepts v2 or v1; `compare_digest` constant-time).
- `__init__.py` (1 LOC) — package docstring only.

**Dependencies:**
- Inbound:
  - `legis.service.governance` -> enforcement — imports EnforcementEngine/EnforcementResult, evaluate_override_rate, ProtectedGate/ProtectedResult/TamperError, SignoffGate/SignoffResult (`src/legis/service/governance.py:14-17`)
  - `legis.service.wardline` -> enforcement — EnforcementEngine, SignoffGate (`src/legis/service/wardline.py:9-10`)
  - `legis.service.explain` -> enforcement — EnforcementEngine (`src/legis/service/explain.py:8`)
  - `legis.mcp` -> enforcement — EnforcementEngine, build_judge_from_env, ProtectedGate/TrailVerifier/TamperError, SignoffGate, SignoffState/Verdict (`src/legis/mcp.py:23-27`)
  - `legis.api.app` -> enforcement — EnforcementEngine, ProtectedGate/TamperError/TrailVerifier, SignoffGate, build_judge_from_env (`src/legis/api/app.py:31-33,325,333-334,341`)
  - `legis.cli` -> enforcement — GateStatus/evaluate_override_rate, TrailVerifier/TamperError (`src/legis/cli.py:172,228`)
  - `legis.wardline.governor` -> enforcement — EnforcementEngine, SignoffGate (`src/legis/wardline/governor.py:33-34`)
  - `legis.wardline.ingest` -> enforcement — signing.verify (`src/legis/wardline/ingest.py:14`)
  - `legis.governance.signoff_binding` -> enforcement — signing.sign (`src/legis/governance/signoff_binding.py:20`)
  - `legis.governance.binding_ledger` -> enforcement — signing.sign, signing.verify (`src/legis/governance/binding_ledger.py:19`)
- Outbound:
  - enforcement -> `legis.clock` (Clock) — engine.py:20, protected.py:16, signoff.py:15
  - enforcement -> `legis.identity.entity_key` (EntityKey) — engine.py:23, protected.py:21, signoff.py:18, lifecycle.py:17
  - enforcement -> `legis.records.override_record` (OverrideRecord) — engine.py:24, judge.py:17, judge_factory.py:12, protected.py:22, signoff.py:19, lifecycle.py:18
  - enforcement -> `legis.store.protocol` (AppendOnlyStore) — engine.py:25, protected.py:23, signoff.py:20
  - enforcement -> `legis.canonical` (canonical_json, content_hash) — signing.py:15, signoff.py:14
  - NOTE: cluster does NOT import `legis.governance` or `legis.policy` — those depend on enforcement, not vice versa (one-directional, clean).

**Patterns Observed:**
- Dependency injection / ports-and-adapters: store (`AppendOnlyStore` protocol), `Clock`, `Judge` and `LLMClient` are all injected Protocols; the only non-test concrete is `OpenRouterLLMClient`. The chill/coached distinction is literally a single nullable `judge` arg (engine.py:42,70).
- Single-source-of-signed-fields: `signing_fields` / `signoff_signing_fields` are called by both the writing gate and the reading `TrailVerifier`, so signer and verifier cannot drift (protected.py:40,206,150; signoff.py:29,81,138).
- Fail-closed everywhere: unreadable/ambiguous judge output → BLOCKED (judge.py:40,106); unconfigured provider → `FailClosedJudge` (judge_factory.py:30); structurally malformed protected record → `TamperError` (protected.py:151).
- Append-only single trail: every submission, every governance event, and every sign-off step is one immutable hash-chained record; no silent path (engine.py:12 docstring, record_event).
- Config-driven trust boundary: protected-policy set lives in config not the record (ADR-0002), preventing flag-flip downgrade (protected.py:96-102).
- Layered verdict provenance: simple verdicts stamp extensions; protected layers HMAC over the same extensions; lifecycle reads the trail read-only without re-writing.
- Security-hardened egress: HTTPS/loopback-only, no-redirect, size-capped, shape-validated LLM transport (llm_client.py:76-129).

**Concerns:**
- Verifier coupling to `extensions` shape: `TrailVerifier._requires_verification` keys off in-record markers (`file_fingerprint`, `ast_path`, `protected_cell`, signature presence) in *addition* to the config protected set (protected.py:112-121). The config set is the authoritative anti-downgrade guard, but the OR-with-record-markers means a record that omits both the protected policy and all markers is treated as unprotected — correct only if the config protected-policy set is always complete/current. Coupling between signing-field layout and verifier is implicit (dict-shape, not a typed schema).
- Dual signing-field functions (`signing_fields` vs `legacy_signing_fields`, v1/v2 prefixes) create a migration surface: `verify` tries v2 then falls back to legacy v1 fields (protected.py:155-159), widening the accept set during the legacy window. Acceptable as transitional but worth a deprecation/removal milestone.
- `EntityKey.from_dict(p["entity_key"])` in `decay_sweep` and `sign_off` will `KeyError`/raise on a malformed historical record; decay_sweep has no per-record try/except, so one bad row aborts the whole sweep (lifecycle.py:55-62). The protected write path guards this (TamperError) but the lifecycle read path does not.
- `evaluate_override_rate` and `decay_sweep` silently include/exclude records by `judge_verdict` extension presence; a protected record missing that key is simply skipped — denominator/sweep coverage depends on upstream always stamping it.
- HMAC key lifecycle (rotation, provenance) is out of cluster scope — `key: bytes` is injected; no rotation/versioned-key support visible here (signing.py only versions the algorithm, not the key).
- `record_event` (engine.py:107) bypasses the judge/verdict path entirely for raw events; if a protected-policy event were routed here it would not be signed — relies on callers not misusing it.

**Confidence:** High — Read all 12 files in `src/legis/enforcement/` end-to-end (engine.py 115, protected.py 288, signoff.py 151, lifecycle.py 122, judge.py 111, llm_client.py 168, judge_factory.py 31, signing.py 47, verdict.py 28, __init__.py 1; judge_factory.py and llm_client.py are mode 0600 but readable). Outbound edges cross-verified by `grep -n '^from legis\.'` over the cluster (5 distinct targets, zero governance/policy imports). Inbound edges grepped across `src/` with file:line for all 10 importing modules. The only uncertainty is runtime behaviour of injected concretes defined outside the cluster (store impls, Clock, EntityKey internals), which were not read.
