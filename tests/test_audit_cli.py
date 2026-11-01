"""gate/audit_cli.py: 감사 로그 보기(gate audit)."""

import argparse
import hashlib
import json
import os
import unicodedata
from datetime import datetime, timedelta, timezone

import pytest

from gate import cli
from gate import audit_cli as a
from gate.audit import KEY_BYTES, KEY_FILE_NAME, AuditLog, content_hmac_of, load_key, load_or_create_key
from gate.daemon import RequestEvent
from gate.peer import Peer
from gate.pipeline import Judgment

PEER = Peer(pid=4321, uid=501, exe="/usr/local/bin/python3.12")
KST = timezone(timedelta(hours=9))
START = datetime(2026, 10, 31, 9, 0, 0, tzinfo=KST)
KEY = bytes(range(KEY_BYTES))
RESUME = "이름 홍길동\n연락처 010-1234-5678\n".encode()
NOTE = "다음 진료는 화요일 오후입니다.\n".encode()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# 시각 순서대로: (분, op, path, errno, judgment)
EVENTS = [
    (0, "ls", "/", None, None),
    (1, "cat", "/이력서/resume.txt", None,
     Judgment("mask", "번호를 가렸습니다", "jev", policy_version="pv", content_sha256=sha(RESUME),
              findings={"mobile": 1}, jev_label="safe", jev_confidence=0.93, jev_calls=1)),
    (2, "cat", "/설정/.env", "EACCES",
     Judgment("deny", "경로 규칙으로 거부합니다 (*.env)", "path", policy_version="pv")),
    (3, "cat", "/진료/메모.txt", None,
     Judgment("allow", "사용자가 승인했습니다", "human", policy_version="pv", content_sha256=sha(NOTE),
              jev_label="sensitive", jev_confidence=0.71, jev_calls=1, approval="session", approved_by="dialog")),
    (4, "cat", "/진료/메모.txt", "EACCES",
     Judgment("deny", "2초 안에 승인이 없어 차단했습니다", "human", policy_version="pv", content_sha256=sha(NOTE),
              jev_label="sensitive", jev_confidence=0.71, jev_calls=0, cached=True, approval="timeout")),
    (5, "cat", "/../밖.txt", "EACCES", None),
    (6, "cat", "/없는파일.txt", "ENOENT", None),
    (7, "cat", "/진료/메모.txt", None,
     Judgment("allow", "허용했습니다", "human", policy_version="pv", content_sha256=sha(NOTE), jev_label="sensitive",
              jev_confidence=0.71, cached=True, approval="remembered")),
]


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "audit" / "audit.jsonl"
    with AuditLog(path) as audit:
        for minute, op, vpath, errno_name, judgment in EVENTS:
            audit.record(RequestEvent(PEER, op, vpath, errno_name, 1.0, judgment), now=START + timedelta(minutes=minute))
    return path


