# Comprehensive Read-Only Codebase Audit

Date: 2026-06-04  
Repository: `/home/john/legis`  
Mode: Strict read-only audit of codebase behavior. The only repository write performed by the coordinator is this requested markdown artifact.

## Method

Seven specialist subagents were dispatched with read-only instructions and explicit prompts to avoid write tools, MCP/connector tools, and escaped double quotes in tool arguments. The available subagent API did not expose literal `enable_write_tools=false` or `enable_mcp_tools=false` fields, so those constraints were enforced through the `explorer` role and prompt instructions.

Specialist lanes completed:

- Architecture Critic
- Systems Thinker
- Python Engineer
- Quality Engineer
- Security Architect
- Static Tools Analyst
- MCP and CLI Specialist

No tests, formatters, or mutating commands were run. Local coordinator inspection used read-only shell commands. There is no shipped `scanner/ast_primitives.py`, `scanner/rules/`, PY-WL-101..111 rule implementation, trust-lattice implementation, SCC implementation, or Tarjan implementation in the current tree; those terms appear in roadmap/planning material and test fixtures, not shipped source.

## Executive Summary

The highest-risk theme is adapter drift: HTTP and MCP expose overlapping governance capabilities, but MCP omits several server-side constraints present in HTTP/CLI. In particular, MCP can route Wardline scan results using caller-selected cells, skip Wardline artifact HMAC verification, and read protected governance trails without the HMAC verification that HTTP/CLI perform.

The second major theme is evidence loss or weak binding in governance records. Decay re-judging drops the original source/Clarion context, signed sign-off approvals do not bind the full request evidence, protected source binding can remain `unverified` while still producing a signed protected record, and the CI governance gate can pass with no governance trail artifact at all.

## Critical Findings

### C1. CI governance gate passes when the governance trail is absent

Locations:

- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:134) lines 134-140
- [.github/workflows/ci.yml](/home/john/legis/.github/workflows/ci.yml:23) lines 23-33
- [.gitignore](/home/john/legis/.gitignore:13) lines 13-14
- [tests/test_cli.py](/home/john/legis/tests/test_cli.py:92) lines 92-97

Evidence:

`governance-gate` returns success with `PASS_WITH_NOTICE` when the SQLite DB does not exist. The CI job runs `uv run legis governance-gate --db sqlite:///legis-governance.db`, while `*.db` is ignored and the workflow does not create or download that database.

Impact:

In a fresh CI checkout, the governance gate can be a successful no-op. The project receives a clean CI signal even when there is no override-rate trail to evaluate.

Remediation:

Add an explicit CI mode that fails closed on a missing or empty governance trail. For example, change `_check_override_rate()` to return non-zero when the DB is absent unless a local-development flag such as `--allow-missing-trail` or `LEGIS_ALLOW_MISSING_GOVERNANCE_DB=1` is set. Update CI to either produce/download the expected trail artifact or fail before evaluating override rate. Replace `test_governance_gate_missing_sqlite_db_is_pass_with_notice_without_creating_it` with separate dev-mode and CI-mode tests.

### C2. MCP Wardline routing lets the governed agent choose the governance cell

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:213) lines 213-226
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:623) lines 623-665
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:693) lines 693-720
- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:120) lines 120-130

Evidence:

The HTTP API rejects request-owned Wardline routing unless server routing is configured or `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING=1`. MCP `scan_route` accepts `cell`, `severity_map`, and `fail_on` directly from tool arguments and routes with those values.

Impact:

An agent using MCP can downgrade a severe scan finding to a softer path such as `surface_only`, bypassing the server-owned escalation policy that HTTP enforces.

Remediation:

Move Wardline routing ownership into a shared service helper used by both HTTP and MCP. Load `LEGIS_WARDLINE_CELL` and `LEGIS_WARDLINE_CELL_BY_SEVERITY` in the MCP runtime. Reject caller-provided routing unless an explicit unsafe dev flag is set. Add MCP tests matching the HTTP server-owned routing tests, including rejection of request-owned `cell`, `severity_map`, and `fail_on` by default.

