"""`legis doctor` — view and repair legis install/config health.

Operator/CLI tool only: it inspects and repairs the *host* install and legis's
own per-member artifacts. It is NOT on the agent MCP surface or the service
layer, and per hub doctrine C-9(b) it NEVER writes weft.toml.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

from legis import install as _install


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


def check_mcp_json(root: Path, *, repair: bool) -> DoctorCheck:
    """Check that `.mcp.json` exists and has a `legis` server entry."""
    cid = "install.mcp_json"
    path = root / ".mcp.json"
    present = False
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            present = (
                isinstance(data, dict)
                and isinstance(data.get("mcpServers"), dict)
                and "legis" in data["mcpServers"]
            )
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
    return DoctorCheck(
        cid, "error", message="legis server not registered (run: legis install --mcp)"
    )


# ---------------------------------------------------------------------------
# Install-wiring checks (Task 6)
# ---------------------------------------------------------------------------


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
    """Check that <root>/<filename> has the legis instruction block at the current token."""
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
    """True iff the skill pack under <root>/<base>/skills/ matches the source fingerprint."""
    source = _install._get_skills_source_dir() / _install.SKILL_NAME
    target = root / base / "skills" / _install.SKILL_NAME
    if not source.is_dir() or not target.is_dir():
        return False
    return _install._skill_tree_fingerprint(target) == _install._skill_tree_fingerprint(source)


def check_skill_pack(root: Path, base: str, *, repair: bool) -> DoctorCheck:
    """Check that the legis skill pack under <root>/<base>/skills/ is present and fresh."""
    cid = "install.claude_skill" if base == ".claude" else "install.agents_skill"
    installer = _install.install_skills if base == ".claude" else _install.install_codex_skills
    if _skill_fresh(root, base):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = installer(root)
        if ok and _skill_fresh(root, base):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(
        cid,
        "error",
        message=f"{base}/skills/{_install.SKILL_NAME} missing or drifted (run: legis install)",
    )


def _hook_present(root: Path) -> bool:
    """True iff the SessionStart hook is registered in .claude/settings.json."""
    settings_path = root / ".claude" / "settings.json"
    if not settings_path.exists():
        return False
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return _install._has_unscoped_session_start_hook(settings, _install.SESSION_CONTEXT_COMMAND)


def check_hook(root: Path, *, repair: bool) -> DoctorCheck:
    """Check that the legis SessionStart hook is registered."""
    cid = "install.hook"
    if _hook_present(root):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = _install.install_claude_code_hooks(root)
        if ok and _hook_present(root):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(cid, "error", message="SessionStart hook not registered (run: legis install)")


def check_gitignore(root: Path, *, repair: bool) -> DoctorCheck:
    """Check that legis .gitignore rules are present."""
    cid = "install.gitignore"
    if _install.gitignore_rules_present(root):
        return DoctorCheck(cid, "ok")
    if repair:
        ok, msg = _install.ensure_gitignore(root)
        if ok and _install.gitignore_rules_present(root):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(cid, "error", message=".weft/legis/ not in .gitignore (run: legis install)")


# ---------------------------------------------------------------------------
# Task 7: config & store checks
# ---------------------------------------------------------------------------

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
        return DoctorCheck(
            cid,
            "error",
            message=f"present but unparseable; [legis] silently not applying ({exc})",
        )
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

    store_dir_rel = config._store_dir()
    store_dir = store_dir_rel if store_dir_rel.is_absolute() else (root / store_dir_rel)
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


def check_db_overrides(root: Path) -> DoctorCheck:  # noqa: ARG001
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
        return DoctorCheck(
            cid,
            "warn",
            message="legacy DB at repo root (move to .weft/legis/): " + ", ".join(stray),
        )
    return DoctorCheck(cid, "ok")


def collect_checks(root: Path, *, repair: bool) -> list[DoctorCheck]:
    """Run every check against *root*. Repairs run inside individual checks
    when *repair* is True; each returned check reflects post-repair state."""
    checks: list[DoctorCheck] = []
    checks.append(check_instruction_block(root, "CLAUDE.md", repair=repair))
    checks.append(check_instruction_block(root, "AGENTS.md", repair=repair))
    checks.append(check_skill_pack(root, ".claude", repair=repair))
    checks.append(check_skill_pack(root, ".agents", repair=repair))
    checks.append(check_hook(root, repair=repair))
    checks.append(check_gitignore(root, repair=repair))
    checks.append(check_mcp_json(root, repair=repair))
    checks.append(check_weft_toml(root))
    checks.append(check_store_dir(root, repair=repair))
    checks.append(check_db_overrides(root))
    checks.append(check_legacy_stray_db(root))
    return checks


def run_doctor(root: Path, *, repair: bool, fmt: str) -> int:
    checks = collect_checks(root, repair=repair)
    print(render_json(checks) if fmt == "json" else render_text(checks))
    return 0 if all(c.ok for c in checks) else 1
