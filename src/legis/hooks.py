"""SessionStart / MCP-boot refresh for legis instruction artifacts.

Two callers drive ``refresh_instructions``:

- the ``legis session-context`` CLI subcommand, registered as a Claude Code
  SessionStart hook, and
- ``legis mcp`` startup (best-effort), which is the universal trigger that also
  covers Codex-only repos with no ``.claude/`` hook.

Both refresh *in place* only — they never create a block or skill pack that is
not already present (that is ``legis install``'s job). A non-project cwd simply
produces no work, because the refresh only ever touches marker-bearing files.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from legis.install import (
    INSTRUCTIONS_MARKER,
    SKILL_NAME,
    _get_skills_source_dir,
    _marker_token,
    _skill_tree_fingerprint,
    inject_instructions,
    install_codex_skills,
    install_skills,
)

logger = logging.getLogger(__name__)

_MARKER_TOKEN_RE = re.compile(r"<!-- legis:instructions:(v[^:]+:[0-9a-f]+) -->")


def _extract_marker_token(content: str) -> str | None:
    """Return the ``v{version}:{hash}`` token from a legis marker, or ``None``."""
    m = _MARKER_TOKEN_RE.search(content)
    return m.group(1) if m else None


def refresh_instructions(root: Path) -> list[str]:
    """Refresh drifted legis instruction blocks and skill packs under *root*.

    Compares the embedded ``v{version}:{hash}`` token against the current one
    for ``CLAUDE.md`` / ``AGENTS.md`` (re-injecting on drift), and each installed
    skill pack's tree fingerprint against the bundled source (reinstalling on
    drift). Returns human-readable update messages (empty when everything is
    current). Only marker-bearing files and already-installed skill packs are
    touched.
    """
    messages: list[str] = []
    current_token = _marker_token()

    for filename in ("CLAUDE.md", "AGENTS.md"):
        md_path = root / filename
        if not md_path.exists():
            continue
        try:
            content = md_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            logger.debug("Could not read %s for freshness check", md_path, exc_info=True)
            continue
        if INSTRUCTIONS_MARKER not in content:
            continue
        if _extract_marker_token(content) == current_token:
            continue
        ok, reason = inject_instructions(md_path)
        if ok:
            messages.append(f"Updated legis instructions in {filename}")
        else:
            # Drift was detected and re-injection was attempted but failed
            # (e.g. a symlinked target → (False, reason), not a raise). Never
            # drop it: agents would keep running on drifted instructions with no
            # signal. Surface it for the operator (peer of the boot-log path).
            logger.warning(
                "legis instruction refresh failed for %s: %s", md_path, reason
            )

    source_root = _get_skills_source_dir() / SKILL_NAME
    if source_root.is_dir():
        source_hash = _skill_tree_fingerprint(source_root)
        skill_targets = (
            (root / ".claude" / "skills" / SKILL_NAME, install_skills, "Updated legis skill pack"),
            (root / ".agents" / "skills" / SKILL_NAME, install_codex_skills, "Updated legis Codex skill pack"),
        )
        for target_root, installer, msg in skill_targets:
            if not target_root.is_dir():
                continue
            if _skill_tree_fingerprint(target_root) != source_hash:
                ok, reason = installer(root)
                if ok:
                    messages.append(msg)
                else:
                    logger.warning(
                        "legis skill refresh failed for %s: %s", target_root, reason
                    )

    return messages


def generate_session_context() -> str | None:
    """Refresh instruction drift in the cwd and return any update messages.

    Returns ``None`` when nothing changed (silent SessionStart output — legis
    keeps no project snapshot and depends on no governance database here).
    """
    try:
        messages = refresh_instructions(Path.cwd())
    except (OSError, UnicodeDecodeError, ValueError):
        logger.warning("Instruction freshness check failed", exc_info=True)
        return None
    if not messages:
        return None
    return "\n".join(messages)
