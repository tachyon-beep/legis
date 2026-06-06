# Changelog

All notable changes to Legis are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
versions per [PEP 440](https://peps.python.org/pep-0440/) /
[SemVer](https://semver.org/) (pre-release: `1.0.0rc1`).

## [Unreleased]

### Added
- **Dirty-tree dev path** — `verify_wardline_artifact` now recognises the
  unsigned `dirty: true` dev artifact emitted by `wardline scan --format legis
  --allow-dirty`. In the keyless posture it governs but records the marker
  honestly (`artifact_status: "dirty"`). In the CI posture (artifact key
  configured) a dirty dev artifact is a typed amber **`SKIPPED_DIRTY_TREE`**
  outcome on `scan_route` / `/wardline/scan-results` — distinguishable from the
  generic red, never governed — unless `LEGIS_WARDLINE_ALLOW_DIRTY=1` opts into
  governing it unsigned (recorded as `"dirty"`). The relaxation is scoped to
  exactly `dirty is True AND no signature`: a signed payload still verifies
  (a forged signature stays red) and a clean unsigned payload still requires a
  signature, so the clean-tree signing guarantee is intact. (legis-d731c760c5,
  legis-7e85e8e7ba; upstream wardline `--allow-dirty`.)

## [1.0.0rc1] — 2026-06-03

First release candidate for 1.0. Everything built through Sprint 6 plus the
WP-M1 service-layer extraction, consolidated behind a stable version.

### Added
- **git/CI surface** — stateless `GitSurface` (branches, commits, renames with
  `-M`, merge-base) and a recorded CI `CheckSurface`, exposed over `/git/*` and
  `/checks/*`; injectable `PullRequestSource` seam with `/git/pull-requests/{n}`.
- **Graded 2×2 enforcement engine** — chill / coached / structured / protected
  cells; LLM judge behind an injected `LLMClient` seam (fail-closed verdict
  parsing); HMAC-signed protected verdicts; decay sweep and the override-rate
  gate (`legis check-override-rate`, exits 1 on FAIL).
- **Agent-programmable policy grammar** — `/policy/evaluate` returning
  CLEAR / VIOLATION / UNKNOWN, with honest `provenance_gap` events (no silent
  false-green); TOML-backed one-off exemptions.
- **SEI-keyed attestations** — `identity/loomweave_client.py` + resolver
  (resolve-then-key, honest degrade, lineage snapshot); all governance write
  paths key on Stable Entity Identity when alive; `/governance/identity-gaps`
  and `/governance/lineage-integrity` read surfaces.
- **Suite combinations** — Wardline findings route into the 2×2 via
  `/wardline/scan-results`; governed SEI-keyed sign-off binding to Filigree
  issues via a tamper-bound `BindingLedger`.
- **Console scripts** — `legis serve` (uvicorn factory) and
  `legis check-override-rate`.
- **Transport-agnostic service layer (WP-M1)** — `legis.service` extracts the
  cross-cutting governance logic (`resolve_for_record`, `verified_records`,
  `compute_override_rate`, the `submit_override` seam) out of the FastAPI route
  closures and raises domain errors (`ServiceError` subclasses) rather than
  `HTTPException`, so both HTTP and the forthcoming MCP adapter drive one code
  path. Behavior-preserving; FastAPI handlers are now thin adapters.

### Known limitations
- The agent-facing **MCP surface** is designed and decomposed
  (`docs/superpowers/specs/2026-06-03-legis-mcp-surface-design.md`) with WP-M1
  landed; WP-M2..M6 (registry + `legis_explain`, the MCP stdio server, the
  write/governance tools, safety hardening, judge reason-classification) are not
  yet built.
- The git-rename provider to Loomweave is contract-locked but operatively gated on
  Loomweave driving a committed rev-range.
- `HttpLoomweave` runs loopback-unauthenticated; sibling-gated work packages
  (Filigree signature column, live-Loomweave oracle + HMAC auth, operative
  git-rename feed) remain.

[Unreleased]: https://peps.python.org/pep-0440/
[1.0.0rc1]: https://peps.python.org/pep-0440/
