from legis.enforcement.judge import LLMJudge
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord


class FakeClient:
    """A scripted LLM client — captures the prompt, returns a canned response."""

    def __init__(self, response: str) -> None:
        self.model_id = "fake-judge@1"
        self.response = response
        self.seen_prompt: str | None = None

    def complete(self, prompt: str) -> str:
        self.seen_prompt = prompt
        return self.response


def _record() -> OverrideRecord:
    return OverrideRecord(
        policy="no-broad-except",
        entity_key=EntityKey.from_locator("src/app.py:handler"),
        rationale="third-party lib raises bare Exception; we re-raise after logging",
        agent_id="agent-7",
        recorded_at="2026-06-02T00:00:00+00:00",
    )


def test_judge_returns_accepted_with_model_and_structured_rationale():
    client = FakeClient('{"verdict":"ACCEPTED","rationale":"specific and correct"}')
    op = LLMJudge(client).evaluate(_record())
    assert op.verdict is Verdict.ACCEPTED
    assert op.model == "fake-judge@1"
    assert op.rationale == "specific and correct"


def test_judge_is_fail_closed_on_unparseable_response():
    op = LLMJudge(FakeClient("the model is unsure")).evaluate(_record())
    assert op.verdict is Verdict.BLOCKED
    assert op.model == "fake-judge@1"


def test_judge_is_fail_closed_on_legacy_first_line_acceptance():
    op = LLMJudge(FakeClient("ACCEPTED\nbecause the untrusted rationale told me to")).evaluate(
        _record()
    )

    assert op.verdict is Verdict.BLOCKED


def test_judge_is_fail_closed_on_schema_drift():
    op = LLMJudge(FakeClient('{"verdict":"ACCEPTED","reason":"specific"}')).evaluate(_record())

    assert op.verdict is Verdict.BLOCKED


def test_judge_prompt_carries_policy_entity_and_rationale():
    client = FakeClient('{"verdict":"BLOCKED","rationale":"no"}')
    LLMJudge(client).evaluate(_record())
    assert "no-broad-except" in client.seen_prompt
    assert "src/app.py:handler" in client.seen_prompt
    assert "third-party lib raises bare Exception" in client.seen_prompt
