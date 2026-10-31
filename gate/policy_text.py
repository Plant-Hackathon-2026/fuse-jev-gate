"""정책 문장 해석기: 사용자가 쓴 문장을 정해진 설정값이나 맥락 규칙으로 바꾼다.

Jev 방식이다. 해석기(PolicyInterpreter)는 글을 만들지 않고, 질문마다 정해진 선택지 중 하나와 확신도(Choice)만 돌려준다.
interpret()가 아래 질문을 차례로 던지고 답을 모아 정책 항목(gate.policy.Entry)을 만든다. 이 모듈은 파일을 건드리지 않는다.
저장은 사용자 확인을 받은 뒤 gate/policy_cli.py가 한다.

질문 구성 (질문 키: 선택지)
  1. type: number | entity | path | context | unknown     문장 종류
       number → 2.   entity → 6.   context → 5.   path → 경로 규칙 안내(저장하지 않음).   unknown → 다시 말해 달라고 한다.
  2. kind: KINDS                                   어떤 번호인가 (rrn, foreigner_rrn, card, mobile, phone, email, account, secret)
  3. target: action | visible                      그 번호의 처리 방식을 바꾸나, 가릴 때 공개 범위를 바꾸나
  4. action: deny | ask_human | mask               (target이 action일 때)
     visible: 그 종류의 공개 범위                    (target이 visible일 때. gate.detectors.VISIBLE_CHOICES)
       → Entry(settings={"numbers.<kind>.<target>": 답}, said=문장)
  6. entity: ENTITY_OPTIONS                        NER·규칙이 찾는 어떤 정보인가 (ENTITY_GROUPS 9개 + unsupported) (3d, 3e)
       unsupported → 저장하지 않는다. 날짜·사건·시간·연도·직함·과목은 2026-11-01 NER 교체로 찾지 않는다.
  7. entity_action: deny | ask_human | mask | allow  그 정보를 어떻게 처리하나
       → Entry(settings={"entities.<묶음>.action": 답}, said=문장)
       전화번호·이메일·비밀값은 NER이 찾아도 numbers 칸을 따르므로 number 쪽 문장이다.
       plain 묶음은 나이 하나다. allow는 기본보다 느슨해서 저장 전에 한 번 더 확인한다(gate.policy.loosened).
  5. direction: stricter | looser                  맥락 문장이 더 막는 쪽인가, 더 보여 주는 쪽인가
       stricter → Entry(context=문장, said=문장). 문장은 해석하지 않고 그대로 Jev 질문의 "사용자 정책" 칸에 들어간다.
       looser   → 저장하지 않는다(unsupported). Jev 질문은 사용자 정책을 "보여주면 안 되는 내용"으로만 쓰고,
                  맥락 규칙을 더하는 것은 gate.policy.loosened가 완화로 보지 않아 두 번째 확인 없이 들어가기 때문이다.

저장하지 않는 경우 (모르면 바꾸지 않는다. fail-closed와 같은 뜻)
  - unclear: 답 하나라도 확신도가 threshold(기본 0.7) 미만이거나 type이 unknown. 다시 말해 달라고 한다. 추측해서 저장하지 않는다.
  - error: 해석기 오류. 예외, 타임아웃, 선택지 밖의 답, Choice가 아닌 응답.
  - path: 경로 패턴은 선택지로 고를 수 없어 `gate policy set path <패턴> <동작>`을 안내한다.
  - unsupported: 느슨하게 하는 맥락 문장, 지금 찾지 않는 정보(날짜·사건·시간·연도·직함·과목)에 대한 문장.

해석기는 make_interpreter(이름)으로 고른다(jev_client.make_client와 같은 구조).
  - mock: 키워드 규칙. 기본값이다.
  - laya: 로컬 laya-serve에 질문마다 선택형(choice) 질문을 zero-shot으로 묻는다(3d 실험). 평가는 eval/run_policy_text.py.
"""

import math
import os
import re
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from gate.detectors import KINDS
from gate.jev_client import LAYA_DEFAULT_MODEL, JevError, laya_endpoint, laya_opener, laya_post
from gate.ner_client import ENTITY_GROUPS, RULE_GROUPS
from gate.policy import ACTION_NAMES, KIND_NAMES, VISIBLE_NAMES, Entry, PolicyError, describe_setting, setting_choices

CONFIDENCE_THRESHOLD = 0.7
DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_SENTENCE_CHARS = 500  # jev_client가 사용자 정책 한 줄을 500자에서 자르므로 그보다 긴 문장은 받지 않는다

Outcome = Literal["setting", "context", "path", "unsupported", "unclear", "error"]

TYPE_OPTIONS = ("number", "entity", "path", "context", "unknown")
TARGET_OPTIONS = ("action", "visible")
DIRECTION_OPTIONS = ("stricter", "looser")
ENTITY_ACTION_OPTIONS = ("deny", "ask_human", "mask", "allow")  # gate.policy의 entities.<묶음>.action 선택지와 같다
ENTITY_OPTIONS = (*ENTITY_GROUPS, "unsupported")  # unsupported: 지금 찾지 않는 정보(저장하지 않음)

# 질문 키 → 질문 첫 줄. 첫 줄은 키마다 고정이라 question_key()로 어느 질문인지 알 수 있다.
QUESTION_TITLES: dict[str, str] = {
    "type": "이 문장은 어떤 종류의 정책인가요?",
    "kind": "이 문장은 어떤 번호에 대한 것인가요?",
    "target": "이 번호의 무엇을 바꾸라는 것인가요?",
    "action": "이 번호가 있는 파일을 어떻게 처리하라는 것인가요?",
    "visible": "이 번호를 가릴 때 어디까지 보여 주라는 것인가요?",
    "direction": "이 문장은 더 막는 쪽인가요, 더 보여 주는 쪽인가요?",
    "entity": "이 문장은 문서 속 어떤 정보에 대한 것인가요?",
    "entity_action": "이 정보를 어떻게 처리하라는 것인가요?",
}
_TITLE_TO_KEY = {title: key for key, title in QUESTION_TITLES.items()}
_UNSURE = {
    "type": "어떤 종류의 정책인지", "kind": "어떤 번호인지", "target": "무엇을 바꾸라는 것인지",
    "action": "어떻게 처리하라는 것인지", "visible": "어디까지 보여 주라는 것인지", "direction": "막는 쪽인지 보여 주는 쪽인지",
    "entity": "어떤 정보인지", "entity_action": "어떻게 처리하라는 것인지",
}

