"""Interactive remote file browser (arrow keys, filter, mark, download)."""

from __future__ import annotations

import posixpath
import stat
from dataclasses import dataclass, field
from pathlib import Path

import paramiko
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sshget.connect import Connection
from sshget.errors import SSHGetError
from sshget.paths import parent_path
from sshget.term import (
    BACKSPACE,
    CTRL_C,
    CTRL_D,
    CTRL_G,
    CTRL_Q,
    DOWN,
    ENTER,
    ESC,
    LEFT,
    RIGHT,
    SPACE,
    UP,
    read_key,
)
from sshget.transfer import ExistingHandler, download_paths, format_size, is_directory

HELP = "up/down  enter=open  bksp=up  type=filter  space=mark  d=get  q=quit"


@dataclass
class DirEntry:
    name: str
    path: str
    is_dir: bool
    size: int
    is_link: bool = False

    @property
    def kind(self) -> str:
        if self.is_dir:
            return "dir"
        if self.is_link:
            return "link"
        return "file"


@dataclass
class BrowserState:
    host_label: str
    remote_cwd: str
    entries: list[DirEntry] = field(default_factory=list)
    cursor: int = 0
    filter: str = ""
    marked: set[str] = field(default_factory=set)
    status: str = ""

    def visible(self) -> list[DirEntry]:
        needle = self.filter.lower()
        if not needle:
            return self.entries
        return [entry for entry in self.entries if needle in entry.name.lower()]

    def current(self) -> DirEntry | None:
        visible = self.visible()
        if not visible:
            return None
        self.cursor = max(0, min(self.cursor, len(visible) - 1))
        return visible[self.cursor]


def list_entries(sftp: paramiko.SFTPClient, remote_cwd: str) -> list[DirEntry]:
    """List a remote directory: `..` first, then dirs, then files."""
    entries: list[DirEntry] = []
    parent = parent_path(remote_cwd)
    if parent != remote_cwd:
        entries.append(DirEntry("..", parent, True, 0))

    try:
        attrs = sftp.listdir_attr(remote_cwd)
    except OSError as exc:
        raise SSHGetError(f"cannot list {remote_cwd}: {exc}") from exc

    dirs: list[DirEntry] = []
    files: list[DirEntry] = []
    for attr in attrs:
        name = attr.filename
        if name in {".", ".."}:
            continue
        path = posixpath.join(remote_cwd, name) if remote_cwd != "/" else "/" + name
        directory = is_directory(sftp, path, attr)
        mode = attr.st_mode or 0
        item = DirEntry(
            name=name,
            path=path,
            is_dir=directory,
            size=int(attr.st_size or 0),
            is_link=stat.S_ISLNK(mode),
        )
        (dirs if directory else files).append(item)
    dirs.sort(key=lambda item: item.name.lower())
    files.sort(key=lambda item: item.name.lower())
    return entries + dirs + files


def apply_key(state: BrowserState, key: str) -> str:
    """
    Mutate browser state from one keypress.

    Returns an action: `none`, `quit`, `download`, `open`, `up`, `refresh`.
    """
    visible = state.visible()
    n = len(visible)

    if key in {CTRL_C, CTRL_Q}:
        return "quit"
    if key == ESC:
        if state.filter:
            state.filter = ""
            state.cursor = 0
            return "none"
        return "quit"
    if key in {CTRL_G, CTRL_D}:
        return "download"
    if key == "q" and not state.filter:
        return "quit"
    if key == "d" and not state.filter:
        return "download"

    if key == UP:
        if n:
            state.cursor = max(0, state.cursor - 1)
        return "none"
    if key == DOWN:
        if n:
            state.cursor = min(n - 1, state.cursor + 1)
        return "none"
    if key in {LEFT, BACKSPACE}:
        if key == BACKSPACE and state.filter:
            state.filter = state.filter[:-1]
            state.cursor = 0
            return "none"
        return "up"
    if key == RIGHT:
        current = state.current()
        if current and current.is_dir:
            return "open"
        return "none"
    if key == ENTER:
        current = state.current()
        if current is None:
            return "none"
        if current.name == "..":
            return "up"
        if current.is_dir:
            return "open"
        # Enter on a file: download that file (quick get).
        state.marked = {current.path}
        return "download"
    if key == SPACE:
        current = state.current()
        if current is None or current.name == "..":
            return "none"
        if current.path in state.marked:
            state.marked.discard(current.path)
        else:
            state.marked.add(current.path)
        return "none"

    # Type-to-filter: printable characters, including letters used as commands
    # once a filter is already active.
    if len(key) == 1 and key.isprintable():
        state.filter += key
        state.cursor = 0
        return "none"
    return "none"


def paths_to_download(state: BrowserState) -> list[str]:
    """Marked items, or the current row if nothing is marked."""
    if state.marked:
        return sorted(state.marked)
    current = state.current()
    if current is None or current.name == "..":
        return []
    return [current.path]


