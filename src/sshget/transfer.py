"""Recursive SFTP download with a progress bar and existing-file policy."""

from __future__ import annotations

import posixpath
import stat
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import paramiko
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from sshget.errors import SSHGetError
from sshget.paths import local_target, remote_basename, safe_local_child

ExistingMode = str  # ask | skip | overwrite
AskFn = Callable[[str], str]


def format_size(num: int) -> str:
    """Human-readable size using 1024-byte units."""
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num} B"


def is_directory(sftp: paramiko.SFTPClient, path: str, attr: paramiko.SFTPAttributes | None = None) -> bool:
    mode = attr.st_mode if attr is not None else None
    if mode is not None and stat.S_ISDIR(mode):
        return True
    if mode is not None and not stat.S_ISLNK(mode):
        return False
    try:
        followed = sftp.stat(path)
    except OSError:
        return False
    return bool(followed.st_mode and stat.S_ISDIR(followed.st_mode))


@dataclass
class PlannedFile:
    remote: str
    local: Path
    size: int


@dataclass
class ExistingHandler:
    """Decide whether to write a local path that already exists."""

    mode: ExistingMode = "ask"
    ask_fn: AskFn = input
    _always: str | None = None  # overwrite | skip, after "all" / "none"

    def should_write(self, local_path: Path) -> bool:
        if not local_path.exists():
            return True
        if self._always == "overwrite" or self.mode == "overwrite":
            return True
        if self._always == "skip" or self.mode == "skip":
            return False
        prompt = (
            f"{local_path} already exists. "
            "[o]verwrite / [s]kip / [a]ll overwrite / [n]one skip / [q]uit: "
        )
        while True:
            answer = self.ask_fn(prompt).strip().lower()
            if answer in {"o", "overwrite", "y", "yes"}:
                return True
            if answer in {"s", "skip"}:
                return False
            if answer in {"a", "all"}:
                self._always = "overwrite"
                return True
            if answer in {"n", "none"}:
                self._always = "skip"
                return False
            if answer in {"q", "quit"}:
                raise SSHGetError("download cancelled")
            sys.stderr.write("Please answer o, s, a, n, or q.\n")


def _dir_entries(sftp: paramiko.SFTPClient, remote_dir: str) -> list[paramiko.SFTPAttributes]:
    try:
        return list(sftp.listdir_attr(remote_dir))
    except OSError as exc:
        raise SSHGetError(f"cannot list {remote_dir}: {exc}") from exc


def plan_download(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    dest: Path,
) -> tuple[Path, list[PlannedFile]]:
    """
    Build the list of files to copy.

    Returns (local_root, files). `local_root` is the file itself or the
    top downloaded folder (scp-style).
    """
    try:
        attr = sftp.stat(remote_path)
    except OSError as exc:
        raise SSHGetError(f"remote path not found: {remote_path}") from exc

    local_root = local_target(remote_path, dest)
    files: list[PlannedFile] = []

    if is_directory(sftp, remote_path, attr):
        _walk_dir(sftp, remote_path, local_root, files)
    else:
        files.append(PlannedFile(remote_path, local_root, int(attr.st_size or 0)))
    return local_root, files


def _walk_dir(
    sftp: paramiko.SFTPClient,
    remote_dir: str,
    local_dir: Path,
    files: list[PlannedFile],
) -> None:
    for attr in _dir_entries(sftp, remote_dir):
        name = attr.filename
        if name in {".", ".."}:
            continue
        remote = posixpath.join(remote_dir, name)
        local = safe_local_child(local_dir, name)
        if is_directory(sftp, remote, attr):
            _walk_dir(sftp, remote, local, files)
        else:
            files.append(PlannedFile(remote, local, int(attr.st_size or 0)))


def download_paths(
    sftp: paramiko.SFTPClient,
    remote_paths: Iterable[str],
    dest: Path,
    existing: ExistingHandler | str = "ask",
) -> list[Path]:
    """Download each remote file or folder into `dest`. Returns local roots written."""
    handler = existing if isinstance(existing, ExistingHandler) else ExistingHandler(existing)
    dest.mkdir(parents=True, exist_ok=True)

    planned: list[PlannedFile] = []
    roots: list[Path] = []
    for remote in remote_paths:
        root, files = plan_download(sftp, remote, dest)
        roots.append(root)
        planned.extend(files)

    if not planned:
        # Empty directory: still create the local folder.
        for root in roots:
            root.mkdir(parents=True, exist_ok=True)
        return roots

    total = sum(item.size for item in planned)
    written = 0
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    ) as progress:
        overall = progress.add_task("total", total=max(total, 1))
        file_task = progress.add_task("file", total=1)

        for item in planned:
            if not handler.should_write(item.local):
                progress.console.print(f"skip {item.local}")
                written += item.size
                progress.update(overall, completed=min(written, total))
                continue
            item.local.parent.mkdir(parents=True, exist_ok=True)
            _get_file(sftp, item, progress, file_task, overall, written)
            written += item.size
            progress.update(overall, completed=min(written, max(total, 1)))

    return roots


def _get_file(
    sftp: paramiko.SFTPClient,
    item: PlannedFile,
    progress: Progress,
    file_task: TaskID,
    overall: TaskID,
    written_before: int,
) -> None:
    label = remote_basename(item.remote)
    progress.update(file_task, description=label, completed=0, total=max(item.size, 1))

    def callback(transferred: int, total: int) -> None:
        progress.update(file_task, completed=transferred, total=max(total, 1))
        progress.update(overall, completed=written_before + transferred)

    try:
        sftp.get(item.remote, str(item.local), callback=callback)
    except OSError as exc:
        raise SSHGetError(f"failed to download {item.remote}: {exc}") from exc
