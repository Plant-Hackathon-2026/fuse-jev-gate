"""설정 화면(gate ui)의 자료와 바꾸기. HTTP와 상관없는 부분이다(gate.ui가 HTTP로 감싸고 토큰·Host·Origin을 검사한다).

읽기
  state()   지금 정책(번호·비밀값 8줄, NER 묶음 9줄), 내가 정한 항목, 기본값, 정규식 정보("찾는 규칙 보기").
            NER 묶음 중 host(IP·내부 주소)는 규칙이 찾아 NER을 꺼도 적용된다(rule: true). 뺀 묶음(날짜·사건)은 줄이 없다
  status()  게이트 데몬·판단 모델(Laya)·NER 서버 상태
  audit()   감사 기록(기간·결과·파일 이름으로 거르기)
바꾸기 (계산·완화 판단·되돌리기 기록·0600 쓰기는 모두 gate.policy_change. `gate policy`와 같은 파일·기록을 만든다)
  interpret(문장) → save(표), set_choice(키, 값), remove(번호, 지문), undo(), ner(켜기·끄기)
  - 느슨해지는 바꿈(gate.policy.loosened)과 잘못된 정책 파일을 기록으로 덮는 undo는 확인 값(confirm)이 맞을 때만 쓴다.
    확인 값이 없거나 틀리면 쓰지 않고 {"status": "confirm", "confirm": 값, "loosening": [...]}를 돌려준다.
    화면은 내용을 보여 주고 사람이 한 번 더 누르면 그 값을 실어 다시 보낸다(gate.policy_change.Change.confirm_token).
  - 해석 결과는 서버가 표(ticket)로 들고 있다. 저장할 때 화면이 항목 내용을 보내지 않는다(본 그대로 저장).
  - 화면에서 바로 고르는 것(set_choice)은 번호·비밀값(numbers.*)과 NER 묶음(entities.*.action)만 받는다.
    NER 켜기·끄기(ner_backend)는 ner()로만 바꾼다. 켜기는 NER 서버 /health가 응답할 때만 쓴다(화면을 거치지 않은 요청도 같다).
    응답하지 않는 서버를 켜면 게이트가 모든 읽기를 막기 때문이다. 서버를 띄우는 일은 하지 않는다. 끄기는 검사 없이 된다
    (느슨해지는 바꿈이라 확인은 받는다).

상태 확인
  - 판단 모델·NER 서버 /health는 이 컴퓨터의 IP 주소(127.0.0.1 같은 loopback IP)에만 묻는다. 이름(localhost 등)은 묻지 않는다.
    주소는 이 프로세스의 JEV_BASE_URL·NER_BASE_URL(데몬과 같은 이름), 없으면 127.0.0.1:8000·127.0.0.1:8001이다.
  - 게이트 데몬은 소켓에 연결만 해 보고 바로 끊는다(요청을 보내지 않아 감사 로그에 남지 않는다). 소켓 경로를 모르면 "확인할 수 없음".
  - 마지막 요청 시각은 감사 로그의 마지막 기록에서 구한다. 데몬이 정책 파일을 언제 다시 읽었는지는 알 수 없어서,
    정책 파일을 바꾼 시각만 보여 준다(데몬은 다음 요청 때 바뀐 것을 보고 다시 읽는다).
"""

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from gate import audit_cli
from gate.approval import clean_text, kinds_text
from gate.detectors import KINDS, PATTERNS, VISIBLE_CHOICES, mask
from gate.jev_client import LAYA_DEFAULT_MODEL
from gate.ner_client import DEFAULT_BASE_URL, ENTITY_GROUPS, ENTITY_NAMES, RULE_GROUPS
from gate.policy import (
    KIND_NAMES,
    VISIBLE_NAMES,
    Entry,
    Policy,
    PolicyError,
    build_policy,
    default_settings,
    describe_setting,
    is_retired,
    read_entries,
    setting_choices,
)
from gate.policy_change import (
    Change,
    ChangeError,
    apply,
    history,
    needs_ner,
    plan_add,
    plan_remove,
    plan_undo,
    read_current,
    setting_entry,
    stamped,
)
from gate.policy_text import PolicyInterpreter, interpret, make_interpreter

