"""사람 승인 (Phase 5, 명세 3.6).

흐름
  - 판단 결과가 ask_human이면 Inspector가 Approvals.request()를 부른다. 답이 오거나 시간이 지날 때까지 막힌다.
  - 기다리는 요청에는 두 곳에서 답할 수 있고, 먼저 온 답을 쓴다.
      맥 대화상자(DialogChannel): 데몬이 osascript로 띄운다. 버튼은 거부·이번만 허용·이 폴더 허용이고 기본 버튼은 거부다.
      사용자 터미널(gate approve / gate deny): 별도 승인 소켓(gate.daemon.ApprovalServer)이 Approvals.answer()를 부른다.
        에이전트가 쓰는 게이트 소켓과 나눈 것은 에이전트가 스스로 승인하지 못하게 하려는 것이다(Phase 6 샌드박스가 게이트 소켓만 연다).
  - 시간이 지나면 timeout이고 차단이다. 떠 있는 대화상자는 닫는다.
  - 같은 요청(같은 파일·요청 프로그램·정책 버전·내용)이 이미 기다리는 중이면 새로 묻지 않고 그 답을 같이 쓴다.
  - 대화상자는 한 번에 하나만 띄운다. 차례를 기다리다 시간이 지나면 띄우지 않는다. 그동안에도 터미널로는 답할 수 있다.

세션 단위 승인 (SessionApprovals)
  - "이 폴더 허용"이면 (폴더, 요청 프로그램, 정책 버전)을 approval.session_minutes 동안 기억한다. 데몬이 끝나면 사라진다.
  - 폴더는 실제 경로의 부모 폴더이고, 그 폴더 바로 안의 파일에만 적용한다(하위 폴더는 다시 묻는다).
  - 요청 프로그램은 소켓 상대의 실행 파일이다. gate CLI는 Python으로 돌기 때문에 사실상 "gate CLI로 읽는 요청"이다.
  - 정책이 바뀌면 버전이 달라져 기억이 맞지 않게 된다. 거부와 무응답은 기억하지 않는다.

- 승인 결과는 Jev 캐시(gate.cache)에 넣지 않는다.
- 승인 요청에는 경로, 이유, 요청 프로그램, 에이전트가 밝힌 목적, 종류별 탐지 건수만 담는다. 파일 내용은 담지 않는다.
- osascript에는 값을 스크립트 글자에 끼워 넣지 않고 argv로 넘긴다(파일 이름이나 목적으로 AppleScript를 주입하지 못하게).
  대화상자에 보이는 값은 제어 문자를 공백으로 바꾸고 길이를 자른다(줄바꿈으로 다른 항목인 척하지 못하게).
"""

import os
import platform
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol

from gate.policy import KIND_NAMES

Answer = Literal["once", "session", "deny"]                            # 사람이 고를 수 있는 답
Approval = Literal["once", "session", "remembered", "deny", "timeout"]  # 판단 요약에 남기는 결과
AnsweredBy = Literal["dialog", "terminal"]
ANSWERS: tuple[Answer, ...] = ("once", "session", "deny")

DIALOG_TITLE = "파일 접근 게이트"
DIALOG_BUTTONS: dict[Answer, str] = {"deny": "거부", "once": "이번만 허용", "session": "이 폴더 허용"}
_BUTTON_ANSWERS = {label: answer for answer, label in DIALOG_BUTTONS.items()}
_MAX_FIELD_CHARS = 200

# 인자: 1 메시지, 2 기다릴 초, 3.. 버튼(첫 버튼이 기본). 시간이 지나 닫히면 빈 문자열을 돌려준다.
_DIALOG_SCRIPT = (
    "on run argv",
    "set msg to item 1 of argv",
    "set giveUp to (item 2 of argv) as integer",
    "set btns to items 3 thru -1 of argv",
    "activate",
    f'set r to display dialog msg with title "{DIALOG_TITLE}" buttons btns default button (item 1 of btns) '
    "with icon caution giving up after giveUp",
    'if gave up of r then return ""',
    "return button returned of r",
    "end run",
)


class ApprovalError(Exception):
    """승인 창구가 요청을 보여 주지 못했다. 요청은 다른 창구의 답이나 시간 초과를 기다린다."""


@dataclass(frozen=True)
class ApprovalRequest:
    path: str                   # 요청한 가상 경로
    folder: str                 # 세션 승인 단위(실제 경로의 부모 폴더)
    reason: str                 # 묻는 이유 (판단 요약의 reason)
    exe: str | None             # 요청 프로그램
    pid: int | None
    purpose: str | None         # 에이전트가 밝힌 목적. 확인되지 않은 내용이다
    findings: Mapping[str, int] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    session_minutes: int = 60   # 0이면 "이 폴더 허용"을 받지 않는다

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", MappingProxyType(dict(self.findings)))


