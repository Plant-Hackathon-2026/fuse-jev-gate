"""게이트 정책: 기본값, 사용자 정책 파일, 판단 규칙.

원칙 (2026-10-31 사용자 결정)
  - 사용자가 지정하면 그 값, 지정하지 않으면 기본값. 기본값만으로도 안전해야 한다
    (번호는 종류와 상관없이 전부 가리고, Jev가 실패하면 차단한다).
  - 정책은 사람(사용자)만 자기 터미널에서 `gate policy`나 그 터미널에서 연 설정 화면(`gate ui`)으로 바꾼다.
    둘 다 gate.policy_change로 쓴다. 소켓에는 정책을 바꾸는 요청이 없다.
  - 정책 파일(기본 위치 hackathon/gate-data/policy.yaml, GATE_POLICY)에는 사용자가 지정한 항목(Entry)만 순서대로 담는다.
    실제 정책은 기본값 위에 항목을 차례로 적용한 결과다. 파일이 없으면 기본값 그대로다.
  - 데몬은 요청마다 파일이 바뀌었는지 보고 다시 읽는다(PolicySource). 파일을 읽지 못하거나 형식이 틀리면 PolicyError이고,
    데몬은 그동안 내용 읽기를 모두 차단한다.
  - 맥락 규칙(context)은 더 막는 쪽으로만 받는다. Jev 질문은 이 문장을 "보여주면 안 되는 내용"의 기준으로만 쓰고,
    loosened()는 맥락 규칙을 더하는 것을 완화로 보지 않기 때문이다. 느슨하게 하는 문장("병원 기록은 보여줘도 돼")은
    문장 해석(gate.policy_text)에서 거절한다. 실제 해석 백엔드를 붙일 때도 이 가정을 지킨다.

정책 파일 형식 (항목마다 set·context·path_rule 중 하나만 쓴다)
  version: 1
  entries:
    - said: 주민번호는 생년월일까지만 보여줘      # 사용자가 쓴 문장. gate policy set이면 없다
      set: {numbers.rrn.visible: birth}
    - said: 병원 기록은 보여주지 마
      context: 병원 기록은 보여주지 마            # Jev 질문의 "사용자 정책" 칸에 들어간다
    - path_rule: {pattern: "공개/**", action: allow}

설정 키 (setting_keys(), 기본값은 config/policy.yaml에 설명과 함께 있다)
  numbers.<종류>.action   mask | ask_human | deny     정규식이 그 종류를 찾았을 때. 기본 mask (종류에는 비밀값 secret도 있다)
  numbers.<종류>.visible  종류별 공개 범위              gate.detectors.VISIBLE_CHOICES. 기본 none(전부 가림)
  entities.<묶음>.action  allow | mask | ask_human | deny   NER·규칙이 그 묶음을 찾았을 때(3c, 3e). 기본 mask, plain(나이)은 allow
                                                       묶음은 gate.ner_client.ENTITY_GROUPS. 전화·이메일은 numbers 칸을 따른다.
                                                       host(IP·내부 주소)는 규칙이 찾아 NER을 꺼도 적용된다
  (뺀 키) entities.date.action, entities.event.action  2026-11-01 NER 교체로 찾지 않는 묶음. 예전 정책 파일에 있으면 읽되
                                                       효과가 없다(정책 설정에 넣지 않음). 새로 지정할 수는 없다(validate_setting이 거부)
  ner_backend, ner_timeout_seconds                     NER 백엔드(none | server, 기본 none = 끔), NER 응답 제한 시간(초)
  thresholds.allow_if_safe_confidence_gte              Jev가 safe이고 확신도가 이 값 이상이면 허용
  thresholds.deny_if_sensitive_confidence_gte          Jev가 safe가 아니고 확신도가 이 값 이상이면 차단 (그 사이는 사람 승인)
  jev_backend, jev_timeout_seconds, max_inspect_bytes  Jev 백엔드 이름, 타임아웃(초), Jev 한 번에 넘기는 최대 바이트
  approval.timeout_seconds                             사람 승인을 기다리는 시간(초). 넘으면 차단 (Phase 5)
  approval.session_minutes                             "이 폴더 허용"을 기억하는 시간(분). 0이면 그 선택지를 없앤다

판단 순서 (gate/pipeline.py가 차례로 부른다)
  1. path_action: 경로 규칙. deny·ask_human이면 그 결과, allow면 검사 없이 원본, 맞는 규칙이 없으면 None(검사 계속).
  2. number_action: 정규식이 찾은 종류별 동작 중 가장 엄격한 것. 찾은 게 없으면 None.
  3. slot_action: NER이 찾은 개체의 칸(묶음 또는 번호 종류)별 동작 중 가장 엄격한 것. NER이 꺼져 있거나 찾은 게 없으면 None.
  4. jev_action: Jev 답과 임계값 두 개(명세 3.4). Jev 오류(None)는 deny.
     NER이 켜져 있고 가린 것(정규식·NER)이 있으면 Jev는 가린 결과를 판단하고, safe면 가린 채 보여 주고 아니면 ask_human이다
     (3c ②, gate.pipeline).
  5. strictest(...)로 합친다. allow인데 가린 것이 있으면 가린 내용(mask)을 돌려준다.

경로 규칙 매칭
  - 패턴은 가상 경로 기준 glob이다. `**`는 폴더 0개 이상, `*`·`?`·`[...]`는 한 단계 안에서만 맞는다.
    `/`가 없는 패턴(`*.env`)은 어느 깊이에서든 파일·폴더 이름과 맞춘다(gitignore와 같다).
  - 맥은 파일 이름의 대소문자와 한글 정규화(NFC/NFD)를 구분하지 않으므로, 엄격한 규칙(deny, ask_human)은
    대소문자와 정규화 차이를 무시하고 맞춘다. 완화 규칙(allow)은 NFC로 맞춘 뒤 정확히 같을 때만 맞는다.
  - 경로 후보가 여럿이면(요청 경로, 심볼릭 링크를 따라간 실제 경로) 엄격한 규칙은 하나라도 맞으면,
    allow는 모두 맞아야 적용한다. 여러 규칙이 맞으면 가장 엄격한 것이 이긴다(규칙 순서와 무관).
"""

