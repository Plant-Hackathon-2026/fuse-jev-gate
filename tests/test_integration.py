"""Phase 4 완료 조건: 샘플 파일을 데몬과 gate CLI로 읽어 허용·마스킹·차단이 정책대로 나오는지 본다. Jev는 Mock이다.

- 정책 파일이 없으면 기본값, 있으면 지정값으로 동작한다. 실행 중인 데몬이 정책 변경을 바로 반영한다.
- 같은 파일을 다시 읽으면 Jev를 호출하지 않는다(gate cat은 부를 때마다 새 프로세스여도).
- 데몬 결과와 평가 스크립트(eval.run_eval.gate_outcome)의 결과가 샘플 전체에서 같다.
- 이 파일은 정규식·Jev·정책을 본다. NER은 명시적으로 끈다(기본은 켜짐, tests/test_ner_pipeline.py가 따로 본다).
"""

import errno
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from eval.run_eval import DEFAULT_PURPOSE, evaluate, gate_outcome, load_labels
from gate.client import GateClient
from gate.daemon import GateServer
from gate.detectors import mask
from gate.jev_client import MockJevClient
from gate.pipeline import Inspector
from gate.policy import Entry, PathRule, PolicySource, write_entries
from gate.protocol import GateError
from gate.store import PlainStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def socket_dir():
    path = Path(tempfile.mkdtemp(prefix="gate-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _serve(server: GateServer):
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()


def _gate(socket_path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "gate.cli", "--socket", str(socket_path), *args],
                          cwd=ROOT, capture_output=True, timeout=20)


# --- (A) 키워드 Mock, 기본 정책: 데몬 결과 = 평가 스크립트 결과 ---

def test_daemon_matches_eval_on_every_sample(tmp_path, socket_dir):
    samples = load_labels()
    source = tmp_path / "source"
    for s in samples:
        target = source / s.path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / s.path, target)

    expected = {r.sample.path: gate_outcome(r) for r in evaluate(samples, MockJevClient(), masker=mask)}
    store = PlainStore(source)  # 기본 정책 + 기본 Jev 백엔드. 평가 스크립트(evaluate)는 NER 없이 내므로 NER은 끈다
    server = GateServer(socket_dir / "gate.sock", store, inspector=Inspector(store, ner_factory=lambda p: None))
    _serve(server)
    try:
        client = GateClient(server.socket_path)
        actual = {}
        for s in samples:
            try:
                actual[s.path] = client.read("/" + s.path, purpose=DEFAULT_PURPOSE).decision
            except GateError as exc:
                assert exc.errno == errno.EACCES
                actual[s.path] = "ask_human" if "사람 승인" in exc.message else "deny"
    finally:
        server.shutdown()
        server.server_close()
    assert actual == expected
    assert set(actual.values()) >= {"allow", "ask_human", "deny"}


# --- (B) 고정 답 Mock + gate CLI: 정책을 바꾸면 결과가 바뀐다 ---

@pytest.fixture
def demo(tmp_path, socket_dir):
    source = tmp_path / "source"
    for vpath, sample in (("이력서/박을순.txt", "resume/pii_1.txt"), ("진료/메모.txt", "medical_note/quasi_1.txt"),
                          ("상담/요금제.txt", "consult_chat/none_1.txt")):
        (source / vpath).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "samples" / sample, source / vpath)
    (source / "설정").mkdir()
    (source / "설정" / ".env").write_text("TOKEN=not-a-real-token\n", encoding="utf-8")

    # 가린 뒤의 이력서는 safe라고 답하게 해서 마스킹 결과를 본다. 진료 메모만 sensitive.
    jev = MockJevClient("fixed", label="safe", confidence=0.99, by_path={"/진료/메모.txt": ("sensitive", 0.97)})
    policy_path = tmp_path / "gate-data" / "policy.yaml"
    store = PlainStore(source)
    server = GateServer(socket_dir / "gate.sock", store,
                        inspector=Inspector(store, PolicySource(policy_path), jev_factory=lambda policy: jev,
                                            ner_factory=lambda policy: None))
    _serve(server)
    yield server.socket_path, source, jev, policy_path
    server.shutdown()
    server.server_close()


