"""gate.audit: 요청 이벤트를 JSONL 감사 로그로 남긴다. 모든 값은 가짜 예시다."""

import errno
import hashlib
import hmac
import json
import os
import re
import stat
import threading
from datetime import datetime, timedelta, timezone

import pytest

from gate.audit import (
    AUDIT_SCHEMA_VERSION,
    KEY_BYTES,
    AuditLog,
    content_hmac,
    content_hmac_of,
    load_or_create_key,
    read_records,
    to_record,
)
from gate.daemon import RequestEvent
from gate.jev_client import MockJevClient
from gate.peer import Peer
from gate.pipeline import Blocked, Inspector, Judgment
from gate.policy import PolicySource
from gate.store import PlainStore

PEER = Peer(pid=4321, uid=501, exe="/usr/local/bin/python3.12")
KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 31, 14, 3, 5, 123456, tzinfo=KST)
TS = "2026-10-31T14:03:05.123+09:00"
SHA = "ab" * 32
KEY = bytes(range(KEY_BYTES))
SHA_HMAC = hmac.new(KEY, bytes.fromhex(SHA), hashlib.sha256).hexdigest()
KEYS = ["v", "ts", "pid", "uid", "exe", "process", "op", "path", "errno", "decision", "reason", "stage",
        "policy_version", "content_hmac", "findings", "jev_label", "jev_confidence", "jev_calls", "cached",
        "approval", "approved_by", "elapsed_ms"]
JUDGMENT_KEYS = KEYS[KEYS.index("reason"):KEYS.index("elapsed_ms")]
FAKE_PHONE = "010-1234-5678"
FAKE_RRN = "900101-1234567"


def event(op="cat", path="/문서/회의.txt", errno_name=None, judgment=None, elapsed_ms=12.345, peer=PEER):
    return RequestEvent(peer, op, path, errno_name, elapsed_ms, judgment)


def base(**changes) -> dict:
    """판단이 없는 성공 기록. changes로 바꾼다."""
    record = {
        "v": AUDIT_SCHEMA_VERSION, "ts": TS, "pid": 4321, "uid": 501, "exe": "/usr/local/bin/python3.12",
        "process": "python3.12", "op": "cat", "path": "/문서/회의.txt", "errno": None, "decision": "ok",
        **dict.fromkeys(JUDGMENT_KEYS), "elapsed_ms": 12.3,
    }
    record.update(changes)
    return record


# --- 기록 모양 ---

CAT_CASES = {
    "allow": (
        Judgment("allow", "Jev 판단: 안전", "jev", policy_version="pv-1", content_sha256=SHA, jev_label="safe",
                 jev_confidence=0.97, jev_calls=1),
        None,
        {"decision": "allow", "reason": "Jev 판단: 안전", "stage": "jev", "policy_version": "pv-1",
         "content_hmac": SHA_HMAC, "findings": {}, "jev_label": "safe", "jev_confidence": 0.97, "jev_calls": 1,
         "cached": False},
    ),
    "mask": (
        Judgment("mask", "번호를 가렸습니다", "jev", policy_version="pv-1", content_sha256=SHA,
                 findings={"phone": 2, "rrn": 1}, jev_label="safe", jev_confidence=0.9, jev_calls=0, cached=True),
        None,
        {"decision": "mask", "reason": "번호를 가렸습니다", "stage": "jev", "policy_version": "pv-1",
         "content_hmac": SHA_HMAC, "findings": {"phone": 2, "rrn": 1}, "jev_label": "safe", "jev_confidence": 0.9,
         "jev_calls": 0, "cached": True},
    ),
    "deny": (
        Judgment("deny", "카드 번호가 있어 차단합니다", "number", policy_version="pv-1", content_sha256=SHA,
                 findings={"card": 1}),
        "EACCES",
        {"errno": "EACCES", "decision": "deny", "reason": "카드 번호가 있어 차단합니다", "stage": "number",
         "policy_version": "pv-1", "content_hmac": SHA_HMAC, "findings": {"card": 1}, "jev_calls": 0, "cached": False},
    ),
    "approved": (
        Judgment("mask", "사용자가 승인했습니다 (진료 기록일 수 있음)", "human", policy_version="pv-1",
                 content_sha256=SHA, findings={"phone": 1}, jev_label="sensitive", jev_confidence=0.71, jev_calls=1,
                 approval="session", approved_by="dialog"),
        None,
        {"decision": "mask", "reason": "사용자가 승인했습니다 (진료 기록일 수 있음)", "stage": "human",
         "policy_version": "pv-1", "content_hmac": SHA_HMAC, "findings": {"phone": 1}, "jev_label": "sensitive",
         "jev_confidence": 0.71, "jev_calls": 1, "cached": False, "approval": "session", "approved_by": "dialog"},
    ),
    "timeout": (
        Judgment("deny", "승인 대기 시간이 지났습니다", "human", policy_version="pv-1", content_sha256=SHA,
                 jev_label="sensitive", jev_confidence=0.6, jev_calls=1, approval="timeout"),
        "EACCES",
        {"errno": "EACCES", "decision": "deny", "reason": "승인 대기 시간이 지났습니다", "stage": "human",
         "policy_version": "pv-1", "content_hmac": SHA_HMAC, "findings": {}, "jev_label": "sensitive",
         "jev_confidence": 0.6, "jev_calls": 1, "cached": False, "approval": "timeout", "approved_by": None},
    ),
}