import errno
import fnmatch
import hashlib
import json
import math
import os
import tempfile
import threading
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, get_args

import yaml

from gate.detectors import KINDS, VISIBLE_CHOICES, Finding
from gate.jev_client import JevDecision, available_backends
from gate.ner_client import (
    DEFAULT_ENTITY_ACTIONS,
    ENTITY_GROUPS,
    ENTITY_NAMES,
    NER_BACKENDS,
    RETIRED_ENTITY_GROUPS,
    RETIRED_ENTITY_NAMES,
)

Action = Literal["allow", "mask", "ask_human", "deny"]  # 느슨한 것 → 엄격한 것
ACTIONS: tuple[Action, ...] = get_args(Action)
_STRICTNESS = {action: i for i, action in enumerate(ACTIONS)}
NumberAction = Literal["mask", "ask_human", "deny"]
PathAction = Literal["allow", "ask_human", "deny"]
PATH_ACTIONS: tuple[PathAction, ...] = get_args(PathAction)

POLICY_FILE_VERSION = 1
MAX_POLICY_FILE_BYTES = 1024 * 1024
_ENTRY_KINDS = ("set", "context", "path_rule")
_ENTRY_FIELDS = frozenset({"said", "at", *_ENTRY_KINDS})

# 사용자에게 보여 줄 이름. gate policy show와 문장 해석이 같이 쓴다.
KIND_NAMES = {
    "rrn": "주민등록번호", "foreigner_rrn": "외국인등록번호", "card": "카드번호", "mobile": "휴대폰 번호",
    "phone": "일반 전화번호", "email": "이메일", "account": "계좌번호", "secret": "비밀값(비밀번호·키·토큰)",
    **ENTITY_NAMES,  # NER 묶음(3c). 번호 종류와 이름이 겹치지 않는다
}
ACTION_NAMES = {"allow": "그대로 보여줌", "mask": "가려서 보여줌", "ask_human": "사람에게 물어봄", "deny": "거부"}
SETTING_NAMES = {
    "thresholds.allow_if_safe_confidence_gte": "Jev가 '안전'이라고 답할 때 보여 주는 최소 확신도",
    "thresholds.deny_if_sensitive_confidence_gte": "Jev가 '민감'이라고 답할 때 거부하는 최소 확신도",
    "jev_backend": "Jev 백엔드",
    "jev_timeout_seconds": "Jev 응답 제한 시간(초)",
    "max_inspect_bytes": "Jev에 한 번에 넘기는 최대 크기(바이트)",
    "ner_backend": "NER(이름·주소·소속 가리기) 백엔드",
    "ner_timeout_seconds": "NER 응답 제한 시간(초)",
    "approval.timeout_seconds": "사람 승인을 기다리는 시간(초)",
    "approval.session_minutes": "'이 폴더 허용'을 기억하는 시간(분, 0이면 기억하지 않음)",
}
VISIBLE_NAMES = {
    "none": "전부 가림", "birth": "생년월일(앞 6자리)까지 공개", "birth_gender": "성별 자리(앞 7자리)까지 공개",
    "prefix": "앞자리(010 등) 공개", "area": "지역번호 공개", "tld": "최상위 도메인(.com 등) 공개", "last4": "끝 4자리 공개",
}