def test_defaults_without_policy_file(demo):
    sock, source, jev, policy_path = demo
    assert not policy_path.exists()

    plain = _gate(sock, "cat", "/상담/요금제.txt", "--purpose", "요약")
    assert plain.returncode == 0
    assert plain.stdout == (source / "상담" / "요금제.txt").read_bytes()

    resume = _gate(sock, "cat", "/이력서/박을순.txt", "--purpose", "요약")
    original = (source / "이력서" / "박을순.txt").read_bytes()
    assert resume.returncode == 0
    assert len(resume.stdout) == len(original)  # 같은 길이
    text = resume.stdout.decode()
    for secret in ("900101", "2345678", "010", "1234-5678", "eulsoon", ".com"):
        assert secret not in text
    assert "******-*******" in text and "***-****-****" in text
    assert "가렸습니다" in resume.stderr.decode()

    for path in ("/진료/메모.txt", "/설정/.env"):
        denied = _gate(sock, "cat", path)
        assert denied.returncode == 1 and denied.stdout == b""
        assert "EACCES" in denied.stderr.decode()
    assert "판단 모델 결과(sensitive" in _gate(sock, "cat", "/진료/메모.txt").stderr.decode()


def test_user_policy_changes_results_without_restart(demo):
    sock, source, jev, policy_path = demo
    write_entries(policy_path, [
        Entry(settings={"numbers.rrn.visible": "birth", "numbers.mobile.visible": "prefix"},
              said="주민번호는 생년월일까지, 휴대폰은 앞자리만 보여줘"),
        Entry(path_rule=PathRule("진료/**", "allow")),
    ])
    text = _gate(sock, "cat", "/이력서/박을순.txt").stdout.decode()
    assert "900101-*******" in text and "010-****-****" in text
    assert _gate(sock, "cat", "/진료/메모.txt").returncode == 0  # 경로 규칙 allow는 Jev를 거치지 않는다

    write_entries(policy_path, [Entry(settings={"numbers.email.action": "deny"}, said="이메일 있는 파일은 거부")])
    denied = _gate(sock, "cat", "/이력서/박을순.txt")
    assert denied.returncode == 1 and "이메일" in denied.stderr.decode()
    assert _gate(sock, "cat", "/진료/메모.txt").returncode == 1  # allow 규칙이 빠졌다

    write_entries(policy_path, [Entry(context="병원 기록은 보여주지 마")])
    _gate(sock, "cat", "/상담/요금제.txt")
    assert "병원 기록은 보여주지 마" in jev.calls[-1].question


def test_rereading_with_cli_does_not_call_jev(demo):
    sock, _, jev, _ = demo
    for _ in range(3):
        assert _gate(sock, "cat", "/상담/요금제.txt", "--purpose", "요약").returncode == 0
    assert len([c for c in jev.calls if c.context.path == "/상담/요금제.txt"]) == 1
    _gate(sock, "cat", "/상담/요금제.txt", "--purpose", "다른 목적")
    assert len([c for c in jev.calls if c.context.path == "/상담/요금제.txt"]) == 2


def test_cli_search(demo):
    sock, _, _, _ = demo
    found = _gate(sock, "search", "휴대폰")
    assert found.returncode == 0
    assert found.stdout.decode().splitlines() == ["/이력서/박을순.txt:5:휴대폰: ***-****-****"]
    assert "건너뜀 /진료/메모.txt" in found.stderr.decode()
    assert "건너뜀 /설정/.env" in found.stderr.decode()
    assert _gate(sock, "search", "1234").returncode == 1  # 가린 값은 찾지 못한다
    assert _gate(sock, "search", "무릎").returncode == 1   # 차단된 진료 메모 내용은 찾지 못한다


# --- gate policy 연결 (gate/cli.py) ---