_OPTION_GUIDES: dict[str, dict[str, str]] = {
    "type": {
        "number": "주민등록번호·카드번호·전화번호·이메일·계좌번호·비밀번호처럼 형식이 정해진 번호나 비밀값을 어떻게 처리할지",
        "entity": "사람 이름·주소·생년월일·식별번호(학번·사번 등)·소속(학교·회사·병원)·장소·나이·IP 주소처럼 "
                  "문서에 나오는 특정 정보를 가릴지 보여줄지",
        "path": "특정 폴더나 파일 이름(경로)에 대한 규칙",
        "context": "문서의 내용이나 주제(예: 병원 기록, 가족 이야기)를 보여줄지에 대한 규칙",
        "unknown": "정책 문장이 아니거나 알 수 없음",
    },
    "kind": KIND_NAMES,
    "target": {
        "action": "이 번호가 있는 파일을 가려서 보여줄지, 사람에게 물어볼지, 거부할지",
        "visible": "번호를 가릴 때 어느 부분까지 보여줄지",
    },
    "action": {key: ACTION_NAMES[key] for key in ("deny", "ask_human", "mask")},
    "visible": VISIBLE_NAMES,
    "direction": {
        "stricter": "보여주지 말라거나 가리라거나 물어보라는 쪽",
        "looser": "보여줘도 된다거나 허용하라는 쪽",
    },
    # NER 묶음(gate.ner_client.ENTITY_GROUPS)과 unsupported. ENTITY_NAMES보다 풀어 쓴다. 해석기가 고를 때 읽는 설명이다.
    "entity": {
        "name": "사람 이름(본인·가족·지인·상담사·교수 등)",
        "address": "집 주소나 사는 곳",
        "birth": "생년월일이나 생일",
        "student_id": "학번·사번·여권번호·면허번호·회원번호 같은 식별번호",
        "org": "학교·학과·부서·회사·기관·병원·동아리·프로젝트 같은 소속 이름",
        "place": "장소나 지역·지명",
        "etc": "메신저·SNS 아이디 같은 그 밖의 연락처",
        "host": "IP 주소나 내부 서버 주소",
        "plain": "나이",
        "unsupported": "날짜·사건·시간·연도·직함·과목처럼 지금은 따로 찾지 않는 정보",
    },
    "entity_action": {
        "deny": "이 정보가 있는 파일은 읽기를 거부",
        "ask_human": "이 정보가 있는 파일은 사람에게 물어본 뒤 보여줌",
        "mask": "이 정보를 가려서 보여줌",
        "allow": "이 정보를 가리지 않고 그대로 보여줌",
    },
}

_EXAMPLES = (
    "주민번호는 생년월일까지만 보여줘",
    "카드번호 있는 파일은 거부",
    "이메일은 물어보고 보여줘",
    "이름은 가려줘",
    "병원 기록은 보여주지 마",
)
_ACTION_NOTES = {
    "deny": "이 번호가 있는 파일은 읽기를 거부합니다.",
    "ask_human": "이 번호가 있는 파일은 사람에게 물어본 뒤 보여줍니다(허용해도 번호는 가린 채로 보여줍니다).",
    "mask": "번호를 가린 뒤 보여줍니다.",
}
# {finder}·{sample}은 묶음에 따라 바뀐다: NER·[이름], IP·내부 주소(host)는 규칙·[IP] (_entity_action_note)
_ENTITY_ACTION_NOTES = {
    "deny": "{finder}이 이 정보를 찾은 파일은 읽기를 거부합니다.",
    "ask_human": "{finder}이 이 정보를 찾은 파일은 사람에게 물어본 뒤 보여줍니다(허용해도 이 정보는 가린 채로 보여줍니다).",
    "mask": "{finder}이 찾은 부분을 {sample}처럼 종류 이름으로 바꿔 보여줍니다.",
    "allow": "{finder}이 찾아도 가리지 않습니다. 다만 판단 모델(Jev)이 문서를 민감하다고 보면 사람에게 묻거나 막을 수 있습니다.",
}
UNSUPPORTED_ENTITY_MESSAGE = (
    "지금은 날짜·사건·시간·연도·직함·과목을 따로 찾지 않아요. 그래서 저장하지 않았어요.\n"
    "나이·생년월일은 \"나이는 가려줘\"처럼 정할 수 있어요."
)


class InterpretError(Exception):
    """해석기 실패. 받는 쪽은 저장하지 않는다."""


@dataclass(frozen=True)
class Choice:
    """해석기의 답. 답이 문자열이 아니거나 확신도가 범위 밖이면 만들 때 InterpretError를 던진다."""
    answer: str        # options 중 하나
    confidence: float  # 0.0 ~ 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.answer, str) or not self.answer:
            raise InterpretError("해석기 답이 비어 있지 않은 문자열이 아닙니다")
        c = self.confidence
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not math.isfinite(c) or not 0.0 <= c <= 1.0:
            raise InterpretError("해석기 확신도가 0.0~1.0 사이 숫자가 아닙니다")
        object.__setattr__(self, "confidence", float(c))


class PolicyInterpreter(Protocol):
    def choose(self, sentence: str, question: str, options: Sequence[str]) -> Choice:
        """sentence에 대해 question의 options 중 하나를 고르고 확신도를 붙인다. 실패하면 InterpretError."""
        ...