class PolicyError(Exception):
    """정책 파일을 읽지 못했거나 형식이 틀렸다. 데몬은 내용 읽기를 차단한다."""


@dataclass(frozen=True)
class PathRule:
    pattern: str
    action: PathAction

    def __post_init__(self) -> None:
        if not isinstance(self.pattern, str) or not self.pattern.strip("/ ") or "\x00" in self.pattern:
            raise PolicyError(f"경로 규칙의 패턴이 비어 있거나 올바르지 않습니다: {self.pattern!r}")
        if ".." in self.pattern.split("/"):
            raise PolicyError(f"경로 규칙에 '..'을 쓸 수 없습니다: {self.pattern!r}")
        if self.action not in PATH_ACTIONS:
            raise PolicyError(f"경로 규칙의 동작은 {', '.join(PATH_ACTIONS)} 중 하나여야 합니다: {self.action!r}")


# 기본 경로 규칙. 비밀 값이 든 파일은 내용을 검사하지 않고 거부한다(명세 3.4 예시의 *.env에 키 파일을 더함).
# 그 밖의 설정 파일(yaml, toml, json 등)은 내용을 열어 보고 비밀값(numbers.secret)을 가린다.
DEFAULT_PATH_RULES: tuple[PathRule, ...] = (
    PathRule("*.env", "deny"),
    PathRule(".env.*", "deny"),
    PathRule("*.pem", "deny"),
    PathRule("*.key", "deny"),
    PathRule("id_rsa*", "deny"),
    PathRule("id_ed25519*", "deny"),
    # Phase 4 보완: 자격 증명을 담는 것으로 널리 알려진 파일
    PathRule(".netrc", "deny"),
    PathRule(".npmrc", "deny"),
    PathRule(".pypirc", "deny"),
    PathRule(".pgpass", "deny"),
    PathRule(".git-credentials", "deny"),
    PathRule("credentials.json", "deny"),
    PathRule("**/.aws/credentials", "deny"),
    PathRule("*.p12", "deny"),
    PathRule("*.pfx", "deny"),
    PathRule("*.jks", "deny"),
    PathRule("*.keystore", "deny"),
)


