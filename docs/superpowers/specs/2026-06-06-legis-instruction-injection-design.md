# Legis instruction injection — design spec

**Date:** 2026-06-06
**Status:** Approved for implementation (ultracode)
**Author:** John Morrissey (with Claude)

## Goal

Make legis "stand itself up" the way its siblings do: a coding agent that opens
a legis project finds an **agent-calibrated orientation block** in
`CLAUDE.md` / `AGENTS.md` plus a `legis-workflow` **skill pack**, and that
content stays **automatically fresh** (versioned content hash; re-injected on
drift) for **both** Claude Code and Codex agents.

This mirrors Filigree's proven mechanism
(`filigree/src/filigree/install.py`, `hooks.py`,
`install_support/hooks.py`) and adopts Loomweave's skill-tree fingerprint
drift detection — with one improvement over both siblings: refresh also fires
on **MCP server boot**, closing the "Codex-only repo never refreshes" gap.

## Doctrine anchor

From `README.md`: *"Each tool stands itself up preloaded with agent-calibrated
instructions — the instruction layer is the configuration mechanism."* and
*"Agent-first: humans on the loop, not in the loop."* This feature is the legis
realization of that instruction layer.

## Architecture

### Two-tier content (best practice: lean block + skill pack)

1. **Lean orientation block** (~20 lines) injected into `CLAUDE.md` / `AGENTS.md`.
   - States what legis is (the git/CI + governance layer of Weft), how to reach
     it (`mcp__legis__*` tools when present; `legis` CLI fallback), and points to
     the `legis-workflow` skill for the full reference.
   - Delimited by versioned markers:
     - open: `<!-- legis:instructions:v{version}:{hash} -->`
     - close: `<!-- /legis:instructions -->`
   - `{version}` = `importlib.metadata.version("legis")` → falls back to
     `legis.__version__` (currently `1.0.0rc4`).
   - `{hash}` = first 8 hex chars of `sha256(block_body_text)`.
   - **Freshness compares the full `v{version}:{hash}` token**, so a body edit
     (hash drift) *or* a package-version bump both trigger re-injection and keep
     the marker truthful. (Filigree compares hash-only; legis compares both so
     "automatic versioning" actually tracks the version.)

2. **`legis-workflow` skill pack** carrying the depth: CLI command reference,
   MCP tool catalogue, error-code/recovery table, workflow patterns. Shipped as
   package data; installed into `.claude/skills/legis-workflow/` and
   `.agents/skills/legis-workflow/` (Codex). Drift-detected via a skill-tree
   fingerprint (sorted relative POSIX path + bytes, sha256[:8]).

### Refresh triggers (two — full coverage)

- **Claude Code SessionStart hook** (`legis session-context`) registered in
  `.claude/settings.json`. Refreshes block + skill drift when Claude opens the
  repo.
- **`legis mcp` startup** — best-effort `refresh_instructions(cwd)` invoked from
  the CLI `mcp` branch before the stdio loop starts. This is the **load-bearing
  trigger for Codex-only repos** (no `.claude/` hook). Idempotent: writes only
  when the embedded hash differs, so no git churn in steady state. All failures
  are swallowed — the refresh must never block or crash the MCP server.

