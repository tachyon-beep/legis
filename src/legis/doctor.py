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
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from legis import config
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
    has_error = any(c.status == "error" for c in checks)
    has_warn = any(c.status == "warn" for c in checks)
    problems = [c for c in checks if c.status != "ok"]
    if not has_error:
        # warn-only or all-ok: the project is healthy; surface any warns below
        if has_warn:
            warn_count = sum(1 for c in checks if c.status == "warn")
            lines = [f"legis doctor: ok ({warn_count} warning(s))"]
        else:
            return "legis doctor: ok"
    else:
        lines = ["legis doctor:"]
    for c in problems:
        lines.append(f"  {c.id}: {c.status} — {c.message}" if c.message else f"  {c.id}: {c.status}")
    return "\n".join(lines)


def check_mcp_json(root: Path, *, repair: bool) -> DoctorCheck:
    """Check that `.mcp.json` has a current legis server entry.

    'Current' means: a legis entry exists, its args invoke `mcp`, and its
    command resolves to an existing executable. Byte-equality with the canonical
    entry is deliberately NOT required — a valid but differently-resolved legis
    binary (uv-tool vs venv path) must not read as drift.
    """
    cid = "install.mcp_json"
    if _install.mcp_entry_is_current(root):
        return DoctorCheck(cid, "ok")
    if repair:
        from legis.install import register_mcp_json

        ok, msg = register_mcp_json(root)
        if ok and _install.mcp_entry_is_current(root):
            return DoctorCheck(cid, "ok", fixed=True)
        return DoctorCheck(cid, "error", message=msg)
    return DoctorCheck(
        cid, "error", message="legis server missing or stale (run: legis install --mcp)"
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

# Sourced from config's single store-identity registry so adding a store there
# can't silently drop doctor coverage (review #2).
_DB_OVERRIDE_ENVS = tuple(env for env, _ in config.STORE_DB_SPECS)
_LEGACY_DB_NAMES = tuple(name for _, name in config.STORE_DB_SPECS)


def _store_dir_for(root: Path) -> Path:
    """legis's store dir resolved from root/weft.toml (root-anchored, never cwd).
    Returns an absolute path: an operator-set absolute store_dir is honored as-is;
    otherwise the (relative) store_dir / default is joined to root. Malformed
    weft.toml falls back to the default (check_weft_toml reports the malformed file)."""
    configured: Path | None = None
    wt = root / "weft.toml"
    if wt.exists():
        try:
            data = tomllib.loads(wt.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
            data = {}
        legis = data.get("legis")
        if isinstance(legis, dict):
            sd = legis.get("store_dir")
            if isinstance(sd, str) and sd:
                configured = Path(sd)
    store_dir = configured if configured is not None else Path(".weft") / "legis"
    return store_dir if store_dir.is_absolute() else (root / store_dir)


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
    store_dir = _store_dir_for(root)
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
        # Match config's precedence: a present-but-empty override is a verbatim
        # (broken) override, not "unset" — so validate membership, not truthiness.
        if env not in os.environ:
            continue
        try:
            make_url(os.environ[env])
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


# ---------------------------------------------------------------------------
# Task 8: governance integrity + runtime/sibling checks
# ---------------------------------------------------------------------------


def _store_url(root: Path, db_name: str, env: str) -> str:
    """Resolve a store URL anchored at *root* via ``root/weft.toml`` (never cwd).
    The LEGIS_*_DB override wins when set (present-but-empty included, matching
    config's verbatim-override precedence); otherwise a file URL is built under
    the root-anchored store_dir."""
    if env in os.environ:
        return os.environ[env]
    return "sqlite:///" + (_store_dir_for(root) / db_name).as_posix()


def check_audit_chain(cid: str, url: str) -> DoctorCheck:
    """Report-only. Absent file store => ok (nothing to verify; must NOT create
    the DB). A tampered chain => error (cannot/must not be auto-repaired)."""
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001
        return DoctorCheck(cid, "ok", message="store URL not a file store")
    db = parsed.database
    if parsed.get_backend_name() != "sqlite" or not db or db == ":memory:":
        return DoctorCheck(cid, "ok", message="not a file store")
    if not Path(db).exists():
        return DoctorCheck(cid, "ok", message="no store yet")
    from legis.store.audit_store import AuditStore

    try:
        intact = AuditStore(url).verify_integrity()
    except Exception as exc:  # noqa: BLE001 — surface any verify failure, never raise from doctor
        return DoctorCheck(cid, "error", message=f"integrity check failed: {exc}")
    if intact:
        return DoctorCheck(cid, "ok")
    return DoctorCheck(
        cid, "error", message="hash chain verification FAILED (report-only; cannot repair)"
    )


def check_hmac_key(root: Path) -> DoctorCheck:  # noqa: ARG001
    """Presence-only; NEVER renders the key value."""
    cid = "runtime.hmac_key"
    if not config.protected_policies():
        return DoctorCheck(cid, "ok", message="no protected policies configured")
    if os.environ.get("LEGIS_HMAC_KEY"):
        return DoctorCheck(cid, "ok")
    return DoctorCheck(
        cid,
        "warn",
        message="protected policies configured but LEGIS_HMAC_KEY not set; protected submissions will fail",
    )


def check_sibling_url(cid: str, env: str) -> DoctorCheck:
    url = os.environ.get(env)
    if not url:
        return DoctorCheck(cid, "ok", message="not configured")
    parsed = urlsplit(url)
    if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
        return DoctorCheck(cid, "ok")
    return DoctorCheck(cid, "error", message=f"{env} invalid URL: {url!r}")


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
    checks.append(check_audit_chain("store.governance_chain", _store_url(root, "legis-governance.db", "LEGIS_GOVERNANCE_DB")))
    checks.append(check_audit_chain("store.binding_chain", _store_url(root, "legis-binding.db", "LEGIS_BINDING_DB")))
    checks.append(check_hmac_key(root))
    checks.append(check_sibling_url("runtime.loomweave_url", "LOOMWEAVE_API_URL"))
    checks.append(check_sibling_url("runtime.filigree_url", "FILIGREE_API_URL"))
    return checks


def run_doctor(root: Path, *, repair: bool, fmt: str) -> int:
    checks = collect_checks(root, repair=repair)
    print(render_json(checks) if fmt == "json" else render_text(checks))
    return 0 if all(c.ok for c in checks) else 1
