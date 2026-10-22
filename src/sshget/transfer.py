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
from sshget.paths import local_target, parent_path, remote_basename, remote_join, safe_local_child

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


def remote_exists(sftp: paramiko.SFTPClient, path: str) -> bool:
    try:
        sftp.stat(path)
        return True
    except OSError:
        return False


def remote_makedirs(sftp: paramiko.SFTPClient, path: str) -> None:
    """Create a remote directory and any missing parents (like `mkdir -p`)."""
    path = path.rstrip("/") or "/"
    if path == "/":
        return
    try:
        attr = sftp.stat(path)
        if not is_directory(sftp, path, attr):
            raise SSHGetError(f"remote path is not a directory: {path}")
        return
    except OSError:
        pass
    parent = parent_path(path)
    if parent != path:
        remote_makedirs(sftp, parent)
    try:
        sftp.mkdir(path)
    except OSError as exc:
        if is_directory(sftp, path):
            return
        raise SSHGetError(f"cannot create remote directory {path}: {exc}") from exc


def _progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )


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

    def should_write(self, path: Path | str, exists: bool | None = None) -> bool:
        if exists is None:
            exists = Path(path).exists()
        if not exists:
            return True
        if self._always == "overwrite" or self.mode == "overwrite":
            return True
        if self._always == "skip" or self.mode == "skip":
            return False
        prompt = (
            f"{path} already exists. "
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
                raise SSHGetError("cancelled")
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
    with _progress() as progress:
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


def plan_upload(
    local_path: Path,
    remote_dest: str,
    *,
    into_dir: bool,
) -> tuple[list[str], list[PlannedFile]]:
    """
    Map a local file or folder onto remote paths.

    If `into_dir`, the item is placed under `remote_dest` using its basename
    (scp into a directory). Otherwise `remote_dest` is the exact remote path.
    """
    local_path = local_path.expanduser().resolve()
    if not local_path.exists():
        raise SSHGetError(f"local path not found: {local_path}")
    if into_dir:
        remote_root = remote_join(remote_dest.rstrip("/") or "/", local_path.name)
    else:
        remote_root = remote_dest.rstrip("/") or local_path.name

    dirs: list[str] = []
    files: list[PlannedFile] = []
    if local_path.is_dir():
        _walk_local(local_path, remote_root, dirs, files)
    elif local_path.is_file():
        files.append(PlannedFile(remote_root, local_path, local_path.stat().st_size))
    else:
        raise SSHGetError(f"not a regular file or directory: {local_path}")
    return dirs, files


def _walk_local(
    local_dir: Path,
    remote_dir: str,
    dirs: list[str],
    files: list[PlannedFile],
) -> None:
    dirs.append(remote_dir)
    for entry in sorted(local_dir.iterdir(), key=lambda item: item.name.lower()):
        if entry.is_dir() and not entry.is_symlink():
            _walk_local(entry, remote_join(remote_dir, entry.name), dirs, files)
        elif entry.is_file():
            files.append(
                PlannedFile(
                    remote_join(remote_dir, entry.name),
                    entry,
                    entry.stat().st_size,
                )
            )


def infer_into_dir(
    sftp: paramiko.SFTPClient,
    remote_raw: str,
    sources: list[Path],
) -> bool:
    """Whether uploads go *inside* `remote_raw` (scp directory destination)."""
    if len(sources) > 1 or remote_raw.endswith("/"):
        return True
    remote = remote_raw.rstrip("/") or "/"
    try:
        attr = sftp.stat(remote)
    except OSError:
        return False
    return is_directory(sftp, remote, attr)


def upload_paths(
    sftp: paramiko.SFTPClient,
    local_paths: Iterable[Path | str],
    remote_dest: str,
    existing: ExistingHandler | str = "ask",
    *,
    into_dir: bool | None = None,
) -> list[str]:
    """Upload local files/folders to `remote_dest`. Returns remote roots written."""
    handler = existing if isinstance(existing, ExistingHandler) else ExistingHandler(existing)
    sources = [Path(path) for path in local_paths]
    if not sources:
        raise SSHGetError("need at least one local path to upload")
    if into_dir is None:
        into_dir = infer_into_dir(sftp, remote_dest, sources)
    remote_dest = remote_dest.rstrip("/") or "/"

    planned: list[PlannedFile] = []
    dirs: list[str] = []
    roots: list[str] = []
    for source in sources:
        item_dirs, files = plan_upload(source, remote_dest, into_dir=into_dir)
        if into_dir:
            roots.append(remote_join(remote_dest, source.expanduser().resolve().name))
        else:
            roots.append(remote_dest)
        dirs.extend(item_dirs)
        planned.extend(files)

    if into_dir:
        remote_makedirs(sftp, remote_dest)
    for directory in dirs:
        remote_makedirs(sftp, directory)

    if not planned:
        return roots

    total = sum(item.size for item in planned)
    written = 0
    with _progress() as progress:
        overall = progress.add_task("total", total=max(total, 1))
        file_task = progress.add_task("file", total=1)
        for item in planned:
            exists = remote_exists(sftp, item.remote)
            if not handler.should_write(item.remote, exists=exists):
                progress.console.print(f"skip {item.remote}")
                written += item.size
                progress.update(overall, completed=min(written, total))
                continue
            remote_makedirs(sftp, parent_path(item.remote))
            _put_file(sftp, item, progress, file_task, overall, written)
            written += item.size
            progress.update(overall, completed=min(written, max(total, 1)))
    return roots


def _put_file(
    sftp: paramiko.SFTPClient,
    item: PlannedFile,
    progress: Progress,
    file_task: TaskID,
    overall: TaskID,
    written_before: int,
) -> None:
    label = item.local.name
    progress.update(file_task, description=label, completed=0, total=max(item.size, 1))

    def callback(transferred: int, total: int) -> None:
        progress.update(file_task, completed=transferred, total=max(total, 1))
        progress.update(overall, completed=written_before + transferred)

    try:
        sftp.put(str(item.local), item.remote, callback=callback)
    except OSError as exc:
        raise SSHGetError(f"failed to upload {item.local} → {item.remote}: {exc}") from exc