@dataclass(frozen=True)
class Entry:
    """사용자가 지정한 항목 하나. settings·context·path_rule 중 정확히 하나를 가진다."""
    settings: Mapping[str, object] | None = None  # 파일에서는 set
    context: str | None = None
    path_rule: PathRule | None = None
    said: str | None = None  # 사용자가 쓴 문장 (gate policy set이면 None)
    at: str | None = None    # 추가한 시각 (ISO 8601, 표시용)

    def __post_init__(self) -> None:
        present = [k for k, v in (("set", self.settings), ("context", self.context), ("path_rule", self.path_rule))
                   if v is not None]
        if len(present) != 1:
            raise PolicyError(f"항목에는 {', '.join(_ENTRY_KINDS)} 중 하나만 있어야 합니다: {present or '없음'}")
        if self.settings is not None:
            if not isinstance(self.settings, Mapping) or not self.settings:
                raise PolicyError("set은 비어 있지 않은 '설정 키: 값' 묶음이어야 합니다")
            checked = {key: _retired_value(key, value) if key in RETIRED_SETTING_KEYS else validate_setting(key, value)
                       for key, value in self.settings.items()}
            object.__setattr__(self, "settings", MappingProxyType(checked))
        if self.context is not None and (not isinstance(self.context, str) or not self.context.strip()):
            raise PolicyError("context는 비어 있지 않은 문장이어야 합니다")
        if self.path_rule is not None and not isinstance(self.path_rule, PathRule):
            raise PolicyError("path_rule은 PathRule이어야 합니다")
        for name in ("said", "at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise PolicyError(f"{name}은 문자열이어야 합니다")

    def to_dict(self) -> dict:
        out: dict = {}
        if self.said is not None:
            out["said"] = self.said
        if self.at is not None:
            out["at"] = self.at
        if self.settings is not None:
            out["set"] = dict(self.settings)
        elif self.context is not None:
            out["context"] = self.context
        else:
            out["path_rule"] = {"pattern": self.path_rule.pattern, "action": self.path_rule.action}
        return out

    @classmethod
    def from_dict(cls, data: object) -> "Entry":
        if not isinstance(data, Mapping):
            raise PolicyError("항목은 '키: 값' 묶음이어야 합니다")
        unknown = set(data) - _ENTRY_FIELDS
        if unknown:
            raise PolicyError(f"항목에 알 수 없는 키가 있습니다: {', '.join(sorted(map(str, unknown)))}")
        rule = data.get("path_rule")
        if rule is not None:
            if not isinstance(rule, Mapping) or set(rule) != {"pattern", "action"}:
                raise PolicyError("path_rule은 pattern과 action만 가져야 합니다")
            rule = PathRule(rule["pattern"], rule["action"])
        return cls(data.get("set"), data.get("context"), rule, data.get("said"), data.get("at"))


# --- 설정 키 ---

def _number_settings() -> dict[str, tuple[str, ...]]:
    """번호 종류별 선택지. 엄격한 것 → 느슨한 것 순서이고 첫 값이 기본이다(action은 기본 mask라 따로 둔다)."""
    out: dict[str, tuple[str, ...]] = {}
    for kind in KINDS:
        out[f"numbers.{kind}.action"] = ("deny", "ask_human", "mask")
        out[f"numbers.{kind}.visible"] = VISIBLE_CHOICES[kind]
    return out


def _entity_settings() -> dict[str, tuple[str, ...]]:
    """NER 묶음별 동작 선택지(3c). 엄격한 것 → 느슨한 것. allow(그대로 보여 줌)가 있다는 점이 번호와 다르다."""
    return {f"entities.{group}.action": ("deny", "ask_human", "mask", "allow") for group in ENTITY_GROUPS}


_CHOICES: dict[str, tuple[str, ...]] = {**_number_settings(), **_entity_settings(), "ner_backend": NER_BACKENDS}
# 뺀 묶음의 키(2026-11-01). 정책 파일에서 읽을 때만 받고(Entry에 남아 gate policy show가 보여 준다) 정책에는 넣지 않는다.
RETIRED_SETTING_KEYS: frozenset[str] = frozenset(f"entities.{group}.action" for group in RETIRED_ENTITY_GROUPS)
_RETIRED_CHOICES = ("deny", "ask_human", "mask", "allow")
_DEFAULTS: dict[str, object] = {
    **{key: ("mask" if key.endswith(".action") else "none") for key in _number_settings()},
    **{f"entities.{group}.action": action for group, action in DEFAULT_ENTITY_ACTIONS.items()},
    "thresholds.allow_if_safe_confidence_gte": 0.95,
    "thresholds.deny_if_sensitive_confidence_gte": 0.95,
    "jev_backend": "mock",
    "jev_timeout_seconds": 3.0,
    "max_inspect_bytes": 200_000,
    "approval.timeout_seconds": 30.0,
    "approval.session_minutes": 60,
    # NER 서버가 없으면 모든 읽기가 차단되므로 기본은 끔이다. 끄면 3b와 똑같이 동작한다(3c, 2026-10-31)
    "ner_backend": "none",
    "ner_timeout_seconds": 10.0,
}
_FLOAT_RANGES = {
    "thresholds.allow_if_safe_confidence_gte": (0.0, 1.0),
    "thresholds.deny_if_sensitive_confidence_gte": (0.0, 1.0),
    "jev_timeout_seconds": (0.1, 600.0),
    "ner_timeout_seconds": (0.1, 600.0),
    "approval.timeout_seconds": (1.0, 600.0),
}
_INT_RANGES = {"max_inspect_bytes": (1_000, 16 * 1024 * 1024), "approval.session_minutes": (0, 24 * 60)}


def setting_keys() -> tuple[str, ...]:
    return tuple(_DEFAULTS)


def default_settings() -> dict[str, object]:
    return dict(_DEFAULTS)


def setting_choices(key: str) -> tuple[str, ...] | None:
    """선택지가 정해진 설정이면 선택지(엄격한 것 → 느슨한 것), 아니면 None. jev_backend는 등록된 백엔드 이름."""
    if key == "jev_backend":
        return available_backends()
    return _CHOICES.get(key)


def is_retired(key: object) -> bool:
    """2026-11-01 NER 교체로 뺀 묶음의 키인가(entities.date.action, entities.event.action)."""
    return key in RETIRED_SETTING_KEYS


def _retired_value(key: str, value: object) -> object:
    """뺀 키의 값. 효과는 없지만 정책 파일이 깨졌는지는 지금처럼 본다(예전 선택지가 아니면 PolicyError)."""
    if not isinstance(value, str) or value not in _RETIRED_CHOICES:
        raise PolicyError(f"{key}는 {', '.join(_RETIRED_CHOICES)} 중 하나여야 합니다: {value!r}")
    return value


def validate_setting(key: object, value: object) -> object:
    """설정 하나를 검사해 정리한 값을 돌려준다. 모르는 키나 값, 뺀 키(is_retired)면 PolicyError."""
    if is_retired(key):
        group = key.split(".")[1]
        raise PolicyError(f"{key}는 지정할 수 없습니다. 지금은 {RETIRED_ENTITY_NAMES[group]}을(를) 따로 찾지 않습니다"
                          "(2026-11-01 NER 교체)")
    if key not in _DEFAULTS:
        raise PolicyError(f"알 수 없는 설정 키: {key!r}")
    choices = setting_choices(key)
    if choices is not None:
        if not isinstance(value, str) or value not in choices:
            raise PolicyError(f"{key}는 {', '.join(choices)} 중 하나여야 합니다: {value!r}")
        return value
    if key in _FLOAT_RANGES:
        low, high = _FLOAT_RANGES[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not low <= value <= high:
            raise PolicyError(f"{key}는 {low:g}~{high:g} 사이 숫자여야 합니다: {value!r}")
        return float(value)
    low, high = _INT_RANGES[key]
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise PolicyError(f"{key}는 {low}~{high} 사이 정수여야 합니다: {value!r}")
    return value


# --- 정책 ---

@dataclass(frozen=True)
class Policy:
    settings: Mapping[str, object]      # 모든 설정 키가 들어 있다
    path_rules: tuple[PathRule, ...]    # 기본 규칙 + 사용자 규칙
    context_rules: tuple[str, ...]
    entries: tuple[Entry, ...] = ()
    version: str = field(init=False)    # 동작에 영향을 주는 값의 해시. 캐시 키에 들어간다

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))
        canonical = json.dumps({
            "settings": dict(sorted(self.settings.items())),
            "path_rules": [[r.pattern, r.action] for r in self.path_rules],
            "context_rules": list(self.context_rules),
        }, ensure_ascii=False, sort_keys=True)
        object.__setattr__(self, "version", hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16])

    @property
    def visible(self) -> dict[str, str]:
        """gate.detectors.mask에 넘길 종류별 공개 범위."""
        return {kind: self.settings[f"numbers.{kind}.visible"] for kind in KINDS}

    def number_rule(self, kind: str) -> NumberAction:
        return self.settings[f"numbers.{kind}.action"]

    def entity_rule(self, group: str) -> Action:
        return self.settings[f"entities.{group}.action"]

    def slot_rule(self, slot: str) -> Action:
        """NER 개체의 칸(gate.ner_client.policy_slot) 동작. 묶음이면 entities, 번호 종류면 numbers 칸이다."""
        return self.entity_rule(slot) if slot in ENTITY_GROUPS else self.number_rule(slot)

    @property
    def ner_backend(self) -> str:
        return self.settings["ner_backend"]

    @property
    def ner_timeout_seconds(self) -> float:
        return self.settings["ner_timeout_seconds"]

    @property
    def allow_threshold(self) -> float:
        return self.settings["thresholds.allow_if_safe_confidence_gte"]

    @property
    def deny_threshold(self) -> float:
        return self.settings["thresholds.deny_if_sensitive_confidence_gte"]

    @property
    def jev_backend(self) -> str:
        return self.settings["jev_backend"]

    @property
    def jev_timeout_seconds(self) -> float:
        return self.settings["jev_timeout_seconds"]

    @property
    def max_inspect_bytes(self) -> int:
        return self.settings["max_inspect_bytes"]

    @property
    def approval_timeout_seconds(self) -> float:
        return self.settings["approval.timeout_seconds"]

    @property
    def approval_session_minutes(self) -> int:
        return self.settings["approval.session_minutes"]


