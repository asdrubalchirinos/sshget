"""Command-line entry point for sshget."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sshget import __version__
from sshget.browser import run_browser
from sshget.connect import open_connection
from sshget.errors import SSHGetError
from sshget.hosts import Target, parse_target, resolve_host
from sshget.paths import expand_remote_path
from sshget.transfer import ExistingHandler, download_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sshget",
        description=(
            "Download files or folders from an SSH server. "
            "With only a host, opens an interactive remote file browser. "
            "With host:/path, downloads that path directly."
        ),
        epilog=(
            "examples:\n"
            "  sshget myserver\n"
            "  sshget user@host:2222\n"
            "  sshget myserver:/var/log/syslog\n"
            "  sshget myserver:/var/www ./backup\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target",
        help="SSH config alias, user@host[:port], or host:/remote/path",
    )
    parser.add_argument(
        "local_dest",
        nargs="?",
        default=".",
        help="Local destination (default: current directory)",
    )
    parser.add_argument(
        "--existing",
        choices=("ask", "skip", "overwrite"),
        default="ask",
        help="What to do when a local file already exists (default: ask)",
    )
    parser.add_argument(
        "-y",
        "--overwrite",
        action="store_true",
        help="Overwrite existing local files (same as --existing overwrite)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip existing local files (same as --existing skip)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"sshget {__version__}",
    )
    return parser


def _existing_mode(args: argparse.Namespace) -> str:
    if args.overwrite and args.skip_existing:
        raise SSHGetError("use only one of --overwrite / --skip-existing")
    if args.overwrite:
        return "overwrite"
    if args.skip_existing:
        return "skip"
    return args.existing


def run(args: argparse.Namespace) -> int:
    target = parse_target(args.target)
    dest = Path(args.local_dest)
    existing = _existing_mode(args)
    host = resolve_host(target if target.remote_path is None else _host_only(target))

    with open_connection(host) as conn:
        if target.remote_path is None:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise SSHGetError(
                    "interactive browser needs a TTY. "
                    "Pass host:/remote/path to download without browsing."
                )
            return run_browser(conn, dest, existing)

        home = conn.sftp.normalize(".")
        remote = expand_remote_path(home, target.remote_path)
        dest.mkdir(parents=True, exist_ok=True)
        handler = ExistingHandler(existing)
        download_paths(conn.sftp, [remote], dest, handler)
    return 0


def _host_only(target: Target) -> Target:
    return Target(host=target.host, user=target.user, port=target.port)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        sys.stderr.write("\nAborted.\n")
        return 130
    except SSHGetError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
