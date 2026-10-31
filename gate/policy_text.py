"""정책 문장 해석기: 사용자가 쓴 문장을 정해진 설정값이나 맥락 규칙으로 바꾼다.

Jev 방식이다. 해석기(PolicyInterpreter)는 글을 만들지 않고, 질문마다 정해진 선택지 중 하나와 확신도(Choice)만 돌려준다.
interpret()가 아래 질문을 차례로 던지고 답을 모아 정책 항목(gate.policy.Entry)을 만든다. 이 모듈은 파일을 건드리지 않는다.
저장은 사용자 확인을 받은 뒤 gate/policy_cli.py가 한다.

질문 구성 (질문 키: 선택지)
  1. type: number | path | context | unknown     문장 종류
       number → 2.   context → 5.   path → 경로 규칙 안내(저장하지 않음).   unknown → 다시 말해 달라고 한다.
  2. kind: KINDS                                   어떤 번호인가 (rrn, foreigner_rrn, card, mobile, phone, email, account)
  3. target: action | visible                      그 번호의 처리 방식을 바꾸나, 가릴 때 공개 범위를 바꾸나
  4. action: deny | ask_human | mask               (target이 action일 때)
     visible: 그 종류의 공개 범위                    (target이 visible일 때. gate.detectors.VISIBLE_CHOICES)
       → Entry(settings={"numbers.<kind>.<target>": 답}, said=문장)
  5. direction: stricter | looser                  맥락 문장이 더 막는 쪽인가, 더 보여 주는 쪽인가
       stricter → Entry(context=문장, said=문장). 문장은 해석하지 않고 그대로 Jev 질문의 "사용자 정책" 칸에 들어간다.
       looser   → 저장하지 않는다(unsupported). Jev 질문은 사용자 정책을 "보여주면 안 되는 내용"으로만 쓰고,
                  맥락 규칙을 더하는 것은 gate.policy.loosened가 완화로 보지 않아 두 번째 확인 없이 들어가기 때문이다.

저장하지 않는 경우 (모르면 바꾸지 않는다. fail-closed와 같은 뜻)
  - unclear: 답 하나라도 확신도가 threshold(기본 0.7) 미만이거나 type이 unknown. 다시 말해 달라고 한다. 추측해서 저장하지 않는다.
  - error: 해석기 오류. 예외, 타임아웃, 선택지 밖의 답, Choice가 아닌 응답.
  - path: 경로 패턴은 선택지로 고를 수 없어 `gate policy set path <패턴> <동작>`을 안내한다.
  - unsupported: 느슨하게 하는 맥락 문장.

실제 해석 백엔드는 아직 없다. make_interpreter(이름)으로 고르고 지금은 mock만 있다(jev_client.make_client와 같은 구조).
"""

import math
import threading
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from gate.detectors import KINDS
from gate.policy import ACTION_NAMES, KIND_NAMES, VISIBLE_NAMES, Entry, PolicyError, describe_setting, setting_choices

CONFIDENCE_THRESHOLD = 0.7
DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_SENTENCE_CHARS = 500  # jev_client가 사용자 정책 한 줄을 500자에서 자르므로 그보다 긴 문장은 받지 않는다

Outcome = Literal["setting", "context", "path", "unsupported", "unclear", "error"]

TYPE_OPTIONS = ("number", "path", "context", "unknown")
TARGET_OPTIONS = ("action", "visible")
DIRECTION_OPTIONS = ("stricter", "looser")

# 질문 키 → 질문 첫 줄. 첫 줄은 키마다 고정이라 question_key()로 어느 질문인지 알 수 있다.
QUESTION_TITLES: dict[str, str] = {
    "type": "이 문장은 어떤 종류의 정책인가요?",
    "kind": "이 문장은 어떤 번호에 대한 것인가요?",
    "target": "이 번호의 무엇을 바꾸라는 것인가요?",
    "action": "이 번호가 있는 파일을 어떻게 처리하라는 것인가요?",
    "visible": "이 번호를 가릴 때 어디까지 보여 주라는 것인가요?",
    "direction": "이 문장은 더 막는 쪽인가요, 더 보여 주는 쪽인가요?",
}
_TITLE_TO_KEY = {title: key for key, title in QUESTION_TITLES.items()}
_UNSURE = {
    "type": "어떤 종류의 정책인지", "kind": "어떤 번호인지", "target": "무엇을 바꾸라는 것인지",
    "action": "어떻게 처리하라는 것인지", "visible": "어디까지 보여 주라는 것인지", "direction": "막는 쪽인지 보여 주는 쪽인지",
}

