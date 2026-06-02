import pytest

from legis.enforcement.judge import parse_verdict
from legis.enforcement.verdict import Verdict


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("ACCEPTED", Verdict.ACCEPTED),
        ("accepted — the rationale is specific and correct", Verdict.ACCEPTED),
        ("VERDICT: ACCEPTED\nbecause ...", Verdict.ACCEPTED),
        ("BLOCKED", Verdict.BLOCKED),
        ("blocked: rationale is boilerplate", Verdict.BLOCKED),
        # Ambiguity is fail-closed: BLOCKED wins when both tokens appear.
        ("I would say ACCEPTED but actually BLOCKED", Verdict.BLOCKED),
        # Unparseable / unknown is fail-closed.
        ("", Verdict.BLOCKED),
        ("   ", Verdict.BLOCKED),
        ("maybe?", Verdict.BLOCKED),
        ("the model timed out", Verdict.BLOCKED),
    ],
)
def test_parse_verdict_is_fail_closed(raw, expected):
    assert parse_verdict(raw) is expected
