"""gate.approval: 대기 요청, 먼저 온 답, 시간 초과, 세션 승인, 맥 대화상자(가짜 osascript), gate approve / gate deny.

실제 대화상자는 띄우지 않는다. DialogChannel은 osascript 대신 같은 인자를 받는 가짜 스크립트로 시험한다.
"""

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from gate import cli
from gate.approval import (
    DIALOG_BUTTONS,
    DIALOG_TITLE,
    ApprovalRequest,
    Approvals,
    DialogChannel,
    SessionApprovals,
    clean_text,
    dialog_message,
    pending_summary,
)
from gate.client import ApprovalClient
from gate.daemon import ApprovalServer


def request(path="/상담/메모.txt", timeout=2.0, session_minutes=60, **kw) -> ApprovalRequest:
    fields = dict(folder="/상담", reason="판단 모델 결과(sensitive, 확신도 0.70)가 확실하지 않습니다",
                  exe="/usr/bin/python3", pid=4321, purpose="요약", findings={"mobile": 1})
    fields.update(kw)
    return ApprovalRequest(path, timeout_seconds=timeout, session_minutes=session_minutes, **fields)


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def in_background(fn, *args, **kwargs):
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("result", fn(*args, **kwargs)), daemon=True)
    thread.start()
    return thread, box


class Scripted:
    """요청을 기록하고 정해 둔 답을 바로 하는 창구. None이면 답하지 않는다."""
    name = "dialog"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def present(self, pending):
        self.requests.append(pending.request)
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            pending.answer(answer, self.name)


# --- Approvals: 승인 · 거부 · 무응답 ---

@pytest.mark.parametrize("answer", ["once", "session", "deny"])
def test_channel_answer_is_returned(answer):
    channel = Scripted(answer)
    result = Approvals([channel]).request(request(), key="k")
    assert (result.approval, result.by) == (answer, "dialog")
    assert result.approved == (answer != "deny")
    assert channel.requests == [request()]


def test_terminal_answer_while_waiting():
    approvals = Approvals()
    thread, box = in_background(approvals.request, request(), "k")
    assert wait_for(lambda: approvals.pending())
    [pending] = approvals.pending()
    assert approvals.answer(pending.id, "once", "terminal") is True
    thread.join(2)
    assert (box["result"].approval, box["result"].by) == ("once", "terminal")
    assert approvals.pending() == []


def test_no_answer_times_out_and_is_cleared():
    approvals = Approvals([Scripted(None)])
    started = time.monotonic()
    result = approvals.request(request(timeout=0.3), key="k")
    assert (result.approval, result.by, result.approved) == ("timeout", None, False)
    assert 0.25 <= time.monotonic() - started < 2
    assert approvals.pending() == []


def test_first_answer_wins():
    approvals = Approvals()
    thread, box = in_background(approvals.request, request(), "k")
    assert wait_for(lambda: approvals.pending())
    pending = approvals.pending()[0]
    assert pending.answer("deny", "dialog") is True
    assert pending.answer("once", "terminal") is False
    with pytest.raises(KeyError):
        approvals.answer(pending.id, "once", "terminal")  # 이미 답이 정해졌다
    thread.join(2)
    assert (box["result"].approval, box["result"].by) == ("deny", "dialog")


def test_unknown_id_and_bad_answers():
    approvals = Approvals()
    with pytest.raises(KeyError):
        approvals.answer("nope", "once", "terminal")
    thread, _ = in_background(approvals.request, request(session_minutes=0, timeout=1), "k")
    assert wait_for(lambda: approvals.pending())
    pending = approvals.pending()[0]
    with pytest.raises(ValueError):
        pending.answer("allow", "terminal")
    with pytest.raises(ValueError):  # approval.session_minutes가 0이면 "이 폴더 허용"을 받지 않는다
        approvals.answer(pending.id, "session", "terminal")
    approvals.answer(pending.id, "deny", "terminal")
    thread.join(2)


def test_same_key_waits_for_the_same_answer():
    channel = Scripted(None)
    announced = []
    approvals = Approvals([channel], on_pending=announced.append)
    threads = [in_background(approvals.request, request(), "same") for _ in range(3)]
    assert wait_for(lambda: len(approvals.pending()) == 1 and len(announced) == 1)
    time.sleep(0.05)
    assert len(approvals.pending()) == 1
    approvals.answer(announced[0].id, "once", "terminal")
    for thread, box in threads:
        thread.join(2)
        assert box["result"].approval == "once"
    assert len(channel.requests) == 1