def build_policy(entries: Sequence[Entry] = ()) -> Policy:
    """기본값 위에 사용자 항목을 차례로 적용한다. 같은 설정을 여러 번 지정하면 나중 것이 이긴다.
    뺀 키(is_retired)는 건너뛴다(예전 정책 파일이 그대로 읽히되 효과가 없다)."""
    settings = default_settings()
    path_rules = list(DEFAULT_PATH_RULES)
    context_rules: list[str] = []
    for entry in entries:
        if entry.settings is not None:
            settings.update((k, v) for k, v in entry.settings.items() if not is_retired(k))
        elif entry.context is not None:
            context_rules.append(entry.context.strip())
        else:
            path_rules.append(entry.path_rule)
    return Policy(settings, tuple(path_rules), tuple(context_rules), tuple(entries))


DEFAULT_POLICY = build_policy()


# --- 정책 파일 ---

def read_entries(path: str | os.PathLike | None) -> tuple[Entry, ...]:
    """정책 파일의 항목을 읽는다. path가 None이거나 파일이 없으면 빈 묶음(= 기본값). 그 밖의 실패는 PolicyError."""
    if path is None:
        return ()
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_POLICY_FILE_BYTES + 1)
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise PolicyError(f"정책 파일을 읽을 수 없습니다: {errno.errorcode.get(exc.errno, exc.errno)}") from exc
    if len(raw) > MAX_POLICY_FILE_BYTES:
        raise PolicyError(f"정책 파일이 너무 큽니다 ({MAX_POLICY_FILE_BYTES} 바이트 이하)")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise PolicyError(f"정책 파일 형식이 올바르지 않습니다: {type(exc).__name__}") from exc
    if data is None:
        return ()
    if not isinstance(data, Mapping) or set(data) - {"version", "entries"}:
        raise PolicyError("정책 파일에는 version과 entries만 있어야 합니다")
    if data.get("version") != POLICY_FILE_VERSION:
        raise PolicyError(f"정책 파일 version은 {POLICY_FILE_VERSION}이어야 합니다: {data.get('version')!r}")
    items = data.get("entries")
    if items is None:
        return ()
    if not isinstance(items, list):
        raise PolicyError("entries는 목록이어야 합니다")
    return tuple(Entry.from_dict(item) for item in items)