DEFAULT_JEV_URL = "http://127.0.0.1:8000"
DEFAULT_NER_URL = DEFAULT_BASE_URL  # 데몬(gate.ner_client)과 같은 기본 주소
HEALTH_TIMEOUT_SECONDS = 1.5
AUDIT_LIMIT = 200
TICKET_SECONDS = 30 * 60
MAX_TICKETS = 32
_MAX_HEALTH_BYTES = 64 * 1024
_TAIL_BYTES = 64 * 1024

# 화면의 예시 문장 버튼. 모두 Mock 해석기가 알아듣는 문장이다(tests/test_ui.py가 확인한다).
EXAMPLE_SENTENCES = ("이름은 가려줘", "카드번호 있는 파일은 거부", "주민번호는 생년월일까지만 보여줘",
                     "학번이 있는 파일은 물어보고 보여줘")


@dataclass(frozen=True)
class RuleText:
    """"찾는 규칙 보기"의 쉬운 설명. 정규식 자체는 gate.detectors.PATTERNS에서 읽는다."""
    text: str
    example: str     # 예시 입력. 가린 모양은 gate.detectors.mask로 그때그때 만든다
    checks: str      # 정규식 밖에서 코드가 더 확인하는 것
    lock_reason: str = ""  # 공개 범위가 하나뿐인 종류(gate.detectors.VISIBLE_CHOICES)에서 그 이유


RULES: dict[str, RuleText] = {
    "rrn": RuleText("6자리 + (- 또는 공백 또는 없음) + 7자리.", "900101-1234567",
                    "앞 6자리가 실제 날짜이고 오늘 이후가 아님 · 7번째 자리가 1~4·9·0"),
    "foreigner_rrn": RuleText("주민등록번호와 같은 모양이고 7번째 자리가 5~8.", "900101-5234567",
                              "앞 6자리가 실제 날짜이고 오늘 이후가 아님 · 7번째 자리가 5~8"),
    "card": RuleText("13~19자리. 붙여 쓰거나 4-4-4-4, 4-6-5, 4-6-4로 끊어 쓴 것.", "4111 1111 1111 1111",
                     "카드번호 검사 숫자(Luhn)가 맞음 · 날짜(YYYYMMDD)로 시작하는 숫자는 뺌"),
    "mobile": RuleText("010·011·016~019(또는 +82 10) + 3~4자리 + 4자리.", "010-1234-5678",
                       "구분자 - . 공백은 있어도 없어도 됨 · 앞뒤에 숫자가 더 붙어 있으면 뺌"),
    "phone": RuleText("지역번호(02, 031~033, 041~044, 051~055, 061~064)나 070 + 3~4자리 + 4자리.", "02-123-4567",
                      "(02)처럼 괄호를 쳐도 됨 · 15xx·16xx·18xx 대표번호, 080, 050x 안심번호는 뺌"),
    "email": RuleText("영문 이메일 주소.", "hong@example.com",
                      "최상위 도메인이 영문 2자 이상 · 뒤에 붙은 한국어 조사는 경계로 봄"),
    "account": RuleText("10~14자리(붙여 쓰거나 - 로 2~4묶음).", "신한 110-123-456789",
                        "앞 20자·뒤 10자 안에 \"계좌·입금·은행\" 같은 말이 있을 때만 · 금액(원·₩)이나 날짜로 시작하는 숫자는 뺌",
                        "공개 범위를 고를 수 없게 제품에 고정한 안전 규칙이에요. 계좌번호는 일부만 보여 주는 방식을 두지 않았어요."),
    "secret": RuleText("형식이 알려진 키·토큰, 개인 키 블록, URL 속 비밀번호, 이름이 password·token·api_key 등으로 끝나는 키의 값.",
                       "password: hunter2",
                       "빈 값·null·true 같은 값, $·{{ }} 같은 자리표시자, 이미 가린 값은 뺌 · 키 이름과 구조는 남기고 값만 가림",
                       "공개 범위를 고를 수 없게 제품에 고정한 안전 규칙이에요. 비밀값은 일부라도 보이면 안 되니 값 전체를 가려요."),
}

