"""`legis doctor` — view and repair legis install/config health.

Operator/CLI tool only: it inspects and repairs the *host* install and legis's
own per-member artifacts. It is NOT on the agent MCP surface or the service
layer, and per hub doctrine C-9(b) it NEVER writes weft.toml.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    return checks


def run_doctor(root: Path, *, repair: bool, fmt: str) -> int:
    checks = collect_checks(root, repair=repair)
    print(render_json(checks) if fmt == "json" else render_text(checks))
    return 0 if all(c.ok for c in checks) else 1
