"""One-off policy exemptions — the decorator's companion (WP-A8).

A TOML file lists explicit, attributable exemptions: a proven VIOLATION for an
exempted ``(policy, value)`` is downgraded to CLEAR with the exemption reason as
provenance. Loaded via stdlib ``tomllib`` (no new dependency). A malformed file
or entry fails closed — it raises rather than yielding a partial registry, so a
typo can never silently widen what is exempt. (The roadmap names this a "YAML
allowlist"; TOML is the substance-equivalent that holds legis's no-new-dependency
posture.)
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Exemption:
    policy: str
    value: str
    reason: str


class ExemptionRegistry:
    def __init__(self, exemptions: Iterable[Exemption]) -> None:
        # Duplicate (policy, value) keys are last-entry-wins; harmless, since
        # both entries address the same key and cannot widen the exempt surface.
        self._by_key: dict[tuple[str, str], Exemption] = {
            (e.policy, e.value): e for e in exemptions
        }

    def is_exempt(self, policy: str, value: str) -> Exemption | None:
        return self._by_key.get((policy, value))


def load_exemptions(path: str | Path) -> ExemptionRegistry:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)  # malformed TOML raises tomllib.TOMLDecodeError
    raw = data.get("exemption", [])
    if not isinstance(raw, list):
        raise ValueError(
            "exemption table must be an array of tables ([[exemption]]), "
            f"got {type(raw).__name__!r}"
        )
    exemptions: list[Exemption] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"exemption[{i}] is malformed: expected a table ([[exemption]]), "
                f"got {type(entry).__name__!r}"
            )
        missing = []
        for k in ("policy", "value", "reason"):
            if k not in entry:
                missing.append(k)
            else:
                val = entry[k]
                if val is None or (isinstance(val, str) and not val.strip()):
                    missing.append(k)
        if missing:
            raise ValueError(
                f"exemption[{i}] is malformed: missing/empty {', '.join(missing)}"
            )
        exemptions.append(Exemption(str(entry["policy"]), str(entry["value"]), str(entry["reason"])))
    return ExemptionRegistry(exemptions)
