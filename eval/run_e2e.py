"""실제 모델 e2e 시험: 게이트 데몬과 gate CLI를 실제 판단 모델(Laya)·NER 서버에 붙여, 사람이 쓰는 흐름 그대로 돌린다.

    uv run python -m eval.run_e2e [--keep] [--only mask,approve,...] [--wait 300]

- 저장소 테스트(pytest)는 NER·Laya를 가짜 서버로 돌리고, eval.run_eval은 판단 함수만 직접 부른다. 이 스크립트는 데몬(gate serve),
  gate ls·stat·cat·search, 사람 승인(gate approve·deny), 정책 바꾸기(gate policy), 감사 로그(gate audit), 설정 화면(gate ui)을
  실제 laya-serve·nerserve와 한 번에 엮어 본다. 모두 이 컴퓨터 안에서 돌아 API 비용은 없다.
- 준비: laya-serve와 nerserve를 먼저 띄워 둔다(README "판단 모델 연결", "이름·주소 가리기"). 이 스크립트는 서버를 띄우거나 끄지 않는다.
  주소는 JEV_BASE_URL(기본 http://127.0.0.1:8000), JEV_MODEL(기본 gate-laya-v3, laya-serve에 등록한 이름),
  JEV_MODEL_DIR(기본: 저장소 위 폴더들에서 찾은 models/laya-v3), NER_BASE_URL(기본 http://127.0.0.1:8001)이다.
  시작 전에 두 서버의 /health가 200이 될 때까지 --wait초(기본 300) 기다린다(모델을 불러오는 중일 수 있다).
  JEV_MODEL이 laya-serve의 loaded 목록에 없으면 시작하지 않는다.
- 데이터: /tmp/ge2e-XXXX(맥 Unix 소켓 경로는 104바이트를 넘으면 안 되므로 짧은 곳)에 원본 폴더(samples/ 일부 + 직접 만든
  .env·서버 설정 메모·안내문), 정책 파일, 감사 로그, 소켓(gate.sock 옆에 approve.sock)을 만든다. 끝나면(Ctrl-C·SIGTERM 포함)
  데몬·gate ui를 끄고 폴더를 지운다. --keep이면 폴더를 남기고 경로를 알린다. 실제 데모 폴더(gate-data/)와 사용자의
  GATE_* 환경변수는 쓰지 않는다(자식 프로세스 환경에서 GATE_*를 지우고 임시 폴더 경로만 넣는다).
- 데몬: gate serve --no-dialog(맥 대화상자 없이 터미널 승인만). 시작 정책은 jev_backend: laya 한 줄이다(기본은 mock).
  gate policy는 터미널이 아니면 바꾸기를 거부하므로 시작 정책은 gate.policy.write_entries로 쓴다.
- 사람 역할: 사람이 하는 명령(gate policy add·set·undo, gate approve <id>, gate deny <id>)은 의사 터미널(pty)에서 돌리고
  [y/N]마다 정해 둔 답을 넣는다(정하지 않은 질문에는 기본값 N). 제품의 터미널 검사는 그대로 두며, 터미널 없이 부르면
  거부되는 것도 확인한다. 데몬은 요청마다 정책 파일 변경을 감지하므로 다시 띄우지 않는다.
- 시나리오가 정책을 바꾸고 되돌리지 못한 채 끝나면 다음 시나리오 전에 시작 정책으로 되돌리고 근거에 적는다.
- 읽기 결과는 에이전트가 보는 그대로(종료 코드, stdout, stderr 안내 줄)로 판정한다. 기대하지 않은 사람 승인이 뜨면 거부로 답해
  기다리지 않고 실패로 적는다. 감사 로그 대조의 기대값은 시나리오에서 실제로 본 결과(허용·가림·차단, 승인 답)다.
- 샘플은 eval.run_eval --client laya --ner server --details(2026-11-01, Laya v3 + NER 재학습판)에서 결과가 뚜렷한 것만 골랐다.
  Laya는 has_pii 확률 p 하나를 주고 게이트는 p ≤ 0.05 허용, p ≥ 0.95 차단, NER로 가린 뒤 다시 볼 때는 p < 0.5면 가린 채 보여 준다.
  그래서 p가 0.05·0.5·0.95 근처인 샘플은 뺐다. 직접 만든 문서도 같은 방법으로 미리 확인했다. 체크포인트를 바꾸면 다시 고른다.

시나리오 (--only에 쓰는 이름, 이 순서로 돈다)
  ls         gate ls /, gate stat (크기 = 원본)
  allow      개인정보 없는 문서가 원문 그대로 나온다
  mask       이름 등이 [이름]처럼 가려지고 eval/must_hide.csv 값이 남지 않는다. gate cat 안내 줄이 실제 가림 방식(*·종류 이름)과 맞는다
  number     휴대폰·주민번호·이메일이 같은 길이 *로 가려진다
  host       IP·내부 주소가 [IP]·[내부 주소]로 가려진다. gate policy set ner_backend none(pty, y) 뒤에도 가려진다. undo로 되돌린다
  path       .env는 경로 규칙으로 EACCES, 이유에 값이 없다
  approve    백그라운드 gate cat → gate approve 목록 → 터미널 없이 답하면 거부 → gate approve <id>(pty) → 가린 내용
  deny       gate deny <id>(pty) → EACCES
  timeout    gate policy set approval.timeout_seconds 3(pty) → 답하지 않으면 EACCES → undo(pty, 느슨해지므로 y)
  search     gate search가 막힌 파일(.env, 사람 승인이 필요한 파일)을 건너뛰고 통과한 파일에서만 찾는다
  policy     gate policy add "이름은 보여줘"(pty, y·y) → 같은 문서에서 [이름]이 사라진다 → undo → 다시 가려진다
  cache      같은 파일을 두 번 읽으면 두 번째 기록은 cached·Jev 호출 0
  ner-down   NER_BASE_URL을 닫힌 포트로 준 두 번째 데몬(다른 소켓·감사 로그): gate cat은 NER 실패로 EACCES, gate ls·stat은 된다
  mount      원본 폴더 추가(Phase 9): gate workspace init으로 작업 폴더 등록 → 터미널 없이 gate mount add는 거부 →
             gate mount add(pty, y·y) → 작업 폴더 설정에 막기(Read·Edit 거부 + denyRead) → 데몬을 다시 띄우지 않아도 gate ls /에
             /가계부 → 그 안 문서는 가려지고 .env는 경로 규칙으로 EACCES → 감사 기록에 게이트 안 경로만(실제 경로 없음) →
             gate mount remove(pty, y): 안 보이고 읽으면 ENOENT, 막기는 남음 → gate mount unprotect(pty, y·y): 막기가 빠짐
  encoding   UTF-16·EUC-KR·바이너리(2026-11-01): UTF-16(BOM) 설정 파일의 비밀번호·토큰이 *로 가려져 UTF-16 그대로 나온다,
             EUC-KR 상담 문서가 UTF-8과 같이 [이름] 등으로 가려져 EUC-KR 그대로 나온다, 바이너리(PNG)는 판단 없이
             사람 승인(이유에 '바이너리') → gate approve <id>(pty) → 원본 그대로
  ui         gate ui --no-browser --port 0: 토큰 없이 403, 일회용 주소 재사용 403, 토큰으로 상태 API에 게이트·Laya·NER 연결 정상,
             원본 폴더 목록 API 응답
  audit      gate audit --json 기록이 위에서 본 결과(결정·단계·승인·캐시)와 맞고, 감사 로그(0600)·데몬·화면 로그에 원문과
             가릴 값(must_hide.csv, IP·내부 주소, .env 비밀값)이 없다. 앞 시나리오가 없으면 읽기 두 번으로 기록을 만든다

종료 코드: 0 모두 통과, 1 실패 있음, 3 시작 전 확인 실패(서버·모델 폴더·데몬 시작), 130 중단.
"""

import argparse
import json
import os
import re
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from eval.run_eval import DEFAULT_MUST_HIDE, PROJECT_ROOT, _table, load_must_hide
from gate.approval import kinds_text
from gate.ner_client import MASK_TEXT
from gate.policy import Entry, write_entries

DEFAULT_JEV_URL = "http://127.0.0.1:8000"
DEFAULT_NER_URL = "http://127.0.0.1:8001"
DEFAULT_JEV_MODEL = "gate-laya-v3"
MODEL_DIR_NAME = Path("models") / "laya-v3"
REAL_DATA = PROJECT_ROOT.parent / "gate-data"  # 실제 데모 폴더. 쓰지 않는다
DEFAULT_WAIT_SECONDS = 300.0
COMMAND_TIMEOUT = 60.0     # gate 명령 하나(사람 승인 대기 30초 포함)
START_TIMEOUT = 20.0       # 데몬·gate ui가 뜰 때까지
SHORT_APPROVAL = 3         # timeout 시나리오의 approval.timeout_seconds

