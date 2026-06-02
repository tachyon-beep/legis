import argparse
import sys

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="legis", description="Legis CLI")
    subparsers = parser.add_subparsers(dest="command")

    serve = subparsers.add_parser("serve", help="Run the Legis API server")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve.add_argument("--port", default=8000, type=int, help="Bind port (default: 8000)")

    return parser


def main(argv: list[str] | None = None, *, run=uvicorn.run) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        run("legis.api.app:create_app", host=args.host, port=args.port, factory=True)
        return 0

    parser.print_help(sys.stderr)
    return 2