# NER 묶음 아래 작은 설명(이름만으로 뜻이 덜 드러나는 것). host는 규칙이 찾는다는 점을 알린다.
ENTITY_HINTS = {
    "name": "본인·가족·상담사·교수 이름",
    "org": "학교·학과·회사·병원·동아리 이름",
    "host": "규칙으로 찾아요 · NER을 꺼도 적용돼요",
}

APPROVAL_LABELS = {"once": "이번만 허용", "session": "이 폴더 허용", "remembered": "폴더 승인 기억",
                   "deny": "거부함", "timeout": "답이 없어 거부"}
CATEGORIES = ("allow", "mask", "ask", "deny")


class UiError(Exception):
    """화면에 돌려줄 실패. status는 HTTP 상태, code는 짧은 영문 이름, message는 사용자에게 보여 줄 말."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass
class UiConfig:
    policy: Path
    audit: Path | None = None
    socket: Path | None = None
    jev_url: str = DEFAULT_JEV_URL
    ner_url: str = DEFAULT_NER_URL
    jev_model: str = LAYA_DEFAULT_MODEL
    health_timeout: float = HEALTH_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls, policy: str | os.PathLike, audit: str | os.PathLike | None, socket_path: str | os.PathLike | None,
                 env: Mapping[str, str] | None = None) -> "UiConfig":
        env = os.environ if env is None else env
        return cls(
            Path(policy).expanduser().absolute(),
            Path(audit).expanduser().absolute() if audit else None,
            Path(socket_path).expanduser().absolute() if socket_path else None,
            (env.get("JEV_BASE_URL") or "").strip() or DEFAULT_JEV_URL,
            (env.get("NER_BASE_URL") or "").strip() or DEFAULT_NER_URL,
            (env.get("JEV_MODEL") or "").strip() or LAYA_DEFAULT_MODEL,
        )


# --- 도움 함수 ---

def _iso_mtime(path: Path | None) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds") if path else None
    except OSError:
        return None


def fingerprint(entry: Entry) -> str:
    """항목 지문. 지우기 요청이 화면에서 본 그 항목인지 맞춰 본다(그사이 목록이 바뀌었으면 지우지 않는다)."""
    raw = json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _has_batchim(word: str) -> bool:
    """마지막 한글 글자에 받침이 있는가. 괄호 설명은 빼고 본다("소속(학교·회사·병원 등)" → "소속")."""
    base = word.split("(", 1)[0].strip()
    for ch in reversed(base):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isalnum():
            return False
    return False


def _josa(word: str, with_batchim: str, without: str) -> str:
    return word + (with_batchim if _has_batchim(word) else without)


def _short_name(key: str) -> str:
    parts = key.split(".")
    if parts[0] == "numbers":
        return KIND_NAMES[parts[1]]
    if parts[0] == "entities":
        return ENTITY_NAMES[parts[1]]
    return key


def effect_text(key: str, value: object) -> str:
    """설정 하나가 하는 일을 화면 말투로 쓴다("이렇게 이해했어요" 상자의 설명)."""
    name = _short_name(key)
    if key.startswith("numbers.") and key.endswith(".visible"):
        if value == "none":
            return f"{_josa(name, '은', '는')} 전부 가려요."
        return f"{_josa(name, '을', '를')} 가릴 때 {VISIBLE_NAMES[value]}해요."
    if key.startswith("numbers."):
        return {
            "deny": f"{_josa(name, '이', '가')} 있는 파일은 읽지 못하게 해요.",
            "ask_human": f"{_josa(name, '이', '가')} 있는 파일은 사람에게 물어본 뒤 보여줘요. "
                         f"허용해도 {_josa(name, '은', '는')} 가린 채로 보여줘요.",
            "mask": f"{_josa(name, '을', '를')} 가린 뒤 보여줘요.",
        }[value]
    if key.startswith("entities."):
        rule = key.split(".")[1] in RULE_GROUPS  # IP·내부 주소는 규칙이 찾는다
        finder, sample = ("규칙이", "[IP]") if rule else ("NER이", "[이름]")
        return {
            "deny": f"{finder} {_josa(name, '을', '를')} 찾은 파일은 읽지 못하게 해요.",
            "ask_human": f"{_josa(name, '이', '가')} 있는 파일은 사람에게 물어본 뒤 보여줘요. "
                         f"허용해도 {_josa(name, '은', '는')} 가린 채로 보여줘요.",
            "mask": f"{_josa(name, '을', '를')} {sample}처럼 종류 이름으로 바꿔 보여줘요.",
            "allow": f"{_josa(name, '을', '를')} 가리지 않고 그대로 보여줘요. "
                     "판단 모델이 문서를 민감하다고 보면 묻거나 막을 수 있어요.",
        }[value]
    if key == "ner_backend":
        return "이름·주소·소속도 NER로 찾아서 가려요." if value == "server" else "NER을 쓰지 않아요. 번호·비밀값은 그대로 가려요."
    return describe_setting(key, value)


# --- 상태 확인 ---

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # 리디렉트를 따라가지 않는다
        return None


def loopback_base(raw: str) -> tuple[str, str]:
    """(기본 주소, 보여 줄 호스트:포트). http(s)이고 호스트가 loopback IP(127.0.0.1, ::1 등)일 때만. 아니면 ValueError."""
    parts = urllib.parse.urlsplit(raw.strip())
    host = parts.hostname
    parts.port  # 잘못된 포트면 ValueError
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password \
            or parts.query or parts.fragment:
        raise ValueError("http(s)://IP:포트 모양이어야 해요")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("이 컴퓨터의 IP 주소(127.0.0.1)로 적어야 해요. 이름(localhost 등)으로는 확인하지 않아요") from None
    if not ip.is_loopback:
        raise ValueError("이 컴퓨터(127.0.0.1) 주소만 확인해요")
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    return base, parts.netloc


def probe_health(raw_url: str, timeout: float = HEALTH_TIMEOUT_SECONDS) -> tuple[bool, str]:
    """서버 /health가 {"status": "ok"}로 답하는가. (응답함, 이유). 프록시·리디렉트는 쓰지 않는다."""
    try:
        base, _ = loopback_base(raw_url)
    except ValueError as exc:
        return False, str(exc)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    request = urllib.request.Request(base + "/health", headers={"Accept": "application/json"}, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_HEALTH_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}로 답했어요"
    except (urllib.error.URLError, OSError):
        return False, "응답이 없어요"
    try:
        data = json.loads(raw) if len(raw) <= _MAX_HEALTH_BYTES else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return False, "답의 형식이 달라요"
    if data.get("status") != "ok":
        return False, "아직 준비되지 않았어요"
    return True, ""


def _address(raw_url: str) -> str:
    try:
        return loopback_base(raw_url)[1]
    except ValueError:
        return raw_url


def probe_socket(path: Path, timeout: float = 1.0) -> tuple[str, str]:
    """게이트 소켓에 연결만 해 본다. (on | off | unknown, 이유). 요청을 보내지 않아 감사 로그에 남지 않는다."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return "off", ""
        except OSError as exc:  # 경로가 너무 김(맥 104바이트), 권한 없음, 시간 초과 등
            return "unknown", exc.strerror or type(exc).__name__
    return "on", ""


