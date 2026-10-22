"""Open an SFTP session with key-only auth and OpenSSH-like host-key checks."""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import paramiko
from paramiko.client import SSHClient
from paramiko.ssh_exception import (
    AuthenticationException,
    BadHostKeyException,
    SSHException,
)

from sshget.errors import SSHGetError
from sshget.hosts import ResolvedHost, load_ssh_config, resolve_host

InputFn = Callable[[str], str]


def default_known_hosts() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def sha256_fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH-style `SHA256:` fingerprint (unpadded base64)."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class PromptUnknownHostPolicy(paramiko.MissingHostKeyPolicy):
    """
    Like ssh: never auto-accept an unknown host. Prompt on a TTY, otherwise fail.
    """

    def __init__(
        self,
        known_hosts: Path,
        input_fn: InputFn = input,
        is_tty: bool | None = None,
        output=None,
    ) -> None:
        self.known_hosts = known_hosts
        self.input_fn = input_fn
        self.is_tty = sys.stdin.isatty() if is_tty is None else is_tty
        self.output = output if output is not None else sys.stderr

    def missing_host_key(self, client: SSHClient, hostname: str, key: paramiko.PKey) -> None:
        kind = key.get_name()
        fingerprint = sha256_fingerprint(key)
        banner = (
            f"The authenticity of host '{hostname}' can't be established.\n"
            f"{kind.upper()} key fingerprint is {fingerprint}.\n"
            "This host key is not in known_hosts; sshget will not accept it automatically.\n"
        )
        self.output.write(banner)
        self.output.flush()
        if not self.is_tty:
            raise SSHGetError("Host key verification failed (unknown host, no TTY to prompt).")
        try:
            answer = self.input_fn("Are you sure you want to continue connecting (yes/no)? ")
        except EOFError as exc:
            raise SSHGetError("Host key verification failed.") from exc
        if answer.strip().lower() not in {"yes", "y"}:
            raise SSHGetError("Host key verification failed.")
        client.get_host_keys().add(hostname, key.get_name(), key)
        save_host_key(self.known_hosts, hostname, key)
        self.output.write(
            f"Warning: Permanently added '{hostname}' ({kind}) to the list of known hosts.\n"
        )
        self.output.flush()


def save_host_key(path: Path, hostname: str, key: paramiko.PKey) -> None:
    """Append/update a single host key in known_hosts without wiping others."""
    path.parent.mkdir(mode=0o700, exist_ok=True)
    host_keys = paramiko.HostKeys()
    if path.exists():
        host_keys.load(str(path))
    host_keys.add(hostname, key.get_name(), key)
    host_keys.save(str(path))


def _load_host_keys(client: SSHClient, known_hosts: Path) -> None:
    # paramiko's load_system_host_keys() with no path actually reads
    # ~/.ssh/known_hosts. Load the real system files instead, then the
    # user known_hosts we were asked to honor.
    for system in (Path("/etc/ssh/ssh_known_hosts"), Path("/etc/ssh/known_hosts")):
        if system.is_file():
            try:
                client.load_system_host_keys(str(system))
            except OSError:
                pass
    if known_hosts.is_file():
        client.load_host_keys(str(known_hosts))


def _connect_client(
    host: ResolvedHost,
    known_hosts: Path,
    sock=None,
    input_fn: InputFn = input,
    is_tty: bool | None = None,
) -> SSHClient:
    client = SSHClient()
    _load_host_keys(client, known_hosts)
    client.set_missing_host_key_policy(
        PromptUnknownHostPolicy(known_hosts, input_fn=input_fn, is_tty=is_tty)
    )
    existing_ids = [p for p in host.identity_files if os.path.isfile(p)]
    try:
        # password=None: keys only (agent, IdentityFile, and ~/.ssh default keys).
        client.connect(
            hostname=host.hostname,
            port=host.port,
            username=host.username,
            key_filename=existing_ids or None,
            allow_agent=not host.identities_only,
            look_for_keys=not host.identities_only,
            password=None,
            passphrase=None,
            sock=sock,
            timeout=20,
            auth_timeout=20,
            banner_timeout=20,
        )
    except BadHostKeyException as exc:
        raise SSHGetError(
            "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n"
            f"Host key for {exc.hostname} does not match known_hosts.\n"
            "Someone could be eavesdropping on you, or the host key may have "
            "changed legitimately. Remove the old key from known_hosts only if "
            "you trust this change."
        ) from exc
    except AuthenticationException as exc:
        raise SSHGetError(
            f"Authentication failed for {host.username}@{host.hostname}:{host.port}. "
            "sshget uses SSH keys only (ssh-agent and IdentityFile); password "
            "login is not supported."
        ) from exc
    except SSHException as exc:
        raise SSHGetError(str(exc)) from exc
    except OSError as exc:
        raise SSHGetError(
            f"could not connect to {host.hostname}:{host.port}: {exc}"
        ) from exc
    return client


def _flatten_jumps(
    specs: list[str],
    config: paramiko.SSHConfig,
    seen: set[tuple[str, int, str]],
) -> list[ResolvedHost]:
    hops: list[ResolvedHost] = []
    for spec in specs:
        hop = resolve_host(spec, config)
        key = (hop.hostname, hop.port, hop.username)
        if key in seen:
            raise SSHGetError(f"ProxyJump loop involving {hop.hostname}")
        seen.add(key)
        if hop.proxy_jumps:
            hops.extend(_flatten_jumps(hop.proxy_jumps, config, seen))
        hops.append(hop)
    return hops


@dataclass
class Connection:
    """Live SSH + SFTP session. Keep jump clients open for the tunnel."""

    client: SSHClient
    sftp: paramiko.SFTPClient
    resolved: ResolvedHost
    jumps: list[SSHClient] = field(default_factory=list)

    def close(self) -> None:
        try:
            self.sftp.close()
        except Exception:
            pass
        try:
            self.client.close()
        except Exception:
            pass
        for jump in self.jumps:
            try:
                jump.close()
            except Exception:
                pass

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_connection(
    host: ResolvedHost | str,
    *,
    known_hosts: Path | None = None,
    config: paramiko.SSHConfig | None = None,
    input_fn: InputFn = input,
    is_tty: bool | None = None,
) -> Connection:
    """Connect with keys, honoring ProxyJump and known_hosts."""
    config = config or load_ssh_config()
    resolved = resolve_host(host, config) if isinstance(host, str) else host
    known_hosts = known_hosts or default_known_hosts()

    jumps: list[SSHClient] = []
    sock = None
    try:
        hops = _flatten_jumps(resolved.proxy_jumps, config, set())
        for index, hop in enumerate(hops):
            jump_client = _connect_client(
                hop, known_hosts, sock=sock, input_fn=input_fn, is_tty=is_tty
            )
            jumps.append(jump_client)
            if index + 1 < len(hops):
                nxt = hops[index + 1]
                dest_host, dest_port = nxt.hostname, nxt.port
            else:
                dest_host, dest_port = resolved.hostname, resolved.port
            transport = jump_client.get_transport()
            if transport is None:
                raise SSHGetError("ProxyJump transport closed unexpectedly")
            sock = transport.open_channel(
                "direct-tcpip",
                (dest_host, dest_port),
                ("127.0.0.1", 0),
            )
        client = _connect_client(
            resolved, known_hosts, sock=sock, input_fn=input_fn, is_tty=is_tty
        )
        sftp = client.open_sftp()
    except Exception:
        for jump in jumps:
            try:
                jump.close()
            except Exception:
                pass
        raise
    return Connection(client=client, sftp=sftp, resolved=resolved, jumps=jumps)
