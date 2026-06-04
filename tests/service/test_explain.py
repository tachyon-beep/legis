from legis.clock import SystemClock
from legis.enforcement.engine import EnforcementEngine
from legis.enforcement.verdict import JudgeOpinion, Verdict
from legis.policy.cells import PolicyCellRegistry, PolicyCellRule
from legis.service.explain import explain_policy
from legis.store.audit_store import AuditStore


class _AcceptingJudge:
    def evaluate(self, record):
        return JudgeOpinion(
            verdict=Verdict.ACCEPTED,
            model="stub-judge",
            rationale="accepted for explain wiring test",
        )


def _engine(tmp_path, *, judge=None):
    return EnforcementEngine(
        AuditStore(f"sqlite:///{tmp_path / 'gov.db'}"),
        SystemClock(),
        judge=judge,
    )


def test_explain_chill_policy_reports_enabled_self_clearable_cell(tmp_path):
    registry = PolicyCellRegistry(default_cell="chill")

    result = explain_policy(
        registry,
        policy="ordinary.policy",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=None,
    )

    assert result.to_payload() == {
        "cell": "chill",
        "judge_inline": False,
        "self_clearable": True,
        "human_in_loop": False,
        "enabled": True,
        "available_moves": ["legis_submit_override"],
        "required_inputs": [],
    }


def test_explain_coached_policy_reports_disabled_without_judge_and_enabled_with_judge(
    tmp_path,
):
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="review.*", cell="coached"),),
    )

    disabled = explain_policy(
        registry,
        policy="review.rationale",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=None,
    )

    assert disabled.to_payload() == {
        "cell": "coached",
        "judge_inline": True,
        "self_clearable": False,
        "human_in_loop": False,
        "enabled": False,
        "available_moves": [],
        "required_inputs": [],
    }

    enabled = explain_policy(
        registry,
        policy="review.rationale",
        entity="src/x.py:f",
        engine=_engine(tmp_path, judge=_AcceptingJudge()),
        protected_gate=None,
        signoff_gate=None,
    )

    assert enabled.enabled is True
    assert enabled.available_moves == ("legis_submit_override",)


def test_explain_protected_policy_reports_required_inputs_even_when_gate_disabled(
    tmp_path,
):
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="protected.*", cell="protected"),),
    )

    result = explain_policy(
        registry,
        policy="protected.source-integrity",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=None,
    )

    assert result.to_payload() == {
        "cell": "protected",
        "judge_inline": True,
        "self_clearable": False,
        "human_in_loop": False,
        "enabled": False,
        "available_moves": [],
        "required_inputs": [
            {
                "field": "file_fingerprint",
                "how": "sha256 of the target file contents",
            },
            {
                "field": "ast_path",
                "how": "dotted path to the AST node",
            },
        ],
    }


def test_explain_structured_policy_reports_human_loop_when_signoff_gate_wired(
    tmp_path,
):
    registry = PolicyCellRegistry(
        default_cell="chill",
        rules=(PolicyCellRule(pattern="human.*", cell="structured"),),
    )

    result = explain_policy(
        registry,
        policy="human.release-signoff",
        entity="src/x.py:f",
        engine=_engine(tmp_path),
        protected_gate=None,
        signoff_gate=object(),
    )

    assert result.to_payload() == {
        "cell": "structured",
        "judge_inline": False,
        "self_clearable": False,
        "human_in_loop": True,
        "enabled": True,
        "available_moves": ["legis_submit_override", "legis_signoff_status"],
        "required_inputs": [],
    }
