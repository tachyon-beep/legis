"""Environment-gated live Loomweave SEI oracle checks."""

from __future__ import annotations

import os

import pytest

from legis.identity.loomweave_client import HttpLoomweaveIdentity, loomweave_hmac_key_from_env
from legis.identity.resolver import IdentityResolver


LOOMWEAVE_URL = os.environ.get("LOOMWEAVE_URL")
LIVE_LOCATOR = os.environ.get("LOOMWEAVE_LIVE_ORACLE_LOCATOR")

pytestmark = pytest.mark.skipif(
    not LOOMWEAVE_URL,
    reason="LOOMWEAVE_URL is not set; live Loomweave oracle is opt-in",
)


def _live_client() -> HttpLoomweaveIdentity:
    assert LOOMWEAVE_URL is not None
    return HttpLoomweaveIdentity(LOOMWEAVE_URL, hmac_key=loomweave_hmac_key_from_env())


def test_live_loomweave_advertises_sei_capability():
    assert _live_client().capability() is True


def test_live_loomweave_resolves_reference_locator_round_trip():
    if not LIVE_LOCATOR:
        pytest.skip("LOOMWEAVE_LIVE_ORACLE_LOCATOR is not set")

    resolved = IdentityResolver(_live_client()).resolve(LIVE_LOCATOR)

    assert resolved.alive is True
    assert resolved.entity_key.identity_stable is True
    assert resolved.entity_key.value.startswith("loomweave:eid:")
    assert resolved.entity_key.value != LIVE_LOCATOR
