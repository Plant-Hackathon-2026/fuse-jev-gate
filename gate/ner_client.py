"""NER 클라이언트 (3c, 3e): 로컬 NER 서버(nerserve)에 이름·연락처·소속 같은 개체의 위치를 묻는다.

- 서버 계약은 plans/ner-parallel.md "공통 계약"이다. 서버는 이 컴퓨터(127.0.0.1)에서만 돌고, 데몬은 표준 라이브러리 HTTP로 묻는다.
  데몬에 torch를 넣지 않으려고 판단 모델(laya-serve)과 같은 방식으로 따로 띄운다(2026-10-31 사용자 결정).
- 실패하면 차단이다. 연결 실패, 타임아웃, HTTP 오류, 응답 형식 오류, 모르는 라벨, 문서 밖 범위, 겹치는 범위는 모두 NerError다.
  받는 쪽(gate.pipeline)은 NerError든 다른 예외든 차단으로 처리한다.
- 서버에는 원문을 보낸다(정규식으로 가리기 전). 이 컴퓨터 안에서만 오가고, 정규식·규칙이 이미 찾은 범위는 받은 뒤에 뺀다.
  UTF-8이 아닌 바이트를 surrogateescape로 푼 대리 문자는 U+FFFD로 바꿔 보낸다(글자 수가 같아 위치가 그대로 맞는다).
- 이 모듈은 gate.policy를 import하지 않는다(정책이 이 모듈의 묶음 목록을 쓴다).

서버 라벨 → 게이트 종류 → 정책 칸 (plans/ner-v2-plan.md 3.1, 2026-11-01 사용자 결정)
  서버는 팀원 재학습판(xlmr-pii-ner)의 여섯 라벨만 준다. 그 밖의 라벨(기존 32종 모델 등)은 모르는 종류라 NerError(차단)다.
  ServerNerClient.find가 원문을 보고 게이트 종류로 나눈다(split_kind, 위치는 그대로). 게이트 종류마다 칸과 가림 표시가 정해진다.
  | 서버 라벨   | 나누는 기준                                  | 게이트 종류  | 정책 칸                      | 가림 표시   |
  |-------------|----------------------------------------------|--------------|------------------------------|-------------|
  | NAME        |                                              | NAME         | entities.name                | [이름]      |
  | CONTACT     | 이메일 형식                                  | EMAIL        | numbers.email                | [이메일]    |
  | CONTACT     | 숫자 7자 이상이고 숫자가 70% 이상(공백 제외)  | PHONE        | numbers.mobile·numbers.phone | [전화번호]  |
  | CONTACT     | 한글이 있음(상세 주소)                       | ADDRESS      | entities.address             | [주소]      |
  | CONTACT     | 그 밖(메신저·SNS 아이디 등)                  | MESSENGER    | entities.etc                 | [연락처]    |
  | ID_NUMBER   |                                              | ID_NUMBER    | entities.student_id          | [식별번호]  |
  | AFFILIATION |                                              | AFFILIATION  | entities.org                 | [소속]      |
  | LOCATION    |                                              | LOCATION     | entities.place               | [장소]      |
  | AGE_BIRTH   | 생년월일 단서(split_kind)가 있으면 BIRTH       | BIRTH / AGE  | entities.birth / plain       | [생년월일] / [나이] |
  | (규칙)      | IPv4·IPv6 (gate.host_rules)                  | IP_ADDRESS   | entities.host                | [IP]        |
  | (규칙)      | 내부 주소 (gate.host_rules)                  | INTERNAL_HOST| entities.host                | [내부 주소] |
  - PHONE은 값을 보고 mobile(01·+82 10로 시작)과 phone을 고른다(정규식 칸을 따른다).
  - IP·내부 주소는 NER이 아니라 규칙이 찾는다(NER을 꺼도 적용). 칸과 가림 표시는 여기서 함께 정한다.
"""

import ipaddress
import json
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from gate.ner_spans import JOSA

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_THRESHOLD = 0.5
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024

DEFAULT_BASE_URL = "http://127.0.0.1:8001"  # NER_BASE_URL이 없을 때. 설정 화면 상태 카드와 같은 기본값

