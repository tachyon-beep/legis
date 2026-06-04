# Filigree Binding Signature Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure Legis sends `signoff_seq` and an HMAC binding signature to Filigree when binding a cleared sign-off to an issue.

**Architecture:** Keep the local `BindingLedger` as Legis's append-only source of truth, but extend the Filigree handoff payload so the sibling can persist the attestation leg when its schema supports it. `bind_signoff_to_issue()` signs the tuple and passes it through the `FiligreeClient` protocol; the API injects `binding_key` from explicit config or `LEGIS_HMAC_KEY`.

**Tech Stack:** Python 3.12, stdlib urllib Filigree client, HMAC signing helper, pytest, mypy.

---

### Task 1: Filigree Client Handoff Shape

**Files:**
- Modify: `src/legis/filigree/client.py`
- Modify: `tests/filigree/test_client.py`

- [x] **Step 1: Verify client accepts attestation kwargs**

`FiligreeClient.attach()` and `HttpFiligreeClient.attach()` accept optional `signoff_seq` and `signature`, and include them in the POST body only when supplied.

- [x] **Step 2: Verify coverage**

`tests/filigree/test_client.py::test_attach_posts_signoff_attestation_when_supplied` asserts the POST body carries `signoff_seq` and `signature`.

### Task 2: Signed Binding Service

**Files:**
- Modify: `src/legis/governance/signoff_binding.py`
- Modify: `tests/governance/test_signoff_binding.py`

- [x] **Step 1: Verify service signs the handoff tuple**

`bind_signoff_to_issue()` accepts `key: bytes | None`; when supplied it signs `{issue_id, entity_id, content_hash, signoff_seq}` and sends both `signoff_seq` and `signature` to Filigree.

- [x] **Step 2: Verify coverage**

`tests/governance/test_signoff_binding.py::test_binding_is_hmac_signed_when_a_key_is_supplied` verifies the signature and proves it reached `FakeFiligree.attach()`.

### Task 3: API Wiring

**Files:**
- Modify: `src/legis/api/app.py`
- Modify: `tests/api/test_combinations_api.py`

- [x] **Step 1: Verify API passes binding key**

`create_app()` accepts `binding_key`, defaults it from `LEGIS_HMAC_KEY`, and passes it into `bind_signoff_to_issue()` from `/signoff/{seq}/bind-issue`.

- [x] **Step 2: Verify coverage**

`tests/api/test_combinations_api.py::test_bind_issue_endpoint_transmits_hmac_binding_signature` verifies the API response signature and the exact Filigree handoff tuple.

### Task 4: Verification

- [x] **Step 1: Run focused tests**

Run: `uv run pytest tests/governance/test_signoff_binding.py tests/filigree/test_client.py tests/api/test_combinations_api.py -q`
Observed: `38 passed`.

- [x] **Step 2: Run release checks**

Run: `uv run pytest -q`
Observed: `363 passed`.

Run: `uv run mypy`
Observed: `Success: no issues found in 56 source files`.