@dataclass(frozen=True)
class Step:
    key: str                  # QUESTION_TITLES의 키
    question: str
    options: tuple[str, ...]
    choice: Choice


@dataclass(frozen=True)
class Interpretation:
    outcome: Outcome
    sentence: str               # 공백을 정리한 문장 (저장할 때 said)
    message: str                # 사용자에게 보여 줄 한국어. setting·context면 "이렇게 이해했어요"
    entry: Entry | None = None  # setting·context일 때만. 사용자 확인 뒤 저장한다
    steps: tuple[Step, ...] = ()

    @property
    def saveable(self) -> bool:
        return self.entry is not None


def question_key(question: str) -> str | None:
    """질문 첫 줄로 질문 키를 찾는다. 모르는 질문이면 None."""
    return _TITLE_TO_KEY.get(question.split("\n", 1)[0].strip())


def build_question(key: str, options: Sequence[str], detail: str = "") -> str:
    """해석기에 보낼 질문. 첫 줄은 키마다 고정이고, 선택지마다 뜻을 붙인다. 문장은 choose의 sentence로 따로 넘긴다."""
    guides = _OPTION_GUIDES[key]
    lines = [QUESTION_TITLES[key]]
    if detail:
        lines.append(detail)
    lines.append("선택지:")
    lines += [f"- {opt}: {guides.get(opt, opt)}" for opt in options]
    lines.append(f"답: {', '.join(options)} 중 하나")
    return "\n".join(lines)


class _Unsure(Exception):
    def __init__(self, step: Step) -> None:
        self.step = step


def _normalize_sentence(sentence: object) -> str:
    if not isinstance(sentence, str):
        return ""
    return " ".join(unicodedata.normalize("NFC", sentence).split())


def _unclear(text: str, reason: str, steps: Sequence[Step] = ()) -> Interpretation:
    examples = "\n".join(f"  {ex}" for ex in _EXAMPLES)
    message = (
        f"문장을 확실히 이해하지 못했어요({reason}). 저장하지 않았습니다.\n"
        f"다시 말해 주세요. 예:\n{examples}\n"
        "또는 `gate policy set <키> <값>`으로 직접 지정할 수 있어요."
    )
    return Interpretation("unclear", text, message, None, tuple(steps))


def interpret(
    sentence: str, interpreter: PolicyInterpreter, *, threshold: float = CONFIDENCE_THRESHOLD
) -> Interpretation:
    """문장을 해석한다. 저장할 항목은 entry에 담고, 저장하면 안 되는 경우는 entry가 None이다(모듈 docstring)."""
    text = _normalize_sentence(sentence)
    if not text:
        return _unclear(text, "문장이 비어 있음")
    if len(text) > MAX_SENTENCE_CHARS:
        return _unclear(text, f"문장이 {MAX_SENTENCE_CHARS}자보다 김")
    steps: list[Step] = []

    def ask(key: str, options: Sequence[str], detail: str = "") -> str:
        options = tuple(options)
        question = build_question(key, options, detail)
        choice = interpreter.choose(text, question, options)
        if not isinstance(choice, Choice):
            raise InterpretError(f"해석기 응답 형식이 올바르지 않습니다: {type(choice).__name__}")
        if choice.answer not in options:
            raise InterpretError("해석기가 선택지 밖의 답을 했습니다")
        step = Step(key, question, options, choice)
        steps.append(step)
        if choice.confidence < threshold:
            raise _Unsure(step)
        return choice.answer

    try:
        kind_of_sentence = ask("type", TYPE_OPTIONS)
        if kind_of_sentence == "unknown":
            return _unclear(text, "정책 문장으로 보이지 않음", steps)
        if kind_of_sentence == "path":
            message = (
                "폴더나 파일 이름(경로)에 대한 규칙으로 보여요. 경로 패턴은 문장에서 정확히 고를 수 없어서 저장하지 않았습니다.\n"
                "다음처럼 직접 지정하세요:\n"
                "  gate policy set path \"<패턴>\" <allow|ask_human|deny>\n"
                "  예: gate policy set path \"공개/**\" allow"
            )
            return Interpretation("path", text, message, None, tuple(steps))
        if kind_of_sentence == "context":
            if ask("direction", DIRECTION_OPTIONS) == "looser":
                message = (
                    "문서 내용에 대한 규칙을 느슨하게 하는 문장으로 보여요. 저장하지 않았습니다.\n"
                    "맥락 규칙은 더 막는 쪽(예: \"병원 기록은 보여주지 마\")으로만 지정할 수 있어요.\n"
                    "특정 폴더를 그대로 보여 주려면: gate policy set path \"<패턴>\" allow"
                )
                return Interpretation("unsupported", text, message, None, tuple(steps))
            entry = Entry(context=text, said=text)
            message = (
                "이렇게 이해했어요:\n"
                f"  맥락 규칙: \"{text}\"\n"
                "  파일을 보여 줄지 Jev가 판단할 때 이 문장을 그대로 함께 봅니다."
            )
            return Interpretation("context", text, message, entry, tuple(steps))
        if kind_of_sentence == "entity":
            group = ask("entity", ENTITY_OPTIONS)
            if group == "unsupported":
                return Interpretation("unsupported", text, UNSUPPORTED_ENTITY_MESSAGE, None, tuple(steps))
            key = f"entities.{group}.action"
            value = ask("entity_action", setting_choices(key), f"정보 종류: {group} ({_OPTION_GUIDES['entity'][group]})")
            entry = Entry(settings={key: value}, said=text)
            lines = ["이렇게 이해했어요:", f"  {describe_setting(key, value)}", f"  {_entity_action_note(group, value)}",
                     f"  (설정 {key} = {value})"]
            return Interpretation("setting", text, "\n".join(lines), entry, tuple(steps))

        kind = ask("kind", KINDS)
        detail = f"번호 종류: {kind} ({KIND_NAMES[kind]})"
        target = ask("target", TARGET_OPTIONS, detail)
        key = f"numbers.{kind}.{target}"
        value = ask(target, setting_choices(key), detail)
        entry = Entry(settings={key: value}, said=text)
        lines = ["이렇게 이해했어요:", f"  {describe_setting(key, value)}"]
        if target == "action":
            lines.append(f"  {_ACTION_NOTES[value]}")
        lines.append(f"  (설정 {key} = {value})")
        return Interpretation("setting", text, "\n".join(lines), entry, tuple(steps))
    except _Unsure as exc:
        step = exc.step
        return _unclear(text, f"{_UNSURE[step.key]} 확실하지 않음, 확신도 {step.choice.confidence:.2f}", steps)
    except (InterpretError, PolicyError) as exc:
        return _error(text, str(exc), steps)
    except Exception as exc:  # 해석기 구현이 어떤 예외를 던지든 저장하지 않는다
        return _error(text, f"해석기 오류: {type(exc).__name__}", steps)


