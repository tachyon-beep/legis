# Legis doctor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `legis doctor [--root .] [--repair] [--format {text,json}]` — an operator/CLI health view that diagnoses and (safely) repairs legis's install + config layer.

**Architecture:** One new module `src/legis/doctor.py` (a `DoctorCheck` dataclass, one function per check, a `run_doctor` orchestrator, `machine_readable_doctor` for JSON), a thin `doctor` subparser in `cli.py`, and one new install capability `register_mcp_json` in `install.py` (with a `legis install --mcp` flag). Checks reuse existing `install.py` / `config.py` / `store` helpers; repairs touch only legis's own per-member artifacts. Bound by C-9(b): **never writes `weft.toml`**.

**Tech Stack:** Python 3.12, argparse, stdlib `tomllib`/`json`, SQLAlchemy `make_url`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-06-07-legis-doctor-design.md`

---

## File Structure

- **Create `src/legis/doctor.py`** — all doctor logic. Responsibilities: the `DoctorCheck` record, every check function (pure: `root: Path` + env → `DoctorCheck`, no mutation), the repair dispatch, the `run_doctor`/`machine_readable_doctor` orchestrators, and text/JSON rendering.
- **Modify `src/legis/install.py`** — add `register_mcp_json(project_root)` + `_legis_mcp_entry(agent_id)` (the `.mcp.json` writer/canonical entry), reusing `_find_legis_command`, `_atomic_write_text`, `reject_symlink`, `project_path`.
- **Modify `src/legis/cli.py`** — add the `doctor` subparser, a `--mcp` flag (+ optional `--agent-id`) on the `install` subparser and its step list, and a thin `_run_doctor` dispatcher.
- **Create `tests/test_doctor.py`** — mirrors `src/legis/doctor.py`.
- **Modify `tests/test_install.py`** — tests for `register_mcp_json`.
- **Modify `scripts/check_coverage_floors.py`** — (only if it enumerates modules) add a floor for `doctor.py`; otherwise the top-level src floor covers it.
- **Modify `CHANGELOG.md`**, **`README.md`** — document the new command.

Reused symbols (verify they exist before relying on them):
- `install.py`: `INSTRUCTIONS_MARKER`, `SKILL_NAME`, `SESSION_CONTEXT_COMMAND`, `_marker_token`, `_extract_marker_token`, `_get_skills_source_dir`, `_skill_tree_fingerprint`, `_has_unscoped_session_start_hook`, `_find_legis_command`, `_LEGIS_IGNORE_RULES`, `inject_instructions`, `install_skills`, `install_codex_skills`, `install_claude_code_hooks`, `ensure_gitignore`, `_atomic_write_text`, `reject_symlink`, `project_path`.
- `config.py`: `project_root`, `governance_db_url`, `binding_db_url`, `protected_policies`, `_store_dir`.
- `store/audit_store.py`: `AuditStore(url).verify_integrity() -> bool`.

---

## Task 1: `DoctorCheck` record + rendering + empty orchestrator

**Files:**
- Create: `src/legis/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_doctor.py
from __future__ import annotations

import json

from legis.doctor import DoctorCheck, render_json, render_text


def test_doctorcheck_to_dict_omits_empty_message():
    assert DoctorCheck("a.b", "ok").to_dict() == {"id": "a.b", "status": "ok", "fixed": False}
    assert DoctorCheck("a.b", "error", message="boom").to_dict() == {
        "id": "a.b",
        "status": "error",
        "fixed": False,
        "message": "boom",
    }


def test_render_json_shape():
    checks = [DoctorCheck("a", "ok"), DoctorCheck("b", "error", message="bad")]
    payload = json.loads(render_json(checks))
    assert payload["ok"] is False
    assert payload["checks"][0] == {"id": "a", "status": "ok", "fixed": False}
    assert payload["next_actions"] == ["b: bad"]


def test_render_text_lists_only_problems_when_healthy_says_ok():
    assert "legis doctor: ok" in render_text([DoctorCheck("a", "ok")])
    out = render_text([DoctorCheck("a", "ok"), DoctorCheck("b", "error", message="bad")])
    assert "b: bad" in out
    assert "legis doctor: ok" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'legis.doctor'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/legis/doctor.py