def load_policy(path: str | os.PathLike | None) -> Policy:
    return build_policy(read_entries(path))


def dump_entries(entries: Sequence[Entry]) -> str:
    header = (
        "# gate 정책 파일. `gate policy` 명령으로 바꾼다(직접 고치지 않아도 된다).\n"
        "# 여기 없는 설정은 기본값을 쓴다. 기본값과 설정 키 설명은 fuse-jev-gate/config/policy.yaml에 있다.\n"
    )
    body = {"version": POLICY_FILE_VERSION, "entries": [entry.to_dict() for entry in entries]}
    return header + yaml.safe_dump(body, allow_unicode=True, sort_keys=False)


def write_entries(path: str | os.PathLike, entries: Sequence[Entry]) -> Policy:
    """항목을 정책 파일에 원자적으로 쓴다(임시 파일 → rename, 권한 0600). 적용한 정책을 돌려준다."""
    policy = build_policy(entries)  # 쓰기 전에 검사
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".policy-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(dump_entries(entries))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return policy


class PolicySource:
    """요청마다 정책 파일이 바뀌었는지(수정 시각·크기·inode) 보고, 바뀌었으면 다시 읽는다. 여러 스레드에서 불러도 된다.

    current()는 정책 파일이 잘못되어 있는 동안 계속 PolicyError를 던진다(고쳐지면 다음 요청부터 정상).
    """

    def __init__(self, path: str | os.PathLike | None) -> None:
        self.path = None if path is None else Path(path)
        self._lock = threading.Lock()
        self._signature: object = object()  # 처음에는 무엇과도 같지 않다
        self._policy: Policy | None = None
        self._error: PolicyError | None = None

    def _stat_signature(self) -> tuple | None:
        if self.path is None:
            return None
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise PolicyError(f"정책 파일 정보를 읽을 수 없습니다: {errno.errorcode.get(exc.errno, exc.errno)}") from exc
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

    def current(self) -> Policy:
        with self._lock:
            signature = self._stat_signature()
            if signature != self._signature:
                self._signature = signature
                try:
                    self._policy, self._error = load_policy(self.path), None
                except PolicyError as exc:
                    self._policy, self._error = None, exc
            if self._error is not None:
                raise self._error
            return self._policy


