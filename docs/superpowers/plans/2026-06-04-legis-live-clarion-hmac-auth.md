# Live Clarion HMAC Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close WP-B2 by adding a live Clarion oracle target and wiring Legis's Clarion client to Clarion's `X-Loom-Component` HMAC authentication contract.

**Architecture:** `HttpClarionIdentity` remains the single stdlib HTTP client seam, with injectable transport for offline tests. Protected routes sign the exact JSON body bytes sent by `_urllib_fetch`; `_capabilities` remains unsigned because Clarion documents it as pre-auth. API, MCP, and CLI construction paths pass `LEGIS_CLARION_HMAC_KEY` or `LEGIS_HMAC_KEY` into the client.

**Tech Stack:** Python 3.13, stdlib `urllib`, `hashlib`, `hmac`, `uuid`, pytest.

---

## Contract

Pinned source: `/home/john/clarion/docs/federation/contracts.md` §Authentication and `/home/john/clarion/crates/clarion-cli/src/http_read/auth.rs`.

Clarion requires these headers on protected routes when `serve.http.identity_token_env` resolves:

```http
X-Loom-Component: clarion:<lowercase-hex-hmac>
X-Loom-Timestamp: <unix-seconds>
X-Loom-Nonce: <opaque-nonce>
```

The HMAC message is:

```text
<METHOD>
<PATH_AND_QUERY>
<SHA256_HEX_OF_REQUEST_BODY>
<X_LOOM_TIMESTAMP>
<X_LOOM_NONCE>
```

## File Map

- Modify: `src/legis/identity/clarion_client.py` for canonical body bytes, request signing, env-key helper, and signed protected routes.
- Modify: `src/legis/api/app.py` so env-built `IdentityResolver` gets the Clarion HMAC key.
- Modify: `src/legis/mcp.py` so MCP runtime identity resolution gets the same key.
- Modify: `src/legis/cli.py` so `sei-backfill` uses the same signed client.
- Modify: `tests/identity/test_clarion_client.py` for fixed-vector HMAC and fake-fetch header assertions.
- Modify: `tests/test_cli.py` to assert `sei-backfill` passes the env-resolved key.
- Create: `tests/conformance/test_live_clarion_oracle.py` as the env-gated live oracle target.

## Tasks

### Task 1: Clarion HMAC Client Contract

- [x] **Step 1: Write failing client tests**

Add tests for:

- `sign_clarion_request()` fixed vector using `timestamp=1900000000` and `nonce=nonce-1`.
- `resolve_locator()` sending `X-Loom-Component`, `X-Loom-Timestamp`, and `X-Loom-Nonce` through the fake transport.
- `clarion_hmac_key_from_env()` preferring `LEGIS_CLARION_HMAC_KEY` over `LEGIS_HMAC_KEY`.

- [x] **Step 2: Verify red**

Run:

```bash
uv run pytest tests/identity/test_clarion_client.py tests/conformance/test_live_clarion_oracle.py -q
```

Expected red before implementation: imports fail for missing `sign_clarion_request` and `clarion_hmac_key_from_env`.

- [x] **Step 3: Implement signing**

Implement in `src/legis/identity/clarion_client.py`:

- compact sorted JSON body bytes shared by signing and `_urllib_fetch`;
- path-and-query extraction with query preservation;
- `sign_clarion_request(key, method, url, body, timestamp, nonce)`;
- optional `hmac_key`, `clock`, and `nonce_factory` constructor arguments;
- protected route signing, leaving `_capabilities` unsigned.

- [x] **Step 4: Verify green**

Run:

```bash
uv run pytest tests/identity/test_clarion_client.py tests/conformance/test_live_clarion_oracle.py -q
```

Expected: pass locally with live tests skipped unless `CLARION_URL` is set.

### Task 2: Env Provisioning and Live Oracle

- [x] **Step 1: Wire construction paths**

Pass `clarion_hmac_key_from_env()` into `HttpClarionIdentity` from:

- `src/legis/api/app.py`
- `src/legis/mcp.py`
- `src/legis/cli.py` for `sei-backfill`

- [x] **Step 2: Add live oracle target**

Create `tests/conformance/test_live_clarion_oracle.py`:

- skip the whole file when `CLARION_URL` is unset;
- assert live `_capabilities` advertises SEI;
- when `CLARION_LIVE_ORACLE_LOCATOR` is set, resolve it through `IdentityResolver` and assert the result is an opaque live `clarion:eid:` SEI.

- [x] **Step 3: Verify focused integration surface**

Run:

```bash
uv run pytest tests/identity/test_clarion_client.py tests/conformance/test_live_clarion_oracle.py tests/test_cli.py tests/mcp/test_server.py tests/api/test_sei_api.py -q
```

Expected: pass locally, with live Clarion tests skipped when `CLARION_URL` is absent.

## Final Verification

- [x] Run full test suite:

```bash
uv run pytest -q
```

Result: `366 passed, 2 skipped in 7.42s`.

- [x] Run type check:

```bash
uv run mypy
```

Result: `Success: no issues found in 56 source files`.

- [x] Audit WP-B2:

- Env-gated test exists and skips without `CLARION_URL`.
- Auth header is wired and unit-tested against fake transport.
- `X-Loom-Component` follows current Clarion contract, including timestamp and nonce freshness headers.
- Operative live completion remains gated on a running reference Clarion with `CLARION_URL` and, for full identity round trip, `CLARION_LIVE_ORACLE_LOCATOR`.
