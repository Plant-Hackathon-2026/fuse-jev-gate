"""gate/folder_picker.py: 설정 화면의 맥 폴더 선택 창.

실제 창은 띄우지 않는다. osascript 자리에 가짜 셸 스크립트를 끼우고, 맥인지 묻는 함수도 바꿔 끼운다.
"""

import threading
import time
from pathlib import Path

import pytest

from gate import folder_picker
from gate.folder_picker import (
    BUSY_MESSAGE,
    PROMPT,
    UNAVAILABLE_MESSAGE,
    MacFolderPicker,
    PickerBusy,
    PickerError,
    PickerUnavailable,
)


def fake_osascript(tmp_path: Path, body: str) -> str:
    path = tmp_path / "osascript"
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def mac_picker(osascript: str, **kwargs) -> MacFolderPicker:
    return MacFolderPicker(osascript, system=lambda: "Darwin", **kwargs)


def wait_for(path: Path, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"{path}가 생기지 않았어요"
        time.sleep(0.02)


def test_returns_picked_posix_path_without_trailing_slash(tmp_path):
    picker = mac_picker(fake_osascript(tmp_path, "printf '/Users/me/Documents/가계부/\\n'"))
    assert picker.pick() == "/Users/me/Documents/가계부"


def test_root_folder_stays_root(tmp_path):
    assert mac_picker(fake_osascript(tmp_path, "printf '/\\n'")).pick() == "/"


def test_prompt_is_passed_as_argv_not_inside_the_script(tmp_path):
    args_file = tmp_path / "args"
    sneaky = 'x" & (do shell script "touch /tmp/owned") & "\n둘째 줄'
    picker = mac_picker(fake_osascript(tmp_path, f"printf '%s\\0' \"$@\" > '{args_file}'\nprintf '/tmp/a/\\n'"),
                        prompt=sneaky)
    assert picker.pick() == "/tmp/a"
    args = args_file.read_bytes().decode("utf-8").split("\0")[:-1]
    assert args[-1] == sneaky                                      # 안내 문구는 마지막 인자 하나로만 간다
    script = args[:-1]
    assert script[0::2] == ["-e"] * (len(script) // 2)
    assert all(sneaky not in line and "touch" not in line for line in script)
    assert "return POSIX path of (choose folder with prompt (item 1 of argv))" in script
    assert "activate" in script                                     # 브라우저 뒤에 숨지 않게 앞으로 띄운다


def test_default_command_layout():
    command = MacFolderPicker().command()
    assert command[0] == "osascript" and command[-1] == PROMPT
    assert command[1:-1] == [arg for line in folder_picker._PICK_SCRIPT for arg in ("-e", line)]


@pytest.mark.parametrize("message", ["execution error: User canceled. (-128)", "실행 오류: 사용자가 취소했습니다. (-128)"])
def test_cancel_is_none(tmp_path, message):
    picker = mac_picker(fake_osascript(tmp_path, f"echo '{message}' >&2\nexit 1"))
    assert picker.pick() is None


def test_other_failure_is_error(tmp_path):
    picker = mac_picker(fake_osascript(tmp_path, "echo 'execution error: 무언가 잘못됨 (-1700)' >&2\nexit 1"))
    with pytest.raises(PickerError) as info:
        picker.pick()
    assert type(info.value) is PickerError and "종료 코드 1" in str(info.value) and "니다" not in str(info.value)


def test_unreadable_output_is_error(tmp_path):
    with pytest.raises(PickerError, match="경로를 읽지 못했어요"):
        mac_picker(fake_osascript(tmp_path, "printf 'not a path\\n'")).pick()


def test_timeout_kills_and_counts_as_cancel(tmp_path):
    picker = mac_picker(fake_osascript(tmp_path, "exec sleep 30"), timeout=0.3)
    started = time.monotonic()
    assert picker.pick() is None
    assert time.monotonic() - started < 5
    assert picker._proc is None
    assert mac_picker(fake_osascript(tmp_path, "printf '/tmp/b/\\n'")).pick() == "/tmp/b"


def test_default_timeout_is_two_minutes():
    assert folder_picker.PICK_SECONDS == 120 and MacFolderPicker().timeout == 120


def test_only_one_picker_at_a_time(tmp_path):
    started, release = tmp_path / "started", tmp_path / "release"
    picker = mac_picker(fake_osascript(tmp_path, f"touch '{started}'\nwhile [ ! -f '{release}' ]; do sleep 0.05; done\n"
                                                 "printf '/tmp/first/\\n'"))
    results = []
    thread = threading.Thread(target=lambda: results.append(picker.pick()))
    thread.start()
    try:
        wait_for(started)
        began = time.monotonic()
        with pytest.raises(PickerBusy) as info:
            picker.pick()
        assert time.monotonic() - began < 1  # 기다리지 않고 바로 거절
        assert str(info.value) == BUSY_MESSAGE
    finally:
        release.touch()
        thread.join(10)
    assert results == ["/tmp/first"]
    assert picker.pick() == "/tmp/first"  # 끝나면 다시 띄울 수 있다


def test_cancel_closes_open_picker(tmp_path):
    started = tmp_path / "started"
    picker = mac_picker(fake_osascript(tmp_path, f"touch '{started}'\nexec sleep 30"))
    results = []
    thread = threading.Thread(target=lambda: results.append(picker.pick()))
    thread.start()
    wait_for(started)
    time.sleep(0.1)  # exec로 sleep이 osascript 자리를 이어받을 때까지
    picker.cancel()
    thread.join(5)
    assert not thread.is_alive() and results == [None]


def test_not_mac_is_unavailable(tmp_path):
    picker = MacFolderPicker(fake_osascript(tmp_path, "printf '/tmp/a/\\n'"), system=lambda: "Linux")
    with pytest.raises(PickerUnavailable) as info:
        picker.pick()
    assert str(info.value) == UNAVAILABLE_MESSAGE and "plant mount add" in UNAVAILABLE_MESSAGE


def test_missing_osascript_is_unavailable(tmp_path):
    with pytest.raises(PickerUnavailable):
        mac_picker(str(tmp_path / "no-such-osascript")).pick()


def test_picker_texts_use_polite_tone():
    for text in (PROMPT, BUSY_MESSAGE, UNAVAILABLE_MESSAGE):
        assert "니다" not in text, text
