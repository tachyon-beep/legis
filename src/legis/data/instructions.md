## Legis (git/CI + governance)

Legis is the git/CI and governance layer of the Weft suite. Reach for it when a policy fires at the CI/git boundary and a change needs a *recordable* override or human sign-off, when you need governance attestations keyed to stable code identity (SEI), or when you need git/CI context — branches, commits, pull requests, check outcomes, and the Loomweave-bound rename feed — around the work. Enforcement is graded: agent-programmable policy cells decide whether a violation self-clears with an audit trail, is judged inline, or escalates to a human; every decision lands in an append-only, SEI-keyed audit trail that survives rename/move.

Prefer the `mcp__legis__*` MCP tools when available; fall back to the `legis` CLI.

CLI subcommands:

- `serve` — run the Legis API server.
- `mcp` — run the Legis MCP stdio server (launch-bound `--agent-id`).
- `check-override-rate` — exit 1 if the override-rate gate is FAIL (for CI).
- `governance-gate` — run governance CI gates (currently the override-rate gate).
- `sei-backfill` — resolve legacy locator-keyed governance records through Loomweave batch resolve.
- `policy-boundary-check` — fail when `@policy_boundary` metadata lacks current behavioural evidence.

Full command + MCP-tool reference: see the `legis-workflow` skill.
