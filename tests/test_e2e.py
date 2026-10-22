"""End-to-end tests against a local OpenSSH server (skipped if sshd is unavailable)."""

from __future__ import annotations

import getpass
import os
import pty
import select
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import paramiko
import pytest

from sshget.connect import open_connection
from sshget.hosts import load_ssh_config, resolve_host
from sshget.transfer import ExistingHandler, download_paths, upload_paths

SSHD = shutil.which("sshd") or "/usr/sbin/sshd"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def sshd_env(tmp_path_factory):
    if not os.path.isfile(SSHD) or not os.access(SSHD, os.X_OK):
        pytest.skip("sshd not available")

    root = tmp_path_factory.mktemp("sshd")
    host_key = root / "host_ed25519"
    client_key = root / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(host_key), "-N", "", "-q"],
        check=True,
    )
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(client_key), "-N", "", "-q"],
        check=True,
    )
    authorized = root / "authorized_keys"
    authorized.write_text((client_key.with_suffix(".pub")).read_text(encoding="utf-8"))
    authorized.chmod(0o600)

    remote_root = root / "remote"
    (remote_root / "docs").mkdir(parents=True)
    (remote_root / "hello.txt").write_text("hello from sshget\n", encoding="utf-8")
    (remote_root / "docs" / "nested.txt").write_text("nested\n", encoding="utf-8")
    (remote_root / "docs" / "photo.bin").write_bytes(b"\x00\x01" * 2048)

    port = _free_port()
    config = root / "sshd_config"
    config.write_text(
        f"""
Port {port}
ListenAddress 127.0.0.1
HostKey {host_key}
PidFile {root / "sshd.pid"}
AuthorizedKeysFile {authorized}
PasswordAuthentication no
KbdInteractiveAuthentication no
ChallengeResponseAuthentication no
PubkeyAuthentication yes
UsePAM yes
StrictModes no
PermitRootLogin yes
Subsystem sftp internal-sftp
LogLevel ERROR
""",
        encoding="utf-8",
    )

    subprocess.run(["sudo", "-n", "mkdir", "-p", "/run/sshd"], check=False)
    sshd_cmd = [SSHD, "-D", "-e", "-f", str(config)]
    # Privilege separation usually needs root; fall back to a direct invoke.
    if os.geteuid() != 0:
        sshd_cmd = ["sudo", "-n", *sshd_cmd]
    proc = subprocess.Popen(
        sshd_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_port("127.0.0.1", port, proc)
        known_hosts = root / "known_hosts"
        pub = (root / "host_ed25519.pub").read_text(encoding="utf-8").split()
        # OpenSSH known_hosts: hostname keytype key
        known_hosts.write_text(f"[127.0.0.1]:{port} {pub[0]} {pub[1]}\n", encoding="utf-8")

        home = root / "home"
        ssh_dir = home / ".ssh"
        ssh_dir.mkdir(parents=True)
        (ssh_dir / "config").write_text(
            f"""
Host testhost
  HostName 127.0.0.1
  User {getpass.getuser()}
  Port {port}
  IdentityFile {client_key}
  IdentitiesOnly yes
""",
            encoding="utf-8",
        )
        shutil.copy(known_hosts, ssh_dir / "known_hosts")

        yield {
            "root": root,
            "port": port,
            "remote_root": remote_root,
            "home": home,
            "known_hosts": ssh_dir / "known_hosts",
            "config": ssh_dir / "config",
            "client_key": client_key,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_for_port(host: str, port: int, proc: subprocess.Popen, timeout: float = 8.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read() if proc.stderr else ""
            pytest.skip(f"sshd exited early: {err}")
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    pytest.skip("sshd did not start listening")


def test_direct_file_and_folder_download(sshd_env, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(sshd_env["home"]))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sshd_env["home"]))

    config = load_ssh_config(sshd_env["config"])
    host = resolve_host("testhost", config)
    dest = tmp_path / "out"
    dest.mkdir()
    remote = sshd_env["remote_root"]

    with open_connection(host, known_hosts=sshd_env["known_hosts"], config=config) as conn:
        download_paths(
            conn.sftp,
            [str(remote / "hello.txt")],
            dest,
            ExistingHandler("overwrite"),
        )
        download_paths(
            conn.sftp,
            [str(remote / "docs")],
            dest,
            ExistingHandler("overwrite"),
        )

    assert (dest / "hello.txt").read_text(encoding="utf-8") == "hello from sshget\n"
    assert (dest / "docs" / "nested.txt").read_text(encoding="utf-8") == "nested\n"
    assert (dest / "docs" / "photo.bin").read_bytes() == b"\x00\x01" * 2048


def _child_env(sshd_env) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(sshd_env["home"])
    src = str(Path(__file__).resolve().parents[1] / "src")
    # HOME is isolated for ssh config; keep the process's site-packages so
    # paramiko/rich still import in the child (user site follows HOME).
    site_packages = str(Path(paramiko.__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(
        [src, site_packages, env.get("PYTHONPATH", "")]
    )
    env["TERM"] = "xterm-256color"
    return env


def test_cli_direct_download(sshd_env, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(sshd_env["home"]))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sshd_env["home"]))
    dest = tmp_path / "cli-out"
    dest.mkdir()
    remote = sshd_env["remote_root"] / "hello.txt"
    env = _child_env(sshd_env)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sshget",
            "-y",
            f"testhost:{remote}",
            str(dest),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (dest / "hello.txt").read_text(encoding="utf-8") == "hello from sshget\n"


def test_browser_starts_on_pty_and_quits(sshd_env, monkeypatch):
    monkeypatch.setenv("HOME", str(sshd_env["home"]))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sshd_env["home"]))
    env = _child_env(sshd_env)

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir("/")
        os.execve(
            sys.executable,
            [sys.executable, "-m", "sshget", "testhost"],
            env,
        )
    buf = b""
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if b"filter:" in buf or b"sshget" in buf:
                    os.write(fd, b"q")
                    break
        else:
            raise AssertionError(f"browser UI never appeared: {buf[-500]!r}")
        # Drain until the child exits.
        deadline = time.time() + 5
        while time.time() < deadline:
            dead, status = os.waitpid(pid, os.WNOHANG)
            if dead:
                assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
                pid = None
                break
            ready, _, _ = select.select([fd], [], [], 0.2)
            if ready:
                try:
                    os.read(fd, 4096)
                except OSError:
                    break
        assert b"filter:" in buf or b"sshget" in buf
    finally:
        if pid:
            try:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass


PUT_MAIN = "import sys; from sshget.put import main; raise SystemExit(main(sys.argv[1:]))"


def test_direct_file_and_folder_upload(sshd_env, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(sshd_env["home"]))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sshd_env["home"]))
    local_dir = tmp_path / "payload"
    (local_dir / "sub").mkdir(parents=True)
    (local_dir / "hello.txt").write_text("uploaded hello\n", encoding="utf-8")
    (local_dir / "sub" / "nested.txt").write_text("uploaded nested\n", encoding="utf-8")
    remote_dest = sshd_env["remote_root"] / "uploads"
    config = load_ssh_config(sshd_env["config"])
    host = resolve_host("testhost", config)
    with open_connection(host, known_hosts=sshd_env["known_hosts"], config=config) as conn:
        upload_paths(
            conn.sftp,
            [local_dir / "hello.txt", local_dir],
            str(remote_dest),
            ExistingHandler("overwrite"),
            into_dir=True,
        )
    assert (remote_dest / "hello.txt").read_text(encoding="utf-8") == "uploaded hello\n"
    assert (remote_dest / "payload" / "sub" / "nested.txt").read_text(
        encoding="utf-8"
    ) == "uploaded nested\n"


def test_cli_direct_upload_and_skip_existing(sshd_env, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(sshd_env["home"]))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: sshd_env["home"]))
    env = _child_env(sshd_env)
    remote_dir = sshd_env["remote_root"] / "cli-inbox"
    remote_dir.mkdir()
    local = tmp_path / "send.txt"
    local.write_text("first\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            PUT_MAIN,
            "-i",
            str(sshd_env["client_key"]),
            "-y",
            str(local),
            f"testhost:{remote_dir}/",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert (remote_dir / "send.txt").read_text(encoding="utf-8") == "first\n"

    local.write_text("second\n", encoding="utf-8")
    skipped = subprocess.run(
        [
            sys.executable,
            "-c",
            PUT_MAIN,
            "--skip-existing",
            str(local),
            f"testhost:{remote_dir}/",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert skipped.returncode == 0, skipped.stderr
    assert (remote_dir / "send.txt").read_text(encoding="utf-8") == "first\n"


def test_unknown_host_is_rejected_without_tty(sshd_env, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(sshd_env["home"]))
    empty_known = tmp_path / "empty_known_hosts"
    empty_known.write_text("", encoding="utf-8")
    config = load_ssh_config(sshd_env["config"])
    host = resolve_host("testhost", config)
    with pytest.raises(Exception, match="Host key verification failed"):
        open_connection(
            host,
            known_hosts=empty_known,
            config=config,
            is_tty=False,
        )
