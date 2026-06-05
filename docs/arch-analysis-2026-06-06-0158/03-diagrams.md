# 03 — Architecture Diagrams

C4-style views (Context → Container → Component) plus the internal dependency layering.
All edges are derived from the `file:line` import evidence collected in the cluster passes
(`temp/catalog-*.md`). Rendered as Mermaid.

---

## Level 1 — System Context

Legis inside the Weft suite. Legis governs *change* and consumes the other tools' authorities.

```mermaid
graph TB
    agent["Coding Agent<br/>(operates &amp; extends)"]
    human["Human Operator<br/>(supervises, signs off, governs)"]

    subgraph legis["Legis — git/CI + governance layer"]
        L["Governance 2×2 engine<br/>+ git/CI operating picture"]
    end

    loom["Loomweave<br/>(SEI authority + structure)"]
    ward["Wardline<br/>(policy findings, taint, dossier)"]
    fil["Filigree<br/>(issue / workflow state)"]
    repo[("Git repository")]
    llm["LLM judge provider<br/>(OpenRouter, optional)"]

    agent -->|"override / scan-route / policy-evaluate<br/>(HTTP · MCP · CLI)"| L
    L -->|"block + escalate"| human
    human -->|"operator sign-off"| L

    L -->|"resolve locator → SEI<br/>(HMAC, HTTPS)"| loom
    L -->|"rename/history feed (provider)"| loom
    ward -->|"scan results (findings)"| L
    L -->|"attach SEI-keyed binding"| fil
    L -->|"shell: what changed?"| repo
    L -->|"judge override (fail-closed)"| llm
```

**Key boundary facts:** Legis is an SEI *consumer* (treats SEI as opaque). Loomweave traffic is
HMAC-signed over HTTPS; **Filigree traffic is unsigned** (app-level attestation only). Wardline
findings are *produced* by Wardline and *routed to cells* by Legis ("one judge, not two").

---

## Level 2 — Container (frontends → service → domain → foundations)

Three frontends are *intended* to converge on one transport-agnostic service layer. Solid edges
follow that intent; **dashed red edges are the drift** where a frontend bypasses or cross-couples.

```mermaid
graph TB
    subgraph frontends["Frontends (adapters)"]
        api["HTTP API<br/>api/app.py (830)"]
        mcp["MCP Server<br/>mcp.py (≈1123)"]
        cli["CLI<br/>cli.py (318)"]
    end

    svc["Service Layer<br/>service/ — transport-agnostic (WP-M1)"]

    subgraph domain["Domain"]
        enf["Enforcement<br/>2×2 engine + judge + protected"]
        pol["Policy grammar"]
        gov["Governance<br/>binding · backfill · gaps"]
        wl["Wardline integration"]
    end

    subgraph integ["Integration surfaces"]
        idy["Identity (SEI)"]
        figc["Filigree client"]
        git["Git domain"]
        chk["Checks"]
        pul["Pulls"]
    end

    subgraph found["Foundations"]
        store["Store (audit log)"]
        rec["Records"]
        can["canonical / clock"]
    end

    api --> svc
    mcp --> svc
    api -.->|"direct reach-through:<br/>SignoffGate, trail verify"| enf
    cli -.->|"bypasses service:<br/>hand-rolls verified_records<br/>+ compute_override_rate"| enf
    cli -.->|"reads store directly"| store
    mcp -.->|"sibling-frontend coupling:<br/>DEFAULT_*_DB constants"| api
    cli -->|"launches (factory)"| api
    cli -->|"launches"| mcp

    svc --> enf
    svc --> pol
    svc --> wl
    svc --> idy
    svc --> gov

    enf --> store
    enf --> rec
    enf --> can
    enf --> idy
    gov --> store
    gov --> enf
    gov --> idy
    gov --> figc
    wl --> enf
    wl --> idy
    pol --> can
    rec --> idy
    idy --> can
    store --> can

    api --> chk
    api --> pul
    api --> git
    mcp --> chk
    mcp --> pul
    mcp --> git

    classDef drift stroke:#c0392b,stroke-width:2px,color:#c0392b;
```

> The dashed red edges are the report's central architectural finding: **the service layer is a
> partial seam.** It owns governance decisions cleanly for `api` and `mcp`, but `api` reaches past
> it for sign-off, `cli` doesn't use it at all, and `mcp` couples to `api` for shared constants.

---

## Level 3 — Component: the Protected cell (the "full machinery")

The most security-critical path — a protected override from submission to tamper-evident record.

