"""One-off policy exemptions — the decorator's companion (WP-A8).

``ExemptionAllowlist`` loads the roadmap-facing YAML format: each exemption must
carry ``policy``, ``entity``, and ``rationale``, and a missing file exempts
nothing. ``load_exemptions`` keeps the earlier TOML registry API for existing
callers. Both surfaces fail closed on malformed entries so a typo never silently
widens what is exempt.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml


class ExemptionError(RuntimeError):
    """A malformed one-off exemption allowlist entry."""


@dataclass(frozen=True)
class Exemption:
    policy: str
    value: str
    reason: str

    @property
    def entity(self) -> str:
        return self.value

    @property
    def rationale(self) -> str:
        return self.reason


class ExemptionRegistry:
    def __init__(self, exemptions: Iterable[Exemption]) -> None:
        # Duplicate (policy, value) keys are last-entry-wins; harmless, since
        # both entries address the same key and cannot widen the exempt surface.
        self._by_key: dict[tuple[str, str], Exemption] = {
            (e.policy, e.value): e for e in exemptions
        }

    def is_exempt(self, policy: str, value: str) -> Exemption | None:
        return self._by_key.get((policy, value))


class ExemptionAllowlist:
    """YAML one-off exemption allowlist, matching the roadmap-facing API."""

    def __init__(self, exemptions: Iterable[Exemption]) -> None:
        self._registry = ExemptionRegistry(exemptions)

    @classmethod
    def from_file(cls, path: str | Path) -> "ExemptionAllowlist":
        p = Path(path)
        if not p.exists():
            return cls([])
        raw = yaml.safe_load(p.read_text()) or {}
        if not isinstance(raw, dict):
            raise ExemptionError("exemption allowlist must be a YAML mapping")
        entries = raw.get("exemptions", [])
        if not isinstance(entries, list):
            raise ExemptionError("exemptions must be a YAML list")
        exemptions: list[Exemption] = []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ExemptionError(
                    f"exemption #{i} is malformed: expected a mapping"
                )
            missing = []
            for key in ("policy", "entity", "rationale"):
                value = entry.get(key)
                if value is None or (isinstance(value, str) and not value.strip()):
                    missing.append(key)
            if missing:
                raise ExemptionError(
                    f"exemption #{i} missing required field(s): {', '.join(missing)}"
                )
            exemptions.append(
                Exemption(
                    policy=str(entry["policy"]),
                    value=str(entry["entity"]),
                    reason=str(entry["rationale"]),
                )
            )
        return cls(exemptions)

    def is_exempt(self, policy: str, entity: str) -> bool:
        return self._registry.is_exempt(policy, entity) is not None

    def exemption(self, policy: str, entity: str) -> Exemption | None:
        return self._registry.is_exempt(policy, entity)


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
