# Legis Read-Only Codebase Audit

Date: 2026-06-04

Repository: `/home/john/legis`

Mode: strictly read-only audit of source/test/config surfaces. The only write performed was creation of this requested markdown artifact.

## Method

Seven specialized read-only subagents reviewed the codebase:

- Architecture Critic
- Systems Thinker
- Python Engineer
- Quality Engineer
- Security Architect
- Static Tools Analyst
- MCP and CLI Specialist

All subagents were instructed to operate with `enable_write_tools=false` and `enable_mcp_tools=false`, avoid write-generating commands, and avoid MCP tools. No test suite, mypy, formatter, server, or package build was run because those can create caches, sqlite files, or other artifacts.

## Scope Notes

- `src/legis/scanner/ast_primitives.py`, `src/legis/scanner/rules/`, PY-WL-101..111 rule implementations, SCC/Tarjan logic, and a trust-lattice engine are not present in this repository. The closest live surfaces are Wardline finding ingestion/routing, policy decorator/grammar, check facts, and governance records.
- A YAML policy surface is not present. The closest implementation is TOML exemption loading via `tomllib`.
- An MCP server implementation is not present. The repository has a transport-agnostic service layer and design notes for a future MCP adapter, but no stdio JSON-RPC server or MCP tool registry.

## Executive Summary

The highest risks are concentrated at trust boundaries where Legis records governance facts from request-body data supplied by the actor being governed. The main pattern is: caller-provided static-analysis payloads, caller-provided routing choices, caller-provided source bindings, and caller-provided identities become audit evidence without enough independent validation.

The protected-cell cryptographic story also has material gaps. Some fields that readers will treat as audit evidence are not HMAC-bound, protected verification skips one malformed record class, and protected sign-off binding can use unsigned Loomweave metadata.

No source code was changed during this audit.

## Findings By Severity

### Critical

#### C1. Wardline governance can be bypassed or distorted by caller-shaped scan and routing input

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:110) lines 110-114
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:517) lines 517-560
- [src/legis/wardline/ingest.py](/home/john/legis/src/legis/wardline/ingest.py:45) lines 45-65
- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:76) lines 76-88

Evidence:

- `ScanResultsIn.scan` is an untyped `dict`.
- `/wardline/scan-results` accepts `cell` or `cell_by_severity` from the same request that supplies the scan.
- `active_defects()` trusts `kind` and `suppressed` fields and drops anything not `kind == "defect"` and `suppressed == "active"`.
- `WardlineFinding.from_wire()` indexes required fields directly, so malformed payloads can become uncaught `KeyError` or `TypeError` instead of controlled validation failures.
- Per-severity routing defaults unmapped severities to `SURFACE_OVERRIDE`.

Impact:

A caller can omit a finding, mark it suppressed, change its kind/severity, choose softer routing, or submit malformed data that crashes the endpoint. For a governance system, that means critical findings can disappear or become soft audit events without an independently verifiable Wardline artifact.

Remediation:

1. Replace `ScanResultsIn.scan: dict` with typed Pydantic models for scan, finding, severity, suppression, rule id, fingerprint, qualname, and properties.
2. Make routing policy server-owned. Request bodies should not decide whether a finding is `surface_only`, `surface_override`, or `block_escalate` unless the caller has an explicitly authenticated policy-management scope.
3. Require a signed, hash-pinned, or otherwise authenticated Wardline artifact with scanner identity, commit/tree identity, rule-set version, finding count, active count, and suppression proof.
4. Record a raw scan digest and filtered-count provenance in every routed batch.
5. Treat unknown or unsupported suppression states as fail-closed, either rejected or recorded as a provenance gap.
6. Require total `cell_by_severity` mappings or an explicit configured default. Do not silently map omitted severities to `SURFACE_OVERRIDE`.

Acceptance tests:

- Posting a CRITICAL finding with `suppressed: "waived"` and no suppression proof must not disappear; it should reject or create a provenance-gap/block-escalate record.
- Posting `findings: "bad"`, a missing `rule_id`, and severity `BOGUS` should return 422 and write no governance record.
- A request attempting to route CRITICAL to `surface_only` contrary to server policy should be rejected or overridden by server policy.
- A partial severity map containing only `CRITICAL: block_escalate` plus an ERROR finding must not silently route the ERROR to `surface_override`.

#### C2. Protected verdicts sign caller-supplied source bindings that the judge never evaluated

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:75) lines 75-81
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:343) lines 343-355
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:48) lines 48-59
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:183) lines 183-201

Evidence:

- `ProtectedIn` accepts `file_fingerprint` and `ast_path` from the request body.
- `ProtectedGate.submit()` builds the `OverrideRecord` sent to the judge without `file_fingerprint`, `ast_path`, or Loomweave extension context.
- `_record_signed()` later signs those fields into the stored payload.

Impact:

The HMAC proves Legis wrote a record containing those source-binding fields, but it does not prove the judge evaluated that source node or source bytes. A caller can bind a judge verdict to a different AST path or fingerprint and create misleading cryptographic audit evidence.

Remediation:

1. Stop treating `file_fingerprint` and `ast_path` as caller-authoritative fields.
2. Compute them inside Legis or accept them only as part of a trusted Loomweave/Wardline artifact whose digest is verified.
3. Include source-binding context and Loomweave lineage/content context in the `OverrideRecord` before `judge.evaluate()`.
4. Sign exactly the judged record and reject any mismatch between judged fields and persisted fields.
5. Add a typed `JudgedProtectedRecord` or equivalent value object so judge input and signed payload cannot drift.

Acceptance tests:

- A spy judge should receive the exact `file_fingerprint`, `ast_path`, and Loomweave context later signed.
- A request with a fingerprint not matching trusted current content should fail before HMAC signing.
- Mutating source-binding fields between judge evaluation and persistence should be impossible by construction or detected by a unit test.

#### C3. Protected tamper evidence omits audit fields and skips malformed protected records

Locations:

- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:39) lines 39-59
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:76) lines 76-117
- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:60) lines 60-70
- [src/legis/records/override_record.py](/home/john/legis/src/legis/records/override_record.py:30) lines 30-38
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:181) lines 181-187
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:413) lines 413-420

Evidence:

- Protected override payloads store `agent_id` and `extensions.judge_rationale`, but `signing_fields()` does not sign either field.
- Protected sign-off signatures omit `extensions.loomweave`; later `/signoff/{seq}/bind-issue` reads `extensions.loomweave.content_hash` from the stored sign-off request.
- `TrailVerifier.verify()` skips protected-policy records lacking `entity_key` before requiring a signature.
- `LEGIS_PROTECTED_POLICIES` defaults to an empty set; `/protected/overrides` can write a signed record whose policy the verifier later skips because the policy is not configured as protected.

Impact:

An attacker with DB-file access can edit attribution, judge rationale, or sign-off Loomweave content hash, recompute the unkeyed hash chain, and still pass HMAC verification for some cases. A malformed protected record missing `entity_key` can be skipped entirely. This undermines the protected cell's non-repudiation and tamper-evidence guarantees.

Remediation:

1. Introduce `hmac-sha256:v2` signing fields for protected overrides that include `agent_id`, `judge_rationale`, source binding, Loomweave content/lineage fields, policy, entity, verdict, model, rationale, and recorded timestamp.
2. Introduce matching v2 signing fields for protected sign-offs, including Loomweave content hash and lineage snapshot where present.
3. For protected policies, missing required structural fields should raise `TamperError`; never `continue`.
4. Reject `/protected/overrides` for policies outside the configured protected set, or sign and verify an explicit protected-tier marker independent of policy-name configuration.
5. Before binding a sign-off to Filigree, verify the protected trail and verify the sign-off request payload whose content hash is being used.
6. Add migration/version handling so existing v1 records are either grandfathered explicitly or re-signed.

Acceptance tests:

- Tamper `agent_id` and `extensions.judge_rationale`, re-chain sqlite, and assert `TrailVerifier.verify()` plus `GET /overrides` fail closed.
- Remove `entity_key` and signature from a protected record, re-chain, and assert verified reads return an integrity error.
- Mutate a signed sign-off request's `extensions.loomweave.content_hash`, re-chain, and assert binding fails without calling Filigree.
- With HMAC enabled and an empty protected-policy set, protected writes should fail startup or fail the request; tampering a protected-endpoint record must still fail verified reads.

### High

#### H1. Mutating HTTP endpoints are broadly unauthenticated, and actor identity is request-controlled

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:52) lines 52-61
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:68) lines 68-114
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:301) lines 301-316
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:367) lines 367-379
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:446) lines 446-454
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:492) lines 492-507
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:517) lines 517-560
- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:93) lines 93-110

Evidence:

- `verify_operator()` only checks a shared bearer token when `LEGIS_API_SECRET` is set, otherwise it returns `"operator"` without requiring credentials.
- Only operator override and sign-off signing use `verify_operator()`.
- `POST /checks`, `/overrides`, `/protected/overrides`, `/signoff/request`, `/signoff/{seq}/bind-issue`, `/policy/evaluate`, and `/wardline/scan-results` can write state or trigger integrations without required credentials.
- `agent_id` and `operator_id` come from request bodies and are recorded into audit records.
- The authenticated operator value returned by `verify_operator()` is ignored; the body `operator_id` is recorded instead.

Impact:

Any reachable client can pollute CI facts, append governance records, create sign-off requests, submit protected override attempts, bind cleared sign-offs to issues, or spoof agent/operator attribution. This weakens audit non-repudiation and lets the governed actor choose who appears responsible.

Remediation:

1. Require authentication for every mutating route by default.
2. Add separate scopes/roles for agent writes, operator authority, CI/check ingestion, policy evaluation, Wardline ingestion, and Filigree binding.
3. Derive `agent_id` and `operator_id` from authenticated claims, mTLS identity, launch context, or token mapping. Do not accept them as authoritative JSON fields.
4. Use `hmac.compare_digest()` for shared-token comparison while a shared-token mode exists.
5. If unauthenticated local development is required, gate it behind an explicit unsafe-dev flag and make it noisy at startup.

Acceptance tests:

- With `LEGIS_API_SECRET` set, unauthenticated POSTs to every mutating route return 401/403 and write nothing.
- A token/claim for `op-a` with body `operator_id: op-b` records `op-a` or rejects with 403.
- MCP or future adapter schemas must not expose `agent_id` or `operator_id` as ordinary tool arguments.

#### H2. Loomweave lineage failures silently degrade to clean-looking audit state

Locations:

- [src/legis/identity/resolver.py](/home/john/legis/src/legis/identity/resolver.py:38) lines 38-52
- [src/legis/identity/resolver.py](/home/john/legis/src/legis/identity/resolver.py:55) lines 55-72
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:21) lines 21-42
- [src/legis/governance/gaps.py](/home/john/legis/src/legis/governance/gaps.py:56) lines 56-82
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:474) lines 474-488

Evidence:

- Capability errors are cached as `_capable = False`.
- `IdentityResolver._snapshot()` returns `None` on lineage failure.
- `find_lineage_divergence()` skips records with no snapshot and catches lineage probe exceptions with `continue`.
- API lineage surfaces return empty lists when no client is configured and do not distinguish clean from unverified.

Impact:

A Loomweave outage, malformed response, or lineage failure can produce locator-keyed records or SEI-keyed records without snapshots. Later integrity checks can report no divergences even though lineage custody was unavailable.

Remediation:

1. Add explicit `identity_resolution_status` and `lineage_snapshot_status` fields to recorded governance extensions.
2. For protected/complex writes, decide whether lineage custody is mandatory. If mandatory, fail closed when unavailable.
3. Change lineage APIs to return statuses such as `verified`, `unavailable`, `unverified`, and `divergent`; do not conflate unavailable with clean.
4. Avoid permanently caching transient capability failures as incapable without TTL or retry semantics.
5. Validate lineage snapshot shape before use.