# 서버가 주는 라벨. 팀원 재학습판(xlmr-pii-ner) config.json id2label 순서(O 제외, B-/I- 뗌)
NER_LABELS: tuple[str, ...] = ("NAME", "CONTACT", "ID_NUMBER", "AFFILIATION", "LOCATION", "AGE_BIRTH")
# 규칙(gate.host_rules)이 찾는 종류. NER이 꺼져 있어도 찾는다.
RULE_KINDS: tuple[str, ...] = ("IP_ADDRESS", "INTERNAL_HOST")
# 게이트 종류. 서버 라벨을 원문을 보고 나눈 것(split_kind)과 규칙 종류다. Entity.label은 이 중 하나다.
ENTITY_KINDS: tuple[str, ...] = (
    "NAME", "EMAIL", "PHONE", "ADDRESS", "MESSENGER", "ID_NUMBER", "AFFILIATION", "LOCATION", "BIRTH", "AGE",
    *RULE_KINDS,
)

# 정책 묶음. 순서는 표시 순서다. plain은 기본으로 가리지 않는다.
ENTITY_GROUPS: tuple[str, ...] = (
    "name", "address", "birth", "student_id", "org", "place", "date", "event", "etc", "host", "plain",
)
DEFAULT_ENTITY_ACTIONS: dict[str, str] = {group: ("allow" if group == "plain" else "mask") for group in ENTITY_GROUPS}
ENTITY_NAMES: dict[str, str] = {
    "name": "이름", "address": "주소", "birth": "생년월일", "student_id": "식별번호(학번·사번·여권번호 등)",
    "org": "소속(학교·회사·병원 등)", "place": "장소", "date": "날짜", "event": "사건",
    "etc": "기타(메신저·SNS 아이디 등)", "host": "IP·내부 주소", "plain": "시간·연도·나이·직함·과목",
}

_GROUP_OF: dict[str, str] = {
    "NAME": "name",
    "ADDRESS": "address",
    "MESSENGER": "etc",
    "ID_NUMBER": "student_id",
    "AFFILIATION": "org",
    "LOCATION": "place",
    "BIRTH": "birth",
    "AGE": "plain",
    **dict.fromkeys(RULE_KINDS, "host"),
}
# 정규식 번호 종류(gate.detectors.KINDS)를 따르는 게이트 종류. PHONE은 값을 보고 mobile·phone을 고른다.
_NUMBER_KIND_OF: dict[str, str] = {"EMAIL": "email"}

MASK_TEXT: dict[str, str] = {
    "NAME": "[이름]", "EMAIL": "[이메일]", "PHONE": "[전화번호]", "ADDRESS": "[주소]", "MESSENGER": "[연락처]",
    "ID_NUMBER": "[식별번호]", "AFFILIATION": "[소속]", "LOCATION": "[장소]", "BIRTH": "[생년월일]", "AGE": "[나이]",
    "IP_ADDRESS": "[IP]", "INTERNAL_HOST": "[내부 주소]",
}
assert set(_GROUP_OF) | set(_NUMBER_KIND_OF) | {"PHONE"} == set(ENTITY_KINDS)
assert set(MASK_TEXT) == set(ENTITY_KINDS)

# split_kind 기준 (plans/ner-v2-plan.md 2절·3.1)
_EMAIL_FORM = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_HANGUL = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
PHONE_MIN_DIGITS = 7
PHONE_MIN_DIGIT_RATIO = 0.7
# 생년월일 단서: 값에 `생`, 월(개월은 아님)·숫자 뒤 일, 날짜 형식(1990.01.01, 98-03-21), 붙여 쓴 6·8자리가 있음
_BIRTH_VALUE = re.compile(
    r"생|(?<!개)월|\d\s*일|\d{2,4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}|(?<!\d)(?:\d{6}|\d{8})(?!\d)"
)
_BIRTH_BEFORE = re.compile(r"출생|생년|생일|태어")  # 값 바로 앞(BIRTH_LOOKBACK자)에 있으면 생년월일
BIRTH_LOOKBACK = 10