```mermaid
graph TB
    caller["Frontend<br/>(api / mcp)"]
    sgov["service.governance<br/>submit_protected_override"]
    sb["service.source_binding<br/>require_verified_source_binding"]
    pg["enforcement.protected<br/>ProtectedGate.submit"]
    judge["enforcement.judge<br/>LLMJudge (fail-closed)"]
    llm["llm_client<br/>OpenRouter (SSRF-hardened)"]
    sign["enforcement.signing<br/>HMAC-SHA256 v2"]
    can["canonical_json"]
    store[("AuditStore<br/>append-only + hash chain")]
    tv["TrailVerifier.verify<br/>(read path)"]

    caller --> sgov
    sgov --> sb
    sb -->|".py entity: re-hash on-disk source"| sgov
    sgov --> pg
    pg --> judge
    judge --> llm
    llm -->|"ACCEPTED / BLOCKED"| judge
    pg --> sign
    sign --> can
    pg -->|"signing_fields() →<br/>entity+policy+fingerprint+ast_path+lineage"| store
    store -->|"chain_hash = sha256(prev + content_hash)"| store
    tv -->|"protected-policy set from config (ADR-0002),<br/>not the record → no flag-flip downgrade"| store
```

**Invariants enforced on this path:** judge fails closed (BLOCKED on ambiguity / no provider);
every protected record is HMAC-signed via the *same* `signing_fields()` the verifier reads (signer/verifier
can't drift); the protected-policy set is config-owned so a record can't declare itself unprotected.
**Known gap on this path:** a non-`.py` entity passes source binding as `unverified` yet still gets
signed (M1); `verify_integrity` can raise instead of returning `False` on non-finite-float tampering (M6).

---

## Internal dependency layering (the DAG)

No import cycles exist. Modules form a clean DAG; the layer index is the longest path to a leaf.

```mermaid
graph LR
    subgraph L0["L0 — leaves"]
        can["canonical"]
        clk["clock"]
        ek["identity.entity_key"]
        lwc["identity.loomweave_client"]
        figc["filigree.client"]
        gitm["git.*"]
        chk["checks"]
        pul["pulls"]
        prm["governance.params"]
    end
    subgraph L1["L1"]
        res["identity.resolver"]
        rec["records"]
        st["store"]
        pol["policy"]
    end
    subgraph L2["L2"]
        enf["enforcement"]
    end
    subgraph L3["L3"]
        gov["governance"]
        wl["wardline"]
    end
    subgraph L4["L4"]
        svc["service"]
    end
    subgraph L5["L5"]
        api["api"]
    end
    subgraph L6["L6"]
        mcp["mcp"]
    end
    subgraph L7["L7"]
        cli["cli"]
    end

    res --> can
    rec --> ek
    st --> can
    pol --> can
    enf --> st
    enf --> rec
    enf --> can
    enf --> clk
    enf --> ek
    gov --> st
    gov --> enf
    gov --> figc
    wl --> enf
    svc --> enf
    svc --> pol
    svc --> wl
    svc --> gov
    api --> svc
    mcp --> svc
    mcp --> api
    cli --> api
    cli --> mcp
```

**Layer-violation notes (not cycles, but smells):**
- `mcp (L6) -> api (L5)` — a frontend depends on a sibling frontend for shared DB-default constants. The only cross-frontend static edge; should resolve to a shared config module.
- `cli (L7) -> api/mcp` — launcher edges (acceptable), but `cli` also reaches `enforcement (L2)`/`store (L1)` directly, skipping `service (L4)`.
- `api (L5) -> enforcement (L2)` — direct reach-through for sign-off, skipping its own `service (L4)`.

---

## Trust-boundary map

```mermaid
graph TB
    subgraph untrusted["Untrusted / semi-trusted inputs"]
        a1["agent rationale (override)"]
        a2["wardline scan payload"]
        a3["writer-supplied check/PR facts"]
        a4["LLM judge output"]
    end
    subgraph controls["Controls at the boundary"]
        c1["judge: data-framed input, fail-closed parse"]
        c2["artifact HMAC (opt-in via key)"]
        c3["bearer auth: writer/operator scopes"]
        c4["structured-JSON verdict, BLOCKED-wins"]
    end
    subgraph trail["Tamper-evident record"]
        t1[("hash chain + append-only triggers")]
        t2["HMAC signature (protected)"]
    end

    a1 --> c1 --> t1
    a2 --> c2 --> t1
    a3 --> c3 --> t1
    a4 --> c4 --> t1
    t1 --> t2
```

**Residual boundary weaknesses (carried to 05):** writer/operator split is vacuous in single-secret
mode; check/PR facts are recorded on the writer's word (no fact provenance); Filigree transport is
unsigned; LLM judge output is parsed as gate authority (prompt-injection surface in coached/protected).