def test_cli_policy_is_wired_and_needs_no_socket(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    env_free = [sys.executable, "-m", "gate.cli", "policy", "--policy", str(policy_path)]
    shown = subprocess.run([*env_free, "show"], cwd=ROOT, capture_output=True, timeout=20, stdin=subprocess.DEVNULL)
    assert shown.returncode == 0, shown.stderr.decode()
    assert "전부 기본값" in shown.stdout.decode()
    # 에이전트의 Bash처럼 터미널이 없으면 바꾸는 명령은 받지 않는다 (보안 경계는 아니다: Phase 6에서 파일 쓰기를 막는다)
    changed = subprocess.run([*env_free, "set", "numbers.card.action", "deny"], cwd=ROOT, capture_output=True,
                             timeout=20, input=b"y\n")
    assert changed.returncode == 1
    assert "터미널" in changed.stdout.decode()
    assert not policy_path.exists()


# --- Phase 4 보완: 설정 파일의 비밀값 (gate cat으로) ---

def test_cli_masks_config_secrets_and_policy_can_deny(demo):
    sock, source, _, policy_path = demo
    config = (
        "service: billing\n"
        "aws_access_key_id: AKIAEXAMPLEEXAMPLE00\n"
        "openai_api_key: sk-proj-abcdefEXAMPLEghijklmnopqrstuv\n"
        'db_password: "correct-horse-battery-staple"\n'
    )
    (source / "설정" / "deploy.yaml").write_text(config, encoding="utf-8")
    shown = _gate(sock, "cat", "/설정/deploy.yaml")
    assert shown.returncode == 0
    assert shown.stdout.decode().splitlines() == [
        "service: billing",
        "aws_access_key_id: ********************",
        "openai_api_key: **-****-" + "*" * 29,
        'db_password: "*******-*****-*******-******"',
    ]
    assert "비밀값(비밀번호·키·토큰) 3건" in shown.stderr.decode()

    write_entries(policy_path, [Entry(settings={"numbers.secret.action": "deny"}, said="비밀번호가 있는 파일은 거부해줘")])
    denied = _gate(sock, "cat", "/설정/deploy.yaml")
    assert denied.returncode == 1 and denied.stdout == b""
    assert "비밀값" in denied.stderr.decode()


# --- Phase 5 완료 조건: 승인·거부·무응답 (실제 gate serve + gate cat 프로세스) + 감사 로그 ---

import os  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import time  # noqa: E402

from gate.audit import content_hmac_of, read_records  # noqa: E402
from gate.client import ApprovalClient  # noqa: E402


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_human_approval_end_to_end(tmp_path, socket_dir):
    source = tmp_path / "source"
    (source / "진료").mkdir(parents=True)
    note = "다음 진료는 화요일 오후입니다.\n"
    (source / "진료" / "메모.txt").write_text(note, encoding="utf-8")
    policy_path, audit_path = tmp_path / "policy.yaml", tmp_path / "audit" / "audit.jsonl"
    write_entries(policy_path, [Entry(path_rule=PathRule("진료/**", "ask_human")),
                                Entry(settings={"approval.timeout_seconds": 2.0, "ner_backend": "none"})])
    sock = socket_dir / "run" / "gate.sock"
    daemon = subprocess.Popen(
        [sys.executable, "-m", "gate.cli", "--socket", str(sock), "serve", "--source", str(source),
         "--policy", str(policy_path), "--no-dialog", "--audit", str(audit_path)],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    approver = ApprovalClient(socket_dir / "run" / "approve.sock")
    try:
        assert _wait(lambda: (socket_dir / "run" / "approve.sock").exists()), "데몬이 승인 소켓을 열지 않았습니다"

        def cat():
            return subprocess.Popen([sys.executable, "-m", "gate.cli", "--socket", str(sock), "cat", "/진료/메모.txt",
                                     "--purpose", "요약"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        def answer_next(answer):
            assert _wait(lambda: approver.pending())
            [p] = approver.pending()
            listed = _gate(sock, "approve")  # 목록은 터미널이 아니어도 된다
            assert listed.returncode == 0 and f"[{p['id']}] /진료/메모.txt" in listed.stdout.decode()
            refused = _gate(sock, "deny", p["id"])  # 답은 터미널에서만 (에이전트의 Bash처럼 터미널이 없음)
            assert refused.returncode == 1 and "사용자 터미널에서만" in refused.stderr.decode()
            approver.answer(p["id"], answer)

        approved = cat()
        answer_next("once")
        out, err = approved.communicate(timeout=10)
        assert (approved.returncode, out) == (0, note.encode())

        denied = cat()
        answer_next("deny")
        out, err = denied.communicate(timeout=10)
        assert denied.returncode == 1 and out == b"" and "사용자가 거부했습니다 (EACCES)" in err.decode()

        started = time.monotonic()
        silent = cat()
        out, err = silent.communicate(timeout=10)
        assert silent.returncode == 1 and "2초 안에 승인이 없어 차단했습니다" in err.decode()
        assert 1.5 <= time.monotonic() - started < 8
    finally:
        daemon.send_signal(signal.SIGTERM)
        _, log = daemon.communicate(timeout=10)
    log = log.decode()
    assert log.count("승인 대기 [") == 3 and "터미널(pid" in log
    assert not sock.exists() and not (socket_dir / "run" / "approve.sock").exists()

    records = [r for r in read_records(audit_path) if r["op"] == "cat"]
    assert [(r["decision"], r["stage"], r["approval"], r["approved_by"]) for r in records] == [
        ("allow", "human", "once", "terminal"), ("deny", "human", "deny", "terminal"),
        ("deny", "human", "timeout", None)]
    assert {r["path"] for r in records} == {"/진료/메모.txt"}
    assert stat_mode(audit_path) == 0o600
    key = (audit_path.parent / "audit.key").read_bytes()  # 파일 해시는 설치마다 만든 키의 HMAC
    assert {r["content_hmac"] for r in records} == {content_hmac_of(key, note.encode())}
    raw = audit_path.read_text(encoding="utf-8")
    assert "화요일" not in raw and "요약" not in raw  # 원문과 목적은 남기지 않는다

    # 사용자 터미널의 gate audit: 막힌 요청 중 이 파일과 내용이 같은 것
    shown = _gate(sock, "audit", "--audit", str(audit_path), "--blocked", "--file", str(source / "진료" / "메모.txt"))
    lines = shown.stdout.decode().splitlines()
    assert shown.returncode == 0 and len(lines) == 3
    assert "승인: 거부(터미널)" in lines[0] and "승인: 무응답(시간 초과)" in lines[1]
    assert lines[2].endswith("조건에 맞는 2건 · 차단 2")
    assert audit_path.read_text(encoding="utf-8") == raw  # 보기만 한다


def stat_mode(path) -> int:
    return os.stat(path).st_mode & 0o777


def test_default_daemon_has_ner_on_and_blocks_without_ner_server(tmp_path, socket_dir):
    """정책 파일 없이 띄운 gate serve는 NER 켜짐(기본)이다. NER 서버가 없으면 모든 내용 읽기가 막히고, 시작할 때 알린다."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "회의.txt").write_text("회의는 화요일입니다.\n", encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    sock = socket_dir / "run" / "gate.sock"
    env = {k: v for k, v in os.environ.items() if k not in ("GATE_POLICY", "GATE_AUDIT", "GATE_APPROVE_SOCKET")}
    env["NER_BASE_URL"] = closed
    daemon = subprocess.Popen(
        [sys.executable, "-m", "gate.cli", "--socket", str(sock), "serve", "--source", str(source), "--no-dialog"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        assert _wait(lambda: sock.exists()), "데몬이 소켓을 열지 않았습니다"
        read = _gate(sock, "cat", "/회의.txt")
        assert read.returncode == 1 and read.stdout == b""
        assert "NER 판단에 실패해 차단했습니다" in read.stderr.decode()
        assert _gate(sock, "ls", "/").returncode == 0  # 목록은 내용을 읽지 않아 그대로 된다
    finally:
        daemon.send_signal(signal.SIGTERM)
        _, log = daemon.communicate(timeout=10)
    log = log.decode()
    assert "gate: NER 켜짐" in log and "gate policy set ner_backend none" in log