# 게이트 안 경로 → samples/ 파일. 옆 주석은 eval.run_eval --client laya --ner server --details(2026-11-01) 결과다.
SAMPLES: dict[str, str] = {
    "/medical_note/none_1.txt": "samples/medical_note/none_1.txt",    # 허용: safe 1.00
    "/medical_note/none_2.txt": "samples/medical_note/none_2.txt",    # 허용: safe 1.00 (검색에서 찾을 곳)
    "/medical_note/pii_3.txt": "samples/medical_note/pii_3.txt",      # 가림: 가린 뒤 safe 1.00 (이름·휴대폰·이메일·주민번호)
    "/consult_chat/pii_1.txt": "samples/consult_chat/pii_1.txt",      # 가림: 가린 뒤 safe 1.00 (이름·주소·소속)
    "/medical_note/quasi_1.txt": "samples/medical_note/quasi_1.txt",  # 사람 승인: 가린 뒤 sensitive 0.98
    "/medical_note/quasi_2.txt": "samples/medical_note/quasi_2.txt",  # 사람 승인: 가린 뒤 sensitive 0.99
    "/medical_note/quasi_3.txt": "samples/medical_note/quasi_3.txt",  # 사람 승인: 가린 뒤 sensitive 0.98
}
# 직접 만든 문서. 미리 돌려 본 결과(2026-11-01): 안내문 safe 0.998(허용), 서버 설정 메모는 NER 켬·끔 모두 가린 뒤 safe 0.998
NOTICE_DOC = "/안내문.txt"
NOTICE_TEXT = (
    "동네 도서관 휴관 안내\n\n"
    "이번 주 토요일은 시설 점검으로 하루 휴관합니다.\n"
    "대출한 책은 건물 앞 무인 반납함에 넣어 주세요.\n"
    "다음 주 월요일부터는 평소처럼 오전 9시에 문을 엽니다.\n"
)
HOST_DOC = "/서버설정.txt"
HOST_VALUES = {"10.20.30.40": "[IP]", "10.20.30.41": "[IP]", "wiki.intra.example.co.kr": "[내부 주소]",
               "build-07.internal": "[내부 주소]"}
HOST_TEXT = (
    "[서버 이전 작업 메모] 2026-10-20\n\n"
    "- 운영 DB 서버: 10.20.30.40 (포트 5432)\n"
    "- 예비 DB 서버: 10.20.30.41\n"
    "- 사내 위키: wiki.intra.example.co.kr\n"
    "- 빌드 서버: build-07.internal\n"
    "- 점검 시간: 매주 화요일 새벽 2시부터 4시까지\n"
    "- 이전 후에는 접속 주소가 바뀌므로 각 팀에 공지한다.\n"
)
HOST_KEEP = "포트 5432"  # IP 규칙이 잡지 않아야 할 숫자
ENV_DOC = "/.env"

ALLOW_DOCS = ("/medical_note/none_1.txt", NOTICE_DOC)
MASK_DOCS = ("/medical_note/pii_3.txt", "/consult_chat/pii_1.txt")
NUMBER_DOC = "/medical_note/pii_3.txt"
NUMBER_GROUPS = ("phone", "rrn", "email")  # must_hide.csv의 묶음 중 정규식이 같은 길이 *로 가리는 것
APPROVE_DOC = "/medical_note/quasi_2.txt"
DENY_DOC = "/medical_note/quasi_3.txt"
TIMEOUT_DOC = "/medical_note/quasi_1.txt"
SEARCH_WORD = "진료"
SEARCH_FOUND = ("/medical_note/none_1.txt", "/medical_note/none_2.txt")
SEARCH_SKIPPED = (ENV_DOC, TIMEOUT_DOC, APPROVE_DOC, DENY_DOC)
POLICY_DOC = "/medical_note/pii_3.txt"
POLICY_SENTENCE = "이름은 보여줘"
CACHE_DOC = "/medical_note/none_1.txt"
NER_DOWN_DOC = NOTICE_DOC
# 원본 폴더 추가(Phase 9). 임시 폴더 안 extra/가계부를 연결한다. 상담 문서는 /consult_chat/pii_1.txt와 같은 샘플이다
MOUNT_NAME = "가계부"
MOUNT_DOC = f"/{MOUNT_NAME}/상담.txt"
MOUNT_SAMPLE = SAMPLES["/consult_chat/pii_1.txt"]
MOUNT_ENV = f"/{MOUNT_NAME}/.env"
# 인코딩(2026-11-01). 시나리오가 원본 폴더 안 인코딩/에 만든다. 미리 돌려 본 결과(2026-11-01, Laya v3 + NER 재학습판):
# UTF-16 설정은 가린 뒤 safe 1.00, EUC-KR 상담(consult_chat/pii_1과 같은 글)은 UTF-8과 같은 칸을 가리고 safe 1.00
ENC_SECRET_DOC = "/인코딩/설정.txt"
ENC_KOREAN_DOC = "/인코딩/상담.txt"
ENC_KOREAN_SAMPLE = SAMPLES["/consult_chat/pii_1.txt"]
ENC_BINARY_DOC = "/인코딩/사진.png"
ENC_BINARY = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))

# 가림 표시 → 정책 칸(gate.ner_client.policy_slot). [전화번호]는 휴대폰·일반 전화 중 어느 칸인지 글자만으로 알 수 없어 뺀다.
LABEL_SLOTS = {"[이름]": "name", "[주소]": "address", "[생년월일]": "birth", "[식별번호]": "student_id", "[소속]": "org",
               "[장소]": "place", "[연락처]": "etc", "[나이]": "plain", "[이메일]": "email", "[IP]": "host",
               "[내부 주소]": "host"}
LABEL_RE = re.compile("|".join(re.escape(t) for t in sorted(set(MASK_TEXT.values()), key=len, reverse=True)))
PENDING_LINE = re.compile(r"^\s*\[([^\]\s]+)\]\s+(\S+)\s+\(남은 시간", re.MULTILINE)
SKIPPED_LINE = re.compile(r"^gate: 건너뜀 (\S+): (.*) \((\w+)\)$")
UI_URL = re.compile(r"http://127\.0\.0\.1:(\d+)/\?launch=(\S+)")
SESSION_META = re.compile(r'<meta name="gate-session" content="([^"]+)"')
PROMPT = "[y/N]".encode("utf-8")
MIN_LINE_CHARS = 8  # 감사 로그에 원문이 남았는지 볼 때 이보다 짧은 줄은 뺀다(흔한 낱말과 우연히 겹치지 않게)

PASS, FAIL, SKIP = "통과", "실패", "뺌"


class Fail(Exception):
    """시나리오 실패. 메시지는 '기대 …, 실제 …'처럼 짧게."""


class Skip(Exception):
    """지금 구조로 시험할 수 없어 뺀 시나리오."""


def check(condition: object, message: str) -> None:
    if not condition:
        raise Fail(message)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _gate_lines(text: str) -> str:
    """stderr에서 gate:로 시작하는 줄만 (실패 근거용)."""
    return _short(" / ".join(line for line in text.splitlines() if line.startswith("gate")) or text)


# --- 프로세스 ---

@dataclass(frozen=True)
class Done:
    code: int | None  # None이면 시간 초과로 끝냄
    out: str
    err: str = ""


@dataclass(frozen=True)
class PtyDone:
    code: int | None
    out: str          # stdout·stderr를 합친 터미널 화면 (입력한 답의 메아리 포함)
    prompts: int      # [y/N] 질문 수
    unused: int       # 쓰지 않은 답 수


@dataclass
class Flow:
    """gate cat 한 번. 사람 승인이 떴으면 그 id와 목록, 답한 결과도 담는다."""
    path: str
    code: int | None
    data: bytes
    err: str
    seconds: float
    pending_id: str | None = None
    listing: str = ""
    answer: PtyDone | None = None
    answered: str | None = None  # approve | deny | None(답하지 않음)
    notty_refused: bool | None = None

    @property
    def out(self) -> str:
        return self.data.decode("utf-8", "replace")

    @property
    def notice(self) -> str:
        lines = [line for line in self.err.splitlines() if "가렸습니다" in line]
        return lines[-1] if lines else ""

    def observed(self) -> dict:
        """에이전트가 본 결과를 감사 기록 값으로 옮긴 것. 감사 로그 대조의 기대값이다."""
        if self.code == 0:
            decision = "mask" if self.notice else "allow"
        else:
            decision = "deny" if "EACCES" in self.err else "error"
        out: dict = {"op": "cat", "path": self.path, "decision": decision}
        if self.pending_id is not None:
            out["approval"] = {"approve": "once", "deny": "deny", None: "timeout"}[self.answered]
            if self.answered is not None:
                out["approved_by"] = "terminal"
        return out


