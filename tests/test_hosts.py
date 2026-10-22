from pathlib import Path

import pytest

from sshget.errors import SSHGetError
from sshget.hosts import load_ssh_config, parse_target, resolve_host


@pytest.mark.parametrize(
    ("raw", "user", "host", "port", "path"),
    [
        ("myserver", None, "myserver", None, None),
        ("user@host", "user", "host", None, None),
        ("user@host:2222", "user", "host", 2222, None),
        ("myserver:/var/log/syslog", None, "myserver", None, "/var/log/syslog"),
        ("user@host:/tmp/x", "user", "host", None, "/tmp/x"),
        ("user@host:2222:/tmp/x", "user", "host", 2222, "/tmp/x"),
        ("host:file.txt", None, "host", None, "file.txt"),
        ("host:docs/readme", None, "host", None, "docs/readme"),
        ("127.0.0.1:2222", None, "127.0.0.1", 2222, None),
        ("[::1]", None, "::1", None, None),
        ("[::1]:2222", None, "::1", 2222, None),
        ("[::1]:/tmp", None, "::1", None, "/tmp"),
        ("user@[::1]:22:/tmp", "user", "::1", 22, "/tmp"),
    ],
)
def test_parse_target(raw, user, host, port, path):
    target = parse_target(raw)
    assert target.user == user
    assert target.host == host
    assert target.port == port
    assert target.remote_path == path


def test_parse_rejects_empty():
    with pytest.raises(SSHGetError):
        parse_target("  ")


def test_parse_rejects_bad_port():
    with pytest.raises(SSHGetError):
        parse_target("host:99999")


def test_resolve_honors_ssh_config(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    ssh = home / ".ssh"
    ssh.mkdir(parents=True)
    (ssh / "config").write_text(
        """
Host myserver
  HostName 10.0.0.5
  User deploy
  Port 2200
  IdentityFile ~/.ssh/id_deploy
  IdentitiesOnly yes
  ProxyJump bastion

Host bastion
  HostName 10.0.0.1
  User jump
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    config = load_ssh_config(ssh / "config")
    resolved = resolve_host("myserver", config)
    assert resolved.hostname == "10.0.0.5"
    assert resolved.username == "deploy"
    assert resolved.port == 2200
    assert resolved.identities_only is True
    assert resolved.proxy_jumps == ["bastion"]
    assert resolved.identity_files[0].endswith("id_deploy")

    override = resolve_host("alice@myserver:44", config)
    assert override.username == "alice"
    assert override.port == 44
    assert override.hostname == "10.0.0.5"