_SURROGATE = re.compile("[\ud800-\udfff]")
_JOSA = frozenset(JOSA)
_MOBILE_DIGITS = re.compile(r"(?:0|82)1[016789]")


class NerError(Exception):
    """NER 실패. 받는 쪽은 차단으로 처리한다. 메시지에 원문·주소·키를 넣지 않는다."""


@dataclass(frozen=True)
class Entity:
    label: str   # 게이트 종류(ENTITY_KINDS 중 하나). 서버 라벨은 ServerNerClient.find가 split_kind로 바꾼다
    start: int   # 문자(str 인덱스), end 미포함
    end: int
    score: float


@dataclass(frozen=True)
class NerResult:
    entities: tuple[Entity, ...]
    model: str  # 서버가 알려 준 모델 식별값 (캐시 키에 들어간다)


class NerClient(Protocol):
    def cache_tag(self) -> str:
        """지금 쓰는 모델 식별값. 체크포인트가 바뀌면 달라진다."""
        ...

    def find(self, text: str) -> NerResult:
        """text의 개체 위치(문자 인덱스, start 순, 겹치지 않음). label은 게이트 종류다."""
        ...


def split_kind(label: str, text: str, start: int, end: int) -> str:
    """서버 라벨 하나를 원문을 보고 게이트 종류로 나눈다(모듈 docstring 표). 위치는 바꾸지 않는다.

    - CONTACT: 이메일 형식이면 EMAIL, 숫자가 7자 이상이고 공백을 뺀 글자의 70% 이상이면 PHONE,
      한글이 있으면 ADDRESS(상세 주소), 그 밖은 MESSENGER(메신저·SNS 아이디 등). 이 순서로 본다.
    - AGE_BIRTH: 값에 `생`, 월(개월은 아님), 숫자 뒤 일, 날짜 형식(1990.01.01, 98-03-21), 붙여 쓴 6·8자리가 있거나
      바로 앞 BIRTH_LOOKBACK자 안에 출생·생년·생일·태어가 있으면 BIRTH, 아니면 AGE(나이, 기본 공개).
    - 나머지(NAME, ID_NUMBER, AFFILIATION, LOCATION)는 그대로다. 모르는 라벨이면 NerError.
    """
    value = text[start:end]
    if label == "CONTACT":
        if _EMAIL_FORM.search(value):
            return "EMAIL"
        chars = [ch for ch in value if not ch.isspace()]
        digits = sum(ch.isdecimal() for ch in chars)
        if digits >= PHONE_MIN_DIGITS and digits >= PHONE_MIN_DIGIT_RATIO * len(chars):
            return "PHONE"
        return "ADDRESS" if _HANGUL.search(value) else "MESSENGER"
    if label == "AGE_BIRTH":
        before = text[max(0, start - BIRTH_LOOKBACK):start]
        return "BIRTH" if _BIRTH_VALUE.search(value) or _BIRTH_BEFORE.search(before) else "AGE"
    if label in ("NAME", "ID_NUMBER", "AFFILIATION", "LOCATION"):
        return label
    raise NerError("NER 응답에 모르는 종류가 있습니다")


def policy_slot(label: str, value: str) -> str:
    """게이트 종류 → 정책 칸 이름. 묶음(name 등)이거나 번호 종류(mobile·phone·email)다. 모르는 종류면 NerError.

    value는 그 개체의 원문 글자다(PHONE을 휴대폰·일반 전화로 나눌 때만 쓴다).
    """
    if label == "PHONE":
        digits = "".join(ch for ch in value if ch.isdecimal())
        return "mobile" if _MOBILE_DIGITS.match(digits) else "phone"
    slot = _NUMBER_KIND_OF.get(label) or _GROUP_OF.get(label)
    if slot is None:
        raise NerError("NER 결과에 모르는 종류가 있습니다")
    return slot


