from io import StringIO
from pathlib import Path

import paramiko
import pytest

from sshget.connect import PromptUnknownHostPolicy, sha256_fingerprint
from sshget.errors import SSHGetError


def test_sha256_fingerprint_format():
    key = paramiko.ECDSAKey.generate()
    fingerprint = sha256_fingerprint(key)
    assert fingerprint.startswith("SHA256:")
    assert "=" not in fingerprint


def test_unknown_host_rejected_without_tty():
    key = paramiko.ECDSAKey.generate()
    policy = PromptUnknownHostPolicy(
        Path("/tmp/unused_known_hosts"),
        is_tty=False,
        output=StringIO(),
    )
    with pytest.raises(SSHGetError, match="Host key verification failed"):
        policy.missing_host_key(paramiko.SSHClient(), "example.test", key)


def test_unknown_host_prompt_yes_saves(tmp_path: Path):
    known = tmp_path / "known_hosts"
    key = paramiko.ECDSAKey.generate()
    client = paramiko.SSHClient()
    output = StringIO()
    policy = PromptUnknownHostPolicy(
        known,
        input_fn=lambda _prompt: "yes",
        is_tty=True,
        output=output,
    )
    policy.missing_host_key(client, "example.test", key)
    assert known.is_file()
    loaded = paramiko.HostKeys(str(known))
    assert loaded.lookup("example.test")
    assert "Permanently added" in output.getvalue()


def test_unknown_host_prompt_no():
    key = paramiko.ECDSAKey.generate()
    policy = PromptUnknownHostPolicy(
        Path("/tmp/unused_known_hosts"),
        input_fn=lambda _prompt: "no",
        is_tty=True,
        output=StringIO(),
    )
    with pytest.raises(SSHGetError, match="Host key verification failed"):
        policy.missing_host_key(paramiko.SSHClient(), "example.test", key)
