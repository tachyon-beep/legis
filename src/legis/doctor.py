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


def collect_checks(root: Path, *, repair: bool) -> list[DoctorCheck]:
    """Run every check against *root*. Repairs run inside individual checks
    when *repair* is True; each returned check reflects post-repair state."""
    checks: list[DoctorCheck] = []
    checks.append(check_mcp_json(root, repair=repair))
    return checks


def run_doctor(root: Path, *, repair: bool, fmt: str) -> int:
    checks = collect_checks(root, repair=repair)
    print(render_json(checks) if fmt == "json" else render_text(checks))
    return 0 if all(c.ok for c in checks) else 1