### C3. MCP protected-trail reads skip HMAC verification

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:452) lines 452-472
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:734) lines 734-743
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:123) lines 123-163
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:78) lines 78-88
- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:149) lines 149-161

Evidence:

MCP `_verified_records()` checks the unkeyed audit hash chain via `verify_integrity()` but never constructs or calls `TrailVerifier`. HTTP and CLI both have HMAC verification paths for protected records.

Impact:

An attacker with DB-file access can edit a protected record, recompute the unkeyed chain, and have MCP read/scoring tools such as `override_rate_get` consume the forged record. HTTP/CLI would fail closed.

Remediation:

Add `trail_verifier` to `McpRuntime` and build it from `LEGIS_HMAC_KEY` plus `LEGIS_PROTECTED_POLICIES`. Replace MCP-local `_verified_records()` with the shared `service.governance.verified_records()` path or equivalent HMAC verification. Add an MCP regression that mutates a signed protected record, recomputes the hash chain, and asserts `AUDIT_INTEGRITY_FAILURE`.

## High Findings

### H1. MCP skips configured Wardline artifact HMAC verification

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:655) lines 655-664
- [src/legis/service/wardline.py](/home/john/legis/src/legis/service/wardline.py:24) lines 24-36
- [src/legis/wardline/ingest.py](/home/john/legis/src/legis/wardline/ingest.py:67) lines 67-107
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:751) lines 751-765

Evidence:

The shared Wardline service can enforce `artifact_key`, and HTTP passes `LEGIS_WARDLINE_ARTIFACT_KEY`. MCP calls `route_wardline_scan()` without an artifact key, so signed-artifact enforcement is bypassed.

Impact:

A forged or provenance-stripped Wardline scan can enter governance through MCP even when HTTP deployment policy requires authenticated scanner artifacts.

Remediation:

Add `wardline_artifact_key` to `McpRuntime`, populated from `LEGIS_WARDLINE_ARTIFACT_KEY`, and pass it to `route_wardline_scan()`. Map `WardlinePayloadError` to an explicit tool error. Add MCP tests for unsigned rejection and signed acceptance with verified provenance.

### H2. Decay re-judging drops source and Clarion context

Locations:

- [src/legis/enforcement/lifecycle.py](/home/john/legis/src/legis/enforcement/lifecycle.py:36) lines 36-43
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:229) lines 229-242
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:151) lines 151-165

Evidence:

`decay_sweep()` reconstructs an `OverrideRecord` with only policy, entity, rationale, agent, and time. It omits original extensions such as `file_fingerprint`, `ast_path`, `source_binding`, Clarion content hash, and lineage snapshot.

Impact:

Renewal decisions can be made on less evidence than the original protected decision. A judge can re-accept or re-block based on incomplete context.

Remediation:

Preserve a sanitized copy of original evidence extensions for decay re-judging. Exclude prior verdict and signature fields, but include source binding, file fingerprint, AST path, Wardline provenance, Clarion content hash, and lineage snapshot. Add a regression with a judge that asserts those fields are present during decay.

### H3. LLM judge verdict is prompt-injectable through untrusted rationale

Locations:

- [src/legis/enforcement/judge.py](/home/john/legis/src/legis/enforcement/judge.py:52) lines 52-76
- [src/legis/enforcement/engine.py](/home/john/legis/src/legis/enforcement/engine.py:80) lines 80-97

Evidence:

The judge prompt embeds attacker-controlled rationale directly into text and parses the model's first-line verdict as the authoritative decision. Accepted model output becomes an accepted governance override.

Impact:

In coached/protected deployments with a real LLM judge, a malicious rationale can attempt prompt injection to force `ACCEPTED`. In protected mode, the resulting compromised judgment can then be HMAC-signed as tamper-evident evidence.

Remediation:

Treat the model as advisory for high-stakes protected decisions unless backed by deterministic checks or operator sign-off. Use structured output with strict schema validation. Encode user rationale as data, add prompt-injection regression cases, and require non-LLM validation for `ACCEPTED` in protected policies.

### H4. Signed sign-off approvals do not bind the original request evidence

Locations:

- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:28) lines 28-43
- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:93) lines 93-99
- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:110) lines 110-119

Evidence:

Pending sign-off requests can carry Clarion/Wardline extensions. The later `SIGNED_OFF` record includes only `signoff_state` and `request_seq`, yet `signoff_signing_fields()` is designed to include Clarion evidence when present.

Impact:

The signed approval row proves an operator signed a sequence number, but the signature does not directly cover the evidence context from the original request.

Remediation:

Before signing the `SIGNED_OFF` record, bind it to the original request by adding either a canonical request payload hash or a copied immutable evidence block. Include that hash/evidence in `signoff_signing_fields()` and verify it on reads. Add tamper tests that modify the request evidence after sign-off.

### H5. Binding ledger verification omits append-only hash-chain integrity

Locations:

- [src/legis/governance/binding_ledger.py](/home/john/legis/src/legis/governance/binding_ledger.py:59) lines 59-75
- [src/legis/governance/binding_ledger.py](/home/john/legis/src/legis/governance/binding_ledger.py:76) lines 76-82
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:161) lines 161-171
- [src/legis/governance/signoff_binding.py](/home/john/legis/src/legis/governance/signoff_binding.py:54) lines 54-73

Evidence:

`BindingLedger.verify()` validates per-record HMACs but does not call `AuditStore.verify_integrity()`. A deleted binding record can therefore degrade to “no binding” instead of an integrity failure.

Impact:

Legis can silently lose the local tamper-bound binding leg after Filigree has already accepted an association.

Remediation:

Make `BindingLedger.verify()` fail if the underlying audit store hash chain fails. Add deletion, reorder, and rechaining tamper tests. Consider a reconciliation command that compares Filigree associations with local binding ledger entries.

### H6. Unmatched policies default to self-clear behavior

Locations:

- [policy/cells.toml](/home/john/legis/policy/cells.toml:5) lines 5-13
- [src/legis/policy/cells.py](/home/john/legis/src/legis/policy/cells.py:33) lines 33-40
- [src/legis/policy/cells.py](/home/john/legis/src/legis/policy/cells.py:43) lines 43-44
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:504) lines 504-523

Evidence:

Default policy routing is `chill`; unmatched policies fall through to the default cell. MCP `override_submit` treats chill as `ACCEPTED_SELF`.

Impact:

A typo, missing registry entry, or incomplete policy deployment silently downgrades governance to self-clear.

Remediation:

Introduce a production default that fails closed, such as an `unknown` or `structured` cell. Require explicit policy matches for `override_submit` in production mode. Keep `chill` only for local/dev registries, and add tests for unknown policy behavior.

### H7. Unscoped API token mappings grant operator authority

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:59) lines 59-85
- [tests/api/test_complex_api.py](/home/john/legis/tests/api/test_complex_api.py:110) lines 110-122

Evidence:

The token parser enforces scopes only when the actor spec contains a colon. An entry such as `op-a=token-a` returns `op-a` for any required scope, and a test confirms it can perform a protected operator override.

Impact:

A token intended for writer authority can accidentally become an all-scope token, crossing the operator boundary.

Remediation:

Reject unscoped `LEGIS_API_TOKEN_ACTORS` entries by default. Require `actor:writer=...`, `actor:operator=...`, or an explicit `actor:*=...` syntax gated behind `LEGIS_ALLOW_UNSCOPED_API_TOKENS=1` for compatibility. Add startup or first-request validation tests.

## Medium Findings

### M1. Protected source binding can be unverified while the record is still signed

Locations:

- [src/legis/service/source_binding.py](/home/john/legis/src/legis/service/source_binding.py:45) lines 45-66
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:151) lines 151-164
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:183) lines 183-197
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:65) lines 65-77

Evidence:

For non-Python locators, missing `source_root`, or missing source files, source binding returns `status: unverified`. Protected submission signs and records that status, but does not require verification.

Impact:

A protected record can be validly signed while not actually bound to current source bytes. The caveat is preserved, but downstream readers may equate “protected” with “source verified.”

Remediation:

For source-code policies, fail closed unless `source_binding.status == "verified"`. If non-source protected policies are valid, add server-side policy/entity classification so the caller’s entity-string shape cannot choose the verification standard.

### M2. CI/check and recorded PR surfaces accept writer-supplied facts without provenance

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:406) lines 406-430
- [src/legis/checks/surface.py](/home/john/legis/src/legis/checks/surface.py:50) lines 50-65
- [src/legis/pulls/surface.py](/home/john/legis/src/legis/pulls/surface.py:27) lines 27-39

Evidence:

`POST /checks` and `POST /git/pulls` record caller-supplied operational facts. Check facts are inserted without signature/webhook proof, and PR metadata is delete-and-replace.

Impact:

A compromised writer token can record fake passing CI or rewrite PR metadata. If future gates depend on these surfaces, this becomes a policy bypass.

Remediation:

Split writer authority from CI/forge reporter authority. Require signed webhook ingestion, forge API verification, or an HMAC envelope over check/PR facts. Store PR/check changes as append-only provenance events and expose trust status to readers.

### M3. Mixed Wardline batches are not atomic across governance stores

Locations:

- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:58) lines 58-64
- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:88) lines 88-130

Evidence:

The code comment explicitly states successful mixed batches can span engine and signoff stores, and mid-loop runtime failure leaves prior writes permanently persisted.

Impact:

A scan can produce a partial governance picture where early findings are recorded and later findings disappear.

Remediation:

Record a scan-level batch envelope with per-finding statuses, or route through an outbox/reconciliation process. Add tests where the second finding fails after the first write would succeed, and assert either all-or-nothing behavior or explicit partial-failure records.

### M4. Locator-keyed sign-offs can later fail rename-stable Filigree binding

Locations:

- [src/legis/identity/resolver.py](/home/john/legis/src/legis/identity/resolver.py:66) line 66
- [src/legis/governance/signoff_binding.py](/home/john/legis/src/legis/governance/signoff_binding.py:38) lines 38-42
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:587) lines 587-601
- [src/legis/governance/sei_backfill.py](/home/john/legis/src/legis/governance/sei_backfill.py:44) line 44

Evidence:

Identity degradation can create locator-keyed records. Filigree binding rejects locator keys. The bind endpoint uses the original sign-off request’s entity key, while backfill appends separate events rather than rewriting originals.

Impact:

A temporary Clarion outage can permanently block a later issue binding, even after backfill, unless bind-time lookup accounts for backfill records.

Remediation:

For policies that require Filigree binding, fail closed when stable identity is unavailable. Alternatively, teach bind-time lookup to resolve through backfill events and document the rebinding contract.

### M5. `EntityKey.from_dict()` coerces malformed stability values to true

Locations:

- [src/legis/identity/entity_key.py](/home/john/legis/src/legis/identity/entity_key.py:33) lines 33-34
- [src/legis/governance/gaps.py](/home/john/legis/src/legis/governance/gaps.py:48) lines 48-54

Evidence:

`EntityKey.from_dict()` uses `bool(d["identity_stable"])`, so a string like `"false"` becomes `True`.

Impact:

Malformed decoded payloads can be treated as stable SEI-keyed identities, affecting lineage/gap logic and binding decisions.

Remediation:

Validate that `value` is a non-empty string and `identity_stable` is exactly a `bool`. Raise `ValueError` for anything else. Add malformed payload tests.

### M6. Audit integrity verification can raise decode exceptions instead of a controlled integrity failure

Locations:

- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:130) lines 130-144
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:161) lines 161-171
- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:142) lines 142-145

Evidence:

`verify_integrity()` iterates `read_all()`, which JSON-decodes every payload before integrity checks. Malformed JSON can raise before returning `False`.

