"""FUSE 마운트 테스트 공용 헬퍼."""

import contextlib
import os
import platform
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

MOUNT_TIMEOUT_SECONDS = 10


def fuse_unavailable_reason() -> str | None:
    """libfuse를 못 찾으면 그 이유를, 쓸 수 있으면 None을 돌려준다."""
    try:
        import mfusepy  # noqa: F401  (import 시점에 libfuse를 찾는다)
    except (OSError, AttributeError) as exc:
        return f"libfuse 사용 불가: {exc}"
    if platform.system() == "Linux" and not os.path.exists("/dev/fuse"):
        return "/dev/fuse 없음 (컨테이너라면 --device /dev/fuse 필요)"
    return None


requires_fuse = pytest.mark.skipif(
    fuse_unavailable_reason() is not None, reason=str(fuse_unavailable_reason())
)


def unmount(mountpoint: Path) -> None:
    if platform.system() == "Linux":
        tool = shutil.which("fusermount3") or shutil.which("fusermount")
        cmd = [tool, "-u", str(mountpoint)] if tool else ["umount", str(mountpoint)]
    else:
        cmd = ["umount", str(mountpoint)]
    subprocess.run(cmd, check=True, capture_output=True)


def _wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@contextlib.contextmanager
def mounted(module: str, mountpoint: Path, *extra_args: str) -> Iterator[subprocess.Popen]:
    """`python -m <module> <mountpoint> ...` 로 FUSE 데몬을 띄우고, 끝나면 해제한다."""
    proc = subprocess.Popen(
        [sys.executable, "-m", module, str(mountpoint), *extra_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        if not _wait_until(lambda: os.path.ismount(mountpoint) or proc.poll() is not None,
                           MOUNT_TIMEOUT_SECONDS) or proc.poll() is not None:
            output = proc.stdout.read() if proc.poll() is not None else ""
            raise RuntimeError(f"마운트 실패 (exit={proc.poll()}): {output}")
        yield proc
    finally:
        if os.path.ismount(mountpoint):
            unmount(mountpoint)
        try:
            proc.wait(timeout=MOUNT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


@pytest.fixture(autouse=True)
def _no_real_global_setup(tmp_path_factory, monkeypatch):
    """폴더 관리(gate.mount_change)가 전역 설치 기록을 찾을 때 실제 ~/.claude를 보지 않게 한다(테스트마다 빈 임시 폴더).
    전역 설치 기록이 있는 경우를 보는 테스트는 이것을 다시 바꿔 끼운다."""
    from gate import mount_change

    settings = tmp_path_factory.mktemp("claude-config") / "settings.json"
    monkeypatch.setattr(mount_change, "global_settings_file", lambda: settings)
