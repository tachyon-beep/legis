import argparse
import sys

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
        "--hmac-key",
        help="HMAC signing key (falls back to LEGIS_HMAC_KEY env var)",
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

    rate = subparsers.add_parser(
        "check-override-rate",
        help="Fail (exit 1) if the override-rate gate is FAIL — for CI",
    )
    rate.add_argument(
        # Literal duplicates api.app.DEFAULT_GOVERNANCE_DB deliberately: importing it
        # would pull FastAPI at CLI load time, defeating the deferred-import decoupling.
        "--db", default="sqlite:///legis-governance.db",
        help="Governance store URL (mirrors the server's DEFAULT_GOVERNANCE_DB)",
    )

    return parser


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
        if args.hmac_key:
            os.environ["LEGIS_HMAC_KEY"] = args.hmac_key
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

    if args.command == "check-override-rate":
        from legis.enforcement.lifecycle import GateStatus, evaluate_override_rate
        from legis.governance import params
        from legis.store.audit_store import AuditStore

        res = evaluate_override_rate(
            AuditStore(args.db).read_all(),
            threshold=params.OVERRIDE_RATE_THRESHOLD,
            window=params.OVERRIDE_RATE_WINDOW,
            min_sample=params.OVERRIDE_RATE_MIN_SAMPLE,
        )
        print(f"override-rate gate: {res.status.value} "
              f"(rate={res.rate:.3f}, sample={res.sample_size})")
        return 1 if res.status is GateStatus.FAIL else 0

    parser.print_help(sys.stderr)
    return 2