def _entity_action_note(group: str, value: str) -> str:
    finder, sample = ("규칙", "[IP]") if group in RULE_GROUPS else ("NER", "[이름]")
    return _ENTITY_ACTION_NOTES[value].format(finder=finder, sample=sample)


def _error(text: str, reason: str, steps: Sequence[Step]) -> Interpretation:
    message = (
        f"문장 해석기에 문제가 있어 저장하지 않았습니다({reason}).\n"
        "잠시 뒤 다시 하거나 `gate policy set <키> <값>`으로 직접 지정하세요."
    )
    return Interpretation("error", text, message, None, tuple(steps))


# --- Mock ---

MockMode = Literal["keyword", "fixed"]

# keyword 방식의 단서. 흔한 표현 몇 개일 뿐 실제 Jev가 문장을 어떻게 이해하는지와 무관하다.
# 표 순서대로 찾고 찾은 말은 지운다. 그래서 앞 선택지의 말이 뒤 선택지에 다시 걸리지 않는다
# (예: "휴대전화번호"가 일반 전화의 "전화번호"로도 잡히지 않음, "생년월일과 성별"이 birth로도 잡히지 않음).
_KIND_CUES: dict[str, tuple[str, ...]] = {
    "secret": ("비밀번호", "비밀 번호", "비번", "패스워드", "암호", "비밀값", "비밀 값", "API 키", "api 키", "API키", "api키",
               "API key", "api key", "토큰", "token", "시크릿", "secret", "액세스 키", "개인 키", "비밀 키", "비밀키"),
    "foreigner_rrn": ("외국인등록번호", "외국인 등록번호", "외국인번호", "외국인 번호"),
    "rrn": ("주민등록번호", "주민 등록번호", "주민번호", "주민 번호"),
    "mobile": ("휴대폰", "핸드폰", "휴대전화", "휴대 전화", "모바일"),
    "phone": ("일반 전화", "유선 전화", "집 전화", "집전화", "사무실 전화", "전화번호", "전화 번호"),
    "card": ("카드번호", "카드 번호", "신용카드", "체크카드", "카드"),
    "email": ("이메일 주소", "이메일", "메일 주소", "메일주소", "전자우편", "e-mail", "email"),
    "account": ("계좌번호", "계좌 번호", "계좌", "통장"),
}
_PATH_CUES = ("폴더", "디렉터리", "디렉토리", "경로", "확장자", "파일 이름", "파일이름", "파일명", "이름에", "/", "*")
_FILE_NAME = re.compile(r"[0-9a-z가-힣_\-]+\.[a-z0-9]{2,5}(?![a-z0-9])")  # "주소록.xlsx"처럼 확장자가 붙은 파일 이름
# 한 묶음으로 정할 수 없는 넓은 말. 맥락 규칙으로 저장하지 않고 다시 말해 달라고 한다("개인정보는 다 가려줘").
_BROAD_CUES = ("개인정보", "개인 정보", "연락처", "신상", "인적 사항", "인적사항", "민감 정보", "민감한 정보")
# NER 묶음 단서(3d, 3e). 표 순서대로 찾고 지우므로 "학교 이름"(org)·"ip 주소"(host)·"과목 이름"(unsupported)이 먼저 걸려
# 맨 뒤 name의 "이름"이나 address의 "주소"로 다시 잡히지 않는다. "병원"·"회사"·"사건"처럼 주제로도 쓰는 맨 낱말은 넣지 않는다
# ("병원 기록은 보여주지 마"는 맥락 규칙이다). 그래서 "~ 이름"·"~명"처럼 정보 종류임이 드러나는 말만 넣는다.
# unsupported는 2026-11-01 NER 교체로 찾지 않는 정보(예전 date·event 묶음과 plain의 나이 아닌 것)다. 묶음 문장으로 알아보되
# 저장하지 않는다. birth의 "출생 연도"는 unsupported의 "연도"보다 먼저 지운다(생년은 생년월일로 찾는다).
_ENTITY_CUES: dict[str, tuple[str, ...]] = {
    "host": ("아이피 주소", "아이피주소", "아이피", "내부 주소", "내부주소", "서버 주소", "서버주소", "호스트 이름", "호스트명", "호스트",
             "내부 서버", "사내 주소"),  # 영문 "ip"는 _keyword_choice가 낱말일 때만 "아이피"로 바꾼다("zip"에 걸리지 않게)
    "org": ("학교 이름", "학교이름", "학교명", "다니는 학교", "회사 이름", "회사이름", "회사명", "다니는 회사", "직장 이름", "직장명",
            "병원 이름", "병원이름", "병원명", "기관 이름", "기관명", "단체 이름", "단체명", "동아리 이름", "동아리명", "모임 이름",
            "프로젝트 이름", "프로젝트명", "사업 이름", "사업명", "학과 이름", "학과명", "부서 이름", "부서명", "학과", "부서",
            "소속"),
    "student_id": ("학생 번호", "학생번호", "학번", "식별번호", "식별 번호", "사번", "사원 번호", "사원번호", "직원 번호",
                   "직원번호", "여권번호", "여권 번호", "면허번호", "면허 번호", "차량번호", "차량 번호", "회원번호",
                   "회원 번호"),
    "birth": ("생년월일", "생일", "출생일", "태어난 날", "출생 연도", "출생연도", "출생년도", "태어난 해"),
    "address": ("집 주소", "주소", "사는 곳", "사는곳", "거주지", "도로명", "번지"),
    "place": ("장소 이름", "장소", "지명", "지역 이름", "지역명", "지역", "동네", "도시 이름", "도시명", "나라 이름"),
    "unsupported": ("날짜", "일자", "며칠", "몇 월",
                    "사건 이름", "사건명", "행사 이름", "행사명", "대회 이름", "대회명",
                    "과목 이름", "과목명", "수업 이름", "강의 이름", "강의명", "과목", "연도", "년도", "시각", "시간",
                    "직함", "직급", "직책"),
    "etc": ("기타 민감", "기타 개인", "기타 정보", "그 밖의 민감", "그 밖의 개인", "그밖의 민감", "메신저 아이디",
            "메신저 id", "sns 아이디", "sns id", "카톡 아이디", "카카오톡 아이디", "카톡 id"),
    "plain": ("나이", "연령", "몇 살", "몇살"),
    "name": ("사람 이름", "실명", "성명", "성함", "이름", "인명"),
}
# entity_action 단서(3d). 단계 순서대로 찾고 지운다. 같은 말이 여러 동작에 걸리지 않게 하려는 순서다.
#   0. 이중 부정·의무("가리지 않으면 안", "가려야")는 mask다. 뒤의 "가리지 않"에 allow로 잡히지 않게 맨 먼저 지운다.
#   1. "가리지 마"류(allow): 뒤의 "가리"에 mask로 잡히지 않게 먼저 지운다.
#   2. "물어보고 보여"·"보여주기 전에"·"허락 없이 보여주지 마"(ask_human), 3. "거부"류(deny): 뒤의 "보여"에 allow로
#      잡히지 않게 먼저 지운다.
#   4. "보여주지 마"류: 파일 단위 말("있는 파일", "있으면")이 있으면 deny, 없으면 mask("이름은 보여주지 마" = 이름을 가림).
#   5. "가려서 보여"·"바꿔서 보여"·"가려"류(mask), 6. 남은 "보여줘"류(allow).
# 서로 다른 동작이 둘 이상 걸리면 확신도 0.5다("나이는 가리고 연도는 보여줘").
# 묶음에는 공개 범위가 없으므로 "까지"류(_PARTIAL_CUES)가 있으면 확신도 0.5다("주소는 시·도까지만 보여줘").
_ENTITY_ACTION_STEPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mask", ("가리지 않으면 안", "숨기지 않으면 안", "가려야", "숨겨야", "가려 둬야")),
    ("allow", ("가리지 마", "가리지마", "가리지 말", "가리지 않", "안 가려", "안가려", "안 가리", "숨기지 마", "숨기지 말", "숨기지 않",
               "감추지 마", "감추지 말", "감추지 않", "지우지 마", "지우지 말", "안 숨겨", "가릴 필요 없", "숨길 필요 없",
               "감출 필요 없")),
    ("ask_human", ("허락 없이 보여주지 마", "허락 없이는 보여주지 마", "허락 없이", "보여주기 전에", "보여 주기 전에", "물어보고 보여",
                   "물어보", "물어봐", "물어 보", "확인받고 보여", "확인받", "확인 받", "허락", "승인")),
    ("deny", ("거부", "차단", "막아", "막기", "막을", "열지 마", "열지마", "열지 못", "읽지 마", "읽지마", "읽지 못", "못 읽", "못 열",
              "금지")),
    ("hide", ("보여주지 마", "보여 주지 마", "보여주지마", "보여주지 말", "보여주면 안", "보여 주면 안", "안 보이게", "안보이게",
              "보이지 않게", "보이지 말", "못 보게", "못보게")),
    ("mask", ("가려서 보여", "가린 채 보여", "가린 채로 보여", "가리고 보여", "숨겨서 보여", "지우고 보여", "지워서 보여", "빼고 보여",
              "바꿔서 보여", "바꿔 보여", "바꿔서", "가려", "가리", "가린", "가렸", "숨겨", "숨기", "숨겼", "감춰", "감추", "감췄", "지워",
              "지우", "지웠", "마스킹",
              "익명", "빼줘", "빼 줘")),
    ("allow", ("그대로", "보여줘도", "보여 줘도", "봐도", "보여도", "괜찮", "허용", "공개", "보여줘", "보여 줘", "보여주", "보이게")),
)
_FILE_LEVEL_CUES = ("있는 파일", "있는 문서", "들어간 파일", "들어간 문서", "나오는 파일", "나오는 문서", "포함된 파일", "포함된 문서",
                    "적힌 파일", "적힌 문서", "있으면", "들어 있으면", "들어있으면", "나오면", "적혀 있으면")
