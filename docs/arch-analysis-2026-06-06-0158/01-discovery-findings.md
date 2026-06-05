# 01 — Discovery Findings

## What Legis is
Legis is the git/CI + governance layer of the **Weft** suite (four federated tools sharing one
substrate keyed on Loomweave's Stable Entity Identity / SEI). Legis answers: *what changed, in
which branch/commit/PR/check context, and what governance/attestation state exists for that change?*

Its distinguishing surface is a **governance 2×2** — two independent agent-set axes:
- **structure**: simple ↔ complex
- **judge**: off ↔ on

yielding four cells: **Chill** (simple/off), **Coached** (simple/on), **Structured** (complex/off),
**Protected** (complex/on — HMAC-signed verdicts, decay sweep, override-rate gate). The root invariant
is *agent-first: humans on the loop, not in the loop* — when a policy fires, the cell decides who
answers, and every decision produces an append-only, SEI-keyed audit trail.

Version `1.0.0rc2`. Python ≥3.12. Deps: FastAPI, SQLAlchemy 2.0, PyYAML, uvicorn.

## Technology stack
| Concern | Choice |
|---|---|
| Language | Python 3.12 |
| HTTP | FastAPI + uvicorn |
| Persistence | SQLAlchemy 2.0 over SQLite (`*.db` files: governance, checks, pulls, binding) |
| Agent surface | Hand-rolled MCP server (`mcp.py`), stdio JSON-RPC, protocol `2024-11-05` |
| CLI | `legis` console script → `legis.cli:main` |
| Crypto | HMAC-signed audit records; canonical JSON (RFC-8785 hardening pending) |
| Build/tooling | uv build backend; pytest + pytest-cov; mypy; ruff |

## Entry points
- **CLI** — `legis.cli:main` (`legis governance-gate`, `verify-trail`, server run, etc.)
- **HTTP** — `legis/api/app.py` FastAPI app (bearer-auth mutating routes; writer/operator scopes)
- **MCP** — `legis/mcp.py` stdio JSON-RPC server (launch-bound identity)
- All three are intended to converge on the transport-agnostic **service layer** (`service/`, WP-M1).

## Subsystem inventory (63 files, ~7,353 LOC)
| Subsystem | Files | LOC | Responsibility (first-glance) |
|---|---|---|---|
| `policy/` | 7 | 1072 | Agent-programmable policy grammar, cells, boundary decorator/scan |
| `enforcement/` | 10 | 1062 | 2×2 engine, LLM judge, protected/signoff/decay lifecycle, signing |
| `api/` | 2 | 831 | FastAPI HTTP surface, auth, routing |
| `service/` | 6 | 603 | Transport-agnostic governance/wardline/source-binding helpers |
| `governance/` | 7 | 585 | Attestations, binding ledger, sign-off binding, SEI backfill, gaps |
| `wardline/` | 4 | 386 | Wardline scan ingest + governor (route findings → cells) |
| `identity/` | 4 | 356 | SEI consumption, entity keys, resolver (Loomweave client) |
| `git/` | 5 | 328 | Branch/commit/PR context, working-tree + rename feed |
| `store/` | 3 | 217 | SQLAlchemy audit store + store protocol |
| `checks/` | 3 | 157 | CI check context surface |
| `filigree/` | 2 | 124 | Filigree issue-lifecycle binding client |
| `pulls/` | 3 | 97 | Pull-request context surface |
| `records/` | 2 | 40 | Shared record types (`OverrideRecord`) |
| top-level | 5 | — | `cli.py`, `mcp.py`, `canonical.py`, `clock.py`, `__init__.py` |

## Suite seams (cross-product combinations)
- **Wardline + Legis** (live): agent-defined policy enforced at CI/git boundary; findings route through `wardline/governor.py` into 2×2 cells.
- **Loomweave + Legis** (live, SEI-keyed): attestations key on SEI; git-rename provider contract-locked, pending Loomweave committed-range driving.
- **Filigree + Legis** (live): governed SEI-keyed sign-off binding; closure-gate decision; Filigree retains lifecycle authority.

## Prior-art baseline
Two read-only audits (2026-06-04, recovered from HEAD into `temp/`): 3 Critical, 7 High, 14 Medium, 5 Low.
Dominant themes: **adapter drift** (MCP omits HTTP/CLI server-side constraints) and **evidence loss / weak
binding** in governance records. Partially remediated since (C1 override-rate fail-closed; M11 MCP idempotency).
These feed `05-quality-assessment.md` and `06-architect-handover.md`.

## Orchestration decision
**PARALLEL**, 6 clustered explorers along architectural seams (see `00-coordination.md`). Rationale:
≥5 loosely-coupled subsystems, but several are trivial (records 40, pulls 97, filigree 124) — clustering
preserves the wiring that *is* the product rather than fragmenting it across 13 dispatches.

**Confidence: High** for inventory/stack/entry-points (direct measurement). **Medium** for responsibility
summaries pending per-cluster explorer confirmation.