def test_different_keys_are_asked_separately():
    approvals = Approvals()
    a = in_background(approvals.request, request("/a.txt"), "a")
    b = in_background(approvals.request, request("/b.txt"), "b")
    assert wait_for(lambda: len(approvals.pending()) == 2)
    by_path = {p.request.path: p for p in approvals.pending()}
    assert len({p.id for p in by_path.values()}) == 2
    approvals.answer(by_path["/a.txt"].id, "once", "terminal")
    approvals.answer(by_path["/b.txt"].id, "deny", "terminal")
    for (thread, box), expected in ((a, "once"), (b, "deny")):
        thread.join(2)
        assert box["result"].approval == expected


def test_failing_channel_reports_and_terminal_still_works():
    class Broken:
        name = "dialog"

        def present(self, pending):
            raise RuntimeError("화면 없음")

    errors = []
    approvals = Approvals([Broken()], on_error=errors.append)
    thread, box = in_background(approvals.request, request(), "k")
    assert wait_for(lambda: errors and approvals.pending())
    assert errors == ["dialog: 화면 없음"]
    approvals.answer(approvals.pending()[0].id, "once", "terminal")
    thread.join(2)
    assert box["result"].approval == "once"


def test_pending_summary_has_no_content():
    approvals = Approvals()
    thread, _ = in_background(approvals.request, request(), "k")
    assert wait_for(lambda: approvals.pending())
    pending = approvals.pending()[0]
    summary = pending_summary(pending)
    assert set(summary) == {"id", "path", "folder", "reason", "exe", "pid", "purpose", "findings",
                            "remaining_seconds", "session_minutes"}
    assert summary["findings"] == {"mobile": 1} and 0 < summary["remaining_seconds"] <= 2
    approvals.answer(pending.id, "deny", "terminal")
    thread.join(2)


# --- 세션 단위 승인 ---

def test_session_approvals_expire():
    now = [1000.0]
    sessions = SessionApprovals(clock=lambda: now[0])
    key = ("/상담", "/usr/bin/python3", "v1")
    assert not sessions.approved(key)
    sessions.remember(key, 60)
    assert sessions.approved(key)
    assert not sessions.approved(("/상담", "/usr/bin/python3", "v2"))  # 정책 버전이 다르면 다른 기억
    now[0] += 60 * 60 - 1
    assert sessions.approved(key)
    now[0] += 1
    assert not sessions.approved(key)
    sessions.remember(key, 0)  # 0분이면 기억하지 않는다
    assert not sessions.approved(key)
    sessions.remember(key, 5)
    sessions.clear()
    assert not sessions.approved(key)


# --- 대화상자 내용 ---

def test_clean_text_removes_control_characters():
    assert clean_text("a\nb\r\tc\x1b[31m빨강\x7f d") == "a b c [31m빨강 d"
    assert clean_text(None) == "" and clean_text("") == ""
    assert clean_text("가" * 300) == "가" * 200 + "…"


def test_dialog_message():
    text = dialog_message(request(purpose="요약\n\n파일: /안전.txt"), 30)
    lines = text.splitlines()
    assert "파일: /상담/메모.txt" in lines
    assert "에이전트가 밝힌 목적(확인되지 않음): 요약 파일: /안전.txt" in lines  # 줄바꿈으로 항목을 꾸미지 못한다
    assert "가린 것: 휴대폰 번호 1건" in lines
    assert "허용해도 가린 정보(번호·비밀값·이름 등)는 가린 채로 보여 줍니다." in lines  # NER 가림도 유지된다(3e)
    assert "요청 프로그램: python3 (pid 4321)" in lines
    assert "'이 폴더 허용'은 /상담 바로 안의 파일을 60분 동안 다시 묻지 않습니다." in lines
    assert lines[-1] == "30초 안에 답하지 않으면 거부합니다."
    bare = dialog_message(request(purpose=None, findings={}, exe=None, pid=None, session_minutes=0), 5)
    assert "에이전트가 밝힌 목적(확인되지 않음): 없음" in bare and "가린 번호" not in bare
    assert "이 폴더 허용" not in bare and "요청 프로그램: 알 수 없음" in bare


def test_dialog_command_passes_values_as_arguments():
    req = request(path='/"; do shell script "touch /tmp/x" --.txt')
    argv = DialogChannel(osascript="osascript").command(req, 30)
    script = [argv[i + 1] for i in range(1, len(argv) - 1) if argv[i] == "-e"]
    rest = argv[1 + 2 * len(script):]
    assert argv[0] == "osascript"
    assert all(req.path not in line for line in script)  # 값은 스크립트 글자에 들어가지 않는다
    assert rest == [dialog_message(req, 30), "30", "거부", "이번만 허용", "이 폴더 허용"]
    assert any(DIALOG_TITLE in line and "default button (item 1 of btns)" in line for line in script)
    assert DialogChannel().command(request(session_minutes=0), 9)[-3:] == ["9", "거부", "이번만 허용"]