def kill(proc: subprocess.Popen, grace: float = 5.0) -> None:
    """프로세스(새 세션으로 띄운 것)를 끝낸다. SIGTERM 뒤 grace초 안에 안 끝나면 그룹째 SIGKILL."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait()
    except ProcessLookupError:
        pass


def run_pty(argv: Sequence[str], env: Mapping[str, str], answers: Sequence[str] = (),
            timeout: float = COMMAND_TIMEOUT) -> PtyDone:
    """사람 역할: 의사 터미널에서 명령을 돌리고 [y/N]이 나올 때마다 answers를 차례로 넣는다(다 쓰면 n)."""
    master, slave = os.openpty()
    try:
        proc = subprocess.Popen(list(argv), stdin=slave, stdout=slave, stderr=slave, env=dict(env),
                                start_new_session=True)
    except BaseException:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)
    buffer = bytearray()
    left = list(answers)
    prompts = 0
    timed_out = False
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            ready, _, _ = select.select([master], [], [], min(remaining, 0.2))
            if ready:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # 자식이 끝나 터미널이 닫힘(EIO)
                    chunk = b""
                if not chunk:
                    break
                buffer += chunk
                while prompts < bytes(buffer).count(PROMPT):
                    prompts += 1
                    os.write(master, ((left.pop(0) if left else "n") + "\n").encode("utf-8"))
            elif proc.poll() is not None:
                break
    finally:
        kill(proc)
        os.close(master)
    text = bytes(buffer).decode("utf-8", "replace").replace("\r\n", "\n")
    return PtyDone(None if timed_out else proc.returncode, text, prompts, len(left))


def http_get(url: str, headers: Mapping[str, str] | None = None, timeout: float = 10.0) -> tuple[int, Mapping, str]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers=dict(headers or {}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8", "replace")


def closed_port() -> int:
    """지금 아무도 듣지 않는 127.0.0.1 포트."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def socket_accepts(path: Path) -> bool:
    """연결만 해 본다(요청을 보내지 않으므로 감사 로그에 남지 않는다)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        try:
            sock.connect(str(path))
        except OSError:
            return False
    return True


# --- 설정 ---

@dataclass(frozen=True)
class Settings:
    jev_url: str
    jev_model: str
    jev_model_dir: str
    ner_url: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        return cls(
            (env.get("JEV_BASE_URL") or "").strip() or DEFAULT_JEV_URL,
            (env.get("JEV_MODEL") or "").strip() or DEFAULT_JEV_MODEL,
            (env.get("JEV_MODEL_DIR") or "").strip() or default_model_dir(),
            (env.get("NER_BASE_URL") or "").strip() or DEFAULT_NER_URL,
        )


def default_model_dir() -> str:
    """저장소 위 폴더들에서 models/laya-v3을 찾는다(worktree 안에서 돌려도 같은 체크포인트를 찾게)."""
    for parent in PROJECT_ROOT.parents:
        if (parent / MODEL_DIR_NAME).is_dir():
            return str(parent / MODEL_DIR_NAME)
    return ""


def _health(base: str) -> tuple[bool, str, dict]:
    try:
        status, _, body = http_get(base.rstrip("/") + "/health", timeout=5.0)
    except (urllib.error.URLError, OSError) as exc:
        return False, f"연결 안 됨 ({getattr(exc, 'reason', exc)})", {}
    try:
        data = json.loads(body)
    except ValueError:
        data = {}
    return status == 200, f"HTTP {status}", data if isinstance(data, dict) else {}


def preflight(settings: Settings, wait: float, gate_bin: list[str]) -> list[str]:
    """시작 전 확인. 문제가 있으면 이유 목록(종료 코드 3)."""
    problems = []
    if not Path(gate_bin[0]).exists():
        problems.append(f"gate 실행 파일이 없습니다: {gate_bin[0]} (uv sync)")
    if not settings.jev_model_dir:
        problems.append("JEV_MODEL_DIR이 없고 저장소 위 폴더에서 models/laya-v3도 찾지 못했습니다")
    elif not (Path(settings.jev_model_dir) / "questions.json").is_file():
        problems.append(f"JEV_MODEL_DIR에 questions.json이 없습니다: {settings.jev_model_dir}")
    for path in SAMPLES.values():
        if not (PROJECT_ROOT / path).is_file():
            problems.append(f"샘플이 없습니다: {path}")
    if problems:
        return problems

    deadline = time.monotonic() + wait
    said = False
    while True:
        laya_ok, laya_why, laya = _health(settings.jev_url)
        ner_ok, ner_why, ner = _health(settings.ner_url)
        if laya_ok and ner_ok:
            break
        if time.monotonic() >= deadline:
            if not laya_ok:
                problems.append(f"laya-serve {settings.jev_url}/health: {laya_why}")
            if not ner_ok:
                problems.append(f"nerserve {settings.ner_url}/health: {ner_why}")
            return problems
        if not said:
            print(f"e2e: 서버를 기다립니다 (최대 {wait:g}초). laya-serve {laya_why}, nerserve {ner_why}",
                  file=sys.stderr, flush=True)
            said = True
        time.sleep(3)
    loaded = laya.get("loaded")
    if isinstance(loaded, list) and settings.jev_model not in loaded:
        problems.append(f"laya-serve에 JEV_MODEL={settings.jev_model}이(가) 없습니다 (loaded: {loaded})")
    if ner.get("status") not in (None, "ok"):
        problems.append(f"nerserve 상태가 ok가 아닙니다: {ner.get('status')}")
    return problems


# --- 시험 환경 ---

class E2E:
    """임시 폴더의 원본·정책·감사 로그·소켓과 그 위에서 도는 데몬. 시나리오가 함께 쓴다."""

    def __init__(self, root: Path, settings: Settings, gate_bin: list[str]) -> None:
        self.root = root
        self.settings = settings
        self.gate_bin = gate_bin
        self.source = root / "source"
        self.policy = root / "policy.yaml"
        self.history = root / "policy-history"
        self.audit = root / "audit" / "audit.jsonl"
        self.socket = root / "run" / "gate.sock"
        self.audit2 = root / "audit-ner-down" / "audit.jsonl"
        self.socket2 = root / "run2" / "gate.sock"
        self.logs: list[Path] = []
        self.procs: list[subprocess.Popen] = []
        self.must_hide = load_must_hide(DEFAULT_MUST_HIDE)
        self.secrets = (f"e2e{secrets.token_hex(8)}", f"sk-e2e-{secrets.token_hex(12)}")
        self.expected: dict[str, list[dict]] = {"main": [], "ner-down": []}
        self.env = self._child_env()
        self._start_policy = b""

    def _child_env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
        env.update(GATE_SOCKET=str(self.socket), GATE_POLICY=str(self.policy), GATE_AUDIT=str(self.audit),
                   JEV_BASE_URL=self.settings.jev_url, JEV_MODEL=self.settings.jev_model,
                   JEV_MODEL_DIR=self.settings.jev_model_dir, NER_BASE_URL=self.settings.ner_url)
        env.pop("PYTHONUNBUFFERED", None)  # 사용자가 띄우는 것과 같게(데몬은 줄마다 flush한다)
        return env

    # 데이터

    def create(self) -> None:
        self.source.mkdir(mode=0o700)
        for vpath, sample in SAMPLES.items():
            target = self.source / vpath.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((PROJECT_ROOT / sample).read_bytes())
        (self.source / NOTICE_DOC.lstrip("/")).write_text(NOTICE_TEXT, encoding="utf-8")
        (self.source / HOST_DOC.lstrip("/")).write_text(HOST_TEXT, encoding="utf-8")
        (self.source / ENV_DOC.lstrip("/")).write_text(
            f"DB_PASSWORD={self.secrets[0]}\nAPI_TOKEN={self.secrets[1]}\n", encoding="utf-8")
        write_entries(self.policy, [Entry(settings={"jev_backend": "laya"})])
        self._start_policy = self.policy.read_bytes()

    def original(self, vpath: str) -> bytes:
        return (self.source / vpath.lstrip("/")).read_bytes()

    def hidden_values(self, vpath: str) -> list[str]:
        """그 문서에서 가려져야 할 값: must_hide.csv(샘플), 직접 만든 문서의 IP·내부 주소와 비밀값."""
        if vpath in SAMPLES or vpath == MOUNT_DOC:
            return [m.text for m in self.must_hide.get(SAMPLES.get(vpath, MOUNT_SAMPLE), [])]
        if vpath == HOST_DOC:
            return list(HOST_VALUES)
        if vpath in (ENV_DOC, MOUNT_ENV):
            return list(self.secrets)
        return []

    def all_paths(self) -> list[str]:
        return [*SAMPLES, NOTICE_DOC, HOST_DOC, ENV_DOC]

    # 명령

    def cmd(self, *args: str) -> list[str]:
        return [*self.gate_bin, *args]

    def gate(self, *args: str, env: Mapping[str, str] | None = None, timeout: float = COMMAND_TIMEOUT) -> Done:
        """에이전트·스크립트처럼 터미널 없이 돌린다."""
        try:
            r = subprocess.run(self.cmd(*args), env=dict(env or self.env), stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise Fail(f"gate {' '.join(args)}가 {timeout:g}초 안에 끝나지 않음") from exc
        return Done(r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"))

    def human(self, *args: str, answers: Sequence[str] = ()) -> PtyDone:
        """사람 역할: 의사 터미널에서 돌린다."""
        done = run_pty(self.cmd(*args), self.env, answers)
        check(done.code is not None, f"gate {' '.join(args)}가 끝나지 않음: {_short(done.out)}")
        return done

    def start(self, *args: str, env: Mapping[str, str] | None = None, log: Path | None = None) -> subprocess.Popen:
        if log is not None:
            with open(log, "ab") as handle:
                proc = subprocess.Popen(self.cmd(*args), env=dict(env or self.env), stdin=subprocess.DEVNULL,
                                        stdout=handle, stderr=handle, start_new_session=True)
            self.logs.append(log)
        else:
            proc = subprocess.Popen(self.cmd(*args), env=dict(env or self.env), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        self.procs.append(proc)
        return proc

    def start_daemon(self, sock: Path, env: Mapping[str, str], log: Path) -> subprocess.Popen:
        proc = self.start("serve", "--source", str(self.source), "--no-dialog", env=env, log=log)
        deadline = time.monotonic() + START_TIMEOUT
        while not socket_accepts(sock):
            if proc.poll() is not None:
                raise RuntimeError(f"데몬이 바로 끝났습니다(종료 코드 {proc.returncode}): {_short(log.read_text('utf-8'))}")
            if time.monotonic() > deadline:
                kill(proc)
                raise RuntimeError(f"데몬이 {START_TIMEOUT:g}초 안에 소켓을 열지 않았습니다: {_short(log.read_text('utf-8'))}")
            time.sleep(0.1)
        return proc

    def stop_all(self) -> None:
        for proc in reversed(self.procs):
            kill(proc)
        self.procs.clear()

    # gate cat과 사람 승인

    def read(self, vpath: str, on_pending: str = "fail", record: str | None = "main",
             env: Mapping[str, str] | None = None) -> Flow:
        """gate cat을 백그라운드로 돌리며 gate approve 목록을 본다.

        on_pending: 승인 요청이 뜨면 approve(이번만 허용) | deny(거부) | wait(답하지 않음, 시간 초과 시험) |
                    fail(뜨면 안 됨. 기다리지 않게 거부로 답하고 Fail).
        record: 감사 로그 대조용 기대값을 남길 로그 이름(None이면 남기지 않음).
        """
        started = time.monotonic()
        proc = self.start("cat", vpath, env=env)
        flow_pending: str | None = None
        listing = ""
        answer: PtyDone | None = None
        answered: str | None = None
        notty: bool | None = None
        deadline = started + COMMAND_TIMEOUT
        try:
            while proc.poll() is None:
                if time.monotonic() > deadline:
                    raise Fail(f"gate cat {vpath}가 {COMMAND_TIMEOUT:g}초 안에 끝나지 않음")
                if flow_pending is None:
                    listed = self.gate("approve", env=env)
                    found = [m.group(1) for m in PENDING_LINE.finditer(listed.out) if m.group(2) == vpath]
                    if found:
                        flow_pending, listing = found[0], listed.out
                        if on_pending == "approve":
                            refused = self.gate("approve", flow_pending, env=env)  # 터미널 없이: 거부되어야 함
                            notty = refused.code == 1 and "터미널" in refused.err
                            answer, answered = self.human("approve", flow_pending), "approve"
                        elif on_pending in ("deny", "fail"):
                            answer, answered = self.human("deny", flow_pending), "deny"
                        continue
                time.sleep(0.2)
            data, err = proc.communicate()
        finally:
            kill(proc)
            if proc in self.procs:
                self.procs.remove(proc)
        flow = Flow(vpath, proc.returncode, data, err.decode("utf-8", "replace"), time.monotonic() - started,
                    flow_pending, listing, answer, answered, notty)
        if record is not None:
            self.expected[record].append(flow.observed())
        if on_pending == "fail" and flow_pending is not None:
            raise Fail(f"{vpath}: 사람 승인으로 감(기대: 바로 결과). 거부로 답해 끝냄. 목록: {_short(listing)}")
        return flow

    # 감사 로그

    def audit_records(self, audit: Path | None = None) -> list[dict]:
        args = ["audit", "--json", "-n", "0"] + (["--audit", str(audit)] if audit else [])
        r = self.gate(*args)
        check(r.code == 0, f"gate audit --json 실패: {_gate_lines(r.err)}")
        return [json.loads(line) for line in r.out.splitlines() if line.strip()]

    # 정책

    def policy_changed(self) -> bool:
        files = sorted(p.name for p in self.history.iterdir()) if self.history.is_dir() else []
        return self.policy.read_bytes() != self._start_policy or bool(files)

    def restore_policy(self) -> None:
        """시나리오가 남긴 정책 바꿈을 지운다(시작 정책 파일과 빈 되돌리기 기록)."""
        fd = os.open(self.policy, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(self._start_policy)
        if self.history.is_dir():
            for path in self.history.iterdir():
                path.unlink()


# --- 판정 도움 ---

def notice_problem(original: str, flow: Flow) -> str | None:
    """gate cat 안내 줄이 실제 가림 방식과 맞는지. 맞으면 None.

    내용에서 종류 이름 표시([이름] 등)를 칸별로 세고(나온 순서 = 데몬이 센 순서), 원본보다 *가 늘었는지 본다.
    안내 줄은 *가 늘었을 때만 '같은 길이의 *로', 표시가 있을 때만 '종류 이름으로 (칸별 건수)'를 말해야 한다(labeled 반영).
    """
    line = flow.notice
    if not line:
        return "가림 안내 줄이 없음"
    labels: Counter = Counter()
    unknown = False
    for m in LABEL_RE.finditer(flow.out):
        slot = LABEL_SLOTS.get(m.group())
        if slot is None:
            unknown = True
        else:
            labels[slot] += 1
    stars = flow.out.count("*") > original.count("*")
    said_stars = "같은 길이의 *로" in line
    if said_stars != stars:
        return f"안내 줄의 * 가림 언급 {'있음' if said_stars else '없음'}, 실제 * 가림 {'있음' if stars else '없음'}: {line}"
    if ("종류 이름으로" in line) != bool(labels or unknown):
        return f"안내 줄과 실제 종류 이름 가림({dict(labels)})이 다름: {line}"
    if labels and not unknown and f"({kinds_text(labels)})" not in line:
        return f"안내 줄 기대 ({kinds_text(labels)}), 실제: {line}"
    return None


def label_summary(text: str) -> str:
    counts = Counter(m.group() for m in LABEL_RE.finditer(text))
    return " ".join(f"{label}×{n}" for label, n in counts.items()) or "표시 없음"


def leaks(text: str, values: Sequence[str]) -> list[str]:
    return [v for v in values if v and v in text]


def match_records(expected: Sequence[dict], records: Sequence[dict]) -> tuple[int, list[str]]:
    """기대 기록을 순서대로 실제 기록에서 찾는다(op·path가 같은 다음 기록). (맞은 수, 문제 목록)."""
    problems = []
    matched = 0
    i = 0
    for exp in expected:
        j = next((k for k in range(i, len(records))
                  if records[k].get("op") == exp["op"] and records[k].get("path") == exp["path"]), None)
        if j is None:
            problems.append(f"{exp['op']} {exp['path']}: 기록 없음")
            continue
        rec = records[j]
        wrong = [f"{k} 기대 {v!r} 실제 {rec.get(k)!r}" for k, v in exp.items()
                 if k not in ("op", "path") and rec.get(k) != v]
        if wrong:
            problems.append(f"{exp['op']} {exp['path']}: " + ", ".join(wrong))
        else:
            matched += 1
        i = j + 1
    return matched, problems


# --- 시나리오 ---

def s_ls(e: E2E) -> str:
    r = e.gate("ls", "/")
    check(r.code == 0, f"gate ls / 종료 코드 {r.code}: {_gate_lines(r.err)}")
    names = {line for line in r.out.splitlines() if line}
    want = {".env", "consult_chat/", "medical_note/", "서버설정.txt", "안내문.txt"}
    check(want <= names, f"목록 기대 {sorted(want)}, 없음 {sorted(want - names)}")
    e.expected["main"].append({"op": "ls", "path": "/", "decision": "ok"})
    sub = e.gate("ls", "/medical_note")
    check(sub.code == 0 and len(sub.out.split()) == 6, f"gate ls /medical_note 기대 6개, 실제 {sub.out.split()}")
    e.expected["main"].append({"op": "ls", "path": "/medical_note", "decision": "ok"})
    st = e.gate("stat", CACHE_DOC)
    check(st.code == 0, f"gate stat 종료 코드 {st.code}: {_gate_lines(st.err)}")
    info = json.loads(st.out)
    size = len(e.original(CACHE_DOC))
    check(info.get("type") == "file" and info.get("size") == size, f"stat 기대 file·{size}바이트, 실제 {info}")
    e.expected["main"].append({"op": "stat", "path": CACHE_DOC, "decision": "ok"})
    return f"ls / {len(names)}항목(.env·폴더·직접 만든 파일), ls /medical_note 6개, stat 크기 {size}바이트 = 원본"


def s_allow(e: E2E) -> str:
    parts = []
    for path in ALLOW_DOCS:
        f = e.read(path)
        check(f.code == 0, f"{path}: 기대 허용, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
        check(f.data == e.original(path), f"{path}: 원문과 다름 (가림 안내: {f.notice or '없음'})")
        check(not f.notice, f"{path}: 허용인데 가림 안내가 나옴: {f.notice}")
        parts.append(f"{Path(path).name} 원문 그대로 {len(f.data)}바이트")
    return ", ".join(parts)


def s_mask(e: E2E) -> str:
    parts = []
    for path in MASK_DOCS:
        f = e.read(path)
        check(f.code == 0, f"{path}: 기대 가림, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
        check("[이름]" in f.out, f"{path}: [이름] 표시 없음 ({label_summary(f.out)})")
        values = e.hidden_values(path)
        leaked = leaks(f.out, values)
        check(not leaked, f"{path}: 누출 {leaked}")
        problem = notice_problem(e.original(path).decode("utf-8"), f)
        check(problem is None, f"{path}: {problem}")
        parts.append(f"{Path(path).parent.name}/{Path(path).name} {label_summary(f.out)}, 누출 0/{len(values)}, 안내 줄 맞음")
    return "; ".join(parts)


def s_number(e: E2E) -> str:
    f = e.read(NUMBER_DOC)
    check(f.code == 0, f"기대 가림, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    shown = []
    for m in e.must_hide[SAMPLES[NUMBER_DOC]]:
        if m.group not in NUMBER_GROUPS:
            continue
        stars = re.sub(r"[0-9A-Za-z]", "*", m.text)
        check(m.text not in f.out, f"{m.group} 값이 남음")
        check(stars in f.out, f"{m.group}: 기대 같은 길이 {stars}, 실제 없음")
        shown.append(f"{m.group} {stars}")
    check(len(shown) == len(NUMBER_GROUPS), f"must_hide.csv에서 찾은 번호 묶음 {len(shown)}개(기대 {len(NUMBER_GROUPS)})")
    check("같은 길이의 *로" in f.notice, f"안내 줄에 * 가림 언급 없음: {f.notice}")
    return ", ".join(shown) + " (원래 길이·구분자 그대로)"


def _check_host(f: Flow, when: str) -> None:
    check(f.code == 0, f"{when}: 기대 가림, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    leaked = leaks(f.out, list(HOST_VALUES))
    check(not leaked, f"{when}: 남은 값 {leaked}")
    for label in set(HOST_VALUES.values()):
        want = list(HOST_VALUES.values()).count(label)
        check(f.out.count(label) == want, f"{when}: {label} 기대 {want}개, 실제 {f.out.count(label)}개")
    check(HOST_KEEP in f.out, f"{when}: '{HOST_KEEP}'까지 가려짐")
    check("IP·내부 주소 4건" in f.notice and "같은 길이" not in f.notice, f"{when}: 안내 줄 {f.notice}")


def s_host(e: E2E) -> str:
    _check_host(e.read(HOST_DOC), "NER 켬")
    off = e.human("policy", "set", "ner_backend", "none", answers=["y"])
    check(off.code == 0 and "저장했어요" in off.out, f"ner_backend none 저장 실패: {_short(off.out)}")
    check(off.prompts == 1, f"NER 끄기는 느슨해지는 바꿈이라 한 번 묻기 기대, 실제 질문 {off.prompts}번")
    _check_host(e.read(HOST_DOC), "NER 끔")
    back = e.human("policy", "undo")
    check(back.code == 0 and back.prompts == 0, f"undo(NER 다시 켬) 기대 묻지 않고 저장, 실제 코드 {back.code}·질문 {back.prompts}번")
    return "NER 켬·끔 모두 [IP]×2 [내부 주소]×2, 값 0개 남음, '포트 5432'는 그대로; set none(pty y) → undo(묻지 않음)"


def s_path(e: E2E) -> str:
    f = e.read(ENV_DOC)
    check(f.code == 1 and "EACCES" in f.err, f"기대 EACCES, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    check("경로 규칙" in f.err, f"이유에 경로 규칙 없음: {_gate_lines(f.err)}")
    check(not f.data, "차단인데 내용이 나옴")
    check(not leaks(f.err, e.secrets), "이유에 비밀값이 들어 있음")
    e.expected["main"][-1]["stage"] = "path"
    return _gate_lines(f.err)


def s_approve(e: E2E) -> str:
    f = e.read(APPROVE_DOC, on_pending="approve")
    check(f.pending_id, f"기대 사람 승인, 실제 바로 끝남(종료 코드 {f.code}) {_gate_lines(f.err)}")
    check("이유:" in f.listing, f"gate approve 목록에 이유 없음: {_short(f.listing)}")
    check(f.notty_refused, "터미널 없이 gate approve <id>가 거부되지 않음")
    check(f.answer is not None and f.answer.code == 0 and "이번만 허용" in f.answer.out,
          f"gate approve <id>(pty) 실패: {_short(f.answer.out if f.answer else '')}")
    check(f.code == 0, f"승인 뒤 기대 내용, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    check(LABEL_RE.search(f.out), "승인 뒤 내용에 가림 표시가 없음")
    leaked = leaks(f.out, e.hidden_values(APPROVE_DOC))
    check(not leaked, f"승인 뒤 누출 {leaked}")
    reason = re.search(r"이유: (.*)", f.listing)
    return (f"목록 [{f.pending_id}] 이유 '{_short(reason.group(1), 60) if reason else ''}', 터미널 없이 답하면 거부, "
            f"pty 승인 → {label_summary(f.out)}, 누출 0")


def s_deny(e: E2E) -> str:
    f = e.read(DENY_DOC, on_pending="deny")
    check(f.pending_id, f"기대 사람 승인, 실제 바로 끝남(종료 코드 {f.code}) {_gate_lines(f.err)}")
    check(f.answer is not None and f.answer.code == 0 and "거부" in f.answer.out,
          f"gate deny <id>(pty) 실패: {_short(f.answer.out if f.answer else '')}")
    check(f.code == 1 and "EACCES" in f.err and "사용자가 거부했습니다" in f.err,
          f"기대 EACCES(사용자가 거부), 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    check(not f.data, "거부인데 내용이 나옴")
    return f"[{f.pending_id}] pty 거부 → {_gate_lines(f.err)}"


def s_timeout(e: E2E) -> str:
    short = e.human("policy", "set", "approval.timeout_seconds", str(SHORT_APPROVAL))
    check(short.code == 0 and short.prompts == 0, f"대기 시간 줄이기 기대 묻지 않고 저장, 실제 코드 {short.code}·질문 {short.prompts}번")
    f = e.read(TIMEOUT_DOC, on_pending="wait")
    check(f.pending_id, f"기대 사람 승인, 실제 바로 끝남(종료 코드 {f.code}) {_gate_lines(f.err)}")
    check(f.code == 1 and "EACCES" in f.err and f"{SHORT_APPROVAL}초 안에 승인이 없어" in f.err,
          f"기대 EACCES({SHORT_APPROVAL}초 시간 초과), 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    check(f.seconds < SHORT_APPROVAL + 15, f"시간 초과까지 {f.seconds:.1f}초 걸림")
    back = e.human("policy", "undo", answers=["y"])
    check(back.code == 0 and back.prompts == 1,
          f"undo(3→30초, 느슨해짐) 기대 한 번 묻고 저장, 실제 코드 {back.code}·질문 {back.prompts}번")
    return f"approval.timeout_seconds {SHORT_APPROVAL}(pty) → 답 없음 {f.seconds:.1f}초 뒤 EACCES → undo(pty y)"


def s_search(e: E2E) -> str:
    r = e.gate("search", SEARCH_WORD, "/")
    e.expected["main"].append({"op": "search", "path": "/", "decision": "ok"})
    check(r.code == 0, f"gate search 종료 코드 {r.code}: {_gate_lines(r.err)}")
    found: dict[str, int] = Counter()
    for line in r.out.splitlines():
        path, _, rest = line.partition(":")
        check(rest, f"출력 형식이 '경로:줄:내용'이 아님: {_short(line)}")
        found[path] += 1
    skipped = {}
    for line in r.err.splitlines():
        m = SKIPPED_LINE.match(line)
        if m:
            skipped[m.group(1)] = (m.group(2), m.group(3))
    check(set(SEARCH_FOUND) <= set(found), f"기대 찾음 {list(SEARCH_FOUND)}, 실제 {sorted(found)}")
    hit = set(found) & set(SEARCH_SKIPPED)
    check(not hit, f"막혀야 할 파일에서 찾음: {sorted(hit)}")
    missing = set(SEARCH_SKIPPED) - set(skipped)
    check(not missing, f"건너뜀 목록에 없음: {sorted(missing)}")
    check(all(skipped[p][1] == "EACCES" for p in SEARCH_SKIPPED), f"건너뛴 이유 {skipped}")
    check("경로 규칙" in skipped[ENV_DOC][0], f".env 건너뛴 이유: {skipped[ENV_DOC][0]}")
    check(all("사람 승인이 필요" in skipped[p][0] for p in SEARCH_SKIPPED if p != ENV_DOC), "승인 파일 건너뛴 이유가 다름")
    leaked = leaks(r.out + r.err, [v for p in e.all_paths() for v in e.hidden_values(p)])
    check(not leaked, f"검색 결과에 값이 남음: {leaked}")
    return (f"'{SEARCH_WORD}' {sum(found.values())}줄(파일 {', '.join(Path(p).name for p in sorted(found))}), "
            f"건너뜀 {len(skipped)}개(.env 경로 규칙, quasi 3개 승인 필요), 값 누출 0")


def s_policy(e: E2E) -> str:
    names = [m.text for m in e.must_hide[SAMPLES[POLICY_DOC]] if m.group == "name"]
    check(names, "must_hide.csv에 이름이 없음")
    name = names[0]
    before = e.read(POLICY_DOC)
    check(before.code == 0 and "[이름]" in before.out and name not in before.out, "시작 상태에서 이름이 가려지지 않음")
    refused = e.gate("policy", "add", POLICY_SENTENCE)
    check(refused.code == 1 and "터미널" in refused.out, f"터미널 없이 policy add가 거부되지 않음: {_short(refused.out)}")
    added = e.human("policy", "add", POLICY_SENTENCE, answers=["y", "y"])
    check(added.code == 0 and "저장했어요" in added.out, f"policy add 저장 실패: {_short(added.out)}")
    check(added.prompts == 2, f"느슨해지는 문장이라 두 번 묻기 기대, 실제 {added.prompts}번")
    after = e.read(POLICY_DOC, on_pending="approve")
    check(after.code == 0, f"이름 보여 주기 뒤 기대 내용, 실제 종료 코드 {after.code} {_gate_lines(after.err)}")
    check("[이름]" not in after.out and name in after.out, f"[이름]이 그대로임 ({label_summary(after.out)})")
    check("***-****-****" in after.out, "이름 말고 다른 가림(휴대폰)까지 풀림")
    undo = e.human("policy", "undo")
    check(undo.code == 0 and undo.prompts == 0, f"undo(다시 가림) 기대 묻지 않고 저장, 실제 코드 {undo.code}·질문 {undo.prompts}번")
    again = e.read(POLICY_DOC)
    check(again.code == 0 and "[이름]" in again.out and name not in again.out, "undo 뒤에도 이름이 보임")
    how = "Jev가 남은 이름을 보고 사람 승인 → pty 승인" if after.pending_id else "바로 가림"
    return f"add(pty y·y) → [이름] 사라지고 이름이 보임({how}, 휴대폰은 계속 *) → undo(묻지 않음) → 다시 [이름]"


def s_cache(e: E2E) -> str:
    first = e.read(CACHE_DOC)
    second = e.read(CACHE_DOC)
    check(first.code == 0 and second.code == 0 and first.data == second.data, "두 번 읽은 결과가 다름")
    e.expected["main"][-1]["cached"] = True
    e.expected["main"][-1]["jev_calls"] = 0
    records = [r for r in e.audit_records() if r.get("op") == "cat" and r.get("path") == CACHE_DOC]
    check(len(records) >= 2, f"감사 기록 {len(records)}건(기대 2건 이상)")
    last = records[-1]
    check(last.get("cached") is True and last.get("jev_calls") == 0,
          f"두 번째 기록 기대 cached true·jev_calls 0, 실제 cached {last.get('cached')}·jev_calls {last.get('jev_calls')}")
    first_rec = records[-2]
    return (f"두 번째 기록 cached true·jev_calls 0 "
            f"(첫 기록 cached {first_rec.get('cached')}·jev_calls {first_rec.get('jev_calls')})")


def s_ner_down(e: E2E) -> str:
    port = closed_port()
    env = {**e.env, "NER_BASE_URL": f"http://127.0.0.1:{port}", "GATE_SOCKET": str(e.socket2),
           "GATE_AUDIT": str(e.audit2)}
    log = e.root / "serve-ner-down.log"
    try:
        daemon = e.start_daemon(e.socket2, env, log)
    except RuntimeError as exc:
        raise Fail(f"두 번째 데몬을 띄우지 못함: {exc}") from exc
    try:
        listed = e.gate("ls", "/", env=env)
        check(listed.code == 0 and ".env" in listed.out.split(),
              f"gate ls 기대 목록, 실제 코드 {listed.code} {_gate_lines(listed.err)}")
        e.expected["ner-down"].append({"op": "ls", "path": "/", "decision": "ok"})
        st = e.gate("stat", NER_DOWN_DOC, env=env)
        check(st.code == 0, f"gate stat 기대 성공, 실제 코드 {st.code} {_gate_lines(st.err)}")
        e.expected["ner-down"].append({"op": "stat", "path": NER_DOWN_DOC, "decision": "ok"})
        f = e.read(NER_DOWN_DOC, env=env, record="ner-down")
        check(f.code == 1 and "EACCES" in f.err and "NER" in f.err,
              f"기대 NER 실패로 EACCES, 실제 코드 {f.code} {_gate_lines(f.err)}")
        check(not f.data, "차단인데 내용이 나옴")
        e.expected["ner-down"][-1]["stage"] = "entity"
        records = e.audit_records(e.audit2)
        cats = [r for r in records if r.get("op") == "cat"]
        check(cats and cats[-1].get("decision") == "deny" and cats[-1].get("stage") == "entity",
              f"감사 기록 기대 deny·entity, 실제 {[(r.get('decision'), r.get('stage')) for r in cats]}")
    finally:
        kill(daemon)
        if daemon in e.procs:
            e.procs.remove(daemon)
    startup = log.read_text("utf-8")
    check("NER 켜짐" in startup, "데몬 시작 안내에 NER 켜짐 경고가 없음")
    return f"닫힌 포트 {port}: ls·stat 됨, cat → {_gate_lines(f.err)}; 데몬 시작 때 NER 경고 있음"


def s_mount(e: E2E) -> str:
    folder = e.root / "extra" / MOUNT_NAME
    folder.mkdir(parents=True)
    (folder / "상담.txt").write_bytes((PROJECT_ROOT / MOUNT_SAMPLE).read_bytes())
    (folder / ".env").write_text(f"DB_PASSWORD={e.secrets[0]}\nAPI_TOKEN={e.secrets[1]}\n", encoding="utf-8")
    real = str(folder.resolve())  # /tmp는 /private/tmp
    mounts_file = e.root / "mounts.yaml"
    workspace = e.root / "ws"
    r = e.gate("workspace", "init", str(workspace), "--source", str(e.source))
    check(r.code == 0, f"gate workspace init 실패: {_short(r.out + r.err)}")
    settings_file = workspace / ".claude" / "settings.json"

    def protected() -> tuple[bool, bool]:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        deny = settings["permissions"]["deny"]
        rules = all(f"{tool}(/{real}{tail})" in deny for tool in ("Read", "Edit") for tail in ("", "/**"))
        return rules, real in settings["sandbox"]["filesystem"]["denyRead"]

    check(protected() == (False, False), "연결 전인데 작업 폴더 설정에 이 폴더 막기가 있음")
    r = e.gate("mount", "add", str(folder))
    check(r.code == 1 and "터미널" in r.out + r.err and not mounts_file.exists(),
          f"터미널 없이 gate mount add 기대 거부, 실제 종료 코드 {r.code} {_short(r.out + r.err)}")

    added = e.human("mount", "add", str(folder), answers=("y", "y"))
    check(added.code == 0 and added.prompts == 2 and f"연결했어요: /{MOUNT_NAME}" in added.out,
          f"gate mount add(pty) 기대 질문 2번·연결, 실제 종료 코드 {added.code} 질문 {added.prompts} {_short(added.out)}")
    check("다시 시작" in added.out, f"세션 다시 시작 안내가 없음: {_short(added.out)}")
    check(protected() == (True, True), f"연결 뒤 작업 폴더 설정에 막기 기대 (규칙, denyRead) 둘 다, 실제 {protected()}")

    listed = e.gate("ls", "/")
    check(listed.code == 0 and f"{MOUNT_NAME}/" in listed.out.split(),
          f"데몬을 다시 띄우지 않고 gate ls /에 {MOUNT_NAME}/ 기대, 실제 {listed.out.split()}")
    e.expected["main"].append({"op": "ls", "path": "/", "decision": "ok"})
    f = e.read(MOUNT_DOC)
    check(f.code == 0 and "[이름]" in f.out, f"{MOUNT_DOC}: 기대 가림, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    leaked = leaks(f.out, e.hidden_values(MOUNT_DOC))
    check(not leaked, f"{MOUNT_DOC}: 누출 {leaked}")
    g = e.read(MOUNT_ENV)
    check(g.code == 1 and "EACCES" in g.err and "경로 규칙" in g.err and not g.data,
          f"{MOUNT_ENV}: 기대 경로 규칙 EACCES, 실제 종료 코드 {g.code} {_gate_lines(g.err)}")
    check(not leaks(g.err, e.secrets), "차단 이유에 비밀값이 들어 있음")
    e.expected["main"][-1]["stage"] = "path"
    records = [rec for rec in e.audit_records() if str(rec.get("path", "")).startswith(f"/{MOUNT_NAME}")]
    check({rec.get("path") for rec in records} >= {MOUNT_DOC, MOUNT_ENV},
          f"감사 기록에 게이트 안 경로 기대 {MOUNT_DOC}·{MOUNT_ENV}, 실제 {sorted({rec.get('path') for rec in records})}")
    check(real not in e.audit.read_text(encoding="utf-8"), "감사 로그에 추가 폴더의 실제 경로가 남음")
    shown = e.gate("mount", "list")
    check(shown.code == 0 and f"/{MOUNT_NAME}" in shown.out and "연결됨" in shown.out,
          f"gate mount list에 연결됨 기대: {_short(shown.out)}")

    removed = e.human("mount", "remove", MOUNT_NAME, answers=("y",))
    check(removed.code == 0 and "보호만 남아" in removed.out, f"gate mount remove(pty) 실패: {_short(removed.out)}")
    listed = e.gate("ls", "/")
    check(listed.code == 0 and f"{MOUNT_NAME}/" not in listed.out.split(), f"뺀 뒤에도 gate ls /에 보임: {listed.out.split()}")
    e.expected["main"].append({"op": "ls", "path": "/", "decision": "ok"})
    gone = e.read(MOUNT_DOC, record=None)
    check(gone.code == 1 and "ENOENT" in gone.err, f"뺀 뒤 읽기 기대 ENOENT, 실제 종료 코드 {gone.code} {_gate_lines(gone.err)}")
    check(protected() == (True, True), "뺀 뒤 작업 폴더 설정의 막기가 사라짐(남아야 함)")

    freed = e.human("mount", "unprotect", MOUNT_NAME, answers=("y", "y"))
    check(freed.code == 0 and freed.prompts == 2 and "보호를 풀었어요" in freed.out,
          f"gate mount unprotect(pty) 기대 질문 2번·풂, 실제 종료 코드 {freed.code} 질문 {freed.prompts} {_short(freed.out)}")
    check(protected() == (False, False), f"보호를 푼 뒤에도 막기가 남음: {protected()}")
    return (f"작업 폴더 등록, 터미널 없이 add 거부, add(pty y·y) → 막기(규칙 4줄 + denyRead)·세션 다시 시작 안내, "
            f"다시 띄우지 않고 ls에 /{MOUNT_NAME}, {Path(MOUNT_DOC).name} {label_summary(f.out)}·누출 0, .env 경로 규칙 EACCES, "
            f"감사 기록은 게이트 안 경로만, remove → 안 보임·ENOENT·막기 남음, unprotect(y·y) → 막기 빠짐")


def s_encoding(e: E2E) -> str:
    folder = e.source / "인코딩"
    folder.mkdir(exist_ok=True)
    utf16 = f"password: {e.secrets[0]}\nAPI_TOKEN={e.secrets[1]}\n".encode("utf-16")
    (folder / "설정.txt").write_bytes(utf16)
    korean = (PROJECT_ROOT / ENC_KOREAN_SAMPLE).read_text(encoding="utf-8")
    (folder / "상담.txt").write_bytes(korean.encode("cp949"))
    (folder / "사진.png").write_bytes(ENC_BINARY)

    f = e.read(ENC_SECRET_DOC)
    check(f.code == 0, f"{ENC_SECRET_DOC}: 기대 가림, 실제 종료 코드 {f.code} {_gate_lines(f.err)}")
    check(f.data.startswith(b"\xff\xfe"), f"{ENC_SECRET_DOC}: UTF-16 BOM이 없음(원래 인코딩으로 돌려주지 않음)")
    shown = f.data.decode("utf-16")
    check(not leaks(shown, e.secrets), f"{ENC_SECRET_DOC}: 비밀값이 남음")
    check(shown.startswith("password: *") and "API_TOKEN=**-***-" in shown, f"{ENC_SECRET_DOC}: 가린 모양이 다름: {shown!r}")
    check("같은 길이의 *로" in f.notice, f"{ENC_SECRET_DOC}: 안내 줄 {f.notice!r}")

    g = e.read(ENC_KOREAN_DOC)
    check(g.code == 0, f"{ENC_KOREAN_DOC}: 기대 가림, 실제 종료 코드 {g.code} {_gate_lines(g.err)}")
    try:
        text = g.data.decode("cp949")
    except UnicodeDecodeError:
        raise Fail(f"{ENC_KOREAN_DOC}: EUC-KR로 읽히지 않음(원래 인코딩으로 돌려주지 않음)") from None
    values = [m.text for m in e.must_hide.get(ENC_KOREAN_SAMPLE, [])]
    leaked = leaks(text, values)
    check("[이름]" in text and not leaked, f"{ENC_KOREAN_DOC}: [이름] {'있음' if '[이름]' in text else '없음'}, 누출 {leaked}")

    b = e.read(ENC_BINARY_DOC, on_pending="approve")
    check(b.pending_id, f"{ENC_BINARY_DOC}: 기대 사람 승인, 실제 바로 끝남(종료 코드 {b.code}) {_gate_lines(b.err)}")
    check("바이너리" in b.listing, f"{ENC_BINARY_DOC}: 승인 목록 이유에 '바이너리' 없음: {_short(b.listing)}")
    check(b.code == 0 and b.data == ENC_BINARY, f"{ENC_BINARY_DOC}: 승인 뒤 원본 그대로 기대, 실제 종료 코드 {b.code}")
    return (f"UTF-16 설정 비밀값 2개 * 가림·BOM 그대로, EUC-KR 상담 {label_summary(text)}·누출 0/{len(values)}·EUC-KR 그대로, "
            f"PNG 사람 승인(이유 '바이너리') → 승인 → 원본 {len(ENC_BINARY)}바이트")


def s_audit(e: E2E) -> str:
    if not e.expected["main"]:  # --only audit만 돌릴 때도 볼 기록을 만든다
        e.read(ALLOW_DOCS[0])
        e.read(ENV_DOC)
        e.expected["main"][-1]["stage"] = "path"
    notes = []
    for name, log in (("main", e.audit), ("ner-down", e.audit2)):
        if not e.expected[name]:
            continue
        records = e.audit_records(log)
        matched, problems = match_records(e.expected[name], records)
        check(not problems, f"{name} 감사 로그: " + "; ".join(problems[:4]))
        notes.append(f"{'게이트' if name == 'main' else 'NER 끊긴 데몬'} 기록 {len(records)}건 중 기대 {matched}건 일치")
    for log in (e.audit, e.audit2):
        if log.is_file():  # README "감사 로그": 파일 0600, 폴더 0700
            modes = (oct(log.stat().st_mode & 0o777), oct(log.parent.stat().st_mode & 0o777))
            check(modes == ("0o600", "0o700"), f"{log} 권한 기대 (0o600, 0o700), 실제 {modes}")
    files = [p for p in (e.audit, e.audit2, *e.logs) if p.is_file()]
    texts = {p: p.read_text("utf-8", "replace") for p in files}
    values = sorted({v for p in e.all_paths() for v in e.hidden_values(p)})
    lines = sorted({line.strip() for p in e.all_paths()
                    for line in e.original(p).decode("utf-8").splitlines() if len(line.strip()) >= MIN_LINE_CHARS})
    found = [(p.name, v) for p, text in texts.items() for v in [*values, *lines] if v in text]
    check(not found, f"로그에 원문·값이 남음: {found[:4]}")
    audit_files = sum(1 for p in files if p.suffix == ".jsonl")
    return (", ".join(notes) + f"; 가릴 값 {len(values)}개·원문 줄 {len(lines)}개가 감사 로그 {audit_files}개(0600)·"
            f"데몬·화면 로그 {len(files) - audit_files}개 어디에도 없음")


def s_ui(e: E2E) -> str:
    log = e.root / "ui.log"
    with open(log, "ab") as handle:  # 화면 서버의 거부 로그(경로·토큰 없음). 누출 검사에도 넣는다
        proc = subprocess.Popen(
            e.cmd("--socket", str(e.socket), "ui", "--no-browser", "--port", "0", "--policy", str(e.policy),
                  "--audit", str(e.audit)),
            env=e.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=handle, start_new_session=True)
    e.procs.append(proc)
    e.logs.append(log)
    found: list[re.Match] = []
    ready = threading.Event()

    def watch() -> None:
        for raw in proc.stdout:
            if not found and (m := UI_URL.search(raw.decode("utf-8", "replace"))):
                found.append(m)
                ready.set()
        ready.set()

    threading.Thread(target=watch, daemon=True).start()
    try:
        ready.wait(START_TIMEOUT)
        check(found, f"gate ui가 {START_TIMEOUT:g}초 안에 주소를 내지 않음 (종료 코드 {proc.poll()})")
        port, launch = found[0].group(1), found[0].group(2)
        base = f"http://127.0.0.1:{port}"
        status, _, body = http_get(f"{base}/api/status")
        check(status == 403 and "bad-token" in body, f"토큰 없이 기대 403 bad-token, 실제 {status} {_short(body, 80)}")
        status, _, body = http_get(f"{base}/api/status", {"X-Gate-Token": "not-a-session"})
        check(status == 403 and "bad-token" in body, f"틀린 토큰 기대 403, 실제 {status}")
        status, headers, page = http_get(f"{base}/?launch={launch}")
        check(status == 200, f"일회용 주소 기대 200, 실제 {status}")
        session = SESSION_META.search(page)
        check(session, "페이지에 세션 토큰이 없음")
        cookie = headers.get("Set-Cookie") or ""
        check(f"gate_ui_{port}=" in cookie and "HttpOnly" in cookie, f"세션 쿠키가 없음: {cookie}")
        status, _, _ = http_get(f"{base}/?launch={launch}")
        check(status == 403, f"같은 일회용 주소 다시 열기 기대 403, 실제 {status}")
        status, _, body = http_get(f"{base}/api/status", {"X-Gate-Token": session.group(1)})
        check(status == 200, f"토큰으로 상태 API 기대 200, 실제 {status} {_short(body, 80)}")
        data = json.loads(body)
        state = (data.get("gate", {}).get("state"), data.get("laya", {}).get("state"), data.get("ner", {}).get("state"))
        check(data.get("ok") and data.get("policy_ok") and state == ("on", "connected", "connected"),
              f"상태 기대 게이트 on·Laya connected·NER connected, 실제 {state} (policy_ok {data.get('policy_ok')}, "
              f"Laya {data.get('laya', {}).get('reason')}, NER {data.get('ner', {}).get('reason')})")
        check(data["laya"].get("backend") == "laya" and data["ner"].get("enabled") is True,
              f"정책 기대 laya·NER 켬, 실제 {data['laya'].get('backend')}·{data['ner'].get('enabled')}")
        status, _, body = http_get(f"{base}/api/audit?period=all", {"X-Gate-Token": session.group(1)})
        audit = json.loads(body) if status == 200 else {}
        check(audit.get("available"), f"감사 기록 API 기대 available, 실제 {status} {_short(body, 80)}")
        status, _, body = http_get(f"{base}/api/mounts", {"X-Gate-Token": session.group(1)})
        mounts = json.loads(body) if status == 200 else {}
        check(mounts.get("ok") and mounts.get("error") is None and isinstance(mounts.get("folders"), list),
              f"원본 폴더 API 기대 200·오류 없음, 실제 {status} {_short(body, 80)}")
        return (f"토큰 없음·틀린 토큰 403, 일회용 주소 재사용 403, 토큰으로 상태: 게이트 on·Laya connected"
                f"({data['laya'].get('model')})·NER connected, 감사 기록 {audit.get('total')}건 보임, "
                f"원본 폴더 {len(mounts['folders'])}줄")
    finally:
        kill(proc)
        if proc in e.procs:
            e.procs.remove(proc)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    samples: str
    run: Callable[[E2E], str]


def _names(*paths: str) -> str:
    return ", ".join(p.lstrip("/") for p in paths)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("ls", "목록·stat", "/, medical_note/, " + _names(CACHE_DOC), s_ls),
    Scenario("allow", "허용", _names(*ALLOW_DOCS), s_allow),
    Scenario("mask", "가림·누출 0·안내 줄", _names(*MASK_DOCS), s_mask),
    Scenario("number", "번호 같은 길이 *", _names(NUMBER_DOC), s_number),
    Scenario("host", "IP·내부 주소 (NER 켬·끔)", _names(HOST_DOC), s_host),
    Scenario("path", "경로 규칙 .env", _names(ENV_DOC), s_path),
    Scenario("approve", "사람 승인 허용", _names(APPROVE_DOC), s_approve),
    Scenario("deny", "사람 승인 거부", _names(DENY_DOC), s_deny),
    Scenario("timeout", "승인 시간 초과", _names(TIMEOUT_DOC), s_timeout),
    Scenario("search", "검색", f"'{SEARCH_WORD}' in /", s_search),
    Scenario("policy", "정책 문장·undo", _names(POLICY_DOC), s_policy),
    Scenario("cache", "캐시", _names(CACHE_DOC), s_cache),
    Scenario("ner-down", "NER 서버 응답 없음", _names(NER_DOWN_DOC), s_ner_down),
    Scenario("mount", "원본 폴더 추가·빼기", _names(MOUNT_DOC, MOUNT_ENV), s_mount),
    Scenario("encoding", "UTF-16·EUC-KR·바이너리", _names(ENC_SECRET_DOC, ENC_KOREAN_DOC, ENC_BINARY_DOC), s_encoding),
    Scenario("ui", "설정 화면", "gate ui", s_ui),
    Scenario("audit", "감사 로그 대조·누출", "audit.jsonl ×2, 데몬·화면 로그", s_audit),  # 마지막: 앞 시나리오의 기록을 모두 본다
)


@dataclass
class Result:
    scenario: Scenario
    outcome: str
    evidence: str
    seconds: float
    notes: list[str] = field(default_factory=list)


def select_scenarios(only: str | None) -> list[Scenario]:
    if not only:
        return list(SCENARIOS)
    wanted = [x.strip() for x in only.split(",") if x.strip()]
    known = {s.id for s in SCENARIOS}
    unknown = [x for x in wanted if x not in known]
    if unknown:
        raise ValueError(f"모르는 시나리오: {', '.join(unknown)} (있는 것: {', '.join(s.id for s in SCENARIOS)})")
    return [s for s in SCENARIOS if s.id in wanted]


def run_scenario(e: E2E, scenario: Scenario) -> Result:
    started = time.monotonic()
    try:
        evidence = scenario.run(e)
        outcome = PASS
    except Fail as exc:
        outcome, evidence = FAIL, str(exc)
    except Skip as exc:
        outcome, evidence = SKIP, str(exc)
    except Exception as exc:  # 시험 코드 오류도 다른 시나리오와 정리를 놓치지 않게 실패로 적는다
        outcome, evidence = FAIL, f"시험 오류: {type(exc).__name__}: {exc}"
    result = Result(scenario, outcome, evidence, time.monotonic() - started)
    if e.policy_changed():
        e.restore_policy()
        result.notes.append("시나리오가 남긴 정책 바꿈을 시작 정책으로 되돌림")
    return result


def format_report(results: Sequence[Result], settings: Settings, root: Path, elapsed: float,
                  interrupted: bool) -> str:
    lines = [f"실제 모델 e2e 시험: Laya {settings.jev_url} ({settings.jev_model}), NER {settings.ner_url}",
             f"데이터 {root}, 시나리오 {len(results)}개, {elapsed:.1f}초", ""]
    rows = []
    for r in results:
        evidence = r.evidence + "".join(f" [{n}]" for n in r.notes)
        rows.append([r.scenario.id, r.scenario.title, r.outcome, r.scenario.samples, f"{r.seconds:.1f}", evidence])
    lines += _table(["시나리오", "내용", "결과", "쓴 샘플", "초", "근거"], rows, align="llllrl")
    counts = Counter(r.outcome for r in results)
    lines += ["", f"통과 {counts[PASS]}, 실패 {counts[FAIL]}, 뺌 {counts[SKIP]}" + (" (중단됨)" if interrupted else "")]
    return "\n".join(lines)


def _on_term(signum, frame) -> None:
    raise KeyboardInterrupt


def gate_command() -> list[str]:
    """지금 파이썬 옆의 gate(uv sync가 만든 .venv/bin/gate)."""
    return [str(Path(sys.executable).with_name("gate"))]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run_e2e",
                                     description="실제 Laya·NER 서버로 게이트 데몬·CLI·승인·감사 로그·설정 화면을 엮어 시험한다")
    parser.add_argument("--keep", action="store_true", help="끝난 뒤 임시 폴더를 지우지 않고 경로를 알린다")
    parser.add_argument("--only", help=f"돌릴 시나리오 (쉼표로 구분): {', '.join(s.id for s in SCENARIOS)}")
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SECONDS, help="서버 /health를 기다릴 최대 초 (기본 300)")
    args = parser.parse_args(argv)
    try:
        scenarios = select_scenarios(args.only)
    except ValueError as exc:
        parser.error(str(exc))

    settings = Settings.from_env(os.environ)
    gate_bin = gate_command()
    problems = preflight(settings, args.wait, gate_bin)
    if problems:
        for p in problems:
            print(f"e2e: 시작하지 않습니다: {p}", file=sys.stderr)
        return 3

    started = time.monotonic()
    root = Path(tempfile.mkdtemp(prefix="ge2e-", dir="/tmp"))
    if root.resolve().is_relative_to(REAL_DATA.resolve()):  # 일어나지 않지만, 실제 데모 폴더는 절대 쓰지 않는다
        print(f"e2e: 임시 폴더가 실제 데모 폴더 안입니다: {root}", file=sys.stderr)
        return 3
    e = E2E(root, settings, gate_bin)
    results: list[Result] = []
    interrupted = False
    previous = signal.signal(signal.SIGTERM, _on_term)
    try:
        e.create()
        try:
            e.start_daemon(e.socket, e.env, root / "serve.log")
        except RuntimeError as exc:
            print(f"e2e: 시작하지 않습니다: {exc}", file=sys.stderr)
            return 3
        print(f"e2e: 데이터 {root}, 데몬 {e.socket}", file=sys.stderr, flush=True)
        for scenario in scenarios:
            result = run_scenario(e, scenario)
            results.append(result)
            print(f"[{scenario.id}] {result.outcome} ({result.seconds:.1f}초) {_short(result.evidence, 200)}",
                  file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        # 정리하는 동안 Ctrl-C·SIGTERM이 또 와도 끝까지 정리한다(데몬을 끄고 폴더를 지움)
        previous_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            e.stop_all()
            if args.keep:
                print(f"e2e: 임시 폴더를 남겼습니다: {root}", file=sys.stderr)
            else:
                shutil.rmtree(root, ignore_errors=True)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous)
    print(format_report(results, settings, root, time.monotonic() - started, interrupted))
    if interrupted:
        return 130
    return 1 if any(r.outcome == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
