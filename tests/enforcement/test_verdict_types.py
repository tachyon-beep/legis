from legis.enforcement.verdict import JudgeOpinion, Verdict


def test_verdict_values_are_stable_strings():
    assert Verdict.ACCEPTED.value == "ACCEPTED"
    assert Verdict.BLOCKED.value == "BLOCKED"


def test_judge_opinion_carries_verdict_model_rationale():
    op = JudgeOpinion(verdict=Verdict.BLOCKED, model="m-1", rationale="too vague")
    assert op.verdict is Verdict.BLOCKED
    assert op.model == "m-1"
    assert op.rationale == "too vague"