_PARTIAL_CUES = ("까지", "성만", "앞글자만", "첫 글자만")
_ACTION_CUES: dict[str, tuple[str, ...]] = {
    "mask": ("가려서 보여", "가려서 보이", "가린 채", "가리고 보여", "마스킹", "가려"),  # "이메일 주소는 가려줘"
    "ask_human": ("물어보", "물어봐", "물어 보", "확인받", "확인 받", "허락", "승인"),
    "deny": ("거부", "차단", "막아", "막기", "막을", "열지 마", "읽지 마", "금지"),
}
_VISIBLE_CUES: dict[str, tuple[str, ...]] = {
    "birth_gender": ("생년월일과 성별", "생년월일이랑 성별", "생년월일하고 성별", "성별", "앞 7자리", "앞7자리", "앞 일곱"),
    "birth": ("생년월일", "생일", "앞 6자리", "앞6자리", "앞 여섯", "앞자리"),  # 주민번호 "앞자리" = 하이픈 앞 6자리
    "none": ("전부 가려", "다 가려", "모두 가려", "전부 가리", "다 가리", "모두 가리", "전부 숨겨", "다 숨겨",
             "하나도 보여주지"),
    "prefix": ("앞자리", "앞 자리", "앞 번호", "앞번호", "앞 3자리", "앞3자리"),
    "area": ("지역번호", "지역 번호"),
    "tld": ("최상위 도메인", "도메인", ".com"),
    "last4": ("끝 4자리", "끝4자리", "끝 네 자리", "끝 네자리", "끝자리", "끝 자리", "뒤 4자리", "뒤4자리", "뒷자리", "뒷 4자리",
              "마지막 4자리", "마지막 네 자리"),
}
_TARGET_CUES: dict[str, tuple[str, ...]] = {
    # "생년월일까지만 보여주고 나머지는 가려줘"의 "나머지는 가려"는 공개 범위 말이다(action의 "가려"보다 먼저 지운다)
    "visible": (*(cue for cues in _VISIBLE_CUES.values() for cue in cues), "까지", "나머지는 가려", "나머지는 가리", "나머지 가려"),
    "action": tuple(cue for cues in _ACTION_CUES.values() for cue in cues),
}
_DIRECTION_CUES: dict[str, tuple[str, ...]] = {
    "stricter": ("보여주지 마", "보여주지마", "보여 주지 마", "보여주면 안", "보이지 않게", "안 보이게", "숨겨", "가려",
                 "가리", "막아", "거부", "차단", "금지", "빼고", "제외", "물어보", "물어봐", "확인받", "확인 받", "읽지 마",
                 "읽지 못", "못 읽", "열지 마", "열지 못"),
    "looser": ("보여줘도", "보여 줘도", "봐도 돼", "봐도 된", "보여도 돼", "보여도 된", "허용", "괜찮", "그냥 보여",
               "다 보여", "전부 보여", "풀어"),
}
_SURE, _AMBIGUOUS, _UNMATCHED, _CONTEXT_GUESS = 0.9, 0.5, 0.2, 0.8


