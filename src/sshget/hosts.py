"""Parse `sshget` targets and resolve them through `~/.ssh/config`."""

from __future__ import annotations

import getpass
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import paramiko

from sshget.errors import SSHGetError

# `user@host:2222:/abs/path`  — port then path
_PORT_THEN_PATH = re.compile(r"^(\d+):(.*)$")


@dataclass
class Target:
    """What the user typed, before applying ssh_config."""

    host: str
    user: str | None = None
    port: int | None = None
    remote_path: str | None = None


@dataclass
class ResolvedHost:
    """Connection parameters after ssh_config lookup."""

    alias: str
    hostname: str
    username: str
    port: int
    identity_files: list[str] = field(default_factory=list)
    identities_only: bool = False
    proxy_jumps: list[str] = field(default_factory=list)


def parse_target(raw: str) -> Target:
    """
    Parse `alias`, `user@host[:port]`, or `host:/remote/path`.

    Port is only recognized when the segment after `:` is all digits, or
    digits followed by `:` and a path (`user@host:2222:/tmp/file`).
    Anything else after `:` is treated as a remote path, like `scp`.
    Bracketed IPv6 is supported: `[::1]:2222:/tmp`.
    """
    raw = raw.strip()
    if not raw:
        raise SSHGetError("empty host")

    user: str | None = None
    rest = raw
    if rest.startswith("["):
        # `[::1]:port:path` — no user
        pass
    elif "@" in rest:
        user, rest = rest.split("@", 1)
        if not user:
            raise SSHGetError("missing user before @")

    host, port, path = _split_host_port_path(rest)
    if not host:
        raise SSHGetError(f"could not parse host from {raw!r}")
    if port is not None and not (1 <= port <= 65535):
        raise SSHGetError(f"invalid port {port}")
    return Target(host=host, user=user, port=port, remote_path=path)


def _split_host_port_path(rest: str) -> tuple[str, int | None, str | None]:
    if rest.startswith("["):
        end = rest.find("]")
        if end < 0:
            raise SSHGetError(f"unclosed IPv6 bracket in {rest!r}")
        host = rest[1:end]
        after = rest[end + 1 :]
        if not after:
            return host, None, None
        if not after.startswith(":"):
            raise SSHGetError(f"unexpected text after IPv6 address: {rest!r}")
        port, path = _parse_port_path(after[1:])
        return host, port, path

    if ":" not in rest:
        return rest, None, None
    host, remainder = rest.split(":", 1)
    port, path = _parse_port_path(remainder)
    return host, port, path


def _parse_port_path(remainder: str) -> tuple[int | None, str | None]:
    if remainder.startswith("/"):
        return None, remainder
    match = _PORT_THEN_PATH.match(remainder)
    if match:
        return int(match.group(1)), match.group(2)
    if remainder.isdigit():
        return int(remainder), None
    return None, remainder


def load_ssh_config(path: Path | None = None) -> paramiko.SSHConfig:
    """Load user then system OpenSSH config (first value wins, like ssh)."""
    config = paramiko.SSHConfig()
    user_path = path or (Path.home() / ".ssh" / "config")
    for candidate in (user_path, Path("/etc/ssh/ssh_config")):
        if candidate.is_file():
            with candidate.open(encoding="utf-8", errors="replace") as fh:
                config.parse(fh)
    return config


def resolve_host(target: Target | str, config: paramiko.SSHConfig | None = None) -> ResolvedHost:
    """Apply `~/.ssh/config` to a target. Explicit user/port on the CLI win."""
    if isinstance(target, str):
        target = parse_target(target)
    if target.remote_path:
        raise SSHGetError("resolve_host expects a host, not a path")

    config = config or load_ssh_config()
    info = config.lookup(target.host)

    hostname = info.get("hostname", target.host)
    username = target.user or info.get("user") or getpass.getuser()
    if target.port is not None:
        port = target.port
    elif "port" in info:
        port = int(info["port"])
    else:
        port = 22

    identity_files: list[str] = []
    raw_ids = info.get("identityfile", [])
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    for item in raw_ids:
        expanded = os.path.expanduser(os.path.expandvars(item))
        identity_files.append(expanded)

    identities_only = str(info.get("identitiesonly", "no")).lower() in {"yes", "true"}

    jumps: list[str] = []
    proxy = info.get("proxyjump")
    if proxy:
        jumps = [part.strip() for part in proxy.split(",") if part.strip()]

    return ResolvedHost(
        alias=target.host,
        hostname=hostname,
        username=username,
        port=port,
        identity_files=identity_files,
        identities_only=identities_only,
        proxy_jumps=jumps,
    )