def run(capsys, *args) -> tuple[int, str, str]:
    code = cli.main(["audit", *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def table(out: str) -> list[str]:
    """요약 줄을 뺀 기록 줄."""
    return out.splitlines()[:-1]


def path_of(line: str) -> str:
    return line.split("  — ")[0].rsplit("  ", 1)[1]


def decision_of(line: str) -> str:
    return line[len("2026-10-31 09:00:00  cat     "):].split("  ")[0]


# --- 표 ---

def test_table_shows_every_record_in_file_order_with_korean_labels(log, capsys):
    code, out, err = run(capsys, "--audit", str(log))
    assert (code, err) == (0, "")
    lines = table(out)
    assert len(lines) == len(EVENTS)
    when = (START + timedelta(minutes=1)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert lines[1].startswith(f"{when}  cat     가림       /이력서/resume.txt  — ")
    assert "가림: 휴대폰 번호 1건" in lines[1] and "판단 모델 safe 0.93" in lines[1] and "python3.12 pid 4321" in lines[1]
    assert "차단" in lines[2] and "경로 규칙으로 거부합니다 (*.env)" in lines[2]
    assert "허용" in lines[3] and "승인: 이 폴더 허용(대화상자)" in lines[3]
    assert "승인: 무응답(시간 초과)" in lines[4] and "판단 모델 sensitive 0.71 (캐시)" in lines[4]
    assert "오류" in lines[5] and "접근 거부 (EACCES)" in lines[5]
    assert "없는 경로 (ENOENT)" in lines[6]
    assert "승인: 폴더 승인 기억" in lines[7] and "(대화상자)" not in lines[7]
    assert out.splitlines()[-1] == "기록 8건 중 조건에 맞는 8건 · 허용 2, 가림 1, 차단 2, 성공 1, 오류 2"


def test_decision_column_lines_up_for_korean_labels(log, capsys):
    _, out, _ = run(capsys, "--audit", str(log))
    starts = {a._width(line[:line.index("/")]) for line in table(out)}
    assert len(starts) == 1


def test_last_keeps_the_most_recent_and_zero_shows_all(log, capsys):
    _, out, _ = run(capsys, "--audit", str(log), "-n", "2")
    assert [path_of(line) for line in table(out)] == ["/없는파일.txt", "/진료/메모.txt"]
    assert "기록 8건 중 조건에 맞는 8건, 그중 최근 2건을 보여 줍니다 (-n 0이면 전부)" in out
    _, out, _ = run(capsys, "--audit", str(log), "-n", "0")
    assert len(table(out)) == len(EVENTS)


def test_default_shows_the_last_twenty(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    with AuditLog(path) as audit:
        for i in range(25):
            audit.record(RequestEvent(PEER, "ls", f"/{i}", None, 1.0), now=START + timedelta(seconds=i))
    _, out, _ = run(capsys, "--audit", str(path))
    lines = table(out)
    assert len(lines) == a.DEFAULT_LAST and lines[0].endswith("/5  — python3.12 pid 4321")


def test_negative_last_is_refused(log, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["audit", "--audit", str(log), "-n", "-1"])
    assert exc.value.code == 2


# --- 거르기 ---

def test_blocked_is_deny_ask_human_and_eacces_without_judgment(log, capsys):
    _, out, _ = run(capsys, "--audit", str(log), "--blocked")
    lines = table(out)
    assert len(lines) == 3
    assert "/설정/.env" in lines[0] and "시간 초과" in lines[1] and "/../밖.txt" in lines[2]
    assert out.splitlines()[-1] == "기록 8건 중 조건에 맞는 3건 · 차단 2, 오류 1"


def test_ask_human_record_counts_as_blocked():
    assert a.is_blocked({"decision": "ask_human", "errno": "EACCES"})
    assert not a.is_blocked({"decision": "error", "errno": "ENOENT"})
    assert not a.is_blocked({"decision": "mask", "errno": None})


def test_decision_can_be_repeated(log, capsys):
    _, out, _ = run(capsys, "--audit", str(log), "--decision", "allow", "--decision", "mask")
    assert [decision_of(line) for line in table(out)] == ["가림", "허용", "허용"]


def test_unknown_decision_is_refused(log, capsys):
    with pytest.raises(SystemExit):
        cli.main(["audit", "--audit", str(log), "--decision", "masked"])


def test_path_matches_part_ignoring_case_and_unicode_normalization(log, capsys):
    nfd = unicodedata.normalize("NFD", "진료")
    _, out, _ = run(capsys, "--audit", str(log), "--path", nfd)
    assert len(table(out)) == 3
    _, out, _ = run(capsys, "--audit", str(log), "--path", "RESUME")
    assert len(table(out)) == 1 and "/이력서/resume.txt" in out


def test_filters_combine(log, capsys):
    _, out, _ = run(capsys, "--audit", str(log), "--path", "진료", "--blocked")
    assert len(table(out)) == 1 and "시간 초과" in out


def test_since_relative_and_absolute():
    now = datetime(2026, 10, 31, 12, 0, tzinfo=KST)
    assert a.parse_since("30m", now=now) == now - timedelta(minutes=30)
    assert a.parse_since("2h", now=now) == now - timedelta(hours=2)
    assert a.parse_since("1d", now=now) == now - timedelta(days=1)
    assert a.parse_since("2026-10-31T09:03+09:00") == START + timedelta(minutes=3)
    local = a.parse_since("2026-10-31")
    assert local.utcoffset() is not None and (local.hour, local.minute) == (0, 0)
    with pytest.raises(argparse.ArgumentTypeError, match="알 수 없는 시각"):
        a.parse_since("어제")


def test_since_keeps_records_at_or_after_the_moment(log, capsys):
    _, out, _ = run(capsys, "--audit", str(log), "--since", "2026-10-31T09:06+09:00")
    assert len(table(out)) == 2


def test_since_skips_records_without_a_valid_time(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(json.dumps({"ts": "언젠가", "decision": "ok"}) + "\n" + json.dumps({"decision": "ok"}) + "\n",
                    encoding="utf-8")
    assert a.select(path, since=START).entries == []
    assert len(a.select(path).entries) == 2


def test_file_selects_records_of_the_same_content(log, tmp_path, capsys):
    copy = tmp_path / "다른 이름.txt"
    copy.write_bytes(NOTE)  # 이름이 달라도 내용이 같으면 같은 기록
    _, out, _ = run(capsys, "--audit", str(log), "--file", str(copy))
    assert len(table(out)) == 3 and all("/진료/메모.txt" in line for line in table(out))
    copy.write_bytes(NOTE + b" ")
    _, out, _ = run(capsys, "--audit", str(log), "--file", str(copy))
    assert table(out) == [] and out.strip() == "기록 8건 중 조건에 맞는 기록이 없습니다."


def test_file_hmac_matches_the_log(log, tmp_path):
    key = load_key(log.parent / KEY_FILE_NAME)
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(3 * a._HASH_CHUNK + 7))  # 나눠 읽어도 같은 값
    assert a.file_hmac(big, log.parent / KEY_FILE_NAME) == content_hmac_of(key, big.read_bytes())


def test_file_without_key_fails_and_does_not_create_one(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    path.write_text("", encoding="utf-8")
    target = tmp_path / "x.txt"
    target.write_bytes(NOTE)
    code, out, err = run(capsys, "--audit", str(path), "--file", str(target))
    assert code == 1 and out == "" and "--file을 비교할 수 없습니다" in err
    assert not (tmp_path / KEY_FILE_NAME).exists()


def test_missing_file_to_compare_is_an_error(log, tmp_path, capsys):
    code, _, err = run(capsys, "--audit", str(log), "--file", str(tmp_path / "없음.txt"))
    assert code == 1 and "--file을 비교할 수 없습니다" in err


# --- JSON, 깨진 줄, 오류 ---

def test_json_prints_the_selected_lines_unchanged(log, capsys):
    raw = log.read_text(encoding="utf-8").splitlines()
    code, out, _ = run(capsys, "--audit", str(log), "--json", "--blocked")
    assert code == 0 and out.splitlines() == [raw[2], raw[4], raw[5]]
    assert all(json.loads(line)["decision"] in ("deny", "error") for line in out.splitlines())


def test_broken_lines_are_skipped_and_reported(log, capsys):
    with open(log, "ab") as file:
        file.write(b"{\"decision\": \"ok\"\n")   # 9: JSON이 아님
        file.write(b"[1, 2]\n")                  # 10: 객체가 아님
        file.write(b"\xff\xfe\n")                # 11: UTF-8이 아님
        file.write(b"\n")                        # 12: 빈 줄
    code, out, err = run(capsys, "--audit", str(log))
    assert code == 0 and len(table(out)) == len(EVENTS)
    assert "깨진 줄 4개를 건너뛰었습니다 (줄 9, 10, 11, 12)" in err
    assert "기록 8건 중" in out


def test_many_broken_lines_are_shortened(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    path.write_bytes(b"x\n" * 7)
    _, _, err = run(capsys, "--audit", str(path))
    assert "깨진 줄 7개를 건너뛰었습니다 (줄 1, 2, 3, 4, 5 …)" in err


def test_control_characters_are_cleaned_in_the_table_but_kept_in_json(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    evil = "/a\x1b[2J\nb.txt"
    with AuditLog(path) as audit:
        audit.record(RequestEvent(Peer(1, 501, "/bin/\x1b]0;x\x07sh"), "cat", evil, "ENOENT", 1.0), now=START)
    _, out, _ = run(capsys, "--audit", str(path))
    assert "\x1b" not in out and "\x07" not in out and "/a [2J b.txt" in out
    _, out, _ = run(capsys, "--audit", str(path), "--json")
    assert json.loads(out)["path"] == evil


def test_odd_values_do_not_crash_the_table(tmp_path, capsys):
    path = tmp_path / "audit.jsonl"
    odd = [{}, {"decision": "새 결정", "op": 3, "path": None, "pid": "x", "findings": [1], "jev_label": 5,
                "approval": "새 승인", "approved_by": "누군가", "ts": "2026-10-31T09:00:00"}]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in odd), encoding="utf-8")
    code, out, _ = run(capsys, "--audit", str(path))
    lines = table(out)
    assert code == 0 and len(lines) == 2
    assert lines[0].startswith("시각 없음") and "  ?  " in lines[0]
    assert "새 결정" in lines[1] and "승인: 새 승인" in lines[1]
    assert "새 결정 1" in out.splitlines()[-1]


def test_missing_log_and_missing_option(tmp_path, capsys, monkeypatch):
    code, out, err = run(capsys, "--audit", str(tmp_path / "없음.jsonl"))
    assert code == 1 and out == "" and "감사 로그가 없습니다" in err
    assert not (tmp_path / "없음.jsonl").exists()
    monkeypatch.delenv("GATE_AUDIT", raising=False)
    code, _, err = run(capsys)
    assert code == 1 and "GATE_AUDIT" in err


def test_audit_path_comes_from_the_environment(log, capsys, monkeypatch):
    monkeypatch.setenv("GATE_AUDIT", str(log))
    code, out, _ = run(capsys, "-n", "1")
    assert code == 0 and len(table(out)) == 1


def test_viewing_changes_nothing(log, tmp_path, capsys):
    target = tmp_path / "x.txt"
    target.write_bytes(NOTE)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_mode) for p in log.parent.iterdir()}
    run(capsys, "--audit", str(log), "--file", str(target), "--blocked", "--since", "1d")
    run(capsys, "--audit", str(log), "--json")
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_mode) for p in log.parent.iterdir()}
    assert before == after


# --- 키 읽기 (gate.audit.load_key) ---

def test_load_key_reads_only(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_key(tmp_path / KEY_FILE_NAME)
    assert not (tmp_path / KEY_FILE_NAME).exists()
    created = load_or_create_key(tmp_path / KEY_FILE_NAME)
    assert load_key(tmp_path / KEY_FILE_NAME) == created == load_or_create_key(tmp_path / KEY_FILE_NAME)


def test_load_key_refuses_links_and_wrong_length(tmp_path):
    real = tmp_path / "real.key"
    real.write_bytes(KEY)
    link = tmp_path / "link.key"
    link.symlink_to(real)
    with pytest.raises(OSError):
        load_key(link)
    real.write_bytes(KEY[:-1])
    with pytest.raises(ValueError, match="32바이트"):
        load_key(real)
