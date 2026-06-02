import pytest

from legis.policy.grammar import (
    AllowlistBoundary,
    PolicyConflictError,
    PolicyGrammar,
    PolicyResult,
    default_grammar,
)


def test_unregistered_policy_is_unknown_not_clear():
    ev = PolicyGrammar().evaluate("nonexistent", {})
    assert ev.result is PolicyResult.UNKNOWN
    assert ev.provenance_gap is True
    assert ev.result is not PolicyResult.CLEAR


def test_allowlist_builtin_clears_violates_and_unknowns():
    g = PolicyGrammar()
    g.register(AllowlistBoundary("imports", frozenset({"json", "os"})))
    assert g.evaluate("imports", {"value": "json"}).result is PolicyResult.CLEAR
    assert g.evaluate("imports", {"value": "socket"}).result is PolicyResult.VIOLATION
    # Missing provenance → cannot prove → UNKNOWN, not CLEAR.
    miss = g.evaluate("imports", {})
    assert miss.result is PolicyResult.UNKNOWN
    assert miss.provenance_gap is True


def test_agent_can_register_a_new_boundary_type_zero_config():
    g = PolicyGrammar()

    class NoTodoBoundary:
        name = "no-todo"

        def evaluate(self, target):
            text = target.get("text", "")
            if "TODO" in text:
                return (PolicyResult.VIOLATION, "contains TODO")
            return (PolicyResult.CLEAR, "clean")

    g.register(NoTodoBoundary())
    assert g.evaluate("no-todo", {"text": "x TODO y"}).result is PolicyResult.VIOLATION
    assert g.evaluate("no-todo", {"text": "clean"}).result is PolicyResult.CLEAR


def test_builtins_cannot_be_shadowed():
    g = default_grammar()
    name = next(iter(g.registered()))

    class Permissive:
        def evaluate(self, target):
            return (PolicyResult.CLEAR, "always ok")

    p = Permissive()
    p.name = name
    with pytest.raises(PolicyConflictError):
        g.register(p)


def test_a_boundary_that_raises_fails_closed_to_unknown():
    g = PolicyGrammar()

    class Exploding:
        name = "boom"

        def evaluate(self, target):
            raise RuntimeError("boundary blew up")

    g.register(Exploding())
    ev = g.evaluate("boom", {})
    assert ev.result is PolicyResult.UNKNOWN  # never propagates, never CLEAR
    assert ev.provenance_gap is True
    assert "boundary blew up" in ev.detail


def test_a_boundary_returning_garbage_fails_closed_to_unknown():
    g = PolicyGrammar()

    class Garbage:
        name = "garbage"

        def evaluate(self, target):
            return "definitely not a (PolicyResult, str)"

    g.register(Garbage())
    assert g.evaluate("garbage", {}).result is PolicyResult.UNKNOWN


def test_exemption_turns_violation_into_clear():
    from legis.policy.exemptions import Exemption, ExemptionRegistry
    from legis.policy.grammar import AllowlistBoundary, PolicyGrammar, PolicyResult
    reg = ExemptionRegistry([Exemption("import-allowlist", "requests", "ticket-123")])
    g = PolicyGrammar(exemptions=reg)
    g.register(AllowlistBoundary("import-allowlist", frozenset({"json"})))
    ev = g.evaluate("import-allowlist", {"value": "requests"})
    assert ev.result is PolicyResult.CLEAR
    assert ev.provenance_gap is False
    assert "ticket-123" in ev.detail
    assert g.evaluate("import-allowlist", {"value": "pickle"}).result is PolicyResult.VIOLATION


def test_exemption_never_rescues_unknown():
    from legis.policy.exemptions import Exemption, ExemptionRegistry
    from legis.policy.grammar import PolicyGrammar, PolicyResult
    reg = ExemptionRegistry([Exemption("unregistered", "x", "r")])
    g = PolicyGrammar(exemptions=reg)
    ev = g.evaluate("unregistered", {"value": "x"})  # no boundary → UNKNOWN
    assert ev.result is PolicyResult.UNKNOWN
    assert ev.provenance_gap is True
