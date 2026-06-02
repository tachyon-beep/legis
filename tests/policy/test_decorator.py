import pytest

from legis.policy.decorator import PolicyBoundaryMetadata, policy_boundary


def test_decorator_is_passthrough_and_attaches_metadata():
    @policy_boundary(
        source="external webhook payload",
        suppresses=("no-eval",),
        invariant="rejects non-dict payloads",
        test_ref="tests.policy.test_decorator::test_handler_rejects",
        test_fingerprint="abc123",
    )
    def handler(payload):
        return payload["ok"]

    assert handler({"ok": 42}) == 42  # strict passthrough
    meta = handler.__policy_boundary__
    assert isinstance(meta, PolicyBoundaryMetadata)
    assert meta.suppresses == ("no-eval",)
    assert meta.qualname.endswith("handler")
    assert meta.test_ref.endswith("test_handler_rejects")


def test_empty_suppresses_is_rejected_at_decoration():
    with pytest.raises(TypeError):

        @policy_boundary(source="s", suppresses=(), invariant="i")
        def f(x):
            return x


def test_stacking_is_rejected():
    with pytest.raises(TypeError):

        @policy_boundary(source="s", suppresses=("p",), invariant="i")
        @policy_boundary(source="s", suppresses=("p",), invariant="i")
        def f(x):
            return x
