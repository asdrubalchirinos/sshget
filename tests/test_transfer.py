from pathlib import Path

from sshget.transfer import ExistingHandler, format_size


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(500) == "500 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1_200_000).endswith("MB")


def test_existing_handler_skip_and_overwrite(tmp_path: Path):
    path = tmp_path / "file.txt"
    path.write_text("old", encoding="utf-8")
    assert ExistingHandler("skip").should_write(path) is False
    assert ExistingHandler("overwrite").should_write(path) is True
    assert ExistingHandler("ask").should_write(tmp_path / "missing.txt") is True


def test_existing_handler_ask_all(tmp_path: Path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("old", encoding="utf-8")
    answers = iter(["a"])
    handler = ExistingHandler("ask", ask_fn=lambda _prompt: next(answers))
    assert handler.should_write(path) is True
    other = tmp_path / "other.txt"
    other.write_text("x", encoding="utf-8")
    assert handler.should_write(other) is True


def test_existing_handler_ask_none(tmp_path: Path):
    path = tmp_path / "file.txt"
    path.write_text("old", encoding="utf-8")
    handler = ExistingHandler("ask", ask_fn=lambda _prompt: "n")
    assert handler.should_write(path) is False
    other = tmp_path / "other.txt"
    other.write_text("x", encoding="utf-8")
    assert handler.should_write(other) is False
