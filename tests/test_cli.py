from sshget.cli import build_parser, existing_mode
from sshget.put import build_parser as build_put_parser


def test_parser_direct_mode():
    args = build_parser().parse_args(["myserver:/tmp/x", "./out"])
    assert args.target == "myserver:/tmp/x"
    assert args.local_dest == "./out"
    assert existing_mode(args) == "ask"


def test_parser_overwrite_flags():
    parser = build_parser()
    assert existing_mode(parser.parse_args(["host", "-y"])) == "overwrite"
    assert existing_mode(parser.parse_args(["host", "--skip-existing"])) == "skip"
    assert existing_mode(parser.parse_args(["host", "--existing", "skip"])) == "skip"


def test_parser_identity_flag():
    args = build_parser().parse_args(["-i", "key.pem", "host"])
    assert args.identity == "key.pem"
    put_args = build_put_parser().parse_args(["-i", "./server.pem", "host", "./file"])
    assert put_args.identity == "./server.pem"
    assert put_args.operands == ["host", "./file"]