Acceptance tests:

- A fake Loomweave client resolving an alive SEI but raising on `lineage()` should cause protected writes to fail or record an explicit provenance gap.
- `/governance/lineage-integrity` should report an unavailable/unverified condition when lineage cannot be fetched, not `{"divergences": []}`.

#### H3. Wardline block-escalate sign-offs drop Loomweave and Wardline metadata

Locations:

- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:18) lines 18-21
- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:83) lines 83-95
- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:96) lines 96-116
- [src/legis/enforcement/signoff.py](/home/john/legis/src/legis/enforcement/signoff.py:75) lines 75-90
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:413) lines 413-420

Evidence:

- `route_findings()` resolves `loomweave_ext` and builds `wardline_ext`.
- `SURFACE_OVERRIDE` and `SURFACE_ONLY` merge and persist those extensions.
- `BLOCK_ESCALATE` calls `signoff.request()` without extensions even though `SignoffGate.request()` accepts them.
- The module docstring still says carrying Wardline tiers is deferred because `SignoffGate.request` has no extensions field, which is now stale.

Impact:

The highest-friction human sign-off path loses fingerprint, severity, trust tiers, Loomweave content hash, and lineage snapshot. Later Filigree binding may fall back to an empty content hash, and lineage-integrity checks cannot inspect what was signed off.

Remediation:

1. Pass `extensions={**loomweave_ext, "wardline": wardline_ext}` into the `BLOCK_ESCALATE` branch.
2. Update the stale docstring.
3. Require or explicitly record missing content hash when binding sign-offs to Filigree.
4. Add regression coverage at both unit and API levels.

Acceptance tests:

- Route a critical finding through `block_escalate` with an SEI/Loomweave resolver and assert the pending sign-off contains `extensions.loomweave` and `extensions.wardline`.
- Binding that sign-off should use the Loomweave content hash from the signed record.

#### H4. `check-override-rate` can create an empty database and pass

Locations:

- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:43) lines 43-51
- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:84) lines 84-119
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:53) lines 53-86
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:88) lines 88-104
- [src/legis/enforcement/lifecycle.py](/home/john/legis/src/legis/enforcement/lifecycle.py:73) lines 73-102

Evidence:

- `AuditStore.__init__()` always creates tables and installs triggers.
- `check-override-rate` constructs `AuditStore(args.db)` before verifying and reading.
- Empty records verify cleanly and evaluate as `PASS_WITH_NOTICE`; CLI returns nonzero only for `FAIL`.

Impact:

A CI gate pointed at a missing or wrong SQLite path can silently create an empty governance trail and return success. This can make a misconfigured governance gate look clean.

Remediation:

1. Add an `AuditStore.open_existing_readonly(url)` mode that refuses missing DB files, missing schema, and write-capable side effects.
2. Use the read-only/open-existing mode in `check-override-rate` and read-only verification paths.
3. Make missing governance DBs a configuration error for CI.
4. Consider a distinct exit code for `PASS_WITH_NOTICE` in CI mode.

Acceptance tests:

- Running `check-override-rate` against a nonexistent sqlite URL should exit nonzero and not create a file.
- Running against an existing valid DB should preserve current evaluation behavior.

#### H5. Policy honesty gate accepts string mentions as behavioral evidence

Locations:

- [src/legis/policy/decorator.py](/home/john/legis/src/legis/policy/decorator.py:188) lines 188-229
- [tests/policy/test_honesty_gate.py](/home/john/legis/tests/policy/test_honesty_gate.py:9) lines 9-12
- [tests/policy/test_honesty_gate.py](/home/john/legis/tests/policy/test_honesty_gate.py:33) lines 33-36

Evidence:

- `check_policy_boundary()` treats `ast.Name` and string constants containing the function or policy name as evidence that a test exercises a boundary.
- The existing positive test only assigns a string containing `handler` and `no-eval` and asserts the string contains `no-eval`.