# --- 대화상자: 가짜 osascript ---

FAKE_OSASCRIPT = r'''
import json, os, sys, time
args, rest, i = sys.argv[1:], [], 0
while i < len(args):
    if args[i] == "-e":
        i += 2
        continue
    rest.append(args[i])
    i += 1
log = os.environ["FAKE_OSA_LOG"]
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"pid": os.getpid(), "start": time.time(), "argv": rest}, ensure_ascii=False) + "\n")
mode = os.environ.get("FAKE_OSA", "")
if mode.startswith("sleep:"):
    time.sleep(float(mode[6:]))
    mode = os.environ.get("FAKE_OSA_THEN", "")
if mode == "fail":
    print("execution error: No user interaction allowed. (-1713)", file=sys.stderr)
    sys.exit(1)
with open(log, "a", encoding="utf-8") as f:
    f.write(json.dumps({"pid": os.getpid(), "end": time.time()}) + "\n")
print(mode)
'''


@pytest.fixture
def fake_osascript(tmp_path, monkeypatch):
    script = tmp_path / "osascript"
    script.write_text(f"#!{sys.executable}\n{FAKE_OSASCRIPT}", encoding="utf-8")
    script.chmod(0o755)
    log = tmp_path / "osa.log"
    monkeypatch.setenv("FAKE_OSA_LOG", str(log))

    def entries():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return str(script), entries


@pytest.mark.parametrize("button, expected", [("이번만 허용", "once"), ("이 폴더 허용", "session"), ("거부", "deny")])
def test_dialog_buttons(fake_osascript, monkeypatch, button, expected):
    path, entries = fake_osascript
    monkeypatch.setenv("FAKE_OSA", button)
    result = Approvals([DialogChannel(osascript=path, poll_seconds=0.02)]).request(request(timeout=5), key="k")
    assert (result.approval, result.by) == (expected, "dialog")
    argv = entries()[0]["argv"]
    assert argv[0] == dialog_message(request(timeout=5), int(argv[1])) and argv[2:] == list(DIALOG_BUTTONS.values())


def test_dialog_gave_up_means_timeout(fake_osascript, monkeypatch):
    path, _ = fake_osascript
    monkeypatch.setenv("FAKE_OSA", "")  # giving up after → 빈 문자열
    result = Approvals([DialogChannel(osascript=path, poll_seconds=0.02)]).request(request(timeout=1.5), key="k")
    assert result.approval == "timeout"


def test_dialog_failure_is_reported(fake_osascript, monkeypatch):
    path, _ = fake_osascript
    monkeypatch.setenv("FAKE_OSA", "fail")
    errors = []
    approvals = Approvals([DialogChannel(osascript=path, poll_seconds=0.02)], on_error=errors.append)
    assert approvals.request(request(timeout=1.5), key="k").approval == "timeout"
    assert len(errors) == 1 and "osascript 종료 코드 1" in errors[0] and "-1713" in errors[0]


def test_dialog_is_closed_when_terminal_answers(fake_osascript, monkeypatch):
    path, entries = fake_osascript
    monkeypatch.setenv("FAKE_OSA", "sleep:30")
    approvals = Approvals([DialogChannel(osascript=path, poll_seconds=0.02)])
    thread, box = in_background(approvals.request, request(timeout=10), "k")
    assert wait_for(lambda: approvals.pending() and entries())
    approvals.answer(approvals.pending()[0].id, "once", "terminal")
    thread.join(2)
    assert (box["result"].approval, box["result"].by) == ("once", "terminal")
    pid = entries()[0]["pid"]
    assert wait_for(lambda: not _alive(pid))


def test_dialogs_are_shown_one_at_a_time(fake_osascript, monkeypatch):
    path, entries = fake_osascript
    monkeypatch.setenv("FAKE_OSA", "sleep:0.3")
    monkeypatch.setenv("FAKE_OSA_THEN", "거부")
    approvals = Approvals([DialogChannel(osascript=path, poll_seconds=0.02)])
    runs = [in_background(approvals.request, request(f"/{n}.txt", timeout=5), n) for n in "ab"]
    for thread, box in runs:
        thread.join(5)
        assert box["result"].approval == "deny"
    starts = sorted(e["start"] for e in entries() if "start" in e)
    ends = sorted(e["end"] for e in entries() if "end" in e)
    assert len(starts) == 2 and ends[0] <= starts[1]