def last_record_time(path: Path | None) -> str | None:
    """감사 로그 마지막 기록의 ts. 파일 끝 64KB만 읽는다. 없거나 읽을 수 없으면 None."""
    if path is None:
        return None
    try:
        with open(path, "rb") as file:
            size = file.seek(0, os.SEEK_END)
            file.seek(max(0, size - _TAIL_BYTES))
            tail = file.read()
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        try:
            record = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("ts"), str):
            return record["ts"]
    return None


# --- 해석 표 ---

class Tickets:
    """해석 결과(저장할 항목)를 잠깐 들고 있는다. 오래되거나 많으면 버린다."""

    def __init__(self, seconds: float = TICKET_SECONDS, limit: int = MAX_TICKETS) -> None:
        self.seconds, self.limit = seconds, limit
        self._items: OrderedDict[str, tuple[float, Entry]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, entry: Entry) -> str:
        ticket = secrets.token_urlsafe(16)
        with self._lock:
            self._expire()
            self._items[ticket] = (time.monotonic(), entry)
            while len(self._items) > self.limit:
                self._items.popitem(last=False)
        return ticket

    def get(self, ticket: object) -> Entry | None:
        if not isinstance(ticket, str):
            return None
        with self._lock:
            self._expire()
            item = self._items.get(ticket)
        return item[1] if item else None

    def drop(self, ticket: str) -> None:
        with self._lock:
            self._items.pop(ticket, None)

    def _expire(self) -> None:
        limit = time.monotonic() - self.seconds
        for key in [k for k, (at, _) in self._items.items() if at < limit]:
            del self._items[key]