Impact:

A policy boundary can pass the honesty gate with a pinned test that never calls the decorated function and never asserts behavior at the boundary. That weakens the anti-vibe guarantee the decorator is intended to provide.

Remediation:

1. Remove string-constant fallback as positive proof for function calls.
2. Require an actual `ast.Call` to the decorated function or a configured helper known to exercise it.
3. Require at least one meaningful assertion path tied to the suppressed policy.
4. Keep fingerprint pinning, but treat it as freshness proof, not behavioral proof.

Acceptance tests:

- The current string-only fake test should fail.
- A real test that calls the decorated function and asserts the relevant policy behavior should pass.

#### H6. MCP server is absent and the service layer is too narrow for MCP/HTTP parity

Locations:

- [pyproject.toml](/home/john/legis/pyproject.toml:15) lines 15-16
- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:11) lines 11-52
- [CHANGELOG.md](/home/john/legis/CHANGELOG.md:40) lines 40-45
- [src/legis/service/__init__.py](/home/john/legis/src/legis/service/__init__.py:1) lines 1-26
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:343) lines 343-455
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:492) lines 492-561

Evidence:

- The only console script is `legis = "legis.cli:main"`.
- CLI subcommands are `serve` and `check-override-rate`; no `legis mcp` exists.
- No `src/legis/mcp.py` or `src/legis/mcp/` implementation exists.
- Changelog states MCP WP-M2..M6 are not yet built.
- The service layer exports only resolution, verified records, override rate, and simple submit override. Protected overrides, sign-off, binding, policy evaluation, Wardline routing, git/check reads, and many error mappings remain inline in FastAPI closures.

Impact:

There is no MCP-over-stdio protocol to audit: no `initialize`, `tools/list`, `tools/call`, tool schemas, launch-bound agent identity, or structured MCP error mapping. If implemented now by reusing HTTP route code directly, MCP would likely duplicate logic or expose a partial behavior surface.

Remediation:

1. Extract service functions for protected override, operator override, sign-off request/sign, binding, policy evaluation, Wardline scan routing, git reads, and check reads.
2. Make HTTP and MCP thin transport adapters over the same services.
3. Add `legis mcp` and a stdlib JSON-RPC server with an explicit tool registry and schemas.
4. Bind MCP `agent_id` at process launch or authenticated session context, not per tool-call JSON.
5. Add table-driven parity tests comparing service, HTTP, and MCP mapped outcomes.

Acceptance tests:

- Spawn `legis mcp --agent-id agent-1`, send `initialize` and `tools/list`, and assert expected tools appear while operator-authority tools do not.
- MCP tool schemas should not include `agent_id` or `operator_id`.
- Disabled protected cell, pending sign-off, unknown policy, invalid Wardline cell, and tampered audit trail should map consistently across service, HTTP, and MCP.

### Medium

#### M1. Static-analysis trust tiers and check facts are loosely validated mutable assertions

Locations:

- [src/legis/wardline/ingest.py](/home/john/legis/src/legis/wardline/ingest.py:15) lines 15-18
- [src/legis/wardline/ingest.py](/home/john/legis/src/legis/wardline/ingest.py:53) lines 53-54
- [src/legis/wardline/governor.py](/home/john/legis/src/legis/wardline/governor.py:87) lines 87-88
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:121) lines 121-132
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:281) lines 281-285
- [src/legis/checks/surface.py](/home/john/legis/src/legis/checks/surface.py:50) lines 50-67
- [src/legis/checks/surface.py](/home/john/legis/src/legis/checks/surface.py:101) lines 101-105

Evidence:

- `TRUST_TIERS` is declared but not enforced.
- `properties` is copied verbatim and recorded as `wardline.tiers`.
- `/checks` accepts caller-supplied `commit_sha`, `run_id`, `rule_set`, and `policy_version`; `latest_state()` is last-write-wins by `check_name`.

Impact:

Governance records can contain non-lattice tier values, and check facts can be spoofed or overwritten unless the deployment adds external authentication and integrity controls.

Remediation:

1. Parse known Wardline trust fields into a typed structure.
2. Validate every tier against `TRUST_TIERS`; preserve unknown fields separately as untrusted metadata.
3. Authenticate CI/check writers and require run identity uniqueness.
4. Validate commit SHAs against the git surface where practical.
5. Model supersession explicitly instead of implicit last-write-wins.

Acceptance tests:

- `properties={"actual_return": "ROOT"}` should be rejected or recorded as invalid/untrusted, not as `extensions.wardline.tiers`.
- Unauthenticated duplicate `wardline` pass for a failed commit should be rejected or retained only as a non-authoritative separate event.

#### M2. External URL, DB, secret, and response boundaries are weakly confined

Locations:

- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:14) lines 14-40
- [src/legis/cli.py](/home/john/legis/src/legis/cli.py:66) lines 66-79
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:157) lines 157-168
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:177) lines 177-224
- [src/legis/identity/loomweave_client.py](/home/john/legis/src/legis/identity/loomweave_client.py:40) lines 40-55
- [src/legis/filigree/client.py](/home/john/legis/src/legis/filigree/client.py:31) lines 31-45

Evidence:

- CLI/env strings flow directly into SQLAlchemy URLs and urllib base URLs.
- Loomweave and Filigree clients only `rstrip("/")` the base URL.
- `urlopen(...).read()` loads full response bodies.
- `--hmac-key` accepts a raw signing secret on the command line and copies it to `LEGIS_HMAC_KEY`.

Impact:

Misconfiguration or compromised launch environment can point Legis at unexpected services or DB locations. Large responses can consume memory. Raw HMAC keys can leak through shell history, process lists, or CI logs.

Remediation:

1. Validate URL scheme and host at startup; require HTTPS except explicit loopback/dev allowlist.
2. Add auth headers or mTLS for Loomweave/Filigree where deployments are not strictly loopback.
3. Add response byte caps and content-type checks before JSON parsing.
4. Validate DB URLs and optionally confine state paths.
5. Remove `--hmac-key`; replace with env-only, secret file with strict permissions, or secret manager/KMS integration.

Acceptance tests:

- `file://` Loomweave/Filigree URLs should fail at client construction.
- Non-allowlisted remote hosts should fail unless an explicit remote opt-in is set.
- Oversized responses should raise controlled client errors.
- Parser should reject `--hmac-key`; `--hmac-key-file` should reject group/world-readable files.

#### M3. Git read error mapping and DoS controls are incomplete

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:252) lines 252-265
- [src/legis/git/surface.py](/home/john/legis/src/legis/git/surface.py:26) lines 26-31
- [src/legis/git/surface.py](/home/john/legis/src/legis/git/surface.py:127) lines 127-130
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:68) lines 68-114
- [src/legis/wardline/ingest.py](/home/john/legis/src/legis/wardline/ingest.py:58) lines 58-65

Evidence:

- `/git/commits/{sha}` catches `GitError`, but `/git/branches` and `/git/renames` do not.
- `GitSurface.renames()` raises `GitError` for invalid revision ranges.
- Git subprocesses have no timeout.
- Request models have no string length, body size, or findings count limits.

Impact:

Invalid git inputs can become 500s instead of structured 4xx errors. Large scan bodies, broad rename ranges, or slow git operations can consume CPU, memory, sqlite writes, or process slots.

Remediation:

1. Catch `GitError` consistently for all git endpoints and map invalid refs/ranges to 400 or 422.
2. Add subprocess timeouts and, if needed, max output caps.
3. Add Pydantic `Field` length constraints and batch size limits.
4. Add request body limits and rate limits at the ASGI/server layer.
5. Ensure batch routing either prevalidates all findings or has transactional/no-partial-write semantics.

Acceptance tests:

- `GET /git/renames?rev_range=--version` should return a stable 4xx JSON error, not 500.
- Oversized Wardline scan input should return 413/422 without partial writes.
- A deliberately slow git command in a controlled test double should time out with a structured error.

#### M4. Loomweave and Filigree JSON response shapes are not validated at the transport seam

Locations:

- [src/legis/identity/loomweave_client.py](/home/john/legis/src/legis/identity/loomweave_client.py:40) lines 40-49
- [src/legis/identity/loomweave_client.py](/home/john/legis/src/legis/identity/loomweave_client.py:71) lines 71-74
- [src/legis/identity/resolver.py](/home/john/legis/src/legis/identity/resolver.py:59) lines 59-72
- [src/legis/filigree/client.py](/home/john/legis/src/legis/filigree/client.py:31) lines 31-40
- [src/legis/filigree/client.py](/home/john/legis/src/legis/filigree/client.py:56) lines 56-59

Evidence:

- `_urllib_fetch()` is annotated as returning `dict`, but `json.loads()` can decode any JSON type.
- `resolve()` uses `res["sei"]` when `alive` is true without validating required fields.
- `lineage()` and `associations_for_entity()` call `.get()` without checking decoded body is a mapping.

Impact:

Malformed upstream responses can cause raw `AttributeError`, `KeyError`, or incorrect degradation instead of controlled client errors or documented fail-closed behavior.

Remediation:

1. Validate decoded JSON type immediately in `_urllib_fetch()`.
2. Add response-shape validators for capability, resolve, SEI resolve, lineage, attach, and associations.
3. Convert malformed responses into `LoomweaveError` or `FiligreeError`.
4. Decide which call sites should degrade and which should fail closed.

Acceptance tests:

- Fake fetch returning `[]`, `{"alive": true}`, and `{"lineage": "not-list"}` should produce controlled errors or explicit degradation, never raw key/attribute errors.

#### M5. API composition is tightly coupled and uses private/internal state across layers

Locations:

- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:26) lines 26-47
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:141) lines 141-236
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:301) lines 301-560
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:303) line 303
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:63) lines 63-67
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:72) lines 72-74
- [src/legis/enforcement/protected.py](/home/john/legis/src/legis/enforcement/protected.py:120) lines 120-127

Evidence:

- `api/app.py` imports and orchestrates almost every domain package.
- The API reads `trail_verifier._protected`.
- The service layer uses `getattr(protected_gate, "_store", None)` to verify hash-chain integrity.

Impact:

Future adapters can easily duplicate or bypass behavior. Fake or alternate gate/verifier implementations can satisfy visible methods but skip protected-policy rejection or hash-chain verification because those requirements live in private attributes.

Remediation:

1. Extract a runtime/application service layer that owns workflow orchestration.
2. Add public contracts such as `TrailVerifier.protected_policies`, `ProtectedGate.verify_integrity()`, or a `VerifiedTrail` protocol.
3. Keep HTTP route handlers limited to request parsing and transport error mapping.
4. Add import-boundary tests for API modules.

Acceptance tests:

- Fake gate/verifier implementations exposing only public protocols should still let `/overrides` reject protected policies and verified reads fail closed on hash-chain failure.
- Route tests should be able to use injected runtime/service fakes without importing concrete stores.

#### M6. Public typing surface is not ready for `py.typed`

Locations:

- [pyproject.toml](/home/john/legis/pyproject.toml:18) lines 18-22
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:105) lines 105-114
- [src/legis/service/governance.py](/home/john/legis/src/legis/service/governance.py:21) lines 21-49
- [src/legis/checks/surface.py](/home/john/legis/src/legis/checks/surface.py:69) lines 69-83

Evidence:

- `src/legis/py.typed` exists, but `pyproject.toml` has no mypy config or mypy dev dependency.
- Several boundaries use bare `dict`, bare `list`, untyped `records`, untyped `whereclause`, and untyped row parameters.

Impact:

Downstream consumers will treat Legis as a typed package, but important APIs leak implicit `Any` and strict checking will be noisy or unreliable.

Remediation:

1. Add mypy or pyright configuration and dev dependency.
2. Replace bare containers with `dict[str, Any]`, `Mapping[str, Any]`, `TypedDict`, Pydantic models, or Protocols.
3. Type store record iterables and SQLAlchemy row conversions.
4. Gate CI on the chosen type checker once baseline is clean.

Acceptance tests:

- `uv run mypy src/legis` or the chosen equivalent should pass under the agreed config.
- Built distributions should include `legis/py.typed`.

#### M7. CI and test hygiene gaps reduce regression protection

Locations:

- [.github/workflows/override-rate.yml](/home/john/legis/.github/workflows/override-rate.yml:15) lines 15-17
- [pyproject.toml](/home/john/legis/pyproject.toml:28) lines 28-36
- [tests/enforcement/test_regressions.py](/home/john/legis/tests/enforcement/test_regressions.py:42) lines 42-58
- [tests/enforcement/test_regressions.py](/home/john/legis/tests/enforcement/test_regressions.py:61) lines 61-139
- [src/legis/api/app.py](/home/john/legis/src/legis/api/app.py:170) lines 170-211
- [src/legis/store/audit_store.py](/home/john/legis/src/legis/store/audit_store.py:85) lines 85-86

Evidence:

- The GitHub workflow installs the package and runs only `legis check-override-rate`.
- There is no CI `pytest`, lint, or static type job.
- Some tests mutate `os.environ` directly and `pop` keys instead of restoring prior values.
- One regression test enables HMAC without setting governance/binding DB env vars, so `create_app()` can fall back to repo-root DB files.
- Pytest has no marker split, while tests mix sqlite, threading, git subprocesses, and FastAPI clients.

Impact:

Substantial local test coverage is not a merge gate. Tests can become order-dependent, pollute local repo state, or hide regressions in skipped CI surfaces.

Remediation:

1. Add CI jobs for `uv run pytest` and selected static checks.
2. Use `monkeypatch` for environment changes.
3. Clear or isolate all `LEGIS_*`, `LOOMWEAVE_API_URL`, and `FILIGREE_API_URL` settings per test.
4. Point default DBs to `tmp_path` in tests that create app state.
5. Add pytest markers such as `unit`, `integration`, `api`, and `contract`.

Acceptance tests:

- A PR with a deliberately failing test fails CI.
- Pre-seeded environment variables are restored after app setup tests.
- Running targeted app setup tests creates no repo-root `.db` files.
- Pytest collection can select pure unit tests separately from sqlite/git/API tests.

## Remediation Roadmap

1. Secure the evidence boundary first: type and authenticate Wardline scan ingestion, remove caller-owned routing, and record artifact digests/provenance.
2. Repair protected-cell HMAC semantics: v2 signing fields, no verifier skips for malformed protected records, sign-off Loomweave metadata bound into signatures, and protected endpoint/config alignment.
3. Move actor identity out of request bodies: authenticated agent/operator/CI scopes, adapter launch context for MCP, and default-deny mutating endpoints.
4. Decide Loomweave fail-closed policy: record explicit identity/lineage statuses, and make protected/complex writes fail or loudly mark provenance gaps when custody is unavailable.
5. Extract shared services before MCP implementation: protected override, sign-off, binding, policy evaluation, Wardline routing, git/check reads, and structured errors.
6. Add read-only store open modes and fix CI gate behavior for missing DB/schema.
7. Harden integration and transport inputs: URL allowlists, response caps, DB path validation, git subprocess timeouts, and request size limits.
8. Add missing regression tests and CI: pytest, type checking, API error mapping, HMAC tamper cases, Wardline metadata preservation, and MCP/HTTP parity tests once MCP lands.

## Residual Risks

- This audit did not run tests or dynamic probes by request, so execution-time behavior was inferred from source.
- Live Loomweave and Filigree contract drift was not tested; current tests use fakes.
- The absent scanner/rules and MCP server mean those specific implementations could not be audited; only the closest present code and design seams were reviewed.