def render_state(state: BrowserState, height: int = 20, width: int = 88) -> RenderableType:
    """Build the rich renderable for one browser frame (also used for screenshots)."""
    visible = state.visible()
    # Header + help + filter + borders take roughly 8 rows.
    body_rows = max(5, height - 8)
    n = len(visible)
    if n == 0:
        start, end = 0, 0
    else:
        state.cursor = max(0, min(state.cursor, n - 1))
        start = 0
        if n > body_rows:
            start = max(0, state.cursor - body_rows // 2)
            start = min(start, n - body_rows)
        end = min(n, start + body_rows)

    table = Table(
        expand=True,
        box=None,
        pad_edge=False,
        show_header=True,
        header_style="dim",
        padding=(0, 1),
    )
    table.add_column(" ", width=1, no_wrap=True)
    table.add_column(" ", width=1, no_wrap=True)
    table.add_column("name", ratio=6, overflow="ellipsis", no_wrap=True)
    table.add_column("type", width=6, no_wrap=True)
    table.add_column("size", width=10, justify="right", no_wrap=True)

    if n == 0:
        empty = Text("(no matches)" if state.filter else "(empty)", style="dim")
        table.add_row("", "", empty, "", "")
    else:
        for index in range(start, end):
            entry = visible[index]
            is_cursor = index == state.cursor
            marked = entry.path in state.marked and entry.name != ".."
            pointer = Text(">" if is_cursor else " ", style="bold yellow" if is_cursor else "")
            star = Text("*" if marked else " ", style="bold cyan" if marked else "")
            display = entry.name + ("/" if entry.is_dir else "")
            style = "bold blue" if entry.is_dir else ""
            if is_cursor:
                style = (style + " reverse").strip()
            name = Text()
            if state.filter:
                _append_highlighted(name, display, state.filter, style)
            else:
                name.append(display, style=style)
            size = "-" if entry.is_dir else format_size(entry.size)
            table.add_row(pointer, star, name, entry.kind, size)

    title = Text()
    title.append("sshget", style="bold")
    title.append("  ")
    title.append(state.host_label, style="cyan")
    title.append("  ")
    title.append(state.remote_cwd, style="green")
    if state.marked:
        title.append(f"   {len(state.marked)} marked", style="bold cyan")

    filter_line = Text("filter: ")
    if state.filter:
        filter_line.append(state.filter, style="bold yellow")
        filter_line.append("▌", style="yellow")
    else:
        filter_line.append("(type to search)", style="dim")

    footer = Text(state.status or HELP, style="dim")
    body = Group(filter_line, Text(""), table)
    return Panel(body, title=title, subtitle=footer, subtitle_align="left", width=width)


def _append_highlighted(text: Text, value: str, needle: str, style: str) -> None:
    lower = value.lower()
    find = needle.lower()
    pos = 0
    while True:
        found = lower.find(find, pos)
        if found < 0:
            text.append(value[pos:], style=style)
            return
        text.append(value[pos:found], style=style)
        text.append(value[found : found + len(needle)], style=(style + " bold underline").strip())
        pos = found + len(needle)


def run_browser(
    conn: Connection,
    dest: Path,
    existing: str = "ask",
    console: Console | None = None,
) -> int:
    """Run the interactive browser until the user quits. Returns a process exit code."""
    console = console or Console()
    sftp = conn.sftp
    try:
        cwd = sftp.normalize(".")
    except OSError:
        cwd = "."
    host = conn.resolved
    label = f"{host.username}@{host.hostname}:{host.port}"
    state = BrowserState(host_label=label, remote_cwd=cwd, entries=list_entries(sftp, cwd))
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    handler = ExistingHandler(existing)

    with Live(
        render_state(state, height=console.size.height, width=console.size.width),
        console=console,
        screen=True,
        auto_refresh=False,
    ) as live:
        while True:
            live.update(
                render_state(state, height=console.size.height, width=console.size.width),
                refresh=True,
            )
            key = read_key()
            action = apply_key(state, key)
            if action == "quit":
                return 0
            if action == "up":
                _chdir(sftp, state, parent_path(state.remote_cwd))
            elif action == "open":
                current = state.current()
                if current is not None:
                    _chdir(sftp, state, current.path)
            elif action == "download":
                paths = paths_to_download(state)
                if not paths:
                    state.status = "nothing to download"
                    continue
                live.stop()
                try:
                    console.print(f"Downloading {len(paths)} item(s) → {dest}")
                    download_paths(sftp, paths, dest, handler)
                    console.print("Done. Press any key to continue browsing, or q to quit.")
                    again = read_key()
                    if again in {"q", CTRL_C, CTRL_Q, ESC}:
                        return 0
                    state.status = f"downloaded {len(paths)} item(s) to {dest}"
                    # Keep marks so the user can see what was fetched; cwd listing stays.
                    state.entries = list_entries(sftp, state.remote_cwd)
                except SSHGetError as exc:
                    console.print(f"[red]error:[/red] {exc}")
                    console.print("Press any key to continue.")
                    read_key()
                    state.status = str(exc)
                finally:
                    live.start(refresh=True)
    return 0


def _chdir(sftp: paramiko.SFTPClient, state: BrowserState, path: str) -> None:
    try:
        normalized = sftp.normalize(path)
        state.entries = list_entries(sftp, normalized)
        state.remote_cwd = normalized
        state.cursor = 0
        state.filter = ""
        state.status = ""
    except (OSError, SSHGetError) as exc:
        state.status = f"cannot open {path}: {exc}"
