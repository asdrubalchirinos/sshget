from sshget.browser import BrowserState, DirEntry, apply_key, paths_to_download, render_state
from sshget.term import BACKSPACE, CTRL_G, ENTER, SPACE, UP


def _state(**kwargs) -> BrowserState:
    entries = [
        DirEntry("..", "/home", True, 0),
        DirEntry("docs", "/home/me/docs", True, 0),
        DirEntry("notes.txt", "/home/me/notes.txt", False, 4100),
        DirEntry("report.pdf", "/home/me/report.pdf", False, 1_200_000),
    ]
    defaults = dict(
        host_label="me@example:22",
        remote_cwd="/home/me",
        entries=entries,
        cursor=1,
    )
    defaults.update(kwargs)
    return BrowserState(**defaults)


def test_type_to_filter_and_backspace():
    state = _state()
    apply_key(state, "p")
    apply_key(state, "d")
    apply_key(state, "f")
    assert [e.name for e in state.visible()] == ["report.pdf"]
    apply_key(state, BACKSPACE)
    assert state.filter == "pd"


def test_space_marks_and_download_uses_marks():
    state = _state(cursor=2)
    apply_key(state, SPACE)
    state.cursor = 3
    apply_key(state, SPACE)
    assert state.marked == {"/home/me/notes.txt", "/home/me/report.pdf"}
    assert paths_to_download(state) == ["/home/me/notes.txt", "/home/me/report.pdf"]
    apply_key(state, SPACE)
    assert "/home/me/report.pdf" not in state.marked


def test_enter_on_dir_opens_and_on_file_downloads():
    state = _state(cursor=1)
    assert apply_key(state, ENTER) == "open"
    state = _state(cursor=2)
    assert apply_key(state, ENTER) == "download"
    assert state.marked == {"/home/me/notes.txt"}


def test_d_and_q_are_commands_only_without_filter():
    state = _state()
    assert apply_key(state, "d") == "download"
    assert apply_key(state, "q") == "quit"
    apply_key(state, "n")
    assert apply_key(state, "d") == "none"
    assert "nd" in state.filter or state.filter.endswith("d")


def test_up_from_parent_row():
    state = _state(cursor=0)
    assert apply_key(state, ENTER) == "up"
    assert apply_key(state, BACKSPACE) == "up"


def test_arrows_move_cursor():
    state = _state(cursor=1)
    apply_key(state, UP)
    assert state.cursor == 0


def test_put_mode_u_uploads_here_and_enter_opens_dirs():
    state = _state(mode="put", cursor=1)
    assert apply_key(state, "u") == "put"
    assert apply_key(state, CTRL_G) == "put"
    assert apply_key(state, ENTER) == "open"
    state = _state(mode="put", cursor=2)
    assert apply_key(state, ENTER) == "none"
    assert apply_key(state, SPACE) == "none"
    assert apply_key(state, "d") == "none"
    assert state.filter == "d"


def test_render_includes_sizes_and_types():
    from rich.console import Console

    state = _state()
    state.marked.add("/home/me/notes.txt")
    console = Console(record=True, width=88, force_terminal=True, color_system=None)
    console.print(render_state(state, height=20, width=88))
    text = console.export_text()
    assert "sshget" in text
    assert "notes.txt" in text
    assert "dir" in text
    assert "file" in text
    assert "filter:" in text


def test_render_put_mode_title():
    from rich.console import Console

    state = _state(mode="put")
    console = Console(record=True, width=88, force_terminal=True, color_system=None)
    console.print(render_state(state, height=20, width=88))
    text = console.export_text()
    assert "sshput" in text
    assert "pick dest folder" in text
    assert "u=upload here" in text
