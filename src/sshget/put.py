"""sshput: upload local files or folders to an SSH server over SFTP."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sshget.browser import run_browser
from sshget.cli import add_common_flags, existing_mode, run_safely
from sshget.connect import open_connection
from sshget.errors import SSHGetError
from sshget.hosts import host_only, parse_put_operands, resolve_host
from sshget.paths import expand_remote_path
from sshget.transfer import ExistingHandler, infer_into_dir, upload_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sshput",
        description=(
            "Upload local files or folders to an SSH server. "
            "With a host and local paths, opens a remote browser to pick the "
            "destination folder. With host:/remote/dir/, uploads directly."
        ),
        epilog=(
            "examples:\n"
            "  sshput myserver ./notes.txt ./photos\n"
            "  sshput ./notes.txt myserver:/var/tmp/\n"
            "  sshput -i key.pem ./site user@host:/var/www/\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "operands",
        nargs="+",
        help="host plus local paths, or local paths plus host:/remote/dir/",
    )
    add_common_flags(parser, existing_noun="remote file")
    return parser


def _require_local_paths(paths: list[Path]) -> list[Path]:
    resolved: list[Path] = []
    for path in paths:
        item = path.expanduser()
        if not item.exists():
            raise SSHGetError(f"local path not found: {item}")
        resolved.append(item)
    return resolved


def run(args: argparse.Namespace) -> int:
    target, local_paths, interactive = parse_put_operands(args.operands)
    local_paths = _require_local_paths(local_paths)
    existing = existing_mode(args)
    host = resolve_host(host_only(target), identity=args.identity)

    with open_connection(host) as conn:
        if interactive:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise SSHGetError(
                    "interactive browser needs a TTY. "
                    "Pass host:/remote/dir/ to upload without browsing."
                )
            return run_browser(
                conn,
                existing=existing,
                mode="put",
                local_paths=local_paths,
            )

        home = conn.sftp.normalize(".")
        raw_dest = target.remote_path or "."
        remote = expand_remote_path(home, raw_dest)
        if raw_dest.endswith("/"):
            into_dir = True
        else:
            into_dir = infer_into_dir(conn.sftp, remote, local_paths)
        handler = ExistingHandler(existing)
        upload_paths(conn.sftp, local_paths, remote, handler, into_dir=into_dir)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_safely(lambda _: run(args), argv)


if __name__ == "__main__":
    raise SystemExit(main())
