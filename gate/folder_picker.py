"""설정 화면(gate ui)의 [폴더 추가]가 띄우는 맥 폴더 선택 창 (Phase 9-2). 계획: plans/mount-ui-plan.md 5절.

- 화면 서버가 osascript `choose folder`로 이 컴퓨터 화면에 띄운다. 사람이 고른 폴더만 연결 후보가 된다
  (화면에는 경로를 직접 적는 칸이 없다. 터미널에서는 `gate mount add <폴더>`).
- 안내 문구는 argv로 넘긴다. 스크립트 글자에 값을 끼워 넣지 않는다(gate.approval.DialogChannel과 같은 원칙).
- 한 번에 하나만 띄운다. 이미 떠 있으면 기다리지 않고 바로 PickerBusy.
- PICK_SECONDS(2분) 안에 고르지 않으면 osascript를 끝내고 취소로 본다(None).
- 취소(osascript 종료 코드 1 + AppleScript 오류 -128)는 None. 그 밖의 실패는 PickerError.
- 맥이 아니거나 osascript가 없으면 PickerUnavailable.
메시지는 화면에 그대로 보여 주는 해요체다.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import threading
from collections.abc import Callable
from typing import Protocol

PICK_SECONDS = 120
PROMPT = "게이트에 연결할 폴더를 골라 주세요"
CANCELLED_CODE = "-128"  # AppleScript "User canceled."

# 인자: 1 안내 문구. 고른 폴더의 POSIX 경로(끝에 /)를 돌려준다.
_PICK_SCRIPT = (
    "on run argv",
    "activate",
    "return POSIX path of (choose folder with prompt (item 1 of argv))",
    "end run",
)

BUSY_MESSAGE = "이미 폴더 선택 창이 떠 있어요. 그 창에서 고르거나 취소해 주세요."
UNAVAILABLE_MESSAGE = "폴더 선택 창은 맥에서만 돼요. 터미널에서 `gate mount add <폴더>`를 쓰세요."


class PickerError(Exception):
    """폴더 선택 창을 띄우지 못했거나 결과를 읽지 못했다. 메시지는 화면에 그대로 보여 준다."""


class PickerBusy(PickerError):
    """이미 폴더 선택 창이 떠 있다."""


class PickerUnavailable(PickerError):
    """이 컴퓨터에서는 폴더 선택 창을 띄울 수 없다(맥이 아니거나 osascript가 없음)."""


class FolderPicker(Protocol):
    def pick(self) -> str | None:
        """고른 폴더의 경로. 취소했거나 시간이 지났으면 None. 못 띄우면 PickerError."""
        ...

    def cancel(self) -> None:
        """떠 있는 창을 닫는다(gate ui를 끌 때)."""
        ...


class MacFolderPicker:
    """macOS 폴더 선택 창(osascript choose folder). 한 번에 하나만 띄운다."""

    def __init__(self, osascript: str = "osascript", timeout: float = PICK_SECONDS, prompt: str = PROMPT,
                 system: Callable[[], str] = platform.system) -> None:
        self.osascript = osascript
        self.timeout = timeout
        self.prompt = prompt
        self._system = system
        self._turn = threading.Lock()
        self._proc: subprocess.Popen | None = None

    def available(self) -> bool:
        return self._system() == "Darwin" and shutil.which(self.osascript) is not None

    def command(self) -> list[str]:
        script = [arg for line in _PICK_SCRIPT for arg in ("-e", line)]
        return [self.osascript, *script, self.prompt]

    def pick(self) -> str | None:
        if not self.available():
            raise PickerUnavailable(UNAVAILABLE_MESSAGE)
        if not self._turn.acquire(blocking=False):
            raise PickerBusy(BUSY_MESSAGE)
        try:
            try:
                proc = subprocess.Popen(self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
            except OSError as exc:
                raise PickerUnavailable(UNAVAILABLE_MESSAGE) from exc
            self._proc = proc
            try:
                out, err = proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                proc.kill()  # 2분 안에 고르지 않았다: 창을 닫고 취소로 본다
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return None
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                self._proc = None
            if proc.returncode != 0:
                if CANCELLED_CODE in (err or ""):
                    return None
                if proc.returncode < 0:  # cancel()로 닫았다(gate ui를 끔)
                    return None
                raise PickerError(f"폴더 선택 창을 띄우지 못했어요(osascript 종료 코드 {proc.returncode}). "
                                  "터미널에서 `gate mount add <폴더>`로도 연결할 수 있어요.")
            return _posix_path(out)
        finally:
            self._turn.release()

    def cancel(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()


def _posix_path(out: str) -> str:
    """osascript 출력(끝에 줄바꿈, 폴더는 끝에 /)에서 경로만 꺼낸다."""
    path = out[:-1] if out.endswith("\n") else out
    if not path.startswith("/") or "\x00" in path:
        raise PickerError("고른 폴더의 경로를 읽지 못했어요. 다시 골라 주세요.")
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return path