# --- 판단 ---

def strictest(*actions: Action | None) -> Action | None:
    """가장 엄격한 동작(allow < mask < ask_human < deny). None은 건너뛴다. 모두 None이면 None."""
    present = [a for a in actions if a is not None]
    return max(present, key=_STRICTNESS.__getitem__) if present else None


def _normalize(path: str, fold: bool) -> list[str]:
    parts = [unicodedata.normalize("NFC", p) for p in path.split("/") if p not in ("", ".")]
    return [p.casefold() for p in parts] if fold else parts


def _match_parts(pattern: list[str], parts: list[str]) -> bool:
    if not pattern:
        return not parts
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(_match_parts(rest, parts[i:]) for i in range(len(parts) + 1))
    return bool(parts) and fnmatch.fnmatchcase(parts[0], head) and _match_parts(rest, parts[1:])


def match_path(pattern: str, vpath: str, *, fold: bool) -> bool:
    """가상 경로가 패턴에 맞는지. fold면 대소문자를 무시한다. 둘 다 NFC로 맞춘 뒤 비교한다.

    `/`가 없는 패턴은 어느 깊이의 이름과도 맞춘다. 폴더 이름이 맞으면 그 아래 파일도 맞는다.
    """
    pat = _normalize(pattern, fold)
    if "/" not in pattern.strip("/"):
        pat = ["**", *pat, "**"]
    return _match_parts(pat, _normalize(vpath, fold))


def path_action(policy: Policy, paths: Iterable[str]) -> PathAction | None:
    """경로 규칙 결과. paths는 요청 경로와 실제 경로(심볼릭 링크를 따라간 것)처럼 같은 파일을 가리키는 후보들이다."""
    candidates = list(paths)
    if not candidates:
        raise ValueError("경로 후보가 없습니다")
    matched: list[Action] = []
    for rule in policy.path_rules:
        if rule.action == "allow":
            if all(match_path(rule.pattern, p, fold=False) for p in candidates):
                matched.append("allow")
        elif any(match_path(rule.pattern, p, fold=True) for p in candidates):
            matched.append(rule.action)
    return strictest(*matched)