_OPTION_GUIDES: dict[str, dict[str, str]] = {
    "type": {
        "number": "주민등록번호·카드번호·전화번호·이메일·계좌번호처럼 형식이 정해진 번호를 어떻게 처리할지",
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
}

_EXAMPLES = (
    "주민번호는 생년월일까지만 보여줘",
    "카드번호 있는 파일은 거부",
    "이메일은 물어보고 보여줘",
    "병원 기록은 보여주지 마",
)
_ACTION_NOTES = {
    "deny": "이 번호가 있는 파일은 읽기를 거부합니다.",
    "ask_human": "이 번호가 있는 파일은 사람에게 물어본 뒤 보여줍니다(허용해도 번호는 가린 채로 보여줍니다).",
    "mask": "번호를 가린 뒤 보여줍니다.",
}


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
    "email": ("이메일", "메일 주소", "메일주소", "전자우편", "e-mail", "email"),
    "account": ("계좌번호", "계좌 번호", "계좌", "통장"),
}
_PATH_CUES = ("폴더", "디렉터리", "디렉토리", "경로", "확장자", "파일 이름", "파일이름", "파일명", "/", "*")
_ACTION_CUES: dict[str, tuple[str, ...]] = {
    "mask": ("가려서 보여", "가려서 보이", "가린 채", "가리고 보여", "마스킹"),
    "ask_human": ("물어보", "물어봐", "물어 보", "확인받", "확인 받", "허락", "승인"),
    "deny": ("거부", "차단", "막아", "막기", "막을", "열지 마", "읽지 마", "금지"),
}
_VISIBLE_CUES: dict[str, tuple[str, ...]] = {
    "birth_gender": ("생년월일과 성별", "생년월일이랑 성별", "생년월일하고 성별", "성별", "앞 7자리", "앞7자리", "앞 일곱"),
    "birth": ("생년월일", "생일", "앞 6자리", "앞6자리", "앞 여섯"),
    "none": ("전부 가려", "다 가려", "모두 가려", "전부 가리", "다 가리", "모두 가리", "전부 숨겨", "다 숨겨",
             "하나도 보여주지"),
    "prefix": ("앞자리", "앞 자리", "앞 번호", "앞번호", "앞 3자리", "앞3자리"),
    "area": ("지역번호", "지역 번호"),
    "tld": ("최상위 도메인", "도메인", ".com"),
    "last4": ("끝 4자리", "끝4자리", "끝자리", "끝 자리", "뒤 4자리", "뒤4자리", "뒷자리", "뒷 4자리", "마지막 4자리"),
}
_TARGET_CUES: dict[str, tuple[str, ...]] = {
    "visible": (*(cue for cues in _VISIBLE_CUES.values() for cue in cues), "까지"),
    "action": tuple(cue for cues in _ACTION_CUES.values() for cue in cues),
}
_DIRECTION_CUES: dict[str, tuple[str, ...]] = {
    "stricter": ("보여주지 마", "보여주지마", "보여 주지 마", "보여주면 안", "보이지 않게", "안 보이게", "숨겨", "가려",
                 "가리", "막아", "거부", "차단", "금지", "빼고", "제외", "물어보", "물어봐", "확인받", "확인 받"),
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
      type 질문은 번호 단서와 경로 단서가 함께 있으면 0.5, 둘 다 없고 막거나 푸는 말이 있으면 context 0.8,
      아무 단서도 없으면 unknown이다. 확신도는 임의로 정한 상수다.
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


def _pick(text: str, cues: Mapping[str, Sequence[str]], options: Sequence[str]) -> Choice:
    found: list[str] = []
    rest = text
    for option, words in cues.items():
        if option not in options:
            continue
        hits = [w for w in words if w in rest]
        if hits:
            found.append(option)
            for w in hits:
                rest = rest.replace(w, " ")
    if len(found) == 1:
        return Choice(found[0], _SURE)
    if found:
        return Choice(found[0], _AMBIGUOUS)
    return Choice(options[0], _UNMATCHED)


def _keyword_choice(key: str, text: str, options: tuple[str, ...]) -> Choice:
    if key == "type":
        number = "번호" in text or any(c in text for cues in _KIND_CUES.values() for c in cues)
        path = any(c in text for c in _PATH_CUES)
        if number and path:
            return Choice("number", _AMBIGUOUS)
        if number:
            return Choice("number", _SURE)
        if path:
            return Choice("path", _SURE)
        if any(c in text for cues in _DIRECTION_CUES.values() for c in cues):
            return Choice("context", _CONTEXT_GUESS)
        return Choice("unknown", _SURE)
    table = {"kind": _KIND_CUES, "target": _TARGET_CUES, "action": _ACTION_CUES, "visible": _VISIBLE_CUES,
             "direction": _DIRECTION_CUES}[key]
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


# 해석기 이름 → 생성 함수.
_BACKENDS: dict[str, Callable[..., PolicyInterpreter]] = {
    "mock": MockPolicyInterpreter,
    # TODO(Phase 3b): 실제 Jev 백엔드를 정하면 PolicyInterpreter 구현을 추가해 등록한다. 요청·응답 형식은 그 백엔드의
    #   공식 문서를 확인해서 구현하고 추측하지 않는다. 답을 선택지 하나 + 확신도로 옮기지 못하면 InterpretError를 던진다.
}


def available_interpreters() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))


def make_interpreter(name: str = "mock", *, timeout: float = DEFAULT_TIMEOUT_SECONDS, **options) -> PolicyInterpreter:
    """이름으로 해석기를 골라 FailClosedInterpreter로 감싸 돌려준다. 모르는 이름이나 잘못된 옵션은 InterpretError."""
    factory = _BACKENDS.get(name)
    if factory is None:
        raise InterpretError(f"알 수 없는 해석기: {name!r} (사용 가능: {', '.join(available_interpreters())})")
    try:
        inner = factory(**options)
    except InterpretError:
        raise
    except Exception as exc:
        raise InterpretError(f"해석기 {name!r}를 만들 수 없습니다: {type(exc).__name__}") from exc
    return FailClosedInterpreter(inner, timeout)