Impact:

Tampering can bypass the documented boolean integrity-failure path and produce inconsistent API/MCP/CLI errors.

Remediation:

Make `verify_integrity()` read raw rows or catch `json.JSONDecodeError` and return `False` or raise a domain `AuditIntegrityError`. Align HTTP, CLI, and MCP error mapping around that domain error.

### M7. Policy-boundary AST honesty gate can accept weak or shadowed evidence

Locations:

- [src/legis/policy/decorator.py](/home/john/legis/src/legis/policy/decorator.py:198) lines 198-212
- [src/legis/policy/decorator.py](/home/john/legis/src/legis/policy/decorator.py:214) lines 214-228
- [tests/policy/test_honesty_gate.py](/home/john/legis/tests/policy/test_honesty_gate.py:10) line 10

Evidence:

The gate walks the entire test AST and accepts any call whose name/attribute matches the boundary function, plus any string/name reference to a suppressed policy. It does not resolve bindings or prove the assertion is tied to the call result.

Impact:

A test can satisfy the gate without proving boundary behavior, especially with local shadows, helper calls, or tautological string references.

Remediation:

Make traversal scope-aware. Ignore nested functions/classes unless explicitly targeted. Resolve the call to the decorated function binding, and require an assertion or exception path connected to the call result and suppressed policy.

### M8. Test suite autouse fixture enables unsafe auth and unsafe Wardline routing globally

Locations:

- [tests/conftest.py](/home/john/legis/tests/conftest.py:18) lines 18-22
- [tests/api/test_auth.py](/home/john/legis/tests/api/test_auth.py:45) lines 45-90

Evidence:

Every test starts with `LEGIS_UNSAFE_DEV_AUTH=1` and `LEGIS_UNSAFE_WARDLINE_REQUEST_ROUTING=1`. Auth coverage relies on a manually maintained route matrix.

Impact:

New mutating routes can be tested under unsafe mode and omitted from the auth matrix.

Remediation:

Replace the autouse unsafe fixture with explicit unsafe-client fixtures. Add route-introspection tests asserting every mutating route denies unauthenticated writes by default.

### M9. MCP tool schemas claim `additionalProperties: false` but unknown arguments are accepted

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:155) lines 155-161
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:300) lines 300-309
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:488) lines 488-514
- [tests/mcp/test_server.py](/home/john/legis/tests/mcp/test_server.py:164) lines 164-170

Evidence:

Schemas reject additional properties, but dispatch does not enforce allowed key sets. Tests show `agent_id` can be supplied and ignored.

Impact:

The structural “launch-bound identity only” invariant is weaker than the schema suggests. Future sensitive fields could be silently accepted.

Remediation:

Validate arguments against each tool schema before dispatch and reject unexpected keys with `INVALID_ARGUMENT`. Add tests for unknown fields on every mutating MCP tool.

### M10. MCP sign-off poll handle has a type mismatch

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:199) lines 199-202
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:328) lines 328-336
- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:552) lines 552-553
- [tests/mcp/test_server.py](/home/john/legis/tests/mcp/test_server.py:319) lines 319-321

Evidence:

`override_submit` returns `poll_handle` as an integer, but `signoff_status_get` declares and requires `seq` as a string.

Impact:

An agent mechanically passing the advertised handle into the advertised poll tool gets an invalid argument error.

Remediation:

Make `seq` an integer schema and accept integers, or return `poll_handle` as a string. Add a round-trip MCP test using the returned handle without manual conversion.

### M11. MCP `override_submit` has no idempotency protection

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:181) lines 181-186
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:106) lines 106-133
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:200) lines 200-219

Evidence:

`override_submit` and sign-off request flows are side-effecting, but the tool schema has no idempotency key and the description does not warn about retry duplication.

Impact:

Host or agent retries after timeout can duplicate audit records or human sign-off requests.

Remediation:

