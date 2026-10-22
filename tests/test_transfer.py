from pathlib import Path

from sshget.transfer import ExistingHandler, format_size, plan_upload


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


def test_existing_handler_explicit_exists_flag():
    handler = ExistingHandler("skip")
    assert handler.should_write("/remote/file.txt", exists=False) is True
    assert handler.should_write("/remote/file.txt", exists=True) is False
    assert ExistingHandler("overwrite").should_write("/remote/file.txt", exists=True) is True


def test_plan_upload_file_into_dir(tmp_path: Path):
    source = tmp_path / "a.txt"
    source.write_text("hello", encoding="utf-8")
    dirs, files = plan_upload(source, "/tmp/dest", into_dir=True)
    assert dirs == []
    assert files[0].remote == "/tmp/dest/a.txt"
    assert files[0].local == source.resolve()
    assert files[0].size == 5


def test_plan_upload_file_rename(tmp_path: Path):
    source = tmp_path / "a.txt"
    source.write_text("hello", encoding="utf-8")
    dirs, files = plan_upload(source, "/tmp/renamed.txt", into_dir=False)
    assert files[0].remote == "/tmp/renamed.txt"


def test_plan_upload_folder(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    dirs, files = plan_upload(root, "/opt", into_dir=True)
    assert "/opt/proj" in dirs
    assert "/opt/proj/src" in dirs
    assert "/opt/proj/empty" in dirs
    assert [item.remote for item in files] == ["/opt/proj/src/app.py"]
