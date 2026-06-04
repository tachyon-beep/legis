"""Service-level discovery contract for the MCP ``policy_explain`` tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legis.enforcement.engine import EnforcementEngine
from legis.policy.cells import PolicyCellRegistry


@dataclass(frozen=True)
class RequiredInput:
    field: str
    how: str

    def to_payload(self) -> dict[str, str]:
        return {"field": self.field, "how": self.how}


@dataclass(frozen=True)
class PolicyExplanation:
    cell: str
    judge_inline: bool
    self_clearable: bool
    human_in_loop: bool
    enabled: bool
    available_moves: tuple[str, ...]
    required_inputs: tuple[RequiredInput, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "judge_inline": self.judge_inline,
            "self_clearable": self.self_clearable,
            "human_in_loop": self.human_in_loop,
            "enabled": self.enabled,
            "available_moves": list(self.available_moves),
            "required_inputs": [
                item.to_payload() for item in self.required_inputs
            ],
        }


_PROTECTED_INPUTS = (
    RequiredInput(
        field="file_fingerprint",
        how="sha256 of the target file contents",
    ),
    RequiredInput(
        field="ast_path",
        how="dotted path to the AST node",
    ),
)


def explain_policy(
    registry: PolicyCellRegistry,
    *,
    policy: str,
    entity: str,
    engine: EnforcementEngine | None,
    protected_gate: object | None,
    signoff_gate: object | None,
) -> PolicyExplanation:
    """Explain the governing cell and legal moves for a policy/entity pair.

    ``entity`` is accepted now because it is part of the ratified tool contract.
    The v1 registry routes by policy only, so the value is not used for routing.
    """
    del entity
    cell = registry.cell_for(policy)
    if cell == "chill":
        enabled = engine is not None and not engine.has_judge
        return PolicyExplanation(
            cell="chill",
            judge_inline=False,
            self_clearable=True,
            human_in_loop=False,
            enabled=enabled,
            available_moves=("override_submit",) if enabled else (),
            required_inputs=(),
        )
    if cell == "coached":
        enabled = engine is not None and engine.has_judge
        return PolicyExplanation(
            cell="coached",
            judge_inline=True,
            self_clearable=False,
            human_in_loop=False,
            enabled=enabled,
            available_moves=("override_submit",) if enabled else (),
            required_inputs=(),
        )
    if cell == "structured":
        enabled = signoff_gate is not None
        return PolicyExplanation(
            cell="structured",
            judge_inline=False,
            self_clearable=False,
            human_in_loop=True,
            enabled=enabled,
            available_moves=(
                "override_submit",
                "signoff_status_get",
            )
            if enabled
            else (),
            required_inputs=(),
        )
    if cell == "protected":
        enabled = protected_gate is not None
        return PolicyExplanation(
            cell="protected",
            judge_inline=True,
            self_clearable=False,
            human_in_loop=False,
            enabled=enabled,
            available_moves=("override_submit",) if enabled else (),
            required_inputs=_PROTECTED_INPUTS,
        )
    raise AssertionError(f"unknown policy cell {cell!r}")