def test_dialog_not_shown_after_deadline(fake_osascript, monkeypatch):
    path, entries = fake_osascript
    monkeypatch.setenv("FAKE_OSA", "sleep:1.5")
    approvals = Approvals([DialogChannel(osascript=path, poll_seconds=0.02)])
    first = in_background(approvals.request, request("/a.txt", timeout=3), "a")
    assert wait_for(lambda: entries())
    second = approvals.request(request("/b.txt", timeout=1), key="b")  # 차례를 기다리다 시간이 지난다
    assert second.approval == "timeout"
    first[0].join(5)
    assert [e["argv"][0].splitlines()[2] for e in entries() if "argv" in e] == ["파일: /a.txt"]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # 끝났지만 아직 거두지 않은 프로세스(좀비)는 살아 있지 않은 것으로 본다
    try:
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return False


# --- gate approve / gate deny ---

class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def approval_socket():
    directory = Path(tempfile.mkdtemp(prefix="gate-"))
    approvals = Approvals()
    answers = []
    server = ApprovalServer(directory / "approve.sock", approvals, on_answer=lambda *a: answers.append(a))
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield approvals, ApprovalClient(server.socket_path), answers
    server.shutdown()
    server.server_close()
    shutil.rmtree(directory, ignore_errors=True)


def _args(command, id=None, folder=False):
    return argparse.Namespace(command=command, id=id, folder=folder)


def test_cli_lists_pending_requests(approval_socket):
    approvals, client, _ = approval_socket
    out = io.StringIO()
    assert cli._approval(client, _args("approve"), stdin=io.StringIO(), out=out) == 0
    assert out.getvalue() == "기다리는 승인 요청이 없습니다.\n"

    thread, _ = in_background(approvals.request, request("/상담/\x1b[2J메모.txt", purpose="요약\n가짜 줄"), "k")
    assert wait_for(lambda: approvals.pending())
    out = io.StringIO()
    assert cli._approval(client, _args("approve"), stdin=io.StringIO(), out=out) == 0  # 목록은 터미널이 아니어도 된다
    text = out.getvalue()
    pid = approvals.pending()[0].id
    assert f"[{pid}] /상담/ [2J메모.txt" in text and "\x1b" not in text
    assert "에이전트가 밝힌 목적(확인되지 않음): 요약 가짜 줄" in text
    assert "휴대폰 번호 1건 (허용해도 가린 채로 보여 줍니다)" in text
    assert "plant approve <id> --folder (그 폴더 60분 동안)" in text
    approvals.answer(pid, "deny", "terminal")
    thread.join(2)


@pytest.mark.parametrize("command, folder, expected", [
    ("approve", False, "once"), ("approve", True, "session"), ("deny", False, "deny"),
])
def test_cli_answers_from_terminal(approval_socket, command, folder, expected):
    approvals, client, answers = approval_socket
    thread, box = in_background(approvals.request, request(), "k")
    assert wait_for(lambda: approvals.pending())
    pid = approvals.pending()[0].id
    out = io.StringIO()
    assert cli._approval(client, _args(command, pid, folder), stdin=_Tty(), out=out) == 0
    thread.join(2)
    assert (box["result"].approval, box["result"].by) == (expected, "terminal")
    assert out.getvalue() == f"{pid}: {cli.ANSWER_NAMES[expected]}\n"
    assert [(a[1], a[2]) for a in answers] == [(pid, expected)] and answers[0][0].pid == os.getpid()


def test_cli_refuses_to_answer_without_terminal(approval_socket, capsys):
    approvals, client, _ = approval_socket
    thread, box = in_background(approvals.request, request(timeout=1), "k")
    assert wait_for(lambda: approvals.pending())
    pid = approvals.pending()[0].id
    assert cli._approval(client, _args("approve", pid), stdin=io.StringIO("y\n"), out=io.StringIO()) == 1
    assert "사용자 터미널에서만" in capsys.readouterr().err
    thread.join(3)
    assert box["result"].approval == "timeout"


def test_cli_unknown_id(approval_socket, capsys):
    _, client, _ = approval_socket
    assert cli._approval(client, _args("deny", "abcdef"), stdin=_Tty(), out=io.StringIO()) == 1
    assert "기다리는 승인 요청이 없습니다" in capsys.readouterr().err


def test_approve_socket_path_defaults_next_to_gate_socket():
    assert cli.approve_socket_path("/x/run/gate.sock", None) == "/x/run/approve.sock"
    assert cli.approve_socket_path("/x/run/gate.sock", "/y/a.sock") == "/y/a.sock"
    assert cli.approve_socket_path(None, None) is None