def number_action(policy: Policy, findings: Iterable[Finding]) -> NumberAction | None:
    """정규식이 찾은 종류별 동작 중 가장 엄격한 것. 찾은 게 없으면 None."""
    return strictest(*(policy.number_rule(f.kind) for f in findings))


def slot_action(policy: Policy, slots: Iterable[str]) -> Action | None:
    """NER 개체 칸별 동작 중 가장 엄격한 것. 칸이 없으면 None."""
    return strictest(*(policy.slot_rule(slot) for slot in slots))


def jev_action(policy: Policy, decision: JevDecision | None) -> Literal["allow", "ask_human", "deny"]:
    """명세 3.4: safe이고 확신도 ≥ allow 임계값이면 allow, safe가 아니고 확신도 ≥ deny 임계값이면 deny, 그 사이는 ask_human.
    Jev 오류(None)는 deny다."""
    if decision is None:
        return "deny"
    if decision.label == "safe":
        return "allow" if decision.confidence >= policy.allow_threshold else "ask_human"
    return "deny" if decision.confidence >= policy.deny_threshold else "ask_human"


# --- 완화 확인 ---

def _describe(key: str, value: object) -> str:
    if key.startswith("numbers."):
        _, kind, what = key.split(".")
        names = ACTION_NAMES if what == "action" else VISIBLE_NAMES
        return f"{KIND_NAMES[kind]}: {names.get(value, value)}"
    if is_retired(key):
        _, group, _ = key.split(".")
        return f"{RETIRED_ENTITY_NAMES[group]}(NER): {ACTION_NAMES.get(value, value)} (지금은 찾지 않음)"
    if key.startswith("entities."):
        _, group, _ = key.split(".")
        return f"{ENTITY_NAMES[group]}(NER): {ACTION_NAMES.get(value, value)}"
    shown = f"{value:g}" if isinstance(value, float) else value
    return f"{SETTING_NAMES.get(key, key)}: {shown}"


def describe_setting(key: str, value: object) -> str:
    """설정 하나를 사용자에게 보여 줄 한국어 한 줄로 바꾼다. 뺀 키는 "(지금은 찾지 않음)"을 붙인다."""
    return _describe(key, value)


def _is_looser(key: str, old: object, new: object) -> bool:
    """엄격해지는 게 확실한 변경만 False다. 방향을 모르는 변경은 완화로 본다(한 번 더 확인)."""
    choices = _CHOICES.get(key)
    if choices is not None:
        return choices.index(new) > choices.index(old)
    if key == "thresholds.allow_if_safe_confidence_gte":
        return new < old
    if key == "thresholds.deny_if_sensitive_confidence_gte":
        return new > old
    if key in ("approval.timeout_seconds", "approval.session_minutes"):
        return new > old  # 기다리는 시간·기억하는 시간이 길어지면 승인될 여지가 커진다
    return True


def loosened(before: Policy, after: Policy) -> list[str]:
    """before → after에서 느슨해진 점을 한국어 설명으로 돌려준다. 비어 있으면 같거나 더 엄격해진 것이다.

    gate policy가 "완화하는 변경은 한 번 더 확인"에 쓴다.
    """
    out = []
    for key in setting_keys():
        old, new = before.settings[key], after.settings[key]
        if old != new and _is_looser(key, old, new):
            out.append(f"{_describe(key, old)} → {_describe(key, new)}")
    removed = [r for r in before.path_rules if r not in after.path_rules]
    added = [r for r in after.path_rules if r not in before.path_rules]
    out += [f"경로 규칙 삭제: {r.pattern} ({ACTION_NAMES[r.action]})" for r in removed if r.action != "allow"]
    out += [f"경로 규칙 추가: {r.pattern} ({ACTION_NAMES[r.action]})" for r in added if r.action == "allow"]
    out += [f"맥락 규칙 삭제: {rule}" for rule in before.context_rules if rule not in after.context_rules]
    return out
