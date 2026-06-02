from legis.enforcement.signing import SIG_PREFIX, sign, verify


def test_sign_is_prefixed_and_deterministic():
    fields = {"verdict": "ACCEPTED", "policy": "p", "entity": "e"}
    sig = sign(fields, b"key-1")
    assert sig.startswith(SIG_PREFIX)
    assert sign(fields, b"key-1") == sig                       # deterministic
    assert sign({"verdict": "ACCEPTED"}, b"key-1") != sig      # field-sensitive


def test_verify_round_trips_and_rejects_wrong_key_or_tamper():
    fields = {"verdict": "ACCEPTED", "policy": "p"}
    sig = sign(fields, b"key-1")
    assert verify(fields, sig, b"key-1") is True
    assert verify(fields, sig, b"key-2") is False              # wrong key
    assert verify({**fields, "policy": "q"}, sig, b"key-1") is False  # tampered field
    assert verify(fields, "not-a-sig", b"key-1") is False      # malformed
    assert verify(fields, "", b"key-1") is False