@pytest.mark.parametrize("case", CAT_CASES)
def test_cat_judgments_are_recorded(case):
    judgment, errno_name, expected = CAT_CASES[case]
    record = to_record(event(errno_name=errno_name, judgment=judgment), NOW, KEY)
    assert list(record) == KEYS
    assert record == base(**expected)


@pytest.mark.parametrize("op", ["ls", "stat", "search"])
def test_unjudged_success_is_ok(op):
    record = to_record(event(op=op, path="/문서"), NOW, KEY)
    assert list(record) == KEYS
    assert record == base(op=op, path="/문서")


@pytest.mark.parametrize("op", ["ls", "stat", "cat", "search"])
def test_failure_without_judgment_is_error(op):
    record = to_record(event(op=op, path="/없는.txt", errno_name="ENOENT"), NOW, KEY)
    assert record == base(op=op, path="/없는.txt", errno="ENOENT", decision="error")


@pytest.mark.parametrize("op, path", [
    (None, None),
    ({"op": "cat"}, ["/a.txt"]),
    (7, {"path": "/a.txt"}),
    (True, 3.5),
])
def test_malformed_request_is_recorded_with_null_fields(tmp_path, op, path):
    malformed = event(op=op, path=path, errno_name="EINVAL", elapsed_ms=0.04)
    assert to_record(malformed, NOW, KEY) == base(op=None, path=None, errno="EINVAL", decision="error", elapsed_ms=0.0)
    with AuditLog(tmp_path / "audit.jsonl") as log:
        log.record(malformed, now=NOW)
    assert read_records(tmp_path / "audit.jsonl") == [to_record(malformed, NOW, KEY)]


def test_peer_without_exe_has_null_process():
    record = to_record(event(peer=Peer(4321, 501, None)), NOW, KEY)
    assert (record["exe"], record["process"]) == (None, None)


def test_elapsed_is_rounded_and_non_finite_numbers_become_null(tmp_path):
    assert to_record(event(elapsed_ms=3.06), NOW, KEY)["elapsed_ms"] == 3.1
    weird = Judgment("allow", "검사 통과", "jev", jev_confidence=float("nan"), jev_calls=1)
    with AuditLog(tmp_path / "audit.jsonl") as log:
        log.record(event(judgment=weird, elapsed_ms=float("inf")), now=NOW)
    line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")

    def reject(constant):
        raise AssertionError(f"엄격한 JSON이 아닙니다: {constant}")

    record = json.loads(line, parse_constant=reject)
    assert (record["jev_confidence"], record["elapsed_ms"]) == (None, None)


