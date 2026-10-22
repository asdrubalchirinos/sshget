from pathlib import Path

import pytest

from sshget.errors import SSHGetError
from sshget.paths import (
    expand_remote_path,
    local_target,
    parent_path,
    remote_basename,
    remote_join,
    safe_local_child,
)


def test_parent_path():
    assert parent_path("/home/me/src") == "/home/me"
    assert parent_path("/home/me") == "/home"
    assert parent_path("/home") == "/"
    assert parent_path("/") == "/"
    assert parent_path("/home/me/") == "/home"


def test_remote_join_and_basename():
    assert remote_join("/home/me", "src") == "/home/me/src"
    assert remote_join("/", "etc") == "/etc"
    assert remote_basename("/var/log/syslog") == "syslog"
    assert remote_basename("/") == "root"
    with pytest.raises(SSHGetError):
        remote_join("/tmp", "..")
    with pytest.raises(SSHGetError):
        remote_join("/tmp", "a/b")


def test_expand_remote_path():
    home = "/home/deploy"
    assert expand_remote_path(home, None) == home
    assert expand_remote_path(home, "~") == home
    assert expand_remote_path(home, "~/src") == "/home/deploy/src"
    assert expand_remote_path(home, "/etc/hosts") == "/etc/hosts"
    assert expand_remote_path(home, "rel/file") == "/home/deploy/rel/file"
    assert expand_remote_path(home, "rel/../x") == "/home/deploy/x"


def test_local_target_into_existing_dir(tmp_path: Path):
    dest = tmp_path / "downloads"
    dest.mkdir()
    assert local_target("/var/www", dest) == dest / "www"
    assert local_target("/tmp/notes.txt", dest) == dest / "notes.txt"


def test_local_target_rename_when_dest_missing(tmp_path: Path):
    dest = tmp_path / "out.txt"
    assert local_target("/tmp/notes.txt", dest) == dest


def test_safe_local_child_rejects_escape(tmp_path: Path):
    root = tmp_path / "out"
    root.mkdir()
    assert safe_local_child(root, "a") == root / "a"
    with pytest.raises(SSHGetError):
        safe_local_child(root, "..")
    with pytest.raises(SSHGetError):
        safe_local_child(root, "ok/no")
