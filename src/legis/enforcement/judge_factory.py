"""Shared runtime wiring for the coached LLM judge."""

from __future__ import annotations

from legis.enforcement.judge import LLMJudge
from legis.enforcement.llm_client import (
    Fetch,
    OpenRouterLLMClient,
    llm_client_config_from_env,
)
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.records.override_record import OverrideRecord


class FailClosedJudge:
    def __init__(self, surface: str) -> None:
        self._surface = surface

    def evaluate(self, record: OverrideRecord) -> JudgeOpinion:
        return JudgeOpinion(
            verdict=Verdict.BLOCKED,
            model="fail-closed-fallback",
            rationale=f"No LLM judge client is configured on this {self._surface} server.",
        )


def build_judge_from_env(surface: str, *, fetch: Fetch | None = None) -> LLMJudge | FailClosedJudge:
    cfg = llm_client_config_from_env()
    if cfg is None:
        return FailClosedJudge(surface)
    return LLMJudge(OpenRouterLLMClient(cfg, fetch=fetch))