# --- 앱 ---

@dataclass
class UiApp:
    config: UiConfig
    interpreter: PolicyInterpreter | None = None
    tickets: Tickets = field(default_factory=Tickets)
    _write: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.interpreter is None:
            self.interpreter = make_interpreter()  # 기본은 키워드 Mock(바꾸지 않는다)

    # ----- 읽기 -----

    def state(self) -> dict:
        path = self.config.policy
        info = {"path": str(path), "exists": path.exists(), "changed_at": _iso_mtime(path), "error": None}
        try:
            entries = read_entries(path)
        except PolicyError as exc:
            info["error"] = str(exc)
            return {"ok": True, "invalid": True, "policy_file": info, "history": len(history(path)),
                    "examples": list(EXAMPLE_SENTENCES)}
        policy = build_policy(entries)
        defaults = default_settings()
        last_set = {key: i for i, entry in enumerate(entries) for key in (entry.settings or {})}
        return {
            "ok": True, "invalid": False, "policy_file": info, "version": policy.version,
            "history": len(history(path)), "jev_backend": policy.jev_backend, "ner_backend": policy.ner_backend,
            "numbers": [self._number_row(kind, policy, defaults, last_set) for kind in KINDS],
            "entities": [self._entity_row(group, policy, defaults, last_set) for group in ENTITY_GROUPS],
            "entries": [self._entry_row(i, entry, policy, last_set) for i, entry in enumerate(entries)],
            "examples": list(EXAMPLE_SENTENCES),
        }

    @staticmethod
    def _number_row(kind: str, policy: Policy, defaults: dict, last_set: dict) -> dict:
        action_key, visible_key = f"numbers.{kind}.action", f"numbers.{kind}.visible"
        choices = VISIBLE_CHOICES[kind]
        locked = len(choices) == 1
        rule = RULES[kind]
        visible = policy.visible[kind]
        return {
            "kind": kind, "name": KIND_NAMES[kind],
            "action": {"key": action_key, "value": policy.number_rule(kind), "default": defaults[action_key],
                       "choices": list(setting_choices(action_key)), "mine": action_key in last_set},
            "visible": {"key": visible_key, "value": visible, "default": defaults[visible_key], "locked": locked,
                        "choices": [{"value": v, "label": VISIBLE_NAMES[v]} for v in choices],
                        "mine": visible_key in last_set},
            "rule": {
                "text": rule.text, "example": rule.example,
                "example_masked": mask(rule.example, visible={kind: visible}).text,
                "patterns": [{"name": name, "pattern": pattern.pattern} for name, pattern in PATTERNS[kind]],
                "checks": rule.checks, "lock_reason": rule.lock_reason if locked else "",
            },
        }

    @staticmethod
    def _entity_row(group: str, policy: Policy, defaults: dict, last_set: dict) -> dict:
        key = f"entities.{group}.action"
        return {"group": group, "name": ENTITY_NAMES[group], "hint": ENTITY_HINTS.get(group, ""), "key": key,
                "value": policy.entity_rule(group), "default": defaults[key], "choices": list(setting_choices(key)),
                "mine": key in last_set, "rule": group in RULE_GROUPS}

    @staticmethod
    def _entry_row(index: int, entry: Entry, policy: Policy, last_set: dict) -> dict:
        overridden = [k for k in (entry.settings or {}) if last_set[k] != index]
        if entry.settings is not None:
            kind, result = "setting", " · ".join(describe_setting(k, v) for k, v in entry.settings.items())
        elif entry.context is not None:
            kind, result = "context", "맥락 규칙 · 판단 모델이 아직 읽지 않아요"
        else:
            kind = "path"
            result = f"경로 규칙 · {entry.path_rule.pattern} → {describe_path_action(entry.path_rule.action)}"
        no_effect = ""
        if entry.settings and all(is_retired(k) for k in entry.settings):
            no_effect = "지금은 찾지 않는 정보"
        elif overridden:
            no_effect = "뒤에 정한 것이 덮어씀"
        elif kind == "context":
            no_effect = "지금은 효과 없음"
        elif entry.settings and any(needs_ner(k) for k in entry.settings) and policy.ner_backend == "none":
            no_effect = "NER을 켜야 효과 있음"
        return {"number": index + 1, "said": entry.said, "at": entry.at, "kind": kind, "result": result,
                "no_effect": no_effect, "fingerprint": fingerprint(entry)}

    def status(self) -> dict:
        try:
            policy: Policy | None = build_policy(read_entries(self.config.policy))
        except PolicyError:
            policy = None
        socket_path = self.config.socket
        gate_state, gate_reason = ("unknown", "") if socket_path is None else probe_socket(socket_path)
        gate = {"state": gate_state, "reason": gate_reason, "socket": str(socket_path) if socket_path else None,
                "last_request": last_record_time(self.config.audit),
                "policy_changed_at": _iso_mtime(self.config.policy)}

        jev_backend = policy.jev_backend if policy else None
        laya = {"backend": jev_backend, "address": _address(self.config.jev_url), "model": self.config.jev_model}
        if jev_backend == "laya":
            ok, reason = probe_health(self.config.jev_url, self.config.health_timeout)
            laya.update(state="connected" if ok else "down", reason=reason)
        else:
            laya.update(state="unused", reason="")

        ner_ok, ner_reason = probe_health(self.config.ner_url, self.config.health_timeout)
        enabled = bool(policy and policy.ner_backend == "server")
        ner = {"enabled": enabled, "responding": ner_ok, "reason": ner_reason, "address": _address(self.config.ner_url),
               "state": ("connected" if ner_ok else "down") if enabled else "unused"}
        return {"ok": True, "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "policy_ok": policy is not None, "gate": gate, "laya": laya, "ner": ner}

    def audit(self, period: str = "7d", category: str = "all", query: str = "", limit: int = AUDIT_LIMIT,
              moment: datetime | None = None) -> dict:
        if period not in ("today", "7d", "all"):
            raise UiError(400, "bad-period", "기간은 오늘·최근 7일·전체 중 하나예요.")
        if category not in ("all", *CATEGORIES):
            raise UiError(400, "bad-category", "결과는 전체·허용·가림·물어봄·거부 중 하나예요.")
        log = self.config.audit
        if log is None:
            return {"ok": True, "available": False, "reason": "no-path"}
        moment = moment or datetime.now().astimezone()
        since = None
        if period == "today":
            since = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        elif period == "7d":
            since = moment - timedelta(days=7)
        query = " ".join(str(query).split())[:200]
        try:
            selection = audit_cli.select(log, last=0, path=query or None, since=since)
        except FileNotFoundError:
            return {"ok": True, "available": False, "reason": "missing", "path": str(log)}
        except OSError:
            return {"ok": True, "available": False, "reason": "unreadable", "path": str(log)}
        rows, hidden = [], 0
        for entry in selection.entries:
            row = audit_row(entry.record)
            if row is None:
                hidden += 1
            else:
                rows.append(row)
        counts = Counter(row["category"] for row in rows)
        chosen = [row for row in rows if category == "all" or row["category"] == category]
        return {"ok": True, "available": True, "path": str(log), "total": selection.total,
                "counts": {c: counts.get(c, 0) for c in CATEGORIES}, "hidden": hidden,
                "broken": len(selection.broken), "matched": len(chosen), "limit": limit,
                "records": list(reversed(chosen[-limit:]))}

    # ----- 바꾸기 -----

    def interpret(self, sentence: object) -> dict:
        if not isinstance(sentence, str):
            raise UiError(400, "bad-sentence", "정책 문장을 글로 적어 주세요.")
        result = interpret(sentence, self.interpreter)
        payload: dict = {"ok": True, "outcome": result.outcome, "sentence": result.sentence, "message": result.message,
                         "saveable": result.saveable}
        if result.entry is None:
            return payload
        try:
            entries = read_current(self.config.policy)
        except ChangeError as exc:
            raise UiError(409, "policy-invalid", str(exc)) from exc
        change = _plan_add(self.config.policy, entries, result.entry)
        notes = [n for n in change.notes() if n != "context_mock"]
        if result.entry.context is not None:
            notes.insert(0, "context_unused")  # Mock·Laya 모두 맥락 규칙을 읽지 않는다(plans/implementation-plan.md 6절)
        payload.update(ticket=self.tickets.put(result.entry), preview=_preview(change), notes=notes,
                       loosening=list(change.loosening))
        return payload

    def save(self, ticket: object, confirm: object = None) -> dict:
        with self._write:  # 같은 표로 두 번 저장하지 않게 표 확인부터 잠근다
            entry = self.tickets.get(ticket)
            if entry is None:
                raise UiError(410, "ticket-expired", "해석한 지 오래되어 저장하지 않았어요. 문장을 다시 해석해 주세요.")
            result = self._commit(_plan_add(self.config.policy, self._entries(), stamped(entry)), confirm)
            if result["status"] == "saved":
                self.tickets.drop(ticket)
        return result

    def set_choice(self, key: object, value: object, confirm: object = None) -> dict:
        if not isinstance(key, str) or not _choosable(key):
            raise UiError(400, "bad-key", "화면에서는 번호·비밀값과 이름·주소 같은 정보의 처리만 고를 수 있어요. "
                                          "다른 설정은 터미널에서 gate policy set으로 바꿔 주세요.")
        if not isinstance(value, str):
            raise UiError(400, "bad-value", "고를 수 없는 값이에요.")
        with self._write:
            entries = self._entries()
            if build_policy(entries).settings[key] == value:
                return {"ok": True, "status": "unchanged"}
            try:
                entry = setting_entry(key, [value])
            except ChangeError as exc:
                raise UiError(400, "bad-value", str(exc)) from exc
            return self._commit(_plan_add(self.config.policy, entries, entry, "set"), confirm)

    def remove(self, number: object, expected: object, confirm: object = None) -> dict:
        if isinstance(number, bool) or not isinstance(number, int):
            raise UiError(400, "bad-number", "지울 항목 번호가 올바르지 않아요.")
        with self._write:
            entries = self._entries()
            if not 1 <= number <= len(entries) or fingerprint(entries[number - 1]) != expected:
                raise UiError(409, "stale", "그사이 목록이 바뀌었어요. 새로 불러온 목록을 보고 다시 지워 주세요.")
            return self._commit(plan_remove(self.config.policy, entries, str(number)), confirm)

    def undo(self, confirm: object = None) -> dict:
        with self._write:
            try:
                change = plan_undo(self.config.policy)
            except ChangeError as exc:
                raise UiError(409, "cannot-undo", str(exc)) from exc
            result = self._commit(change, confirm)
        if result["status"] == "saved":
            result["history_left"] = change.history_left
        return result

    def ner(self, enable: object, confirm: object = None) -> dict:
        if not isinstance(enable, bool):
            raise UiError(400, "bad-value", "켜기·끄기 값이 올바르지 않아요.")
        if enable:
            ok, reason = probe_health(self.config.ner_url, self.config.health_timeout)
            if not ok:
                raise UiError(409, "ner-down", f"NER 서버({_address(self.config.ner_url)})가 응답하지 않아 켜지 않았어요"
                                               f"({reason}). 서버를 먼저 띄운 뒤 다시 확인해 주세요.")
        target = "server" if enable else "none"
        with self._write:
            entries = self._entries()
            if build_policy(entries).ner_backend == target:
                return {"ok": True, "status": "unchanged"}
            change = _plan_add(self.config.policy, entries, setting_entry("ner_backend", [target]), "set")
            return self._commit(change, confirm)

    def _entries(self) -> tuple[Entry, ...]:
        try:
            return read_current(self.config.policy)
        except ChangeError as exc:
            raise UiError(409, "policy-invalid", str(exc)) from exc

    @staticmethod
    def _commit(change: Change, confirm: object) -> dict:
        if change.needs_confirm and not (isinstance(confirm, str)
                                         and hmac.compare_digest(confirm, change.confirm_token)):
            return {"ok": True, "status": "confirm", "confirm": change.confirm_token,
                    "loosening": list(change.loosening), "problem": change.problem}
        try:
            policy = apply(change)
        except ChangeError as exc:
            raise UiError(500, "write-failed", str(exc)) from exc
        return {"ok": True, "status": "saved", "version": policy.version}


def _plan_add(path: Path, entries, entry: Entry, action: str = "add") -> Change:
    """plan_add의 ChangeError(뺀 키 등)를 화면에 돌려줄 400으로 바꾼다."""
    try:
        return plan_add(path, entries, entry, action)
    except ChangeError as exc:
        raise UiError(400, "bad-value", str(exc)) from exc


def describe_path_action(action: str) -> str:
    return {"allow": "그대로 보여줌", "ask_human": "사람에게 물어봄", "deny": "거부"}[action]


def _choosable(key: str) -> bool:
    parts = key.split(".")
    if len(parts) != 3:
        return False
    if parts[0] == "numbers":
        return parts[1] in KINDS and parts[2] in ("action", "visible")
    return parts[0] == "entities" and parts[1] in ENTITY_GROUPS and parts[2] == "action"


def _preview(change: Change) -> dict:
    entry = change.entry
    if entry.context is not None:
        return {"type": "context", "text": entry.context,
                "effect": "파일을 보여 줄지 판단 모델이 정할 때 이 문장을 함께 보도록 남겨요."}
    (key, value), = entry.settings.items()
    before = change.before_policy.settings[key]
    group = key.split(".")[0]
    labels = VISIBLE_NAMES if key.endswith(".visible") else {}
    return {"type": "setting", "key": key, "value": value, "before": before, "group": group,
            "field": key.rsplit(".", 1)[-1], "name": _short_name(key),
            "before_label": labels.get(before, ""), "after_label": labels.get(value, ""),
            "effect": effect_text(key, value)}


def audit_category(record: Mapping) -> str | None:
    """감사 기록 하나의 화면 분류. 사람에게 물었으면 ask, 아니면 결정. 목록·정보 보기(ok)와 그 밖의 오류는 None."""
    decision = record.get("decision")
    if isinstance(record.get("approval"), str) or decision == "ask_human":
        return "ask"
    if decision in ("allow", "mask", "deny"):
        return decision
    if decision == "error" and record.get("errno") == "EACCES":
        return "deny"  # 판단 전에 거부(원본 밖 경로 등)
    return None


def audit_row(record: Mapping) -> dict | None:
    category = audit_category(record)
    if category is None:
        return None
    reason = clean_text(record.get("reason") if isinstance(record.get("reason"), str) else None)
    findings = record.get("findings")
    found = clean_text(kinds_text(findings)) if isinstance(findings, dict) and findings else ""
    if not reason and record.get("errno") == "EACCES":
        reason = "게이트가 접근을 거부했어요(원본 밖 경로 등)"
    approval = record.get("approval")
    return {
        "ts": record.get("ts") if isinstance(record.get("ts"), str) else None,
        "path": clean_text(record.get("path") if isinstance(record.get("path"), str) else None) or "?",
        "op": clean_text(record.get("op") if isinstance(record.get("op"), str) else None),
        "category": category,
        "after": APPROVAL_LABELS.get(approval, "") if isinstance(approval, str) else "",
        "reason": reason,
        "found": found,
        "process": clean_text(record.get("process") if isinstance(record.get("process"), str) else None) or "?",
    }
