import argparse
import sys
from pathlib import Path

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legis", description="Legis CLI")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the Legis API server")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve.add_argument("--port", default=8000, type=int, help="Bind port (default: 8000)")
    serve.add_argument(
        "--governance-db",
        help="Governance store URL (falls back to LEGIS_GOVERNANCE_DB env var)",
    )
    serve.add_argument(
        "--check-db",
        help="Check store URL (falls back to LEGIS_CHECK_DB env var)",
    )
    serve.add_argument(
        "--protected-policies",
        help="Comma-separated protected policy list (falls back to LEGIS_PROTECTED_POLICIES env var)",
    )
    serve.add_argument(
        "--clarion-url",
        help="Clarion identity API URL (falls back to CLARION_API_URL env var)",
    )
    serve.add_argument(
        "--filigree-url",
        help="Filigree issue-tracker API URL (falls back to FILIGREE_API_URL env var)",
    )
    serve.add_argument(
        "--binding-db",
        help="Signoff-binding ledger URL (falls back to LEGIS_BINDING_DB env var)",
    )

    mcp = subparsers.add_parser("mcp", help="Run the Legis MCP stdio server")
    mcp.add_argument("--agent-id", required=True, help="Launch-bound agent identity")
    mcp.add_argument(
        "--governance-db",
        help="Governance store URL (falls back to LEGIS_GOVERNANCE_DB env var)",
    )
    mcp.add_argument(
        "--check-db",
        help="Check store URL (falls back to LEGIS_CHECK_DB env var)",
    )
    mcp.add_argument(
        "--policy-cells",
        help="Policy cell registry TOML path (falls back to LEGIS_POLICY_CELLS env var)",
    )
    mcp.add_argument(
        "--protected-policies",
        help="Comma-separated protected policy list (falls back to LEGIS_PROTECTED_POLICIES env var)",
    )
    mcp.add_argument(
        "--clarion-url",
        help="Clarion identity API URL (falls back to CLARION_API_URL env var)",
    )

    import os
    gov_db_default = os.environ.get("LEGIS_GOVERNANCE_DB", "sqlite:///legis-governance.db")
    rate = subparsers.add_parser(
        "check-override-rate",
        help="Fail (exit 1) if the override-rate gate is FAIL — for CI",
    )
    rate.add_argument(
        "--db", default=gov_db_default,
        help="Governance store URL (mirrors the server's DEFAULT_GOVERNANCE_DB)",
    )
    gate = subparsers.add_parser(
        "governance-gate",
        help="Run governance CI gates; currently the override-rate gate",
    )
    gate.add_argument(
        "--db", default=gov_db_default,
        help="Governance store URL (mirrors the server's DEFAULT_GOVERNANCE_DB)",
    )

    return parser


def _missing_sqlite_db(url: str) -> Path | None:
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    path = Path(database)
    return path if not path.exists() else None


def _check_override_rate(db_url: str) -> int:
    import os
    from legis.enforcement.lifecycle import GateStatus, evaluate_override_rate
    from legis.governance import params
    from legis.store.audit_store import AuditStore

    missing_db = _missing_sqlite_db(db_url)
    if missing_db is not None:
        print(
            "override-rate gate: PASS_WITH_NOTICE "
            f"(governance database is missing: {missing_db})"
        )
        return 0

    store = AuditStore(db_url)
    if not store.verify_integrity():
        print("Error: Database hash chain integrity check failed!", file=sys.stderr)
        return 1

    records = store.read_all()

    hmac_key_str = os.environ.get("LEGIS_HMAC_KEY")
    if hmac_key_str:
        from legis.enforcement.protected import TrailVerifier, TamperError
        protected_policies_str = os.environ.get("LEGIS_PROTECTED_POLICIES", "")
        protected_policies = frozenset(
            p.strip() for p in protected_policies_str.split(",") if p.strip()
        )
        verifier = TrailVerifier(hmac_key_str.encode("utf-8"), protected_policies)
        try:
            verifier.verify(records)
        except TamperError as exc:
            print(f"Error: Protected audit trail verification failed: {exc}", file=sys.stderr)
            return 1

    res = evaluate_override_rate(
        records,
        threshold=params.OVERRIDE_RATE_THRESHOLD,
        window=params.OVERRIDE_RATE_WINDOW,
        min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
    )
    print(f"override-rate gate: {res.status.value} "
          f"(rate={res.rate:.3f}, sample={res.sample_size})")
    return 1 if res.status is GateStatus.FAIL else 0


def main(argv: list[str] | None = None, *, run=uvicorn.run) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        import os
        if args.governance_db:
            os.environ["LEGIS_GOVERNANCE_DB"] = args.governance_db
        if args.check_db:
            os.environ["LEGIS_CHECK_DB"] = args.check_db
        if args.protected_policies:
            os.environ["LEGIS_PROTECTED_POLICIES"] = args.protected_policies
        if args.clarion_url:
            os.environ["CLARION_API_URL"] = args.clarion_url
        if args.filigree_url:
            os.environ["FILIGREE_API_URL"] = args.filigree_url
        if args.binding_db:
            os.environ["LEGIS_BINDING_DB"] = args.binding_db

        run("legis.api.app:create_app", host=args.host, port=args.port, factory=True)
        return 0

    if args.command in {"check-override-rate", "governance-gate"}:
        return _check_override_rate(args.db)

    if args.command == "mcp":
        import os
        if args.governance_db:
            os.environ["LEGIS_GOVERNANCE_DB"] = args.governance_db
        if args.protected_policies:
            os.environ["LEGIS_PROTECTED_POLICIES"] = args.protected_policies
        if args.clarion_url:
            os.environ["CLARION_API_URL"] = args.clarion_url
        if args.check_db:
            os.environ["LEGIS_CHECK_DB"] = args.check_db
        if args.policy_cells:
            os.environ["LEGIS_POLICY_CELLS"] = args.policy_cells

        from legis.mcp import main as mcp_main

        return mcp_main(args.agent_id)

    parser.print_help(sys.stderr)
    return 2
