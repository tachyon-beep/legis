from legis.canonical import canonical_json, content_hash
import pytest


def test_canonical_json_is_key_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_content_hash_is_stable_and_hex():
    h1 = content_hash({"a": 1, "b": [1, 2, 3]})
    h2 = content_hash({"b": [1, 2, 3], "a": 1})
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_canonical_json_rejects_non_standard_float_values():
    with pytest.raises(ValueError):
        canonical_json({"bad": float("nan")})


def test_canonical_json_preserves_non_ascii():
    # ``ensure_ascii=False`` is a deliberate, load-bearing choice: a Wardline
    # ``artifact_signature`` is an HMAC over these exact bytes, and Wardline's
    # signer (wardline/core/legis.py) is a byte-for-byte Python replica using the
    # same params. A non-ASCII finding message must therefore serialise to the
    # literal character, not a ``\\uXXXX`` escape, or the cross-tool signature
    # would diverge. This locks legis's own output; the cross-impl pin lives in
    # Wardline's golden HMAC vector. Mirrors Wardline's
    # ``test_canonical_json_is_sorted_tight_unicode``.
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    # Round-trips through the UTF-8 encode content_hash uses.
    assert canonical_json({"msg": "café—naïve"}).encode("utf-8").decode("utf-8") == (
        '{"msg":"café—naïve"}'
    )