@dataclass(frozen=True)
class ApprovalResult:
    approval: Approval
    by: AnsweredBy | None = None

    @property
    def approved(self) -> bool:
        return self.approval in ("once", "session", "remembered")


class Pending:
    """기다리는 요청 하나. 첫 답만 쓴다."""

    def __init__(self, id: str, request: ApprovalRequest, deadline: float) -> None:
        self.id = id
        self.request = request
        self.deadline = deadline  # time.monotonic 기준
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._result: ApprovalResult | None = None

    def answer(self, answer: Answer, by: AnsweredBy) -> bool:
        """답을 정한다. 이미 정해졌으면 아무것도 하지 않고 False."""
        if answer not in ANSWERS:
            raise ValueError(f"알 수 없는 답: {answer!r}")
        if answer == "session" and self.request.session_minutes <= 0:
            raise ValueError("이 요청에는 '이 폴더 허용'을 쓸 수 없습니다 (approval.session_minutes가 0)")
        return self._settle(ApprovalResult(answer, by))

    def expire(self) -> bool:
        return self._settle(ApprovalResult("timeout"))

    def _settle(self, result: ApprovalResult) -> bool:
        with self._lock:
            if self._result is not None:
                return False
            self._result = result
        self._done.set()
        return True

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def result(self) -> ApprovalResult | None:
        return self._result

    def wait(self, timeout: float | None) -> bool:
        return self._done.wait(timeout)

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())


class Channel(Protocol):
    """요청을 사람에게 보여 주는 창구. present는 별도 스레드에서 불리고, 답을 받으면 pending.answer(..., by=name)을 부른다.
    pending.done이 되면(다른 곳에서 답했거나 시간이 지남) 빨리 끝내야 한다."""
    name: AnsweredBy

    def present(self, pending: Pending) -> None: ...


