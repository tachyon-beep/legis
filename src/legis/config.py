"""Shared default store locations — the single source for the governance and
check database URLs.

These previously lived on ``legis.api.app``, which forced ``mcp`` (and any
other composition root) to import from the HTTP layer just to learn where the
governance store lives (Q-H2). They are transport-agnostic configuration, so
they belong here; ``api`` and ``mcp`` both import them from this module.
"""

from __future__ import annotations

DEFAULT_CHECK_DB = "sqlite:///legis-checks.db"
DEFAULT_GOVERNANCE_DB = "sqlite:///legis-governance.db"
