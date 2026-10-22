"""Remote POSIX paths and local destination mapping (scp-like)."""

from __future__ import annotations

import posixpath
from pathlib import Path

from sshget.errors import SSHGetError


def parent_path(path: str) -> str:
    """Return the parent of a remote POSIX path. `/` is its own parent."""
    path = path.rstrip("/") or "/"
    if path == "/":
        return "/"
    parent = posixpath.dirname(path)
    return parent or "/"


def remote_join(base: str, name: str) -> str:
    """Join a remote directory and a single path component."""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise SSHGetError(f"unsafe remote name: {name!r}")
    if base == "/":
        return "/" + name
    return posixpath.join(base, name)


def remote_basename(path: str) -> str:
    """Basename of a remote path. `/` becomes `root` so we can save it locally."""
    name = posixpath.basename(path.rstrip("/"))
    return name or "root"


def expand_remote_path(cwd: str, path: str | None) -> str:
    """
    Resolve `~`, `~/…`, relative, and absolute remote paths.

    `cwd` should already be an absolute remote directory (typically the
    SFTP session's starting directory, which is the remote home).
    """
    if not path or path == ".":
        return cwd
    if path == "~":
        return cwd
    if path.startswith("~/"):
        return posixpath.join(cwd, path[2:]) if path[2:] else cwd
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(cwd, path))


def local_target(remote_path: str, dest: Path) -> Path:
    """
    Where a remote file or folder should land locally.

    Matches common `scp` behavior:
    - if `dest` is an existing directory, put the item inside it under its basename;
    - otherwise treat `dest` as the exact destination path (rename / new folder).
    """
    dest = dest.expanduser()
    name = remote_basename(remote_path)
    if dest.exists() and dest.is_dir():
        return dest / name
    return dest


def safe_local_child(root: Path, *parts: str) -> Path:
    """Join path parts under `root`, rejecting any component that would escape it."""
    current = root.resolve()
    for part in parts:
        if not part or part in (".", "..") or "/" in part or "\\" in part:
            raise SSHGetError(f"unsafe remote name: {part!r}")
        current = current / part
    try:
        current.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise SSHGetError(f"refusing to write outside {root}") from exc
    return current