class SessionApprovals:
    """"이 폴더 허용"을 정해진 시간 동안 기억한다. 키는 (폴더, 요청 프로그램, 정책 버전)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[Hashable, float] = {}
        self._lock = threading.Lock()

    def remember(self, key: Hashable, minutes: int) -> None:
        if minutes <= 0:
            return
        with self._lock:
            self._until[key] = self._clock() + minutes * 60

    def approved(self, key: Hashable) -> bool:
        with self._lock:
            until = self._until.get(key)
            if until is None:
                return False
            if self._clock() >= until:
                del self._until[key]
                return False
            return True

    def clear(self) -> None:
        with self._lock:
            self._until.clear()


class Approvals:
    """승인 요청을 모아 창구에 보여 주고 답을 기다린다. 데몬의 여러 스레드가 같이 쓴다."""

    def __init__(
        self,
        channels: Sequence[Channel] = (),
        *,
        sessions: SessionApprovals | None = None,
        on_pending: Callable[[Pending], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.channels = tuple(channels)
        self.sessions = sessions if sessions is not None else SessionApprovals()
        self.on_pending = on_pending
        self.on_error = on_error
        self._pending: dict[str, Pending] = {}
        self._by_key: dict[Hashable, Pending] = {}
        self._lock = threading.Lock()

    def request(self, request: ApprovalRequest, key: Hashable) -> ApprovalResult:
        """답(once·session·deny) 또는 시간 초과(timeout)를 돌려준다. key가 같은 요청이 기다리는 중이면 그 답을 같이 쓴다."""
        with self._lock:
            pending = self._by_key.get(key)
            new = pending is None
            if new:
                pending = Pending(self._new_id(), request, time.monotonic() + request.timeout_seconds)
                self._pending[pending.id] = pending
                self._by_key[key] = pending
        if new:
            if self.on_pending is not None:
                self.on_pending(pending)
            for channel in self.channels:
                threading.Thread(target=self._present, args=(channel, pending), daemon=True).start()
        try:
            pending.wait(pending.remaining())
            pending.expire()  # 답이 없었으면 timeout으로 정한다
        finally:
            with self._lock:
                if self._pending.get(pending.id) is pending:
                    del self._pending[pending.id]
                if self._by_key.get(key) is pending:
                    del self._by_key[key]
        return pending.result

    def answer(self, id: str, answer: Answer, by: AnsweredBy) -> bool:
        """기다리는 요청에 답한다. 그런 요청이 없으면 KeyError, 이미 답이 정해졌으면 False."""
        with self._lock:
            pending = self._pending.get(id)
        if pending is None or pending.done:
            raise KeyError(id)
        return pending.answer(answer, by)

    def pending(self) -> list[Pending]:
        with self._lock:
            return [p for p in self._pending.values() if not p.done]

    def _new_id(self) -> str:
        while (id := secrets.token_hex(3)) in self._pending:
            pass
        return id

    def _present(self, channel: Channel, pending: Pending) -> None:
        try:
            channel.present(pending)
        except Exception as exc:  # 창구 하나가 실패해도 다른 창구와 시간 초과는 그대로 동작한다
            if self.on_error is not None:
                self.on_error(f"{channel.name}: {exc}")


def pending_summary(pending: Pending) -> dict:
    """승인 소켓으로 보내는 대기 요청 요약. 파일 내용은 없다."""
    r = pending.request
    return {
        "id": pending.id, "path": r.path, "folder": r.folder, "reason": r.reason, "exe": r.exe, "pid": r.pid,
        "purpose": r.purpose, "findings": dict(r.findings), "remaining_seconds": round(pending.remaining(), 1),
        "session_minutes": r.session_minutes,
    }


# --- 맥 대화상자 ---

def clean_text(text: str | None, limit: int = _MAX_FIELD_CHARS) -> str:
    """제어 문자(줄바꿈 포함)를 공백으로 바꾸고 길이를 자른다."""
    if not text:
        return ""
    flat = "".join(" " if (ord(c) < 32 or 0x7F <= ord(c) < 0xA0 or c in "  ") else c for c in text)
    flat = " ".join(flat.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def kinds_text(findings: Mapping[str, int]) -> str:
    return ", ".join(f"{KIND_NAMES.get(kind, kind)} {n}건" for kind, n in findings.items())


def dialog_message(request: ApprovalRequest, seconds: int) -> str:
    exe = os.path.basename(request.exe) if request.exe else "알 수 없음"
    pid = f" (pid {request.pid})" if request.pid is not None else ""
    lines = [
        "에이전트가 파일을 읽으려 합니다.",
        "",
        f"파일: {clean_text(request.path)}",
        f"이유: {clean_text(request.reason)}",
        f"요청 프로그램: {clean_text(exe)}{pid}",
        f"에이전트가 밝힌 목적(확인되지 않음): {clean_text(request.purpose) or '없음'}",
    ]
    if request.findings:
        lines.append(f"가린 번호·비밀값: {kinds_text(request.findings)}")
    lines += ["", "허용해도 번호·비밀값은 가린 채로 보여 줍니다."]
    if request.session_minutes > 0:
        lines.append(f"'이 폴더 허용'은 {clean_text(request.folder)} 바로 안의 파일을 "
                     f"{request.session_minutes}분 동안 다시 묻지 않습니다.")
    lines.append(f"{seconds}초 안에 답하지 않으면 거부합니다.")
    return "\n".join(lines)


class DialogChannel:
    """macOS 대화상자(osascript display dialog). 한 번에 하나만 띄운다."""
    name: AnsweredBy = "dialog"

    def __init__(self, osascript: str = "osascript", poll_seconds: float = 0.1) -> None:
        self.osascript = osascript
        self.poll_seconds = poll_seconds
        self._turn = threading.Lock()

    @staticmethod
    def available() -> bool:
        return platform.system() == "Darwin" and shutil.which("osascript") is not None

    def command(self, request: ApprovalRequest, seconds: int) -> list[str]:
        buttons = [DIALOG_BUTTONS["deny"], DIALOG_BUTTONS["once"]]
        if request.session_minutes > 0:
            buttons.append(DIALOG_BUTTONS["session"])
        script = [arg for line in _DIALOG_SCRIPT for arg in ("-e", line)]
        return [self.osascript, *script, dialog_message(request, seconds), str(seconds), *buttons]

    def present(self, pending: Pending) -> None:
        while not self._turn.acquire(timeout=self.poll_seconds):
            if pending.done:
                return
        try:
            seconds = int(pending.remaining())
            if pending.done or seconds < 1:
                return
            proc = subprocess.Popen(self.command(pending.request, seconds), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace")
            try:
                while proc.poll() is None:
                    if pending.wait(self.poll_seconds):
                        proc.kill()  # 다른 곳에서 답했거나 시간이 지났다
                out, err = proc.communicate()
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
            if pending.done:
                return
            if proc.returncode != 0:
                raise ApprovalError(f"대화상자를 띄우지 못했습니다 (osascript 종료 코드 {proc.returncode}): "
                                    f"{clean_text(err)}")
            answer = _BUTTON_ANSWERS.get(out.strip())
            if answer is not None:
                pending.answer(answer, self.name)
        finally:
            self._turn.release()
