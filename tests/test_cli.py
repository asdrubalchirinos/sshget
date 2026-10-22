from sshget.cli import build_parser, _existing_mode


def test_parser_direct_mode():
    args = build_parser().parse_args(["myserver:/tmp/x", "./out"])
    assert args.target == "myserver:/tmp/x"
    assert args.local_dest == "./out"
    assert _existing_mode(args) == "ask"


def test_parser_overwrite_flags():
    parser = build_parser()
    assert _existing_mode(parser.parse_args(["host", "-y"])) == "overwrite"
    assert _existing_mode(parser.parse_args(["host", "--skip-existing"])) == "skip"
    assert _existing_mode(parser.parse_args(["host", "--existing", "skip"])) == "skip"
