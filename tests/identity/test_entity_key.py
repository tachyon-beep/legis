from legis.identity.entity_key import EntityKey
import pytest


def test_from_locator_is_not_identity_stable():
    k = EntityKey.from_locator("clarion:func:mod.foo")
    assert k.identity_stable is False
    assert k.value == "clarion:func:mod.foo"


def test_from_sei_is_identity_stable():
    k = EntityKey.from_sei("clarion:eid:01J")
    assert k.identity_stable is True
    assert k.value == "clarion:eid:01J"


def test_locator_to_sei_is_a_value_swap_not_a_schema_change():
    loc = EntityKey.from_locator("clarion:func:mod.foo")
    sei = EntityKey.from_sei("clarion:eid:01J")
    # Same serialized shape; only value + identity_stable differ.
    assert set(loc.to_dict().keys()) == set(sei.to_dict().keys())


def test_round_trips_through_dict():
    k = EntityKey.from_sei("clarion:eid:01J")
    assert EntityKey.from_dict(k.to_dict()) == k


@pytest.mark.parametrize(
    "payload",
    [
        {"value": "clarion:eid:01J", "identity_stable": "false"},
        {"value": "", "identity_stable": True},
        {"value": 123, "identity_stable": True},
    ],
)
def test_from_dict_rejects_malformed_serialized_key(payload):
    with pytest.raises(ValueError):
        EntityKey.from_dict(payload)


def test_key_is_opaque_no_parse_api():
    k = EntityKey.from_locator("clarion:func:mod.foo")
    # Opacity discipline (SEI spec §1, §2): the key offers no structural accessors.
    for forbidden in ("parse", "split", "components", "plugin_id", "kind", "qualname"):
        assert not hasattr(k, forbidden)
