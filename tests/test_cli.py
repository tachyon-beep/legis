from legis.cli import build_parser, main


def test_serve_defaults():
    args = build_parser().parse_args(["serve"])
    assert args.command == "serve"
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_serve_custom_host_and_port():
    args = build_parser().parse_args(["serve", "--host", "0.0.0.0", "--port", "9001"])
    assert args.host == "0.0.0.0"
    assert args.port == 9001


def test_main_serve_invokes_runner_with_factory():
    calls = []

    def fake_run(app, **kw):
        calls.append((app, kw))

    rc = main(["serve", "--host", "0.0.0.0", "--port", "9001"], run=fake_run)
    assert rc == 0
    assert calls == [("legis.api.app:create_app",
                      {"host": "0.0.0.0", "port": 9001, "factory": True})]


def test_main_no_command_returns_2():
    rc = main([], run=lambda *a, **k: None)
    assert rc == 2
