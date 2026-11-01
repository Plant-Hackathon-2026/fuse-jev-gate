import errno
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from gate.client import GateClient
from gate.daemon import GateServer
from gate.jev_client import MockJevClient
from gate.pipeline import Inspector
from gate.policy import Entry, PathRule, PolicySource, write_entries
from gate.protocol import WRITE_OPS, GateError
from gate.store import PlainStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = "상담자 홍길동(가명)과의 상담 기록입니다.\n두 번째 줄.\n"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "상담").mkdir(parents=True)
    (root / "빈폴더").mkdir()
    (root / "readme.txt").write_bytes(b"hello\n")
    (root / "상담" / "2026-03.txt").write_text(SAMPLE, encoding="utf-8")
    (root / "binary.bin").write_bytes(bytes(range(256)))
    (tmp_path / "outside.txt").write_text("원본 폴더 밖\n", encoding="utf-8")
    (root / "escape").symlink_to(tmp_path / "outside.txt")
    (root / "inner").symlink_to(root / "readme.txt")
    return root


@pytest.fixture
def socket_dir():
    # pytest의 tmp_path는 맥에서 소켓 경로 길이 제한(104바이트)을 넘을 수 있어 짧은 경로를 쓴다.
    path = Path(tempfile.mkdtemp(prefix="gate-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def settled(events, n):
    """데몬은 응답을 보낸 뒤 이벤트를 넘긴다. 이벤트가 n개 쌓일 때까지 기다려 마지막 것을 돌려준다."""
    deadline = time.monotonic() + 3
    while len(events) < n and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(events) >= n, f"이벤트 {len(events)}개 (기대 {n}개)"
    return events[n - 1]


def _start(server):
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def gate(source, socket_dir, tmp_path):
    events = []
    # 이 파일은 소켓·정규식·Jev를 본다. NER은 명시적으로 끈다(기본은 켜짐, tests/test_ner_pipeline.py가 따로 본다).
    # 바이너리는 사람 승인이 필요하다(gate.encoding). 여기서는 바이트를 그대로 주고받는지 보려고 경로 규칙으로 허용한다.
    policy_path = tmp_path / "policy.yaml"
    write_entries(policy_path, [Entry(path_rule=PathRule("binary.bin", "allow"))])
    store = PlainStore(source)
    server = GateServer(socket_dir / "run" / "gate.sock", store, on_request=events.append,
                        inspector=Inspector(store, PolicySource(policy_path), ner_factory=lambda p: None))
    _start(server)
    yield server, GateClient(server.socket_path), events
    server.shutdown()
    server.server_close()


def test_ls_matches_source(gate):
    _, client, _ = gate
    assert client.ls("/") == [
        {"name": "binary.bin", "type": "file"},
        {"name": "escape", "type": "other"},
        {"name": "inner", "type": "file"},
        {"name": "readme.txt", "type": "file"},
        {"name": "빈폴더", "type": "dir"},
        {"name": "상담", "type": "dir"},
    ]
    assert client.ls("/상담") == [{"name": "2026-03.txt", "type": "file"}]


def test_stat_matches_source(gate, source):
    _, client, _ = gate
    st = client.stat("/상담/2026-03.txt")
    real = (source / "상담" / "2026-03.txt").stat()
    assert st == {
        "type": "file",
        "size": real.st_size,
        "mode": stat.S_IMODE(real.st_mode),
        "mtime_ns": real.st_mtime_ns,
    }
    assert client.stat("/")["type"] == "dir"


def test_cat_returns_original_bytes(gate, source):
    _, client, _ = gate
    assert client.cat("/상담/2026-03.txt") == (source / "상담" / "2026-03.txt").read_bytes()
    assert client.cat("/binary.bin") == bytes(range(256))
    assert client.cat("/inner") == b"hello\n"


def test_cat_with_offset_and_size(gate):
    _, client, _ = gate
    assert client.cat("/binary.bin", offset=10, size=5) == bytes(range(10, 15))
    assert client.cat("/binary.bin", offset=250, size=100) == bytes(range(250, 256))
    assert client.cat("/binary.bin", offset=1000) == b""


@pytest.mark.parametrize("path, expected", [
    ("/../outside.txt", errno.EACCES),
    ("/상담/../../outside.txt", errno.EACCES),
    ("/escape", errno.EACCES),
    ("/없음.txt", errno.ENOENT),
    ("/상담", errno.EISDIR),
])
def test_cat_errors(gate, path, expected):
    _, client, _ = gate
    with pytest.raises(GateError) as exc:
        client.cat(path)
    assert exc.value.errno == expected


@pytest.mark.parametrize("op", sorted(WRITE_OPS))
def test_write_ops_are_erofs(gate, source, op):
    _, client, _ = gate
    before = sorted(p.name for p in source.rglob("*"))
    with pytest.raises(GateError) as exc:
        client.request(op, path="/readme.txt", data_b64="")
    assert exc.value.errno == errno.EROFS
    assert sorted(p.name for p in source.rglob("*")) == before


def test_unknown_op_is_enosys(gate):
    _, client, _ = gate
    with pytest.raises(GateError) as exc:
        client.request("format_disk")
    assert exc.value.errno == errno.ENOSYS


def test_socket_permissions(gate):
    server, _, _ = gate
    assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(server.socket_path.parent.stat().st_mode) == 0o700


def test_other_uid_is_rejected(source, socket_dir):
    server = GateServer(socket_dir / "gate.sock", PlainStore(source), allowed_uid=os.getuid() + 1)
    _start(server)
    try:
        with pytest.raises(GateError) as exc:
            GateClient(server.socket_path).cat("/readme.txt")
        assert exc.value.errno == errno.EACCES
    finally:
        server.shutdown()
        server.server_close()


def test_second_daemon_on_same_socket_is_refused(gate, source):
    server, _, _ = gate
    with pytest.raises(RuntimeError):
        GateServer(server.socket_path, PlainStore(source))


def _run_cli(socket_path, *args):
    return subprocess.Popen(
        [sys.executable, "-m", "gate.cli", "--socket", str(socket_path), *args],
        cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def test_cli_cat_and_peer_pid(gate, source):
    server, _, events = gate
    proc = _run_cli(server.socket_path, "cat", "/상담/2026-03.txt", "--purpose", "상담 요약")
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err.decode()
    assert out == (source / "상담" / "2026-03.txt").read_bytes()

    event = settled(events, 1)
    assert event.peer.pid == proc.pid
    assert event.peer.uid == os.getuid()
    assert event.peer.exe and Path(event.peer.exe).name.startswith("python")
    assert (event.op, event.path, event.errno) == ("cat", "/상담/2026-03.txt", None)


def test_cli_ls_and_error_exit_code(gate):
    server, _, events = gate
    proc = _run_cli(server.socket_path, "ls", "/")
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert out.decode().splitlines() == ["binary.bin", "escape", "inner", "readme.txt", "빈폴더/", "상담/"]

    proc = _run_cli(server.socket_path, "cat", "/../outside.txt")
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 1
    assert out == b""
    assert "EACCES" in err.decode()
    assert settled(events, 2).errno == "EACCES"


# --- Phase 4: 판단 파이프라인 연결 ---

PHONE_DOC = "상담 연락처 010-1234-5678 입니다.\n"


@pytest.fixture
def judged(source, socket_dir, tmp_path):
    """정책 파일과 Mock Jev(fixed)를 붙인 데몬."""
    (source / "연락처.txt").write_text(PHONE_DOC, encoding="utf-8")
    (source / "카드.txt").write_text("카드 4111 1111 1111 1111\n", encoding="utf-8")
    policy_path = tmp_path / "policy.yaml"
    jev = MockJevClient("fixed", label="safe", confidence=0.99, by_path={"/readme.txt": ("sensitive", 0.97)})
    events = []
    store = PlainStore(source)
    inspector = Inspector(store, PolicySource(policy_path), jev_factory=lambda policy: jev, ner_factory=lambda p: None)
    server = GateServer(socket_dir / "gate.sock", store, on_request=events.append, inspector=inspector)
    _start(server)
    yield GateClient(server.socket_path), jev, policy_path, events
    server.shutdown()
    server.server_close()


def test_cat_masks_numbers_and_reports_decision(judged):
    client, jev, _, events = judged
    result = client.read("/연락처.txt", purpose="상담 요약")
    assert result.data == "상담 연락처 ***-****-**** 입니다.\n".encode()
    assert (result.decision, result.masked, result.labeled) == ("mask", {"mobile": 1}, {})
    assert client.read("/상담/2026-03.txt").decision == "allow"
    judgment = settled(events, 1).judgment
    assert (judgment.action, dict(judgment.findings)) == ("mask", {"mobile": 1})
    assert jev.calls[0].context.purpose == "상담 요약"
    assert jev.calls[0].context.pid == os.getpid()


def test_cat_offset_and_size_slice_the_masked_buffer(judged):
    client, _, _, _ = judged
    whole = client.cat("/연락처.txt")
    assert client.cat("/연락처.txt", offset=10, size=8) == whole[10:18]
    assert client.cat("/연락처.txt", offset=len(whole) + 5) == b""


def test_blocked_read_is_eacces_with_reason_and_no_content(judged):
    client, _, _, events = judged
    with pytest.raises(GateError) as exc:
        client.cat("/readme.txt")
    assert exc.value.errno == errno.EACCES
    assert "판단 모델 결과(sensitive" in exc.value.message
    event = settled(events, 1)
    assert (event.errno, event.judgment.action) == ("EACCES", "deny")


def test_running_daemon_picks_up_policy_changes(judged):
    client, jev, policy_path, _ = judged
    assert client.read("/카드.txt").decision == "mask"
    write_entries(policy_path, [Entry(settings={"numbers.card.action": "deny"})])
    with pytest.raises(GateError) as exc:
        client.cat("/카드.txt")
    assert exc.value.errno == errno.EACCES and "카드번호" in exc.value.message
    write_entries(policy_path, [Entry(settings={"numbers.card.visible": "last4"})])
    assert client.cat("/카드.txt") == "카드 **** **** **** 1111\n".encode()
    policy_path.write_text("version: 1\nentries: [", encoding="utf-8")
    with pytest.raises(GateError) as exc:
        client.cat("/상담/2026-03.txt")
    assert "정책 파일" in exc.value.message
    assert client.ls("/상담") == [{"name": "2026-03.txt", "type": "file"}]  # 목록 조회는 막지 않는다


def test_rereading_through_socket_hits_cache(judged):
    client, jev, _, events = judged
    client.cat("/연락처.txt", purpose="요약")
    client.cat("/연락처.txt", purpose="요약")
    assert len([c for c in jev.calls if c.context.path == "/연락처.txt"]) == 1
    assert [e.judgment.cached for e in events] == [False, True]


def test_search_through_socket(judged):
    client, _, _, _ = judged
    result = client.search("/", "연락처")
    assert {m["path"] for m in result["matches"]} == {"/연락처.txt"}
    assert result["matches"][0]["text"] == "상담 연락처 ***-****-**** 입니다."
    # inner는 readme.txt를 가리킨다. binary.bin은 바이너리라 사람 승인이 필요하고 search는 묻지 않는다
    assert {s["path"] for s in result["skipped"]} == {"/readme.txt", "/inner", "/binary.bin"}
    assert client.search("/", "010-1234")["matches"] == []  # 가려진 값은 찾을 수 없다
    with pytest.raises(GateError) as exc:
        client.search("/", "")
    assert exc.value.errno == errno.EINVAL


@pytest.mark.parametrize("fields", [
    {"offset": -1}, {"offset": "0"}, {"offset": True}, {"size": -5}, {"size": 1.5}, {"purpose": 3},
])
def test_cat_rejects_bad_fields(judged, fields):
    client, _, _, _ = judged
    with pytest.raises(GateError) as exc:
        client.request("cat", path="/연락처.txt", **fields)
    assert exc.value.errno == errno.EINVAL


# --- gate cat 끝 안내: 정규식은 같은 길이 `*`, 규칙·NER은 `[이름]`처럼 종류 이름 ---

from gate import cli  # noqa: E402
from gate.cli import mask_notice  # noqa: E402
from gate.ner_client import Entity, NerResult  # noqa: E402

STARS_ONLY = "plant: 개인정보 일부를 같은 길이의 *로 가렸습니다 (휴대폰 번호 1건)"
LABELS_ONLY = "plant: 개인정보 일부를 [이름]처럼 종류 이름으로 가렸습니다 (이름 1건, IP·내부 주소 1건, 일반 전화번호 1건)"
BOTH = "plant: 개인정보 일부를 같은 길이의 *로 (휴대폰 번호 1건), [이름]처럼 종류 이름으로 (이름 1건) 가렸습니다"


class WordNer:
    """words의 글자가 처음 나오는 곳을 개체 하나로 돌려주는 가짜 NER."""

    def __init__(self, words):
        self.words = words

    def cache_tag(self):
        return "ner:test"

    def find(self, text):
        found = [Entity(label, text.find(w), text.find(w) + len(w), 0.9) for w, label in self.words.items() if w in text]
        return NerResult(tuple(sorted(found, key=lambda e: e.start)), "ner:test")


@pytest.fixture
def ner_gate(source, socket_dir, tmp_path):
    """NER이 켜진(기본 정책) 데몬. 가린 결과는 Jev가 safe라고 답해 가린 채 보여 준다."""
    (source / "번호.txt").write_text("연락처 010-1234-5678\n", encoding="utf-8")
    (source / "이름.txt").write_text("담당 홍길동, 서버 10.0.0.12, 대표번호 1588-1234\n", encoding="utf-8")
    (source / "둘다.txt").write_text("담당 홍길동 010-1234-5678\n", encoding="utf-8")
    ner = WordNer({"홍길동": "NAME", "1588-1234": "PHONE", "010-1234-5678": "PHONE"})
    jev = MockJevClient("fixed", label="safe", confidence=0.99)
    store = PlainStore(source)
    inspector = Inspector(store, PolicySource(tmp_path / "없음.yaml"), jev_factory=lambda p: jev,
                          ner_factory=lambda p: ner)
    server = GateServer(socket_dir / "run" / "gate.sock", store, inspector=inspector)
    _start(server)
    yield server.socket_path
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("path, shown, masked, labeled, notice", [
    ("/번호.txt", "연락처 ***-****-****\n", {"mobile": 1}, {}, STARS_ONLY),
    # 규칙(IP)과 NER만. 정규식이 놓친 대표번호도 칸은 일반 전화번호지만 `*`가 아니라 [전화번호]로 가렸다
    ("/이름.txt", "담당 [이름], 서버 [IP], 대표번호 [전화번호]\n", {"name": 1, "host": 1, "phone": 1},
     {"name": 1, "host": 1, "phone": 1}, LABELS_ONLY),
    ("/둘다.txt", "담당 [이름] ***-****-****\n", {"name": 1, "mobile": 1}, {"name": 1}, BOTH),
])
def test_cli_cat_notice_names_the_masking_actually_used(ner_gate, capsys, path, shown, masked, labeled, notice):
    result = GateClient(ner_gate).read(path)
    assert (result.data.decode(), result.masked, result.labeled) == (shown, masked, labeled)
    assert cli.main(["--socket", str(ner_gate), "cat", path]) == 0
    out, err = capsys.readouterr()
    assert out == shown
    assert err == notice + "\n"


def test_mask_notice_by_kind():
    assert mask_notice({"mobile": 1}, {}) == STARS_ONLY
    assert mask_notice({"name": 1, "host": 1, "phone": 1}, {"name": 1, "host": 1, "phone": 1}) == LABELS_ONLY
    assert mask_notice({"mobile": 1, "name": 1}, {"name": 1}) == BOTH
    # 같은 칸을 정규식과 NER이 나눠 가린 경우(휴대폰 번호 2건 중 1건은 정규식이 놓쳐 NER이 찾았다)
    assert mask_notice({"mobile": 2}, {"mobile": 1}) == (
        "plant: 개인정보 일부를 같은 길이의 *로 (휴대폰 번호 1건), [이름]처럼 종류 이름으로 (휴대폰 번호 1건) 가렸습니다")


def test_old_daemon_response_without_labeled_is_read_by_kind_names(monkeypatch):
    """labeled가 없는 예전 데몬 응답: 번호 종류는 `*`, 그 밖은 종류 이름으로 가렸다고 본다."""
    client = GateClient("/없는/소켓")
    monkeypatch.setattr(client, "request", lambda op, **fields: {
        "ok": True, "data_b64": "", "decision": "mask", "masked": {"mobile": 1, "name": 1}})
    result = client.read("/둘다.txt")
    assert (result.masked, result.labeled) == ({"mobile": 1, "name": 1}, None)
    assert mask_notice(result.masked, result.labeled) == BOTH
    assert mask_notice({"mobile": 1}, None) == STARS_ONLY
    assert mask_notice({"host": 1}, None) == "plant: 개인정보 일부를 [이름]처럼 종류 이름으로 가렸습니다 (IP·내부 주소 1건)"


# --- Phase 5: 사람 승인 (게이트 소켓 + 승인 소켓) ---

from gate.approval import Approvals  # noqa: E402
from gate.client import ApprovalClient  # noqa: E402
from gate.daemon import ApprovalServer  # noqa: E402


@pytest.fixture
def asking_gate(source, socket_dir, tmp_path):
    """Jev가 확신하지 못하는(ask_human) 게이트. 승인 창구는 승인 소켓뿐이다."""
    policy_path = tmp_path / "policy.yaml"
    write_entries(policy_path, [Entry(settings={"approval.timeout_seconds": 1.0})])
    store = PlainStore(source)
    approvals = Approvals()
    jev = MockJevClient("fixed", label="sensitive", confidence=0.7)
    events, answers = [], []
    server = GateServer(socket_dir / "run" / "gate.sock", store, on_request=events.append,
                        inspector=Inspector(store, PolicySource(policy_path), jev_factory=lambda p: jev,
                                            ner_factory=lambda p: None, approvals=approvals))
    approval_server = ApprovalServer(socket_dir / "run" / "approve.sock", approvals,
                                     on_answer=lambda *a: answers.append(a))
    _start(server)
    _start(approval_server)
    yield GateClient(server.socket_path), ApprovalClient(approval_server.socket_path), events, answers
    for s in (server, approval_server):
        s.shutdown()
        s.server_close()


def _read_in_background(client, path):
    box = {}

    def run():
        try:
            box["result"] = client.read(path, purpose="요약")
        except GateError as exc:
            box["error"] = exc
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def _wait_pending(approver, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pending = approver.pending()
        if pending:
            return pending
        time.sleep(0.01)
    raise AssertionError("승인 요청이 오지 않았습니다")


def test_cat_waits_and_terminal_approval_returns_content(asking_gate):
    client, approver, events, answers = asking_gate
    thread, box = _read_in_background(client, "/상담/2026-03.txt")
    [p] = _wait_pending(approver)
    assert (p["path"], p["folder"], p["purpose"], p["pid"]) == ("/상담/2026-03.txt", "/상담", "요약", os.getpid())
    assert p["reason"] == "판단 모델 결과(sensitive, 확신도 0.70)가 확실하지 않습니다"
    approver.answer(p["id"], "once")
    thread.join(3)
    assert box["result"].data == SAMPLE.encode() and box["result"].decision == "allow"
    j = settled(events, 1).judgment
    assert (j.action, j.stage, j.approval, j.approved_by) == ("allow", "human", "once", "terminal")
    assert [(a[1], a[2]) for a in answers] == [(p["id"], "once")]
    assert approver.pending() == []


def test_cat_denied_from_terminal(asking_gate):
    client, approver, events, _ = asking_gate
    thread, box = _read_in_background(client, "/상담/2026-03.txt")
    [p] = _wait_pending(approver)
    approver.answer(p["id"], "deny")
    thread.join(3)
    assert box["error"].errno == errno.EACCES and box["error"].message == "사용자가 거부했습니다"
    event = settled(events, 1)
    assert (event.errno, event.judgment.approval) == ("EACCES", "deny")


def test_cat_without_answer_times_out(asking_gate):
    client, approver, events, _ = asking_gate
    with pytest.raises(GateError) as exc:
        client.read("/상담/2026-03.txt")
    assert exc.value.errno == errno.EACCES and exc.value.message.startswith("1초 안에 승인이 없어 차단했습니다")
    assert settled(events, 1).judgment.approval == "timeout"
    assert approver.pending() == []


def test_folder_approval_over_sockets(asking_gate):
    client, approver, events, _ = asking_gate
    thread, box = _read_in_background(client, "/상담/2026-03.txt")
    approver.answer(_wait_pending(approver)[0]["id"], "session")
    thread.join(3)
    assert client.cat("/상담/2026-03.txt") == SAMPLE.encode()  # 다시 묻지 않는다
    assert settled(events, 2).judgment.approval == "remembered"


def test_approval_socket_errors(asking_gate):
    client, approver, _, _ = asking_gate
    for fields, err in (({"id": "abcdef", "answer": "once"}, errno.ENOENT),
                        ({"id": "abcdef", "answer": "allow"}, errno.EINVAL),
                        ({"id": 3, "answer": "once"}, errno.EINVAL)):
        with pytest.raises(GateError) as exc:
            approver.request("answer", **fields)
        assert exc.value.errno == err
    with pytest.raises(GateError) as exc:
        approver.request("cat", path="/상담/2026-03.txt")  # 승인 소켓으로는 파일을 읽을 수 없다
    assert exc.value.errno == errno.ENOSYS


def test_gate_socket_cannot_approve(asking_gate):
    """에이전트가 쓰는 게이트 소켓에는 승인 요청이 없다(에이전트가 스스로 승인하지 못하게)."""
    client, approver, _, _ = asking_gate
    thread, box = _read_in_background(client, "/상담/2026-03.txt")
    [p] = _wait_pending(approver)
    for op in ("answer", "pending"):
        with pytest.raises(GateError) as exc:
            client.request(op, id=p["id"], answer="once")
        assert exc.value.errno == errno.ENOSYS
    approver.answer(p["id"], "deny")
    thread.join(3)
    assert "error" in box


def test_approval_socket_rejects_other_uid(source, socket_dir):
    server = ApprovalServer(socket_dir / "approve.sock", Approvals(), allowed_uid=os.getuid() + 1)
    _start(server)
    try:
        with pytest.raises(GateError) as exc:
            ApprovalClient(server.socket_path).pending()
        assert exc.value.errno == errno.EACCES
        assert stat.S_IMODE(os.stat(server.socket_path).st_mode) == 0o600
    finally:
        server.shutdown()
        server.server_close()
    assert not server.socket_path.exists()
