"""Raw keyboard input for the interactive browser (macOS / Linux)."""

from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections.abc import Iterator
from contextlib import contextmanager

# Named keys returned by read_key(). Single characters are returned as themselves.
UP = "up"
DOWN = "down"
LEFT = "left"
RIGHT = "right"
ENTER = "enter"
BACKSPACE = "backspace"
ESC = "esc"
SPACE = "space"
TAB = "tab"
CTRL_C = "ctrl-c"
CTRL_D = "ctrl-d"
CTRL_G = "ctrl-g"
CTRL_Q = "ctrl-q"


@contextmanager
def raw_terminal(fd: int) -> Iterator[None]:
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _available(fd: int, timeout: float) -> bool:
    ready, _, _ = select.select([fd], [], [], timeout)
    return bool(ready)


def _decode_escape(seq: bytes) -> str:
    if seq in {b"\x1b[A", b"\x1bOA"}:
        return UP
    if seq in {b"\x1b[B", b"\x1bOB"}:
        return DOWN
    if seq in {b"\x1b[C", b"\x1bOC"}:
        return RIGHT
    if seq in {b"\x1b[D", b"\x1bOD"}:
        return LEFT
    if seq == b"\x1b":
        return ESC
    # Unknown sequence: treat as Escape so we don't inject garbage into the filter.
    return ESC


def _read_utf8(fd: int, first: bytes) -> str:
    lead = first[0]
    if lead < 0x80:
        return first.decode("ascii")
    needed = 2 if lead < 0xE0 else 3 if lead < 0xF0 else 4
    extra = os.read(fd, needed - 1)
    return (first + extra).decode("utf-8", errors="replace")


def read_key(fd: int | None = None) -> str:
    """Block until one keypress; return a canonical name or a one-character string."""
    if fd is None:
        fd = sys.stdin.fileno()
    with raw_terminal(fd):
        chunk = os.read(fd, 1)
        if not chunk:
            return CTRL_D
        if chunk == b"\x1b":
            seq = chunk
            if _available(fd, 0.05):
                seq += os.read(fd, 8)
            return _decode_escape(seq)
        if chunk in {b"\r", b"\n"}:
            return ENTER
        if chunk in {b"\x7f", b"\x08"}:
            return BACKSPACE
        if chunk == b"\x03":
            return CTRL_C
        if chunk == b"\x04":
            return CTRL_D
        if chunk == b"\x07":
            return CTRL_G
        if chunk == b"\x11":
            return CTRL_Q
        if chunk == b" ":
            return SPACE
        if chunk == b"\t":
            return TAB
        return _read_utf8(fd, chunk)
