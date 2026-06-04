from legis.enforcement.judge_factory import build_judge_from_env
from legis.enforcement.verdict import Verdict
from legis.identity.entity_key import EntityKey
from legis.records.override_record import OverrideRecord


def _clear_judge_env(monkeypatch):
    for name in (
        "LEGIS_JUDGE_PROVIDER",
        "OPENROUTER_API_KEY",
        "LEGIS_JUDGE_MODEL",
        "LEGIS_JUDGE_MAX_TOKENS",
        "LEGIS_JUDGE_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def _record():
    return OverrideRecord(
        policy="no-eval",
        entity_key=EntityKey.from_locator("src/x.py:f"),
        rationale="specific rationale",
        agent_id="agent-1",
        recorded_at="2026-06-04T00:00:00+00:00",
        extensions={},
    )


def test_build_judge_from_env_returns_fail_closed_when_unconfigured(monkeypatch):
    _clear_judge_env(monkeypatch)
    judge = build_judge_from_env("API")

    opinion = judge.evaluate(_record())

    assert opinion.verdict is Verdict.BLOCKED
    assert opinion.model == "fail-closed-fallback"
    assert "No LLM judge client is configured on this API server." in opinion.rationale


def test_build_judge_from_env_uses_configured_client(monkeypatch):
    _clear_judge_env(monkeypatch)
    calls = []

    def fetch(method, url, body, headers):
        calls.append(body)
        return {
            "choices": [
                {"message": {"content": '{"verdict":"ACCEPTED","rationale":"ok"}'}}
            ]
        }

    monkeypatch.setenv("LEGIS_JUDGE_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "secret-key")
    monkeypatch.setenv("LEGIS_JUDGE_MODEL", "anthropic/claude-opus-4.7")

    judge = build_judge_from_env("API", fetch=fetch)
    opinion = judge.evaluate(_record())

    assert opinion.verdict is Verdict.ACCEPTED
    assert opinion.model == "openrouter:anthropic/claude-opus-4.7"
    assert calls[0]["messages"][0]["content"].startswith("You are a governance judge.")