def outside(entities: tuple[Entity, ...] | list[Entity], ranges: list[tuple[int, int]], text: str) -> list[Entity]:
    """개체에서 ranges(먼저 찾은 범위)와 겹치는 부분을 뺀다. 남은 조각은 앞뒤 공백을 떼고, 글자(\\w)가 없으면 버린다.

    정규식이 우선이라서다(같은 길이 `*`와 공개 범위 설정이 그대로 적용된다). 규칙(IP·내부 주소)은 정규식 범위를,
    NER 개체는 정규식·규칙 범위를 뺀다(gate.pipeline). 겹치지 않는 개체는 그대로 둔다.
    잘린 개체에서 조사 하나만 남은 조각(`hong@example.com으로`의 `으로`)도 버린다. 낱말 넓히기가 붙인 글자라
    가릴 정보가 아니다.
    """
    ranges = sorted(ranges)
    out: list[Entity] = []
    for e in entities:
        pieces = [(e.start, e.end)]
        for rs, re_ in ranges:
            if re_ <= e.start or rs >= e.end:
                continue
            pieces = [p for s, t in pieces for p in ((s, min(t, rs)), (max(s, re_), t)) if p[0] < p[1]]
        cut = pieces != [(e.start, e.end)]
        for s, t in pieces:
            while s < t and text[s].isspace():
                s += 1
            while t > s and text[t - 1].isspace():
                t -= 1
            if s < t and re.search(r"\w", text[s:t]) and not (cut and text[s:t] in _JOSA):
                out.append(Entity(e.label, s, t, e.score))
    return sorted(out, key=lambda x: x.start)


def clean_text(text: str) -> str:
    """서버에 보낼 사본. 짝 없는 대리 문자(surrogateescape로 푼 바이트 등)를 U+FFFD로 바꾼다. 길이가 같다."""
    return _SURROGATE.sub("�", text)


def loopback_url(raw: str, name: str) -> str:
    """http(s)이고 이 컴퓨터(127.0.0.1·localhost·::1)를 가리키는 주소만 받는다. 아니면 NerError."""
    try:
        parts = urllib.parse.urlsplit(raw.strip())
        host = parts.hostname
        parts.port  # 잘못된 포트면 ValueError
    except ValueError:
        raise NerError(f"{name} 형식이 올바르지 않습니다") from None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password \
            or parts.query or parts.fragment:
        raise NerError(f"{name}은 http(s)://호스트[:포트][/경로] 형식이어야 합니다")
    if host != "localhost":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise NerError(f"{name}은 이 컴퓨터(127.0.0.1, localhost, ::1)만 가리킬 수 있습니다")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # 리디렉트를 따라가지 않는다(다른 주소로 문서가 가지 않게)
        return None


def _check_model(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise NerError("NER 응답 형식이 올바르지 않습니다: model")
    return value


def parse_entities(items: object, length: int) -> tuple[Entity, ...]:
    """서버가 준 개체 목록을 검사한다. 라벨이 NER_LABELS 밖이거나, 범위가 문서 밖이거나, start 순이 아니거나,
    겹치면 NerError. 돌려주는 Entity의 label은 서버 라벨 그대로다(게이트 종류로는 split_kind가 바꾼다)."""
    if not isinstance(items, list):
        raise NerError("NER 응답 형식이 올바르지 않습니다: results")
    out: list[Entity] = []
    for item in items:
        if not isinstance(item, dict):
            raise NerError("NER 응답 형식이 올바르지 않습니다: 개체")
        start, end, label, score = item.get("start"), item.get("end"), item.get("label"), item.get("score")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or not 0 <= start < end <= length:
            raise NerError("NER 응답의 개체 범위가 문서 밖입니다")
        if not isinstance(label, str) or label not in NER_LABELS:
            raise NerError("NER 응답에 모르는 종류가 있습니다")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) \
                or not 0.0 <= score <= 1.0:
            raise NerError("NER 응답 형식이 올바르지 않습니다: score")
        if out and start < out[-1].end:
            raise NerError("NER 응답의 개체가 겹치거나 순서가 맞지 않습니다")
        out.append(Entity(label, start, end, float(score)))
    return tuple(out)