@dataclass(frozen=True)
class MockCall:
    sentence: str
    question: str
    options: tuple[str, ...]
    key: str | None  # question_key(question)


class MockPolicyInterpreter:
    """결정적인 가짜 해석기. 테스트와 시연용이며 **Jev의 실제 성능을 대변하지 않는다.**

    - keyword: 단순 키워드 규칙(시연용). 선택지마다 정해 둔 말이 문장에 있는지 본다. 하나만 맞으면 확신도 0.9,
      여럿이 맞으면 첫 것을 0.5로, 아무것도 맞지 않으면 첫 선택지를 0.2로 답한다(임계값 0.7 미만이라 다시 묻게 된다).
      type 질문은 번호·묶음·경로 단서 중 둘 이상이 함께 있으면 0.5, 셋 다 없고 막거나 푸는 말이 있으면 context 0.8,
      아무 단서도 없으면 unknown이다(_type_choice). entity_action은 단계별 단서(_ENTITY_ACTION_STEPS)로 고른다.
      확신도는 임의로 정한 상수다.
    - fixed: answers={질문 키: (답, 확신도)}로 질문별 답을 정해 둔다. 테스트용. 정하지 않은 질문은 InterpretError.
    - error를 주면 choose가 항상 그 예외를 던진다. delay는 지연을 흉내 낸다. 둘 다 실패 경로 시험용이다.
    - calls에 받은 질문(문장, 질문, 선택지, 질문 키)을 남긴다.
    """

    def __init__(
        self,
        mode: MockMode = "keyword",
        *,
        answers: Mapping[str, tuple[str, float]] | None = None,
        error: BaseException | None = None,
        delay: float = 0.0,
    ) -> None:
        if mode == "fixed":
            if not answers:
                raise InterpretError("Mock fixed 방식은 answers(질문 키별 답)를 정해야 합니다")
            unknown = set(answers) - set(QUESTION_TITLES)
            if unknown:
                raise InterpretError(f"모르는 질문 키: {', '.join(sorted(unknown))}")
            for answer, confidence in answers.values():
                Choice(answer, confidence)  # 값 검증
        elif mode == "keyword":
            if answers:
                raise InterpretError("answers는 Mock fixed 방식에서만 씁니다")
        else:
            raise InterpretError(f"알 수 없는 Mock 방식: {mode!r} (keyword 또는 fixed)")
        self.mode = mode
        self.answers = dict(answers or {})
        self.error = error
        self.delay = delay
        self.calls: list[MockCall] = []

    def choose(self, sentence: str, question: str, options: Sequence[str]) -> Choice:
        options = tuple(options)
        key = question_key(question)
        self.calls.append(MockCall(sentence, question, options, key))
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if key is None:
            raise InterpretError("Mock 해석기가 모르는 질문입니다")
        if self.mode == "fixed":
            if key not in self.answers:
                raise InterpretError(f"Mock fixed 방식에 {key} 질문의 답이 없습니다")
            return Choice(*self.answers[key])
        return _keyword_choice(key, sentence.casefold(), options)


def _consume(text: str, words: Sequence[str], *, once: bool = False) -> tuple[str, bool]:
    """text에서 words를 긴 것부터 지운다(once면 낱말마다 처음 한 번만). 하나라도 있었는지 함께 돌려준다."""
    hit = False
    for word in sorted(words, key=len, reverse=True):
        if word in text:
            text = text.replace(word, " ", 1 if once else -1)
            hit = True
    return text, hit


def _found(found: Sequence[str], options: Sequence[str]) -> Choice:
    if len(found) == 1:
        return Choice(found[0], _SURE)
    if found:
        return Choice(found[0], _AMBIGUOUS)
    return Choice(options[0], _UNMATCHED)