# --- 원문·탐지 값 ---

def test_raw_content_and_found_values_never_reach_the_log(tmp_path):
    """가짜 번호가 든 파일을 실제 파이프라인으로 판단해 그 판단을 기록한다. 목적(purpose)에 넣은 값도 남지 않는다."""
    root = tmp_path / "source"
    root.mkdir()
    (root / "연락처.txt").write_text(f"담당자 연락처 {FAKE_PHONE}\n", encoding="utf-8")
    (root / "주민.txt").write_text(f"주민번호 {FAKE_RRN}\n", encoding="utf-8")
    jev = MockJevClient("fixed", label="safe", confidence=0.99)
    inspector = Inspector(PlainStore(root), PolicySource(tmp_path / "policy.yaml"), jev_factory=lambda policy: jev)
    purpose = f"{FAKE_PHONE} 번호와 {FAKE_RRN} 확인"
    log_path = tmp_path / "logs" / "audit.jsonl"
    with AuditLog(log_path) as log:
        for path in ("/연락처.txt", "/주민.txt"):
            try:
                judgment, errno_name = inspector.inspect(PEER, path, purpose).judgment, None
            except Blocked as exc:
                judgment, errno_name = exc.judgment, "EACCES"
            assert judgment.findings
            log.record(event(path=path, errno_name=errno_name, judgment=judgment))
        # 손으로 만든 판단도 같이 본다
        log.record(event(judgment=Judgment("mask", "번호를 가렸습니다", "number", findings={"phone": 1, "rrn": 1})))

    content = log_path.read_text(encoding="utf-8")
    for secret in (FAKE_PHONE, FAKE_RRN, "1234-5678", "1234567", "900101", "01012345678"):
        assert secret not in content
    for name in ("연락처.txt", "주민.txt"):  # 파일 해시는 SHA-256이 아니라 HMAC으로만 남는다
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() not in content
    records = read_records(log_path)
    assert len(records) == 3
    for record in records:
        assert list(record) == KEYS
        assert sum(record["findings"].values()) >= 1
        assert all(isinstance(count, int) for count in record["findings"].values())


# --- 경로 글자 ---

def test_hangul_newline_and_undecodable_paths_stay_one_line_each(tmp_path):
    undecodable = os.fsdecode(b"/\xed\xa0\x80-\xff\xfe.txt")  # UTF-8이 아닌 파일 이름
    paths = ["/진료 기록/2026년 상담.txt", "/줄\n바꿈\r.txt", undecodable, "/구분 자 \x85.txt",
             "/역슬래시\\" + "\udcff.txt"]
    log_path = tmp_path / "audit.jsonl"
    with AuditLog(log_path) as log:
        for path in paths:
            log.record(event(path=path, peer=Peer(1, 501, os.fsdecode(b"/opt/\xff/bin/agent"))), now=NOW)

    raw = log_path.read_bytes()
    raw.decode("utf-8")  # 올바른 UTF-8이다
    assert raw.count(b"\n") == len(paths)
    assert len(raw.decode("utf-8").splitlines()) == len(paths)
    assert "진료 기록".encode() in raw  # ensure_ascii=False
    records = read_records(log_path)
    assert [record["path"] for record in records] == paths
    assert os.fsencode(records[2]["path"]) == b"/\xed\xa0\x80-\xff\xfe.txt"
    assert records[0]["process"] == "agent"


# --- 동시 기록 ---

