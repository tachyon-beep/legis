"""Environment-gated live Clarion SEI oracle checks."""

from __future__ import annotations

import os

import pytest

from legis.identity.clarion_client import HttpClarionIdentity, clarion_hmac_key_from_env
from legis.identity.resolver import IdentityResolver


CLARION_URL = os.environ.get("CLARION_URL")
LIVE_LOCATOR = os.environ.get("CLARION_LIVE_ORACLE_LOCATOR")

pytestmark = pytest.mark.skipif(
    not CLARION_URL,
    reason="CLARION_URL is not set; live Clarion oracle is opt-in",
)


def _live_client() -> HttpClarionIdentity:
    assert CLARION_URL is not None
    return HttpClarionIdentity(CLARION_URL, hmac_key=clarion_hmac_key_from_env())


def test_live_clarion_advertises_sei_capability():
    assert _live_client().capability() is True


def test_live_clarion_resolves_reference_locator_round_trip():
    if not LIVE_LOCATOR:
        pytest.skip("CLARION_LIVE_ORACLE_LOCATOR is not set")

    resolved = IdentityResolver(_live_client()).resolve(LIVE_LOCATOR)

    assert resolved.alive is True
    assert resolved.entity_key.identity_stable is True
    assert resolved.entity_key.value.startswith("clarion:eid:")
    assert resolved.entity_key.value != LIVE_LOCATOR