Add an idempotency key to side-effecting MCP tools, stored in extensions and de-duplicated before append. If v1 intentionally excludes idempotency, update tool descriptions to warn that retries create new records and return correlation data for clients.

### M12. Core enforcement modules depend directly on the SQLAlchemy-backed audit store

Locations:

- [src/legis/enforcement/engine.py](/home/john/legis/src/legis/enforcement/engine.py:25) line 25
- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:19) line 19
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:23) line 23
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:22) lines 22-33

Evidence:

Domain gates import concrete `AuditStore`, which imports SQLAlchemy and creates schema in its constructor.

Impact:

The enforcement layer is coupled to persistence and database lifecycle. This weakens package boundaries and undermines “dependency-free core” expectations.

Remediation:

Define a minimal audit-log protocol for append/read/verify behavior. Depend on that protocol in enforcement modules, and keep SQLAlchemy inside `store.audit_store`.

### M13. Protected signing canonicalization is not hardened for cross-version/cross-language guarantees

Locations:

- [src/legis/canonical.py](/home/john/legis/src/legis/canonical.py:3) lines 3-5
- [src/legis/canonical.py](/home/john/legis/src/legis/canonical.py:15) lines 15-18
- [src/legis/enforcement/signing.py](/home/john/legis/src/legis/enforcement/signing.py:30) lines 30-34

Evidence:

The code comments state RFC 8785 is future hardening before protected cryptographic guarantees ship. Current signing uses `json.dumps()` without `allow_nan=False`.

Impact:

Signatures are deterministic in this Python process, but less robust for cross-language verification and can encode non-standard `NaN`/`Infinity` values if they appear in `Any` extensions.

Remediation:

Introduce a versioned canonicalizer based on RFC 8785 or a documented strict subset. Reject non-standard JSON values with `allow_nan=False`. Keep compatibility verification for existing signatures.

### M14. Critical-path coverage and live Clarion conformance are not enforced in default CI

Locations:

- [pyproject.toml](/home/john/legis/pyproject.toml:19) lines 19-23
- [.github/workflows/ci.yml](/home/john/legis/.github/workflows/ci.yml:18) lines 18-21
- [tests/conformance/test_live_clarion_oracle.py](/home/john/legis/tests/conformance/test_live_clarion_oracle.py:16) line 16

Evidence:

CI runs pytest and mypy, but there is no coverage dependency, branch coverage threshold, or required live Clarion job. The live Clarion oracle is opt-in.

Impact:

Security/governance behavior can regress without a measurable coverage signal, and Clarion endpoint/header drift can pass default CI.

Remediation:

Add coverage tooling with branch thresholds for `api`, `mcp`, `service`, `enforcement`, `governance`, and `wardline`. Add a scheduled or pre-release live Clarion job with `CLARION_URL`, locator fixture, and HMAC credentials.

## Low Findings

### L1. MCP protocol lifecycle handling is permissive and version-pinned

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:750) lines 750-776

Evidence:

`handle_request()` does not validate `jsonrpc`, initialize params, or initialized lifecycle state, and hardcodes protocol version `2024-11-05`.

Impact:

Newer MCP clients may negotiate unexpectedly or proceed through malformed protocol use.

Remediation:

Validate initialize params, track initialized state before normal operations, and negotiate/echo a supported requested protocol version where possible.

### L2. MCP tool errors lack recovery metadata

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:274) lines 274-298

Evidence:

Tool errors return only `error_code` and `message`.

Impact:

Agents cannot reliably distinguish retryable, user-fixable, and stop-and-escalate failures without hardcoded knowledge.

Remediation:

Add stable fields such as `category`, `retryable`, and `recovery`, preserving existing `error_code`.

### L3. MCP runtime construction can create local state for read-oriented use

Locations:

- [src/legis/mcp.py](/home/john/legis/src/legis/mcp.py:101) lines 101-152
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:85) lines 85-86

Evidence:

`build_runtime()` constructs stores during startup, and `AuditStore.__init__()` creates tables/triggers.

Impact:

Starting an MCP process for read tools can create DB files, making “trail exists” ambiguous for other logic.

Remediation:

Split open-existing read handles from write-capable initializing handles. Make DB creation explicit in server/write mode.

### L4. Indented test sources can fail policy-boundary AST parsing inconsistently

Locations:

- [src/legis/policy/decorator.py](/home/john/legis/src/legis/policy/decorator.py:187) lines 187-196

Evidence:

`fingerprint()` dedents source before parsing elsewhere, but `check_policy_boundary()` reparses `inspect.getsource(test_fn)` without dedenting.

Impact:

Nested/local test functions can fingerprint successfully but fail the later AST heuristic.

Remediation:

Apply the same `textwrap.dedent()` and newline normalization in the second parse path.

### L5. Runtime bytecode artifacts exist in the working tree

Locations:

- `/home/john/legis/src/legis/**/__pycache__/`
- `/home/john/legis/tests/**/__pycache__/`

Evidence:

`find` shows `__pycache__` and `.pyc` files under `src/` and `tests/`. `git ls-files '*__pycache__*' '*.pyc'` returned no tracked files, so this is working-tree hygiene rather than tracked-source corruption.

Impact:

Generated artifacts can pollute review context and packaging if ignore rules regress.

Remediation:

Clean local bytecode artifacts before releases and keep `.gitignore` enforcement in place. Consider a CI cleanliness check if release packaging consumes the working tree.

## Cross-Cutting Notes

### Static Analysis Scope

The requested scanner-specific items are not present in shipped source:

- No `scanner/ast_primitives.py`
- No `scanner/rules/`
- No local PY-WL-101..111 rule implementations
- No local taint propagation lattice
- No SCC/Tarjan implementation

Closest shipped components are:

- `wardline/ingest.py`: validates external Wardline scan payloads and trust-tier names.
- `wardline/governor.py`: routes external findings into governance cells.
- `policy/decorator.py`: performs AST-based policy-boundary evidence checks.
- `service/source_binding.py`: verifies current source fingerprints for recognized relative Python locators.

### Main Trust Boundaries

- HTTP clients to FastAPI: mutating routes use bearer auth, with writer/operator split weakened by unscoped token entries.
- MCP host/agent to stdio JSON-RPC: identity is launch-bound, but MCP skips several HTTP/CLI enforcement checks.
- Wardline scan payloads to governance: HTTP can enforce signed artifacts; MCP currently cannot.
- LLM judge to enforcement: model output is parsed as gate authority when a judge is wired.
- Clarion to identity resolver: HTTPS is required except loopback or explicit insecure override; HMAC headers are used when key material exists.
- Filigree binding: binding tuples can be HMAC-signed, but local ledger and remote attach are not transactional.
- SQLite governance store: hash-chain plus HMAC for protected records, but MCP and binding ledger do not consistently apply all verification layers.

## Prioritized Remediation Plan

1. Fail closed in CI when the governance DB is missing, or explicitly provision the trail artifact before `governance-gate`.
2. Make MCP use the same Wardline routing ownership and artifact HMAC verification as HTTP.
3. Add protected-trail HMAC verification to MCP reads and regression-test rechained tampering.
4. Bind sign-off approval signatures to the original request evidence and fix decay re-judging to preserve source/Clarion context.
5. Require explicit API token scopes and fail closed for unknown production policies.
6. Decide whether protected source-code policies require `source_binding.status == "verified"` and enforce that decision server-side.
7. Harden binding ledger integrity, audit-store malformed JSON handling, and `EntityKey.from_dict()` validation.
8. Add idempotency and stricter argument validation for side-effecting MCP tools.
9. Replace global unsafe test fixtures with explicit fixtures and add route-introspection auth tests.
10. Add coverage thresholds and a scheduled/pre-release live Clarion conformance job.

## Verification Limits

This was a read-only audit. No tests were run, no formatters were run, and no application servers were started. Findings are based on source inspection by seven specialized agents plus coordinator validation of cited code locations.
