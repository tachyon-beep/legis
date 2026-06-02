import argparse
import sys

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legis", description="Legis CLI")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the Legis API server")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve.add_argument("--port", default=8000, type=int, help="Bind port (default: 8000)")

    rate = subparsers.add_parser(
        "check-override-rate",
        help="Fail (exit 1) if the override-rate gate is FAIL — for CI",
    )
    rate.add_argument(
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
