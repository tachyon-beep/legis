"""Policy-to-cell registry for the agent-facing MCP surface.

The registry is deliberately declarative and stdlib-only. Agents submit opaque
policy names; the server maps them to governance cells and reports the mapping
back through ``policy_explain`` and ``override_submit``.
"""

from __future__ import annotations

import fnmatch
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


VALID_CELLS = frozenset({"chill", "coached", "structured", "protected"})


@dataclass(frozen=True)
class PolicyCellRule:
    pattern: str
    cell: str


class PolicyCellRegistry:
    def __init__(
        self, default_cell: str, rules: Iterable[PolicyCellRule] = ()
    ) -> None:
        self.default_cell = _validate_cell(default_cell, "default_cell")
        self._rules = tuple(_validate_rule(i, rule) for i, rule in enumerate(rules))

    def cell_for(self, policy: str) -> str:
        for rule in self._rules:
            if not _has_glob(rule.pattern) and rule.pattern == policy:
                return rule.cell
        for rule in self._rules:
            if _has_glob(rule.pattern) and fnmatch.fnmatchcase(policy, rule.pattern):
                return rule.cell
        return self.default_cell


def default_policy_cells() -> PolicyCellRegistry:
    return PolicyCellRegistry(default_cell="chill")


def load_policy_cells(path: str | Path) -> PolicyCellRegistry:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)

    default_cell = data.get("default_cell")
    if not isinstance(default_cell, str) or not default_cell.strip():
        raise ValueError("missing/empty default_cell")

    raw_rules = data.get("policy", [])
    if not isinstance(raw_rules, list):
        raise ValueError(
            "policy table must be an array of tables ([[policy]]), "
            f"got {type(raw_rules).__name__!r}"
        )

    rules: list[PolicyCellRule] = []
    for i, entry in enumerate(raw_rules):
        if not isinstance(entry, dict):
            raise ValueError(
                f"policy[{i}] is malformed: expected a table ([[policy]]), "
                f"got {type(entry).__name__!r}"
            )
        pattern = entry.get("pattern")
        cell = entry.get("cell")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"policy[{i}] is malformed: missing/empty pattern")
        if not isinstance(cell, str) or not cell.strip():
            raise ValueError(f"policy[{i}] is malformed: missing/empty cell")
        rules.append(PolicyCellRule(pattern=pattern, cell=cell))

    return PolicyCellRegistry(default_cell=default_cell, rules=rules)


def _validate_rule(index: int, rule: PolicyCellRule) -> PolicyCellRule:
    pattern = rule.pattern.strip()
    if not pattern:
        raise ValueError(f"policy[{index}] is malformed: missing/empty pattern")
    return PolicyCellRule(
        pattern=pattern,
        cell=_validate_cell(rule.cell, f"policy[{index}].cell"),
    )


def _validate_cell(raw: str, location: str) -> str:
    cell = raw.strip()
    if cell not in VALID_CELLS:
        allowed = ", ".join(sorted(VALID_CELLS))
        raise ValueError(f"{location} has unknown cell {cell!r}; expected one of: {allowed}")
    return cell


def _has_glob(pattern: str) -> bool:
    return any(char in pattern for char in "*?[")
