"""The coached-cell judge: an interactive wall, not a code generator.

The judge's *logic* — prompt construction, fail-closed verdict parsing, and
model-identity capture — is real and fully tested. Only the model call itself
sits behind the injected ``LLMClient`` seam, so tests need no network and a
production deployment wires a real client. Borrowed *effect* from elspeth's CI
judge, not its vocabulary.
"""

from __future__ import annotations

import re

from legis.enforcement.verdict import Verdict

_TOKEN = re.compile(r"[A-Z]+")


def parse_verdict(raw: str) -> Verdict:
    """Read a model response as a verdict, fail-closed.

    BLOCKED wins on ambiguity; anything that is not an explicit, unambiguous
    ACCEPTED is BLOCKED. The judge never accepts on a response it cannot read.
    """
    tokens = set(_TOKEN.findall(raw.upper()))
    if Verdict.BLOCKED.value in tokens:
        return Verdict.BLOCKED
    if Verdict.ACCEPTED.value in tokens:
        return Verdict.ACCEPTED
    return Verdict.BLOCKED
