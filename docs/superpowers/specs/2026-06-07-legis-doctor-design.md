# Legis doctor — design spec

**Date:** 2026-06-07
**Status:** Approved for implementation
**Author:** John Morrissey (with Claude)

## Goal

Give legis a `legis doctor` command that **views and repairs install/config
problems**, the way its siblings do (`wardline doctor`,
`filigree`'s `install_support/doctor.py`; loomweave has none). One command
answers *"is my legis wiring healthy, and if not, fix what's safe to fix."*

Two distinct gaps motivate it:

1. **No affirmative health view.** legis already self-heals install drift on
   `SessionStart` / MCP boot (`hooks.refresh_instructions`), but that path is
   silent on success — `session-context` prints nothing whether everything is
   current or nothing was checked. There is no way to *ask* "is this healthy?"
   and get an affirmative answer.
2. **No coverage of the config/store layer.** The install path checks
   instruction blocks / skills / hook / `.gitignore`, but nothing checks
   `weft.toml` parseability, the `.weft/legis/` stores, audit-chain integrity,
   the `.mcp.json` server registration, or key/sibling-URL wiring.

These were surfaced concretely while scoping this work (see **Worked examples**):
legis was absent from `.mcp.json` entirely, and `session-context` returned
nothing — both real, both exactly what doctor should catch.

## Doctrine anchors

- **C-9(a) — per-member subtree.** Each member is the **sole writer of its own
  `.weft/<member>/` subtree** and never reads/writes a sibling's. doctor may
  create/repair `.weft/legis/`.
- **C-9(b) — `weft.toml` is operator-write-only; `doctor` is named.** *"No
  member's installer / CLI / `doctor` writes or rewrites `weft.toml`."* Precedent
  is the multi-writer truncation gate `weft-eb3dee402f`. **`legis doctor` is
  fully report-only on `weft.toml` — it does not even scaffold an absent
  `[legis]` table.** Matches `wardline doctor` ("never weft.toml, never a
  sibling's").
- **C-9(c) — malformed = absent (silent fallback) at runtime.** A
  malformed/unreadable `weft.toml` must still boot on defaults. doctor's job is
  to **restore the operator signal** that runtime silences: it reports
  malformed `weft.toml` as an **error** (your config is silently not applying)
  — a diagnostic, never a write.
- **Capability honesty / key carve-out.** Operator signing keys are
  capability-confined and not agent-reachable (`config.py`). doctor
  **presence-checks** keys only — it never prints, logs, or writes a key value.
  legis operator keys are held securely (a Rust key sidecar is planned);
  filigree's auto-generated federation comms key is a separate concern.
- **Agent-first, humans on the loop.** doctor is an **operator/CLI** tool. It
  inspects and repairs the *host* install and operator files, which is not an
  agent-reachable concern, so it is **not** added to the legis MCP tool surface
  or the transport-agnostic `service/` decision layer.

## Architecture

A single new module plus thin CLI wiring and one install capability —
mirroring `wardline/install/doctor.py` and matching legis's flat-module style
(`config.py`, `install.py`, `hooks.py`).

- **`src/legis/doctor.py`** — the logic. A `DoctorCheck` dataclass, one function
  per check, a `run_doctor(root, *, repair, fmt) -> int` orchestrator, and
  `machine_readable_doctor(root, *, repair) -> dict` for the JSON shape.
- **`src/legis/cli.py`** — a `doctor` subparser and a thin `_run_doctor`
  dispatcher (I/O shell + exit code only; same pattern as `_check_override_rate`).
- **`src/legis/install.py`** — a new `register_mcp_json(project_root) ->
  tuple[bool, str]` (and a matching `--mcp` install flag, included in
  install-all), so the `.mcp.json` check has a repair capability to call. This
  closes the asymmetry where `wardline install` registers `.mcp.json` but
  `legis install` did not.

**Reuse (no logic duplication):**
- `install.py`: `INSTRUCTIONS_MARKER`, `_extract_marker_token`, `_marker_token`,
  `_skill_tree_fingerprint`, `_get_skills_source_dir`, `inject_instructions`,
  `install_skills`, `install_codex_skills`, `install_claude_code_hooks`,
  `ensure_gitignore`, and the new `register_mcp_json`.
- `config.py`: `project_root`, `_weft_legis_config`, `_store_dir`,
  `*_db_url`, `protected_policies`, `ensure_sqlite_parent`.
- `store/audit_store.py`: `verify_integrity`.

### `DoctorCheck`

```python
@dataclass(frozen=True, slots=True)
class DoctorCheck:
    id: str                 # stable, e.g. "install.mcp_json", "store.governance_chain"
    status: str             # "ok" | "warn" | "error"
    fixed: bool = False     # True if --repair changed state from not-ok to ok
    message: str | None = None

    @property
    def ok(self) -> bool: return self.status == "ok"
```

`warn` is non-fatal (does not affect exit code); `error` is fatal (exit 1).

## Surface

```
legis doctor [--root .] [--repair] [--format {text,json}]
```

- **default** — report-only, human text. Exit `0` if no `error` checks, else `1`.
- **`--repair`** — apply safe repairs (see model below), **re-check**, then
  report the post-repair state.
- **`--format json`** — emit the federation machine-readable shape:
  `{"ok": bool, "checks": [DoctorCheck.to_dict()...], "next_actions": [str...]}`.
  `next_actions` lists `"{id}: {message}"` for each non-ok check with a message.

`--format` (not wardline's `--fix`) is deliberate: it matches legis's *own*
existing `policy-boundary-check --format {text,json}` convention. `--repair` and
`--format` are orthogonal (you can `--repair --format json`). Exit `2` on usage
error.

## Checks

### Install wiring (repairable)
- `install.claude_md` — CLAUDE.md instruction block present and **not drifted**
  (marker token = current `version:hash`).
- `install.agents_md` — AGENTS.md block present and not drifted.
- `install.claude_skill` — `.claude` skill pack present, tree fingerprint fresh.
- `install.agents_skill` — `.agents` (Codex) skill pack present, fingerprint fresh.
- `install.hook` — Claude Code `SessionStart` hook registered.
- `install.gitignore` — legis `.gitignore` rules present.
- `install.mcp_json` — `.mcp.json` has a usable `legis` server entry: present,
  args invoke `mcp`, and `command` resolves to an existing executable. Deliberately
  NOT byte-canonical — a valid but differently-resolved legis binary (uv-tool vs
  venv path) must not read as drift; only a missing entry, malformed args, or a
  dead `command` path is stale. `--repair` writes the canonical entry via
  `register_mcp_json` (resolved binary at repair time).

### Config & stores
- `config.weft_toml` — **report-only.** ABSENT → `ok` (defaults intentional);
  PRESENT-and-`[legis]`-valid → `ok`; PRESENT-but-unparseable, or `[legis]` not a
  table → `error` ("weft.toml present but malformed; legis is booting on
  defaults and your `[legis]` config is silently not applying").
- `store.dir` — the resolved `store_dir` is usable: its parent is writable so
  stores can be created. An **absent** `.weft/legis/` is `ok` (created lazily on
  first store open — preserves the import-time no-leak guarantee
  `test_build_runtime_initialize_does_not_create_local_state`); a
  **present-but-unwritable** dir is `error`. `--repair` ensures the dir exists as
  a convenience — an explicit operator action, categorically distinct from the
  import-time no-leak guarantee (C-9(a)).
- `store.db_overrides` — any set `LEGIS_*_DB` env var is a well-formed URL.
  Report-only.
- `store.legacy_stray` — legacy `legis-*.db` at the repo root → `warn`
  (informational; never deleted — operator data).

### Governance integrity (report-only)
- `store.governance_chain` — `AuditStore(governance_db_url()).verify_integrity()`.
  Absent DB → `ok` (nothing to verify, not an error). Tamper/broken chain →
  `error` (report-only; a hash chain cannot and must not be auto-repaired).
- `store.binding_chain` — same for the binding ledger.

### Runtime & siblings (report-only)
- `runtime.hmac_key` — if `LEGIS_PROTECTED_POLICIES` is non-empty (protected /
  structured cells configured) but no signing key is available → `warn`
  ("protected policies configured but no signing key; protected submissions
  will fail"). **Presence only; the value is never read out or shown.**
- `runtime.loomweave_url` / `runtime.filigree_url` — if set, well-formed
  http(s) URL; unset → `ok` ("not configured"). Report-only.

## Repair model

`--repair` mutates **only legis's own per-member artifacts**:

| Artifact | Repaired? | How |
|---|---|---|
| CLAUDE.md / AGENTS.md blocks | ✅ | `inject_instructions` (idempotent, drift-aware) |
| `.claude` / `.agents` skills | ✅ | `install_skills` / `install_codex_skills` |
| SessionStart hook | ✅ | `install_claude_code_hooks` |
| `.gitignore` | ✅ | `ensure_gitignore` |
| `.mcp.json` legis entry | ✅ | `register_mcp_json` (new) |
| `.weft/legis/` dir | ✅ | `ensure_sqlite_parent` / `mkdir` |
| `weft.toml` | ❌ never | C-9(b) — report-only, even when absent |
| Audit hash chains | ❌ never | tamper-evidence; report-only |
| Keys, sibling URLs | ❌ never | secrets/values; report-only with guidance |

After repair, every check is **re-run** so the report reflects true post-repair
state and `fixed=True` is set only where a not-ok check became ok.

## `.mcp.json` registration (new install capability)

`register_mcp_json(project_root)` adds/updates a `legis` entry under
`mcpServers` in `<root>/.mcp.json` (creating the file if absent), merging
without disturbing sibling entries. The canonical entry:

```json
"legis": {
  "args": ["mcp", "--agent-id", "<agent-id>"],
  "command": "<resolved legis binary>",
  "env": {},
  "type": "stdio"
}
```

- **Binary resolution** reuses the same logic as the hook installer
  (`install._find_legis_command`) so the entry points at the real `legis`.
- **Agent id**: `legis mcp` requires `--agent-id` (it stamps the governance
  actor). Default `"claude-code"`; overridable via a `--agent-id` option on
  `legis install --mcp` (and `legis doctor --repair` uses the default unless an
  existing entry already carries one, which it preserves).
- Wired into `legis install` as `--mcp` and included in install-all.

## What doctor does NOT do

- Never writes `weft.toml` (C-9(b)).
- Never repairs a hash chain (tamper-evidence is the point).
- Never prints, logs, or writes a key value.
- Never deletes operator data (legacy stray DBs are warned, not removed).
- Not exposed on the agent MCP surface or the `service/` layer.

## Testing

`tests/test_doctor.py` (mirrors `src/legis/doctor.py`), `tmp_path` project
roots, with the **Worked examples** below as red→green fixtures:

- missing `.mcp.json` legis entry → `error`; `--repair` → `fixed=True`, re-check `ok`.
- drifted instruction block (stale marker token) → `error` → repaired.
- absent `weft.toml` → `ok`; malformed `weft.toml` → `error` and **file
  unchanged after `--repair`** (asserts C-9(b)).
- tampered governance chain → `error`, **report-only** (file unchanged after `--repair`).
- `LEGIS_PROTECTED_POLICIES` set with no key → `warn`; assert **no key value
  appears** anywhere in text/JSON output.
- JSON shape: `{ok, checks:[{id,status,fixed,message?}], next_actions}`.
- exit codes: `0` healthy, `1` any error, `2` usage error.

A new per-package coverage floor entry covers `doctor.py`.

## Worked examples (the findings that motivated this)

1. **legis absent from `.mcp.json`** — its `mcp__legis__*` tools never loaded.
   `install.mcp_json` → `error`; repaired by `register_mcp_json`. (Fixed
   manually during scoping; doctor makes it self-diagnosing.)
2. **`session-context` returns nothing** — honest-empty by design
   (`refresh_instructions` → `[]` on no drift). doctor supplies the missing
   affirmative "all current" signal.
3. **wardline rc1↔rc4 version skew (reported)** — not reproducible in this
   environment (uniformly rc4). Cross-tool *version* reconciliation is **out of
   scope** for v1 (doctor checks legis's own wiring, not sibling tool versions);
   noted as a candidate future check.

## Out of scope / future

- Cross-tool version-skew checks (sibling binary versions).
- Reading keys from the planned Rust key sidecar (doctor stays presence-only;
  it will check availability through whatever resolution path exists then).
- Any `weft.toml` write capability (blocked by C-9(b)).
