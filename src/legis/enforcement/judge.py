"""The coached-cell judge: an interactive wall, not a code generator.

The judge's *logic* — prompt construction, fail-closed verdict parsing, and
model-identity capture — is real and fully tested. Only the model call itself
sits behind the injected ``LLMClient`` seam, so tests need no network and a
production deployment wires a real client. Borrowed *effect* from elspeth's CI
judge, not its vocabulary.
"""

from __future__ import annotations

import re
from typing import Protocol

from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.records.override_record import OverrideRecord

_TOKEN = re.compile(r"[A-Z]+")


def parse_verdict(raw: str) -> Verdict:
    """Read a model response as a verdict, fail-closed.

    BLOCKED wins on ambiguity; anything that is not an explicit, unambiguous
    ACCEPTED is BLOCKED. The judge never accepts on a response it cannot read.
    """
    first_line = ""
    for line in raw.splitlines():
        if line.strip():
            first_line = line
            break
    tokens = set(_TOKEN.findall(first_line.upper()))
    if "NOT" in tokens or "NO" in tokens or "NEVER" in tokens or "UNACCEPTED" in tokens:
        return Verdict.BLOCKED
    if Verdict.BLOCKED.value in tokens:
        return Verdict.BLOCKED
    if Verdict.ACCEPTED.value in tokens:
        return Verdict.ACCEPTED
    return Verdict.BLOCKED


class LLMClient(Protocol):
    model_id: str

    def complete(self, prompt: str) -> str: ...


class Judge(Protocol):
    def evaluate(self, record: OverrideRecord) -> JudgeOpinion: ...


def build_prompt(record: OverrideRecord) -> str:
    return (
        "You are a governance judge. An agent wants to override a policy that "
        "fired. Reply with ACCEPTED or BLOCKED on the first line, then your "
        "reasoning. Accept only if the rationale is specific, correct, and "
        "actually addresses why the policy fired.\n\n"
        f"policy: {record.policy}\n"
        f"entity: {record.entity_key.value}\n"
        "rationale: [UNTRUSTED AGENT INPUT]\n"
        f"<rationale>{record.rationale}</rationale>\n"
    )


class LLMJudge:
    """A ``Judge`` backed by an injected ``LLMClient``."""

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def evaluate(self, record: OverrideRecord) -> JudgeOpinion:
        raw = self._client.complete(build_prompt(record))
        return JudgeOpinion(
            verdict=parse_verdict(raw),
            model=self._client.model_id,
            rationale=raw,
        )