"""`legis doctor` — view and repair legis install/config health.

Operator/CLI tool only: it inspects and repairs the *host* install and legis's
own per-member artifacts. It is NOT on the agent MCP surface or the service
layer, and per hub doctrine C-9(b) it NEVER writes weft.toml.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    id: str
    status: str  # "ok" | "warn" | "error"
    fixed: bool = False
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status != "error"

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "status": self.status, "fixed": self.fixed}
        if self.message:
            data["message"] = self.message
        return data


def _next_actions(checks: list[DoctorCheck]) -> list[str]:
    return [f"{c.id}: {c.message}" for c in checks if c.status != "ok" and c.message]


def render_json(checks: list[DoctorCheck]) -> str:
    payload = {
        "ok": all(c.ok for c in checks),
        "checks": [c.to_dict() for c in checks],
        "next_actions": _next_actions(checks),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_text(checks: list[DoctorCheck]) -> str:
    healthy = all(c.status == "ok" for c in checks)
    if healthy:
        return "legis doctor: ok"
    lines = ["legis doctor:"]
    for c in checks:
        if c.status == "ok":
            continue
        lines.append(f"  {c.id}: {c.status} — {c.message}" if c.message else f"  {c.id}: {c.status}")
    return "\n".join(lines)
```

Note: `ok` is True for `warn` (non-fatal) and False only for `error`. `render_text`'s "all ok" banner uses strict `== "ok"` so warns still print.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): DoctorCheck record + text/json rendering"
```

---

## Task 2: `collect_checks` orchestrator + `run_doctor` (still no real checks)

**Files:**
- Modify: `src/legis/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
from pathlib import Path

from legis.doctor import run_doctor


def test_run_doctor_empty_is_healthy(tmp_path, capsys):
    # With no checks registered yet, an empty list renders healthy, exit 0.
    rc = run_doctor(tmp_path, repair=False, fmt="text")
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_run_doctor_json_format(tmp_path, capsys):
    rc = run_doctor(tmp_path, repair=False, fmt="json")
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"ok": True, "checks": [], "next_actions": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -k run_doctor -v`
Expected: FAIL with `ImportError: cannot import name 'run_doctor'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/legis/doctor.py
from pathlib import Path


def collect_checks(root: Path, *, repair: bool) -> list[DoctorCheck]:
    """Run every check against *root*. Repairs run inside individual checks
    when *repair* is True; each returned check reflects post-repair state."""
    checks: list[DoctorCheck] = []
    # Check functions are appended here in later tasks.
    return checks


def run_doctor(root: Path, *, repair: bool, fmt: str) -> int:
    checks = collect_checks(root, repair=repair)
    print(render_json(checks) if fmt == "json" else render_text(checks))
    return 0 if all(c.ok for c in checks) else 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -k run_doctor -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): collect_checks + run_doctor orchestrator skeleton"
```

---

## Task 3: CLI `doctor` subparser + dispatch (walking skeleton end-to-end)

**Files:**
- Modify: `src/legis/cli.py` (subparser in `build_parser`, dispatch in `main`)
- Test: `tests/test_cli.py` (or `tests/test_doctor.py` — match where CLI tests live)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
from legis.cli import main as cli_main


def test_cli_doctor_runs_and_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["doctor"])
    assert rc == 0
    assert "legis doctor: ok" in capsys.readouterr().out


def test_cli_doctor_json(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc = cli_main(["doctor", "--format", "json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -k cli_doctor -v`
Expected: FAIL — argparse exits non-zero / `doctor` is not a known subcommand.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/cli.py`, inside `build_parser()` (after the `install` subparser block, before `return parser`):

```python
    doctor = subparsers.add_parser(
        "doctor",
        help="View and repair legis install/config health",
    )
    doctor.add_argument("--root", default=".", help="Project root to inspect (default: cwd)")
    doctor.add_argument("--repair", action="store_true", help="Apply safe repairs, then re-check")
    doctor.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format: human text (default) or machine-readable json",
    )
```

Add a dispatcher function near `_check_override_rate`:

```python
def _run_doctor(args) -> int:
    from pathlib import Path

    from legis.doctor import run_doctor

    return run_doctor(Path(args.root), repair=args.repair, fmt=args.format)
```

In `main()`, add a branch alongside the other `args.command` checks:

```python
    if args.command == "doctor":
        return _run_doctor(args)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -k cli_doctor -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/cli.py tests/test_doctor.py
git commit -m "feat(doctor): wire 'legis doctor' CLI subcommand"
```

---

## Task 4: `register_mcp_json` install capability + `legis install --mcp`

**Files:**
- Modify: `src/legis/install.py` (add `_legis_mcp_entry`, `register_mcp_json`)
- Modify: `src/legis/cli.py` (`--mcp` flag + step in `_run_install`)
- Test: `tests/test_install.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_install.py
import json
from pathlib import Path

from legis.install import register_mcp_json, _legis_mcp_entry


def test_register_mcp_json_creates_file_with_legis_entry(tmp_path):
    ok, msg = register_mcp_json(tmp_path)
    assert ok, msg
    data = json.loads((tmp_path / ".mcp.json").read_text())
    entry = data["mcpServers"]["legis"]
    assert entry["type"] == "stdio"
    assert entry["args"][0] == "mcp"
    assert "--agent-id" in entry["args"]


def test_register_mcp_json_preserves_sibling_entries(tmp_path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"filigree": {"command": "x", "type": "stdio"}}})
    )
    ok, _ = register_mcp_json(tmp_path)
    assert ok
    data = json.loads((tmp_path / ".mcp.json").read_text())
    assert "filigree" in data["mcpServers"]
    assert "legis" in data["mcpServers"]


def test_register_mcp_json_idempotent(tmp_path):
    register_mcp_json(tmp_path)
    first = (tmp_path / ".mcp.json").read_text()
    register_mcp_json(tmp_path)
    assert (tmp_path / ".mcp.json").read_text() == first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_install.py -k mcp_json -v`
Expected: FAIL with `ImportError: cannot import name 'register_mcp_json'`.

- [ ] **Step 3: Write minimal implementation**

In `src/legis/install.py` (after the `.gitignore` section), add:

```python
# ---------------------------------------------------------------------------
# .mcp.json (agent MCP server registration)
# ---------------------------------------------------------------------------

import shlex

_DEFAULT_AGENT_ID = "claude-code"


def _legis_mcp_entry(agent_id: str = _DEFAULT_AGENT_ID) -> dict[str, Any]:
    """The canonical legis stdio server entry for .mcp.json."""
    return {
        "args": ["mcp", "--agent-id", agent_id],
        "command": _find_legis_command()[0] if len(_find_legis_command()) == 1 else shlex.join(_find_legis_command()),
        "env": {},
        "type": "stdio",
    }


def register_mcp_json(project_root: Path, agent_id: str = _DEFAULT_AGENT_ID) -> tuple[bool, str]:
    """Register (or refresh) the legis server in <root>/.mcp.json.

    Creates the file if absent; merges into mcpServers without disturbing
    sibling entries. Preserves an existing legis entry's agent-id if it already
    carries one (operator choice), refreshing only the command/args shape.
    """
    try:
        path = project_path(project_root, ".mcp.json")
    except UnsafeInstallPathError as exc:
        return False, str(exc)

    data: dict[str, Any] = {}
    if path.exists():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except (json.JSONDecodeError, OSError):
            return False, ".mcp.json present but unreadable; fix or remove it by hand"

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
        data["mcpServers"] = servers

    existing = servers.get("legis")
    keep_agent = agent_id
    if isinstance(existing, dict):
        args = existing.get("args", [])
        if isinstance(args, list) and "--agent-id" in args:
            i = args.index("--agent-id")
            if i + 1 < len(args) and isinstance(args[i + 1], str):
                keep_agent = args[i + 1]

    desired = _legis_mcp_entry(keep_agent)
    if existing == desired:
        return True, "legis already registered in .mcp.json"
    servers["legis"] = desired
    _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return True, "Registered legis server in .mcp.json"
```

Note: `Any` and `json` are already imported at the top of `install.py`; if not, add `from typing import Any` and `import json`. Move `import shlex` to the module top if a linter flags the inline import.

In `src/legis/cli.py` `build_parser()`, add to the `install` subparser:

```python
    install.add_argument("--mcp", action="store_true", help="Register the legis MCP server in .mcp.json only")
    install.add_argument(
        "--agent-id", default="claude-code",
        help="Agent id stamped in the .mcp.json legis entry (default: claude-code)",
    )
```

In `_run_install` (the `steps` list and the imports from `legis.install`), add `register_mcp_json` to the import and a step:

```python
        (install_all or args.mcp, ".mcp.json", lambda: register_mcp_json(project_root, args.agent_id)),
```

and update the `install_all` computation to include `args.mcp`:

```python
    install_all = not any(
        [args.claude_md, args.agents_md, args.skills, args.codex_skills, args.hooks, args.gitignore, args.mcp]
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_install.py -k mcp_json -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/install.py src/legis/cli.py tests/test_install.py
git commit -m "feat(install): register legis MCP server in .mcp.json (+ --mcp flag)"
```

---

## Task 5: doctor `.mcp.json` check + repair

**Files:**
- Modify: `src/legis/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
from legis.doctor import check_mcp_json


def test_mcp_json_absent_is_error(tmp_path):
    c = check_mcp_json(tmp_path, repair=False)
    assert c.id == "install.mcp_json"
    assert c.status == "error"
    assert c.fixed is False


def test_mcp_json_repair_fixes_it(tmp_path):
    c = check_mcp_json(tmp_path, repair=True)
    assert c.status == "ok"
    assert c.fixed is True
    assert (tmp_path / ".mcp.json").exists()


def test_mcp_json_present_is_ok(tmp_path):
    from legis.install import register_mcp_json
    register_mcp_json(tmp_path)
    c = check_mcp_json(tmp_path, repair=False)
    assert c.status == "ok"
    assert c.fixed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -k mcp_json -v`
Expected: FAIL — `cannot import name 'check_mcp_json'`.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/legis/doctor.py
import json as _json  # noqa: F401  (json already imported at top; reuse it)


def check_mcp_json(root: Path, *, repair: bool) -> DoctorCheck:
    cid = "install.mcp_json"
    path = root / ".mcp.json"
    present = False
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            present = isinstance(data, dict) and isinstance(data.get("mcpServers"), dict) and "legis" in data["mcpServers"]
        except (json.JSONDecodeError, OSError):
            present = False
    if present:
        return DoctorCheck(cid, "ok")
    if repair:
        from legis.install import register_mcp_json

        ok, msg = register_mcp_json(root)
        if ok:
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(cid, "error", message="legis server not registered (run: legis install --mcp)")
```

Remove the `import json as _json` line — `json` is already imported at the top of the module from Task 1; this note is a reminder, not new code. Then register the check in `collect_checks`:

```python
    checks.append(check_mcp_json(root, repair=repair))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -k mcp_json -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/legis/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): .mcp.json registration check + repair"
```

---

## Task 6: doctor install-wiring checks (blocks, skills, hook, gitignore)

**Files:**
- Modify: `src/legis/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
from legis.doctor import check_instruction_block, check_skill_pack, check_hook, check_gitignore
from legis import install as legis_install


def test_instruction_block_absent_is_error(tmp_path):
    c = check_instruction_block(tmp_path, "CLAUDE.md", repair=False)
    assert c.id == "install.claude_md"
    assert c.status == "error"


def test_instruction_block_repair_creates_it(tmp_path):
    c = check_instruction_block(tmp_path, "CLAUDE.md", repair=True)
    assert c.status == "ok"
    assert c.fixed is True
    assert legis_install.INSTRUCTIONS_MARKER in (tmp_path / "CLAUDE.md").read_text()


def test_gitignore_absent_is_error_then_repaired(tmp_path):
    assert check_gitignore(tmp_path, repair=False).status == "error"
    fixed = check_gitignore(tmp_path, repair=True)
    assert fixed.status == "ok" and fixed.fixed is True
    assert ".weft/legis/" in (tmp_path / ".gitignore").read_text()


def test_skill_pack_absent_is_error(tmp_path):
    assert check_skill_pack(tmp_path, ".claude", repair=False).status == "error"


def test_skill_pack_repair_installs(tmp_path):
    c = check_skill_pack(tmp_path, ".claude", repair=True)
    assert c.status == "ok" and c.fixed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -k "instruction_block or gitignore or skill_pack" -v`
Expected: FAIL — those check functions don't exist yet.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/legis/doctor.py
from legis import install as _install


def _block_fresh(root: Path, filename: str) -> bool:
    """True iff <root>/<filename> has the legis block at the current token."""
    path = root / filename
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if _install.INSTRUCTIONS_MARKER not in content:
        return False
    return _install._extract_marker_token(content) == _install._marker_token()


def check_instruction_block(root: Path, filename: str, *, repair: bool) -> DoctorCheck:
    cid = "install.claude_md" if filename == "CLAUDE.md" else "install.agents_md"
    if _block_fresh(root, filename):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = _install.inject_instructions(root / filename)
        if ok and _block_fresh(root, filename):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    missing = "missing" if not (root / filename).exists() else "block missing or drifted"
    return DoctorCheck(cid, "error", message=f"{filename} {missing} (run: legis install)")


def _skill_fresh(root: Path, base: str) -> bool:
    source = _install._get_skills_source_dir() / _install.SKILL_NAME
    target = root / base / "skills" / _install.SKILL_NAME
    if not source.is_dir() or not target.is_dir():
        return False
    return _install._skill_tree_fingerprint(target) == _install._skill_tree_fingerprint(source)


def check_skill_pack(root: Path, base: str, *, repair: bool) -> DoctorCheck:
    cid = "install.claude_skill" if base == ".claude" else "install.agents_skill"
    installer = _install.install_skills if base == ".claude" else _install.install_codex_skills
    if _skill_fresh(root, base):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = installer(root)
        if ok and _skill_fresh(root, base):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(cid, "error", message=f"{base}/skills/{_install.SKILL_NAME} missing or drifted (run: legis install)")


def _hook_present(root: Path) -> bool:
    settings_path = root / ".claude" / "settings.json"
    if not settings_path.exists():
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return _install._has_unscoped_session_start_hook(settings, _install.SESSION_CONTEXT_COMMAND)


def check_hook(root: Path, *, repair: bool) -> DoctorCheck:
    cid = "install.hook"
    if _hook_present(root):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = _install.install_claude_code_hooks(root)
        if ok and _hook_present(root):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(cid, "error", message="SessionStart hook not registered (run: legis install)")


def _gitignore_present(root: Path) -> bool:
    path = root / ".gitignore"
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    present = {ln.strip() for ln in content.splitlines() if ln.strip() and not ln.lstrip().startswith("#")}
    return all(rule in present for rule in _install._LEGIS_IGNORE_RULES)


def check_gitignore(root: Path, *, repair: bool) -> DoctorCheck:
    cid = "install.gitignore"
    if _gitignore_present(root):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = _install.ensure_gitignore(root)
        if ok and _gitignore_present(root):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(cid, "error", message=".weft/legis/ not in .gitignore (run: legis install)")
```

Register them in `collect_checks` (before the `.mcp.json` check):

```python
    checks.append(check_instruction_block(root, "CLAUDE.md", repair=repair))
    checks.append(check_instruction_block(root, "AGENTS.md", repair=repair))
    checks.append(check_skill_pack(root, ".claude", repair=repair))
    checks.append(check_skill_pack(root, ".agents", repair=repair))
    checks.append(check_hook(root, repair=repair))
    checks.append(check_gitignore(root, repair=repair))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -k "instruction_block or gitignore or skill_pack or hook" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): install-wiring checks (blocks, skills, hook, gitignore)"
```

---

## Task 7: doctor config & store checks (weft.toml report-only, store dir, db overrides, legacy)

**Files:**
- Modify: `src/legis/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
from legis.doctor import check_weft_toml, check_store_dir, check_db_overrides, check_legacy_stray_db


def test_weft_toml_absent_is_ok(tmp_path):
    assert check_weft_toml(tmp_path).status == "ok"


def test_weft_toml_valid_legis_table_is_ok(tmp_path):
    (tmp_path / "weft.toml").write_text('[legis]\nstore_dir = ".weft/legis"\n')
    assert check_weft_toml(tmp_path).status == "ok"


def test_weft_toml_malformed_is_error_and_unchanged(tmp_path):
    wt = tmp_path / "weft.toml"
    wt.write_text("[legis]\nstore_dir = \n")  # malformed TOML
    before = wt.read_text()
    c = check_weft_toml(tmp_path)
    assert c.status == "error"
    assert wt.read_text() == before  # C-9(b): never written


def test_weft_toml_legis_not_a_table_is_error(tmp_path):
    (tmp_path / "weft.toml").write_text('legis = "oops"\n')
    assert check_weft_toml(tmp_path).status == "error"


def test_store_dir_writable_parent_is_ok(tmp_path):
    assert check_store_dir(tmp_path).status == "ok"


def test_db_override_bad_url_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_GOVERNANCE_DB", "::not a url::")
    assert check_db_overrides(tmp_path).status == "error"


def test_legacy_stray_db_is_warn(tmp_path):
    (tmp_path / "legis-governance.db").write_text("x")
    assert check_legacy_stray_db(tmp_path).status == "warn"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -k "weft_toml or store_dir or db_override or legacy" -v`
Expected: FAIL — functions undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/legis/doctor.py
import os
import tomllib

from sqlalchemy.engine import make_url

_DB_OVERRIDE_ENVS = ("LEGIS_CHECK_DB", "LEGIS_GOVERNANCE_DB", "LEGIS_BINDING_DB", "LEGIS_PULL_DB")
_LEGACY_DB_NAMES = ("legis-checks.db", "legis-governance.db", "legis-binding.db", "legis-pulls.db")


def check_weft_toml(root: Path) -> DoctorCheck:
    """Report-only (C-9(b)): NEVER writes weft.toml. Distinguishes ABSENT (ok —
    defaults intentional) from PRESENT-BUT-BROKEN (error — config silently not
    applying), restoring the operator signal that C-9(c) silences at runtime."""
    cid = "config.weft_toml"
    path = root / "weft.toml"
    if not path.exists():
        return DoctorCheck(cid, "ok", message="absent (built-in defaults)")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        return DoctorCheck(cid, "error", message=f"present but unparseable; [legis] silently not applying ({exc})")
    table = data.get("legis")
    if table is not None and not isinstance(table, dict):
        return DoctorCheck(cid, "error", message="[legis] in weft.toml must be a table")
    return DoctorCheck(cid, "ok")


def _nearest_existing(path: Path) -> Path:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def check_store_dir(root: Path, *, repair: bool = False) -> DoctorCheck:
    """An absent .weft/legis/ is ok (created lazily). A present-but-unwritable
    dir is an error. --repair ensures the dir exists (explicit operator action)."""
    cid = "store.dir"
    from legis import config

    store_dir = (root / config._store_dir()) if not config._store_dir().is_absolute() else config._store_dir()
    if store_dir.exists():
        if not os.access(store_dir, os.W_OK):
            return DoctorCheck(cid, "error", message=f"{store_dir} not writable")
        return DoctorCheck(cid, "ok")
    if repair:
        try:
            store_dir.mkdir(parents=True, exist_ok=True)
            return DoctorCheck(cid, "ok", fixed=True)
        except OSError as exc:
            return DoctorCheck(cid, "error", message=f"cannot create {store_dir}: {exc}")
    anchor = _nearest_existing(store_dir)
    if not os.access(anchor, os.W_OK):
        return DoctorCheck(cid, "error", message=f"{store_dir} not creatable ({anchor} not writable)")
    return DoctorCheck(cid, "ok", message="absent (created on first store open)")


def check_db_overrides(root: Path) -> DoctorCheck:
    cid = "store.db_overrides"
    bad = []
    for env in _DB_OVERRIDE_ENVS:
        val = os.environ.get(env)
        if not val:
            continue
        try:
            make_url(val)
        except Exception:  # noqa: BLE001 — any parse failure is a bad override
            bad.append(env)
    if bad:
        return DoctorCheck(cid, "error", message="invalid URL in: " + ", ".join(bad))
    return DoctorCheck(cid, "ok")


def check_legacy_stray_db(root: Path) -> DoctorCheck:
    cid = "store.legacy_stray"
    stray = [n for n in _LEGACY_DB_NAMES if (root / n).is_file()]
    if stray:
        return DoctorCheck(cid, "warn", message="legacy DB at repo root (move to .weft/legis/): " + ", ".join(stray))
    return DoctorCheck(cid, "ok")
```

Register in `collect_checks`:

```python
    checks.append(check_weft_toml(root))
    checks.append(check_store_dir(root, repair=repair))
    checks.append(check_db_overrides(root))
    checks.append(check_legacy_stray_db(root))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -k "weft_toml or store_dir or db_override or legacy" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): config & store checks (weft.toml report-only, store dir, db overrides, legacy)"
```

---

## Task 8: doctor governance integrity + runtime/sibling checks

**Files:**
- Modify: `src/legis/doctor.py`
- Test: `tests/test_doctor.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
from legis.doctor import check_audit_chain, check_hmac_key, check_sibling_url


def test_audit_chain_absent_db_is_ok(tmp_path):
    c = check_audit_chain("store.governance_chain", "sqlite:///" + str(tmp_path / "nope.db"))
    assert c.status == "ok"


def test_audit_chain_intact_db_is_ok(tmp_path):
    from legis.store.audit_store import AuditStore
    url = "sqlite:///" + str(tmp_path / "gov.db")
    AuditStore(url)  # creates schema
    assert check_audit_chain("store.governance_chain", url).status == "ok"


def test_hmac_key_warn_when_protected_set_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "secrets.read")
    monkeypatch.delenv("LEGIS_HMAC_KEY", raising=False)
    c = check_hmac_key(tmp_path)
    assert c.status == "warn"


def test_hmac_key_never_prints_value(tmp_path, monkeypatch):
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "secrets.read")
    monkeypatch.setenv("LEGIS_HMAC_KEY", "super-secret-value")
    c = check_hmac_key(tmp_path)
    assert c.status == "ok"
    assert "super-secret-value" not in (c.message or "")


def test_sibling_url_invalid_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMWEAVE_API_URL", "localhost:9620")  # no scheme
    c = check_sibling_url("runtime.loomweave_url", "LOOMWEAVE_API_URL")
    assert c.status == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_doctor.py -k "audit_chain or hmac_key or sibling_url" -v`
Expected: FAIL — functions undefined.

- [ ] **Step 3: Write minimal implementation**

```python
# add to src/legis/doctor.py
from urllib.parse import urlsplit


def check_audit_chain(cid: str, url: str) -> DoctorCheck:
    """Report-only. Absent file store => ok (nothing to verify). A tampered
    chain => error (a hash chain cannot/must not be auto-repaired)."""
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001
        return DoctorCheck(cid, "ok", message="store URL not a file store")
    db = parsed.database
    if not str(parsed.drivername).startswith("sqlite") or not db or db == ":memory:":
        return DoctorCheck(cid, "ok", message="not a file store")
    if not Path(db).exists():
        return DoctorCheck(cid, "ok", message="no store yet")
    from legis.store.audit_store import AuditStore

    try:
        intact = AuditStore(url).verify_integrity()
    except Exception as exc:  # noqa: BLE001 — surface any verify failure, never raise from doctor
        return DoctorCheck(cid, "error", message=f"integrity check failed: {exc}")
    return DoctorCheck(cid, "ok") if intact else DoctorCheck(cid, "error", message="hash chain verification FAILED (report-only; cannot repair)")


def check_hmac_key(root: Path) -> DoctorCheck:
    """Presence-only; NEVER renders the key value."""
    cid = "runtime.hmac_key"
    from legis import config

    if not config.protected_policies():
        return DoctorCheck(cid, "ok", message="no protected policies configured")
    if os.environ.get("LEGIS_HMAC_KEY"):
        return DoctorCheck(cid, "ok")
    return DoctorCheck(cid, "warn", message="protected policies configured but LEGIS_HMAC_KEY not set; protected submissions will fail")


def check_sibling_url(cid: str, env: str) -> DoctorCheck:
    url = os.environ.get(env)
    if not url:
        return DoctorCheck(cid, "ok", message="not configured")
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return DoctorCheck(cid, "ok")
    return DoctorCheck(cid, "error", message=f"{env} invalid URL: {url!r}")
```

Register in `collect_checks`:

```python
    from legis import config

    checks.append(check_audit_chain("store.governance_chain", config.governance_db_url()))
    checks.append(check_audit_chain("store.binding_chain", config.binding_db_url()))
    checks.append(check_hmac_key(root))
    checks.append(check_sibling_url("runtime.loomweave_url", "LOOMWEAVE_API_URL"))
    checks.append(check_sibling_url("runtime.filigree_url", "FILIGREE_API_URL"))
```

Note: `config.governance_db_url()` / `binding_db_url()` resolve cwd-relative URLs. `collect_checks` must resolve them relative to `root`; if `root` is not cwd, run the resolution with cwd set to `root` — simplest is to compute these URLs inside a small helper that `os.chdir`-free resolves via `config._store_dir()` joined to `root`. To avoid cwd coupling in tests, compute the path directly:

```python
def _store_url(root: Path, db_name: str, env: str) -> str:
    val = os.environ.get(env)
    if val:
        return val
    from legis import config

    store_dir = config._store_dir()
    base = store_dir if store_dir.is_absolute() else (root / store_dir)
    return "sqlite:///" + (base / db_name).as_posix()
```

and call:

```python
    checks.append(check_audit_chain("store.governance_chain", _store_url(root, "legis-governance.db", "LEGIS_GOVERNANCE_DB")))
    checks.append(check_audit_chain("store.binding_chain", _store_url(root, "legis-binding.db", "LEGIS_BINDING_DB")))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_doctor.py -k "audit_chain or hmac_key or sibling_url" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/legis/doctor.py tests/test_doctor.py
git commit -m "feat(doctor): governance-chain integrity + runtime/sibling checks"
```

---

## Task 9: end-to-end `--repair` re-check + JSON regression test

**Files:**
- Test: `tests/test_doctor.py` (no new logic — repairs already run inside checks; this proves the whole pipeline)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_doctor.py
def test_repair_makes_fresh_project_healthy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # First run: unhealthy (no install artifacts, no .mcp.json).
    assert run_doctor(tmp_path, repair=False, fmt="text") == 1
    # Repair run: install-wiring + .mcp.json get fixed; re-check is healthy.
    assert run_doctor(tmp_path, repair=True, fmt="text") == 0
    # Third run, no repair: stays healthy.
    assert run_doctor(tmp_path, repair=False, fmt="text") == 0


def test_repair_never_writes_weft_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "weft.toml").write_text("[legis]\nstore_dir = \n")  # malformed
    before = (tmp_path / "weft.toml").read_text()
    run_doctor(tmp_path, repair=True, fmt="json")
    assert (tmp_path / "weft.toml").read_text() == before


def test_json_output_has_no_secret(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LEGIS_PROTECTED_POLICIES", "secrets.read")
    monkeypatch.setenv("LEGIS_HMAC_KEY", "TOP-SECRET")
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_doctor(tmp_path, repair=False, fmt="json")
    assert "TOP-SECRET" not in buf.getvalue()
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `uv run pytest tests/test_doctor.py -k "repair_makes or never_writes or no_secret" -v`
Expected: PASS if Tasks 5–8 are wired correctly. If `test_repair_makes_fresh_project_healthy` fails, the offending check's `repair=True` branch isn't reaching `ok` — fix that check, not this test.

- [ ] **Step 3: (only if a test failed) fix the implicated check**

No new code if green. If red, the failing check is reported by name in the assertion — return to that check's task and correct its repair branch.

- [ ] **Step 4: Run the full doctor test file**

Run: `uv run pytest tests/test_doctor.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add tests/test_doctor.py
git commit -m "test(doctor): end-to-end repair pipeline + weft.toml/secret invariants"
```

---

## Task 10: docs, coverage floor, and full gate run

**Files:**
- Modify: `CHANGELOG.md`, `README.md`
- Modify: `scripts/check_coverage_floors.py` (only if it lists modules explicitly)

- [ ] **Step 1: Update CHANGELOG and README**

Add to `CHANGELOG.md` under the unreleased/rc4 section:

```markdown
### Added
- `legis doctor [--root] [--repair] [--format text|json]` — operator health view
  and safe repair for the install + config layer (instruction blocks, skills,
  SessionStart hook, `.gitignore`, `.mcp.json` registration, store dir, audit
  hash-chain integrity, key/sibling wiring). Report-only on `weft.toml` (C-9(b))
  and on hash chains; key values are never rendered.
- `legis install --mcp` — register the legis MCP server in `.mcp.json`
  (also part of `legis install` with no flags).
```

In `README.md`, under the surfaces/commands section, add a `legis doctor` line mirroring the existing `legis install` description.

- [ ] **Step 2: Run the full test suite + lint + types**

Run:
```bash
uv run ruff check src
uv run mypy src/legis
uv run pytest -q
```
Expected: ruff clean, mypy clean, all tests pass.

- [ ] **Step 3: Run coverage floors**

Run: `uv run pytest --cov=legis --cov-report=term-missing && uv run python scripts/check_coverage_floors.py`
Expected: floors hold. If `check_coverage_floors.py` enumerates packages and `doctor.py` is top-level (not in a covered package dir), confirm it falls under the global floor; if the script needs a per-module entry, add one a few points below the achieved coverage.

- [ ] **Step 4: Manual smoke test**

Run:
```bash
cd /tmp && rm -rf doctortest && mkdir doctortest && cd doctortest
legis doctor                 # expect: several errors (fresh dir), exit 1
legis doctor --repair        # expect: install wiring + .mcp.json fixed, exit 0
legis doctor --format json   # expect: {"ok": true, ...}
```
Expected: matches the comments.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md scripts/check_coverage_floors.py
git commit -m "docs(doctor): changelog + readme for legis doctor; coverage floor"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** install wiring (T6) ✓, `.mcp.json` install+check (T4/T5) ✓, config & stores (T7) ✓, governance integrity (T8) ✓, runtime & siblings (T8) ✓, `--repair` model (repairs live inside checks; T9 proves it) ✓, JSON shape + exit codes (T1/T2/T9) ✓, weft.toml never-written invariant (T7/T9) ✓, key-value-never-shown invariant (T8/T9) ✓.
- **C-9(b) guard** is asserted by `test_weft_toml_malformed_is_error_and_unchanged` and `test_repair_never_writes_weft_toml`.
- **No-leak guard:** `check_audit_chain` constructs `AuditStore` only when the DB file already exists; `check_store_dir` creates `.weft/legis/` only under `--repair`.
- **Verify reused private symbols exist** before Task 6/8 (`SESSION_CONTEXT_COMMAND`, `SKILL_NAME`, `_has_unscoped_session_start_hook`, `_LEGIS_IGNORE_RULES`, `_extract_marker_token`, `_marker_token`). If any name differs, adjust the call site — do not duplicate the logic.