class ServerNerClient:
    """로컬 NER 서버(nerserve)에 묻는다.

    - 설정은 인자 또는 환경변수 NER_BASE_URL(이 컴퓨터 주소만). 없으면 DEFAULT_BASE_URL(http://127.0.0.1:8001)이다.
      주소는 예외 메시지·repr에 넣지 않는다. 프록시와 리디렉트는 쓰지 않는다.
    - cache_tag()는 /health의 모델 식별값이다. 체크포인트를 바꿔 서버를 다시 띄우면 달라져 이전 결과를 쓰지 않는다.
    - find()는 문서 전체를 한 번에 보낸다. 서버가 256토큰 창을 겹쳐 가며 읽는다. 받은 서버 라벨은 원문을 보고
      게이트 종류로 나눠 돌려준다(split_kind).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        threshold: float = DEFAULT_THRESHOLD,
        env: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if env is None else env
        raw_url = base_url if base_url is not None else ((env.get("NER_BASE_URL") or "").strip() or DEFAULT_BASE_URL)
        if not raw_url.strip():
            raise NerError("NER 서버 주소(예: http://127.0.0.1:8001)가 비어 있습니다")
        for name, value in (("타임아웃", timeout), ("threshold", threshold)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise NerError(f"NER {name}은 0보다 큰 숫자여야 합니다")
        if threshold >= 1:
            raise NerError("NER threshold는 1보다 작아야 합니다")
        self._base = loopback_url(raw_url, "NER_BASE_URL")
        self.timeout = float(timeout)
        self.threshold = float(threshold)
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)

    def __repr__(self) -> str:
        return f"ServerNerClient(threshold={self.threshold:g})"

    def cache_tag(self) -> str:
        data = self._call("/health", None)
        if not isinstance(data, dict) or data.get("status") != "ok":
            raise NerError("NER 서버가 준비되지 않았습니다")
        return _check_model(data.get("model"))

    def find(self, text: str) -> NerResult:
        if not isinstance(text, str):
            raise NerError("NER에 보낼 내용은 문자열이어야 합니다")
        if not text.strip():
            return NerResult((), "")
        data = self._call("/v1/ner", {"texts": [clean_text(text)], "threshold": self.threshold})
        if not isinstance(data, dict):
            raise NerError("NER 응답 형식이 올바르지 않습니다")
        results = data.get("results")
        if not isinstance(results, list) or len(results) != 1:
            raise NerError("NER 응답 형식이 올바르지 않습니다: results")
        entities = parse_entities(results[0], len(text))
        return NerResult(tuple(Entity(split_kind(e.label, text, e.start, e.end), e.start, e.end, e.score)
                               for e in entities), _check_model(data.get("model")))

    def _call(self, path: str, body: dict | None) -> object:
        url = self._base + path
        if body is None:
            request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        else:
            request = urllib.request.Request(url, json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                             {"Content-Type": "application/json", "Accept": "application/json"},
                                             method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise NerError(f"NER 서버가 오류로 답했습니다: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NerError(f"NER 서버에 연결하지 못했습니다: {type(reason).__name__}") from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise NerError("NER 응답이 너무 큽니다")
        try:
            return json.loads(raw)
        except ValueError:
            raise NerError("NER 응답이 JSON이 아닙니다") from None


_BACKENDS = {"server": ServerNerClient}
NER_BACKENDS: tuple[str, ...] = ("server", "none")  # 정책 선택지. 엄격한 것 → 느슨한 것. none은 끔(YAML이 off를 거짓으로 읽어서 none)


def make_ner_client(name: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS, **options) -> ServerNerClient | None:
    """이름으로 NER 백엔드를 만든다. none(끔)이면 None. 모르는 이름이나 잘못된 설정은 NerError."""
    if name == "none":
        return None
    factory = _BACKENDS.get(name)
    if factory is None:
        raise NerError(f"알 수 없는 NER 백엔드: {name!r} (사용 가능: {', '.join(NER_BACKENDS)})")
    try:
        return factory(timeout=timeout, **options)
    except NerError:
        raise
    except Exception as exc:
        raise NerError(f"NER 백엔드 {name!r}를 만들 수 없습니다: {type(exc).__name__}") from exc
