from legis.enforcement.lifecycle import GateStatus, evaluate_override_rate


def _final(verdict):
    return {"extensions": {"judge_verdict": verdict}}


def trail(n_accept, n_override, n_blocked=0):
    rows = []
    rows += [_final("ACCEPTED") for _ in range(n_accept)]
    rows += [_final("OVERRIDDEN_BY_OPERATOR") for _ in range(n_override)]
    rows += [_final("BLOCKED") for _ in range(n_blocked)]

    class R:
        def __init__(self, payload, seq):
            self.payload = payload
            self.seq = seq

    return [R(p, i + 1) for i, p in enumerate(rows)]


def test_below_sample_floor_passes_with_notice():
    res = evaluate_override_rate(trail(2, 1), threshold=0.2, window=50, min_sample=10)
    assert res.status is GateStatus.PASS_WITH_NOTICE


def test_over_threshold_fails():
    # 5 overrides / 15 final = 0.33 > 0.2
    res = evaluate_override_rate(trail(10, 5), threshold=0.2, window=50, min_sample=10)
    assert res.status is GateStatus.FAIL
    assert round(res.rate, 2) == 0.33


def test_under_threshold_passes_and_blocked_not_in_denominator():
    # 2 overrides / 20 final = 0.10; 100 BLOCKED must not dilute the denominator
    res = evaluate_override_rate(
        trail(18, 2, n_blocked=100), threshold=0.2, window=200, min_sample=10
    )
    assert res.status is GateStatus.PASS
    assert res.sample_size == 20


def test_rolling_window_only_counts_the_most_recent():
    # 30 final records; window=10 keeps the last 10, which here are all overrides.
    res = evaluate_override_rate(
        trail(20, 10), threshold=0.2, window=10, min_sample=5
    )
    assert res.sample_size == 10
    assert res.status is GateStatus.FAIL
    assert res.rate == 1.0