def _pick(text: str, cues: Mapping[str, Sequence[str]], options: Sequence[str]) -> Choice:
    found: list[str] = []
    rest = text
    for option, words in cues.items():
        if option not in options:
            continue
        rest, hit = _consume(rest, words)
        if hit:
            found.append(option)
    return _found(found, options)


_ALL_KIND_CUES = tuple(c for cues in _KIND_CUES.values() for c in cues)
_ALL_VISIBLE_CUES = (*(c for cues in _VISIBLE_CUES.values() for c in cues), "까지")
_ALL_ENTITY_CUES = tuple(c for cues in _ENTITY_CUES.values() for c in cues)


def _type_choice(text: str) -> Choice:
    """번호 → 경로 → 묶음 단서 순서로 찾고 지운다. 둘 이상 걸리면 0.5(다시 묻게 됨), 하나면 0.9.

    - "이메일 주소"는 번호 단서가 먼저 지워서 묶음(주소)으로 다시 잡히지 않는다. "파일 이름"도 경로 단서가 먼저 지운다.
    - 번호 단서가 있으면 공개 범위 말("생년월일", "지역번호", "까지")도 처음 한 번만 지운다. "주민번호는 생년월일까지만 보여줘"의
      "생년월일"은 번호를 어디까지 보여 줄지이지 묶음(birth)이 아니다. "…까지만 보여주고 생년월일은 가려줘"처럼 한 번 더 나오면
      묶음으로 남아 둘이 섞인 문장(0.5)이 된다.
    - 남은 글에 "번호"가 있으면 번호 문장이다("번호는 다 거부"). "학번"·"학생 번호"는 묶음 단서가 먼저 지운다.
    - 확장자가 붙은 파일 이름("주소록.xlsx")은 경로 단서다. "개인정보"·"연락처" 같은 넓은 말(_BROAD_CUES)이 남으면 0.5다.
    """
    rest, number = _consume(text, _ALL_KIND_CUES)
    if number:
        rest, _ = _consume(rest, _ALL_VISIBLE_CUES, once=True)
    path = bool(_FILE_NAME.search(rest))
    rest = _FILE_NAME.sub(" ", rest)
    rest, path_word = _consume(rest, _PATH_CUES)
    path = path or path_word
    rest, entity = _consume(rest, _ALL_ENTITY_CUES)
    rest, broad = _consume(rest, _BROAD_CUES)
    number = number or "번호" in rest
    hits = [name for name, hit in (("number", number), ("entity", entity), ("path", path)) if hit]
    if broad:
        return Choice(hits[0] if hits else "entity", _AMBIGUOUS)
    if hits:
        return Choice(hits[0], _SURE if len(hits) == 1 else _AMBIGUOUS)
    if any(c in text for cues in _DIRECTION_CUES.values() for c in cues):
        return Choice("context", _CONTEXT_GUESS)
    return Choice("unknown", _SURE)


def _entity_action_choice(text: str, options: Sequence[str]) -> Choice:
    found: list[str] = []
    rest = text
    for option, words in _ENTITY_ACTION_STEPS:
        rest, hit = _consume(rest, words)
        if not hit:
            continue
        if option == "hide":
            option = "deny" if any(c in text for c in _FILE_LEVEL_CUES) else "mask"
        if option in options and option not in found:
            found.append(option)
    if any(c in text for c in _PARTIAL_CUES):  # 묶음에는 공개 범위가 없다
        return Choice(found[0] if found else options[0], _AMBIGUOUS)
    return _found(found, options)


_IP_WORD = re.compile(r"(?<![a-z])ip(?![a-z])")


def _keyword_choice(key: str, text: str, options: tuple[str, ...]) -> Choice:
    text = _IP_WORD.sub("아이피", text)
    if key == "type":
        return _type_choice(text)
    if key == "entity_action":
        return _entity_action_choice(text, options)
    table = {"kind": _KIND_CUES, "target": _TARGET_CUES, "action": _ACTION_CUES, "visible": _VISIBLE_CUES,
             "direction": _DIRECTION_CUES, "entity": _ENTITY_CUES}[key]
    return _pick(text, table, options)


class FailClosedInterpreter:
    """다른 해석기를 감싸 어떤 실패든 InterpretError로 바꾼다(저장하지 않는 쪽으로).

    - timeout초 안에 답이 없으면 InterpretError. 안쪽 호출은 데몬 스레드에서 돌고, 시간이 지나면 더 기다리지 않는다.
    - 안쪽 예외는 종류와 상관없이 InterpretError로 바꾼다. 메시지에는 예외 종류만 넣는다.
    - Choice가 아니거나 선택지 밖의 답도 InterpretError다.
    """

    def __init__(self, inner: PolicyInterpreter, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) \
                or timeout <= 0:
            raise InterpretError("해석기 타임아웃은 0보다 큰 숫자(초)여야 합니다")
        self.inner = inner
        self.timeout = float(timeout)

    def choose(self, sentence: str, question: str, options: Sequence[str]) -> Choice:
        options = tuple(options)
        outcome: list[tuple[bool, object]] = []

        def run() -> None:
            try:
                outcome.append((True, self.inner.choose(sentence, question, options)))
            except BaseException as exc:  # 스레드 안의 모든 실패를 호출한 쪽으로 넘긴다
                outcome.append((False, exc))

        worker = threading.Thread(target=run, name="policy-interpret", daemon=True)
        worker.start()
        worker.join(self.timeout)
        if not outcome:
            raise InterpretError(f"해석기 응답이 {self.timeout:g}초 안에 오지 않았습니다")
        ok, value = outcome[0]
        if not ok:
            if isinstance(value, InterpretError):
                raise value
            raise InterpretError(f"해석기 호출 중 오류가 났습니다: {type(value).__name__}") from value
        if not isinstance(value, Choice):
            raise InterpretError(f"해석기 응답 형식이 올바르지 않습니다: {type(value).__name__}")
        if value.answer not in options:
            raise InterpretError("해석기가 선택지 밖의 답을 했습니다")
        return value