Both triggers call the same `refresh_instructions(root)`. Refresh **only updates
files/skills that already carry the marker** (drift refresh in place). Initial
**creation** is the job of `legis install` — an MCP boot or hook never
surprise-creates `CLAUDE.md`. (Matches Filigree's freshness semantics.)

## Components

### `src/legis/data/instructions.md`
The lean block body (no markers — markers are added programmatically). Content:
what legis is, `mcp__legis__*` + CLI fallback, the six CLI subcommands, and a
pointer to the `legis-workflow` skill.

### `src/legis/data/skills/legis-workflow/SKILL.md`
Skill pack with YAML frontmatter (`name: legis-workflow`, a `description:` that
triggers on governance/override/policy-cell/CI-gate/git-rename/closure-gate
tasks). Body documents:
- CLI: `serve`, `mcp`, `check-override-rate`, `governance-gate`,
  `sei-backfill`, `policy-boundary-check`.
- MCP tools: `policy_explain`, `override_submit`, `signoff_status_get`,
  `policy_evaluate`, `scan_route`, `git_branch_list`, `git_commit_get`,
  `git_rename_list`, `git_rename_feed_get`, `filigree_closure_gate_get`,
  `pull_request_get`, `check_list`, `override_rate_get`.
- Error codes / recovery (sourced from `legis/mcp.py` `_recovery_for`).

### `src/legis/install.py`
Mirrors Filigree's injection core, right-sized (no dashboard, no server mode):
- `INSTRUCTIONS_MARKER = "<!-- legis:instructions"`, `_END_MARKER`,
  `SKILL_NAME = "legis-workflow"`.
- `_instructions_text()`, `_instructions_hash()`, `_instructions_version()`,
  `_build_instructions_block()`. (The block is built per-call; the once-planned
  `INSTRUCTIONS = _build_instructions_block()` module constant was not shipped.)
- `inject_instructions(path) -> (bool, str)`: replace if legis owns a block,
  append if file exists without one, create if absent. **Superseded:** the
  original "missing end-marker → replace start-marker→EOF" recovery deleted
  co-resident sibling blocks; the implementation now bounds the rewrite at the
  first *foreign* fence and anchors only on legis's own *top-level* open fence
  (peer of filigree-bcbd4d66fd, legis-068e359d28). See `_first_foreign_fence_pos`
  / `_first_own_open_fence_pos` in the code for the live behavior.
- `_atomic_write_text(path, content)`: temp + `os.replace`, preserve existing
  mode (else respect umask for new files), `reject_symlink`.
- `reject_symlink` / safe-path helpers (port the minimal subset from Filigree's
  `safe_paths`).
- `install_skills(project_root)` → `.claude/skills/legis-workflow/`,
  `install_codex_skills(project_root)` → `.agents/skills/legis-workflow/`,
  copying the packaged skill tree.
- `_get_skills_source_dir()`, `_skill_tree_fingerprint(root)`.
- `install_claude_code_hooks(project_root)`: idempotent SessionStart
  registration of `legis session-context` in `.claude/settings.json`; upgrade
  bare/stale commands to the resolved binary; reuse only an unscoped block
  carrying a known legis hook; otherwise append a dedicated matcher-less block.
  Port Filigree's `_hook_cmd_matches`, `_has_unscoped_session_start_hook`,
  `_upgrade_hook_commands`. **Omit** the PreToolUse/dashboard hook.
- `ensure_gitignore(project_root)`: ensure the Legis stanza covers `.legis/` and
  `legis.yaml` (the missing config entries; the `*.db*` lines already exist).

### `src/legis/hooks.py`
- `refresh_instructions(root) -> list[str]`: for `CLAUDE.md`/`AGENTS.md` carrying
  the marker, compare the embedded `v{version}:{hash}` token (regex
  `<!-- legis:instructions:(v[^:]+:[0-9a-f]+) -->`) to the current
  version+hash, re-inject on mismatch; for each installed skill root, compare
  tree fingerprint to source and reinstall on mismatch. Returns human-readable
  update messages. `root` defaults to the caller's cwd; the MCP-boot caller
  passes `Path.cwd()` and accepts that a non-project cwd simply no-ops (refresh
  only ever touches marker-bearing files).
  Best-effort: callers guard against `OSError`/`UnicodeDecodeError`/`ValueError`.
- `generate_session_context() -> str | None`: run `refresh_instructions(cwd)`;
  return the joined update messages, or `None` when nothing changed (silent —
  no governance snapshot, no DB dependency).

### `src/legis/cli.py`
- `legis install` subcommand: flags `--claude-md`, `--agents-md`, `--skills`,
  `--codex-skills`, `--hooks`, `--gitignore`; no flags ⇒ all. Steps: inject
  `CLAUDE.md`, inject `AGENTS.md`, install skills, install codex skills, install
  hooks, ensure gitignore. Print a per-step result table.
- `legis session-context` subcommand: prints `generate_session_context()` (or
  nothing) and exits 0.
- In the existing `mcp` branch: call `refresh_instructions(Path.cwd())` inside a
  broad `try/except` (swallow all) **before** `mcp_main(...)`.

### `pyproject.toml`
Ensure `src/legis/data/**` (the `instructions.md` and the skill tree) ships in
the wheel/sdist under `uv_build`. Verify via
`importlib.resources.files("legis.data")` at test time.

### `.gitignore`
Extend the existing `# Legis —` stanza so it also ignores the (prophylactic,
sibling-consistent) local config surface:
```
# Legis — local audit/scratch databases + their SQLite WAL sidecars
# and local working dir / config (regenerated/local; never commit)
*.db
*.db-shm
*.db-wal
.legis/
legis.yaml
```

## Out of scope (YAGNI)

- Dashboard / ephemeral-port / server-mode machinery (legis has none).
- A PreToolUse hook (no dashboard to restart).
- A Codex-native hook (the MCP-boot refresh supersedes it).
- Changing how `CLAUDE.md`/`AGENTS.md` are tracked — they remain gitignored
  regenerated artifacts; the legis block coexists with whatever else regenerates
  them.

## Testing

Mirror Filigree/Loomweave coverage (repo floor: 88%):
- `inject_instructions`: create / append / replace / malformed (missing end
  marker) / idempotent re-run.
- `_instructions_hash` stable; `_build_instructions_block` marker shape;
  marker-hash regex extraction.
- `_skill_tree_fingerprint` changes on content/path change; `refresh_instructions`
  updates a drifted `CLAUDE.md` **and** `AGENTS.md` and a drifted skill pack;
  no-ops (returns `[]`) when fresh; skips files without the marker.
- `install_claude_code_hooks`: fresh install, idempotent re-run, bare→absolute
  upgrade, malformed `settings.json` backup, does not duplicate, reuses only
  unscoped blocks.
- `ensure_gitignore`: adds `.legis/`/`legis.yaml`, idempotent, preserves
  existing content.
- `_atomic_write_text`: preserves existing file mode; new file respects umask;
  rejects symlink target.
- CLI: `legis install` (all + each selective flag) writes expected artifacts;
  `legis session-context` prints refresh messages / nothing; `mcp` branch
  refresh is best-effort (a raising `refresh_instructions` does not break
  `mcp` startup).
- Packaging: `importlib.resources.files("legis.data")` resolves the template and
  skill tree.

## Gates

`ruff`, `mypy` (py312, the repo's strict config), `pytest` with the 88% floor,
all green before done.