def test_concurrent_records_do_not_interleave(tmp_path):
    threads_count, per_thread = 8, 200
    log_path = tmp_path / "audit.jsonl"
    filler = "가" * 2000  # 줄을 길게 해 섞이기 쉽게 한다
    with AuditLog(log_path) as log:
        def work(worker: int) -> None:
            for i in range(per_thread):
                log.record(event(path=f"/{worker}/{i}/{filler}", errno_name="ENOENT"))

        threads = [threading.Thread(target=work, args=(worker,)) for worker in range(threads_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    lines = log_path.read_bytes().split(b"\n")
    assert lines[-1] == b""
    assert len(lines) - 1 == threads_count * per_thread
    seen = {tuple(json.loads(line)["path"].split("/")[1:3]) for line in lines[:-1]}
    assert seen == {(str(w), str(i)) for w in range(threads_count) for i in range(per_thread)}


# --- 파일 ---

def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_new_file_is_0600_and_new_parents_are_0700(tmp_path):
    log_path = tmp_path / "새폴더" / "하위" / "audit.jsonl"
    with AuditLog(log_path) as log:
        log.record(event())
    assert mode(log_path) == 0o600
    assert mode(tmp_path / "새폴더") == 0o700
    assert mode(tmp_path / "새폴더" / "하위") == 0o700


def test_existing_file_and_folder_permissions_are_kept(tmp_path):
    folder = tmp_path / "logs"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    log_path = folder / "audit.jsonl"
    log_path.write_text("")
    os.chmod(log_path, 0o640)
    with AuditLog(log_path) as log:
        log.record(event())
    assert (mode(folder), mode(log_path)) == (0o755, 0o640)


def test_symlinked_log_path_is_refused(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("원래 내용\n", encoding="utf-8")
    link = tmp_path / "audit.jsonl"
    link.symlink_to(victim)
    with pytest.raises(OSError):
        AuditLog(link)
    assert victim.read_text(encoding="utf-8") == "원래 내용\n"

    dangling = tmp_path / "dangling.jsonl"
    dangling.symlink_to(tmp_path / "created-by-link.jsonl")
    with pytest.raises(OSError):
        AuditLog(dangling)
    assert not (tmp_path / "created-by-link.jsonl").exists()


def test_reopening_appends(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    with AuditLog(log_path) as log:
        log.record(event(path="/첫째.txt"), now=NOW)
    with AuditLog(log_path) as log:
        log.record(event(path="/둘째.txt"), now=NOW)
    assert [record["path"] for record in read_records(log_path)] == ["/첫째.txt", "/둘째.txt"]


def test_record_after_close_raises_value_error(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    log.close()
    log.close()  # 두 번 닫아도 된다
    assert log.closed
    with pytest.raises(ValueError):
        log.record(event())
    with AuditLog(tmp_path / "other.jsonl") as other:
        assert not other.closed
    assert other.closed
    with pytest.raises(ValueError):
        other.record(event())


def test_write_failure_is_raised(tmp_path, monkeypatch):
    def full_disk(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    with AuditLog(tmp_path / "audit.jsonl") as log:
        monkeypatch.setattr(os, "write", full_disk)
        with pytest.raises(OSError) as info:
            log.record(event())
        monkeypatch.undo()
    assert info.value.errno == errno.ENOSPC


def test_short_write_is_completed(tmp_path, monkeypatch):
    real_write = os.write

    def one_byte_at_a_time(fd, data):
        return real_write(fd, bytes(data[:1]))

    log_path = tmp_path / "audit.jsonl"
    with AuditLog(log_path) as log:
        monkeypatch.setattr(os, "write", one_byte_at_a_time)
        log.record(event(), now=NOW)
        monkeypatch.undo()
    assert read_records(log_path) == [base()]


# --- 시각 ---

def test_ts_uses_given_now():
    assert to_record(event(), NOW, KEY)["ts"] == TS
    utc = datetime(2026, 10, 31, 5, 3, 5, 7000, tzinfo=timezone.utc)
    assert to_record(event(), utc, KEY)["ts"] == "2026-10-31T05:03:05.007+00:00"


def test_ts_defaults_to_local_aware_now(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    before = datetime.now().astimezone()
    with AuditLog(log_path) as log:
        log.record(event())
    after = datetime.now().astimezone()
    ts = read_records(log_path)[0]["ts"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}", ts)
    parsed = datetime.fromisoformat(ts)
    assert parsed.utcoffset() == before.utcoffset()
    assert before - timedelta(milliseconds=1) <= parsed <= after


def test_naive_now_is_refused():
    with pytest.raises(ValueError):
        to_record(event(), datetime(2026, 10, 31, 14, 3, 5), KEY)


# --- 읽기 ---

@pytest.mark.parametrize("broken", [b"{not json\n", b"\n", b"[1, 2]\n", b'{"path": "\xff"}\n'])
def test_read_records_reports_broken_lines(tmp_path, broken):
    log_path = tmp_path / "audit.jsonl"
    with AuditLog(log_path) as log:
        log.record(event(), now=NOW)
    with open(log_path, "ab") as file:
        file.write(broken)
    with pytest.raises(ValueError, match="2번째 줄"):
        read_records(log_path)


def test_read_records_of_empty_file(tmp_path):
    (tmp_path / "audit.jsonl").write_bytes(b"")
    assert read_records(tmp_path / "audit.jsonl") == []


# --- 파일 해시: HMAC (2026-10-31 사용자 결정) ---

def test_content_hmac_is_keyed_sha256():
    assert content_hmac(KEY, SHA) == SHA_HMAC != SHA
    assert content_hmac_of(KEY, b"abc") == content_hmac(KEY, hashlib.sha256(b"abc").hexdigest())
    assert content_hmac(bytes(KEY_BYTES), SHA) != SHA_HMAC  # 키가 다르면 값이 다르다
    record = to_record(event(judgment=Judgment("allow", "허용", "jev", content_sha256=SHA)), NOW, KEY)
    assert record["content_hmac"] == SHA_HMAC
    assert to_record(event(judgment=Judgment("deny", "차단", "policy")), NOW, KEY)["content_hmac"] is None


def test_key_file_is_created_once_and_reused(tmp_path):
    log_path = tmp_path / "logs" / "audit.jsonl"
    judgment = Judgment("allow", "허용", "jev", content_sha256=hashlib.sha256(b"same").hexdigest())
    with AuditLog(log_path) as log:
        log.record(event(judgment=judgment), now=NOW)
    key_path = tmp_path / "logs" / "audit.key"
    key = key_path.read_bytes()
    assert len(key) == KEY_BYTES and stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    with AuditLog(log_path) as log:
        log.record(event(judgment=judgment), now=NOW)
    first, second = read_records(log_path)
    assert first["content_hmac"] == second["content_hmac"] == content_hmac_of(key, b"same")
    assert key_path.read_bytes() == key

    with AuditLog(tmp_path / "other" / "audit.jsonl") as log:  # 다른 설치(다른 키)
        log.record(event(judgment=judgment), now=NOW)
    assert read_records(tmp_path / "other" / "audit.jsonl")[0]["content_hmac"] != first["content_hmac"]


def test_explicit_key_path(tmp_path):
    key_path = tmp_path / "keys" / "my.key"
    with AuditLog(tmp_path / "audit.jsonl", key_path=key_path) as log:
        assert log.key_path == key_path
    assert len(key_path.read_bytes()) == KEY_BYTES
    assert not (tmp_path / "audit.key").exists()
    assert stat.S_IMODE(os.stat(tmp_path / "keys").st_mode) == 0o700


def test_bad_key_files_are_refused(tmp_path):
    short = tmp_path / "short.key"
    short.write_bytes(b"x" * (KEY_BYTES - 1))
    with pytest.raises(ValueError):
        load_or_create_key(short)
    long = tmp_path / "long.key"
    long.write_bytes(b"x" * (KEY_BYTES + 1))
    with pytest.raises(ValueError):
        AuditLog(tmp_path / "audit.jsonl", key_path=long)
    target = tmp_path / "target.key"
    target.write_bytes(b"k" * KEY_BYTES)
    link = tmp_path / "link.key"
    link.symlink_to(target)
    with pytest.raises(OSError):
        load_or_create_key(link)