# --- Laya (3d 실험) ---

LAYA_POLICY_PREFIX = "다음은 사용자가 AI 에이전트의 파일 읽기 정책으로 쓴 한국어 문장이다."
_LAYA_QID = "answer"


def split_question(question: str, options: Sequence[str]) -> tuple[str, dict[str, str]]:
    """build_question이 만든 질문을 (지시문, {선택지: 설명})으로 나눈다. 선택지가 options와 다르면 InterpretError."""
    lines = question.split("\n")
    if "선택지:" not in lines:
        raise InterpretError("질문에 선택지 목록이 없습니다")
    cut = lines.index("선택지:")
    instructions = "\n".join(lines[:cut]).strip()
    criteria: dict[str, str] = {}
    for line in lines[cut + 1:]:
        if line.startswith("- "):
            option, sep, guide = line[2:].partition(": ")
            if sep:
                criteria[option] = guide
    if not instructions or set(criteria) != set(options):
        raise InterpretError("질문의 선택지가 options와 다릅니다")
    return instructions, {option: criteria[option] for option in options}


class LayaPolicyInterpreter:
    """로컬 laya-serve에 선택형(choice) 질문을 묻는 해석기 (3d 실험).

    **체크포인트가 이 질문들을 배우지 않았다(zero-shot).** 기본 해석기로 쓰기 전에 eval/run_policy_text.py로 평가한다.
    - 설정은 인자 또는 환경변수. 판단 모델과 같은 laya-serve를 쓴다: JEV_BASE_URL(필수, 이 컴퓨터 주소만),
      POLICY_LAYA_MODEL(선택, 없으면 JEV_MODEL, 그것도 없으면 gate-laya), JEV_API_KEY(선택).
    - 질문 하나를 {"type": "choice", "instructions": 앞말 + 질문 첫 줄(+ 세부), "criteria": {선택지: 설명}}으로 보낸다.
    - 확신도는 고른 선택지의 확률(probabilities[choice])이다. laya의 confidence(정규화 엔트로피)는 쓰지 않는다.
    - 연결 실패, HTTP 오류, 형식이 다른 응답, 선택지 밖의 답은 InterpretError(저장하지 않음)다.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
    ) -> None:
        env = os.environ if env is None else env
        raw_url = base_url if base_url is not None else env.get("JEV_BASE_URL", "")
        if not raw_url.strip():
            raise InterpretError("laya 해석기는 JEV_BASE_URL(laya-serve 주소, 예: http://127.0.0.1:8000)이 필요합니다")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) \
                or timeout <= 0:
            raise InterpretError("해석기 타임아웃은 0보다 큰 숫자(초)여야 합니다")
        try:
            self._endpoint = laya_endpoint(raw_url)
        except JevError as exc:
            raise InterpretError(str(exc)) from None
        chosen = model if model is not None else (env.get("POLICY_LAYA_MODEL") or env.get("JEV_MODEL") or "")
        self.model = chosen.strip() or LAYA_DEFAULT_MODEL
        self._api_key = api_key if api_key is not None else (env.get("JEV_API_KEY") or None)
        self.timeout = float(timeout)
        self._opener = laya_opener()

    def __repr__(self) -> str:
        return f"LayaPolicyInterpreter(model={self.model!r})"

    def choose(self, sentence: str, question: str, options: Sequence[str]) -> Choice:
        options = tuple(options)
        instructions, criteria = split_question(question, options)
        body = {"model": self.model, "states": [sentence], "questions": {_LAYA_QID: {
            "type": "choice", "instructions": f"{LAYA_POLICY_PREFIX}\n{instructions}", "criteria": criteria}}}
        try:
            data = laya_post(self._opener, self._endpoint, body, api_key=self._api_key, timeout=self.timeout)
        except JevError as exc:
            raise InterpretError(str(exc)) from None
        results = data.get("results") if isinstance(data, dict) else None
        item = results[0] if isinstance(results, list) and len(results) == 1 else None
        answers = item.get("answers") if isinstance(item, dict) else None
        answer = answers.get(_LAYA_QID) if isinstance(answers, dict) else None
        if not isinstance(answer, dict):
            raise InterpretError("Laya 응답 형식이 올바르지 않습니다: answers")
        choice, probabilities = answer.get("choice"), answer.get("probabilities")
        if choice not in options:
            raise InterpretError("해석기가 선택지 밖의 답을 했습니다")
        p = probabilities.get(choice) if isinstance(probabilities, dict) else None
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0.0 <= p <= 1.0:
            raise InterpretError("Laya 응답 형식이 올바르지 않습니다: probabilities")
        return Choice(choice, float(p))


# 해석기 이름 → 생성 함수.
_BACKENDS: dict[str, Callable[..., PolicyInterpreter]] = {
    "laya": LayaPolicyInterpreter,
    "mock": MockPolicyInterpreter,
}
# 네트워크로 묻는 해석기는 같은 타임아웃을 자체 연결에도 건다. FailClosedInterpreter의 타임아웃은 기다림만 끊는다.
_NETWORK_BACKENDS = frozenset({"laya"})


def available_interpreters() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


def make_interpreter(name: str = "mock", *, timeout: float = DEFAULT_TIMEOUT_SECONDS, **options) -> PolicyInterpreter:
    """이름으로 해석기를 골라 FailClosedInterpreter로 감싸 돌려준다. 모르는 이름이나 잘못된 옵션은 InterpretError."""
    factory = _BACKENDS.get(name)
    if factory is None:
        raise InterpretError(f"알 수 없는 해석기: {name!r} (사용 가능: {', '.join(available_interpreters())})")
    try:
        inner = factory(timeout=timeout, **options) if name in _NETWORK_BACKENDS else factory(**options)
    except InterpretError:
        raise
    except Exception as exc:
        raise InterpretError(f"해석기 {name!r}를 만들 수 없습니다: {type(exc).__name__}") from exc
    return FailClosedInterpreter(inner, timeout)
