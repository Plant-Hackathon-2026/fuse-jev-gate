"""gate.policy_text: 정책 문장 해석기 (Jev 방식, Mock)."""

import pytest

from gate.detectors import KINDS, VISIBLE_CHOICES
from gate.policy import Entry, build_policy, describe_setting
from gate.policy_text import (
    CONFIDENCE_THRESHOLD,
    DIRECTION_OPTIONS,
    MAX_SENTENCE_CHARS,
    QUESTION_TITLES,
    TARGET_OPTIONS,
    TYPE_OPTIONS,
    Choice,
    FailClosedInterpreter,
    InterpretError,
    MockPolicyInterpreter,
    available_interpreters,
    build_question,
    interpret,
    make_interpreter,
    question_key,
)


def keyword() -> MockPolicyInterpreter:
    return MockPolicyInterpreter()


def fixed(**answers) -> MockPolicyInterpreter:
    return MockPolicyInterpreter("fixed", answers=answers)


# --- 지시서의 예시 문장 ---

@pytest.mark.parametrize("sentence, key, value", [
    ("주민번호는 생년월일까지만 보여줘", "numbers.rrn.visible", "birth"),
    ("카드번호 있는 파일은 거부", "numbers.card.action", "deny"),
    ("이메일은 물어보고 보여줘", "numbers.email.action", "ask_human"),
    ("휴대폰 번호는 앞자리만 보여줘", "numbers.mobile.visible", "prefix"),
])
def test_number_sentences(sentence, key, value):
    result = interpret(sentence, keyword())
    assert result.outcome == "setting" and result.saveable
    assert result.entry == Entry(settings={key: value}, said=sentence)
    assert "이렇게 이해했어요" in result.message
    assert describe_setting(key, value) in result.message
    build_policy([result.entry])  # 그대로 정책에 넣을 수 있다


def test_context_sentence_is_kept_verbatim():
    result = interpret("병원 기록은 보여주지 마", keyword())
    assert result.outcome == "context"
    assert result.entry == Entry(context="병원 기록은 보여주지 마", said="병원 기록은 보여주지 마")
    assert "이렇게 이해했어요" in result.message and "병원 기록은 보여주지 마" in result.message
    assert [s.key for s in result.steps] == ["type", "direction"]


def test_path_sentence_gives_set_path_hint_without_saving():
    result = interpret("공개 폴더는 그냥 보여줘", keyword())
    assert result.outcome == "path"
    assert result.entry is None and not result.saveable
    assert "plant policy set path" in result.message


def test_unknown_sentence_asks_again():
    result = interpret("아무거나", keyword())
    assert result.outcome == "unclear" and result.entry is None
    assert "다시 말해 주세요" in result.message


# --- 그 밖의 번호 문장 ---

@pytest.mark.parametrize("sentence, key, value", [
    ("외국인등록번호는 생년월일과 성별까지 보여줘", "numbers.foreigner_rrn.visible", "birth_gender"),
    ("주민등록번호는 앞 7자리까지 보여줘", "numbers.rrn.visible", "birth_gender"),
    ("주민번호는 전부 가려줘", "numbers.rrn.visible", "none"),
    ("카드번호는 끝 4자리만 보여줘", "numbers.card.visible", "last4"),
    ("휴대전화번호는 앞자리만 보여줘", "numbers.mobile.visible", "prefix"),  # 일반 전화로 잡히지 않는다
    ("전화번호는 지역번호만 보여줘", "numbers.phone.visible", "area"),
    ("이메일은 도메인만 보여줘", "numbers.email.visible", "tld"),
    ("계좌번호 있는 파일은 물어보고 보여줘", "numbers.account.action", "ask_human"),
    ("주민번호 있는 파일은 가려서 보여줘", "numbers.rrn.action", "mask"),
    ("  주민번호는\n생년월일까지만   보여줘 ", "numbers.rrn.visible", "birth"),
])
def test_more_number_sentences(sentence, key, value):
    result = interpret(sentence, keyword())
    assert result.outcome == "setting"
    assert dict(result.entry.settings) == {key: value}


def test_said_is_whitespace_normalized():
    result = interpret("  주민번호는\n생년월일까지만   보여줘 ", keyword())
    assert result.sentence == result.entry.said == "주민번호는 생년월일까지만 보여줘"


def test_action_message_explains_effect():
    assert "거부" in interpret("카드번호 있는 파일은 거부", keyword()).message


@pytest.mark.parametrize("sentence", [
    "주민번호",                          # 무엇을 바꾸는지 없음
    "번호는 다 거부",                    # 어떤 번호인지 없음
    "주민번호 폴더는 거부",              # 번호와 경로가 섞임
    "계좌번호는 끝 4자리만 보여줘",      # 계좌번호에는 그런 공개 범위가 없다
    "카드번호는 끝 4자리까지만 물어보고 보여줘",  # 공개 범위와 처리 방식이 섞임
    "주민번호와 카드번호는 거부",        # 번호가 둘
    "병원 기록",                         # 막는지 푸는지 없음
])
def test_ambiguous_sentences_are_not_saved(sentence):
    result = interpret(sentence, keyword())
    assert result.outcome == "unclear" and result.entry is None


def test_loosening_context_sentence_is_not_saved():
    result = interpret("병원 기록은 보여줘도 돼", keyword())
    assert result.outcome == "unsupported" and result.entry is None
    assert "더 막는 쪽" in result.message


def test_stricter_cue_wins_over_embedded_looser_cue():
    assert interpret("가족 이야기는 그냥 보여주지 마", keyword()).outcome == "context"


@pytest.mark.parametrize("sentence, make", [
    ("카드번호 있는 파일은 거부", keyword),
    ("이메일은 물어보고 보여줘", keyword),
    ("이메일 주소는 가려줘", keyword),
    ("이름 있는 파일은 물어보고 보여줘", keyword),
    ("이름은 가려줘", keyword),
    ("이름은 그대로 보여줘", keyword),
    ("병원 기록은 보여주지 마", keyword),
    ("공개 폴더는 그냥 보여줘", keyword),
    ("병원 기록은 보여줘도 돼", keyword),
    ("날짜는 가려줘", keyword),
    ("아무거나", keyword),
    ("아무 문장", lambda: fixed(type=("number", 0.9))),                     # 해석기 오류(괄호 안 이유 포함)
    ("아무 문장", lambda: MockPolicyInterpreter(error=RuntimeError("x"))),
])
def test_messages_use_one_polite_tone(sentence, make):
    """문장 해석 안내는 설정 화면과 같은 말투(~해요)로 쓴다. 괄호 안 이유도 섞이지 않는다."""
    assert "니다" not in interpret(sentence, make()).message


# --- 확신도 ---

NUMBER_ANSWERS = dict(type=("number", 0.9), kind=("rrn", 0.9), target=("visible", 0.9), visible=("birth", 0.9))


@pytest.mark.parametrize("low", ["type", "kind", "target", "visible"])
def test_low_confidence_at_any_step_asks_again_and_stops(low):
    answers = dict(NUMBER_ANSWERS)
    answers[low] = (answers[low][0], 0.69)
    mock = fixed(**answers)
    result = interpret("아무 문장", mock)
    assert result.outcome == "unclear" and result.entry is None
    order = ["type", "kind", "target", "visible"]
    assert [c.key for c in mock.calls] == order[:order.index(low) + 1]  # 낮은 답 뒤로는 묻지 않는다
    assert "0.69" in result.message


def test_confidence_at_threshold_is_accepted():
    answers = {k: (v[0], CONFIDENCE_THRESHOLD) for k, v in NUMBER_ANSWERS.items()}
    result = interpret("아무 문장", fixed(**answers))
    assert result.entry == Entry(settings={"numbers.rrn.visible": "birth"}, said="아무 문장")


def test_threshold_can_be_raised():
    assert interpret("주민번호는 생년월일까지만 보여줘", keyword(), threshold=0.95).outcome == "unclear"


def test_unknown_with_high_confidence_is_unclear():
    assert interpret("아무 문장", fixed(type=("unknown", 0.99))).outcome == "unclear"


# --- Mock fixed 방식과 calls ---

def test_fixed_mode_answers_each_question_and_records_calls():
    mock = fixed(type=("number", 0.95), kind=("card", 0.9), target=("action", 0.8), action=("deny", 0.99))
    result = interpret("아무 문장", mock)
    assert result.entry == Entry(settings={"numbers.card.action": "deny"}, said="아무 문장")
    assert [c.key for c in mock.calls] == ["type", "kind", "target", "action"]
    assert all(c.sentence == "아무 문장" for c in mock.calls)
    assert mock.calls[0].options == TYPE_OPTIONS
    assert mock.calls[1].options == KINDS
    assert mock.calls[2].options == TARGET_OPTIONS
    assert mock.calls[3].options == ("deny", "ask_human", "mask")
    assert [s.choice for s in result.steps] == [Choice("number", 0.95), Choice("card", 0.9), Choice("action", 0.8),
                                                Choice("deny", 0.99)]


def test_fixed_context_flow():
    mock = fixed(type=("context", 0.9), direction=("stricter", 0.9))
    result = interpret("가족 사진 설명은 보여주지 마", mock)
    assert result.entry.context == "가족 사진 설명은 보여주지 마"
    assert mock.calls[1].options == DIRECTION_OPTIONS


@pytest.mark.parametrize("kind", KINDS)
def test_visible_question_offers_that_kinds_choices(kind):
    mock = fixed(type=("number", 0.9), kind=(kind, 0.9), target=("visible", 0.9), visible=("none", 0.9))
    result = interpret("아무 문장", mock)
    assert mock.calls[-1].options == VISIBLE_CHOICES[kind]
    assert dict(result.entry.settings) == {f"numbers.{kind}.visible": "none"}


def test_fixed_mode_without_answer_for_question_is_error():
    result = interpret("아무 문장", fixed(type=("number", 0.9)))
    assert result.outcome == "error" and result.entry is None


@pytest.mark.parametrize("kwargs", [
    {"mode": "fixed"},
    {"mode": "fixed", "answers": {"nope": ("x", 0.9)}},
    {"mode": "fixed", "answers": {"type": ("number", 1.5)}},
    {"mode": "keyword", "answers": {"type": ("number", 0.9)}},
    {"mode": "random"},
])
def test_mock_rejects_bad_setup(kwargs):
    with pytest.raises(InterpretError):
        MockPolicyInterpreter(**kwargs)


def test_mock_rejects_unknown_question():
    with pytest.raises(InterpretError):
        keyword().choose("주민번호", "무슨 질문?", ("a", "b"))


def test_mock_doc_says_it_does_not_represent_jev():
    assert "Jev의 실제 성능을 대변하지 않는다" in MockPolicyInterpreter.__doc__


# --- 해석기 오류는 저장하지 않는다 ---

def test_interpreter_exception_is_error():
    mock = MockPolicyInterpreter(error=RuntimeError("boom"))
    result = interpret("주민번호는 생년월일까지만 보여줘", mock)
    assert result.outcome == "error" and result.entry is None
    assert "RuntimeError" in result.message and len(mock.calls) == 1


class _Answer:
    def __init__(self, value):
        self.value = value

    def choose(self, sentence, question, options):
        return self.value


@pytest.mark.parametrize("value", [Choice("allow_everything", 0.99), "number", None])
def test_answer_outside_options_or_wrong_type_is_error(value):
    assert interpret("주민번호는 생년월일까지만 보여줘", _Answer(value)).outcome == "error"


def test_direction_answer_outside_options_is_not_saved_as_context():
    # 선택지 밖의 답을 stricter로 보면 맥락 규칙으로 저장되어 버린다
    result = interpret("병원 기록은 보여주지 마", fixed(type=("context", 0.9), direction=("both", 0.9)))
    assert result.outcome == "error" and result.entry is None


def test_timeout_is_error():
    slow = make_interpreter(timeout=0.05, delay=0.5)
    result = interpret("주민번호는 생년월일까지만 보여줘", slow)
    assert result.outcome == "error" and result.entry is None


def test_fail_closed_wrapper_converts_failures():
    with pytest.raises(InterpretError):
        failing = FailClosedInterpreter(MockPolicyInterpreter(error=ValueError("x")))
        failing.choose("s", build_question("type", TYPE_OPTIONS), TYPE_OPTIONS)
    with pytest.raises(InterpretError):
        FailClosedInterpreter(_Answer(Choice("nope", 0.9))).choose("s", "q", ("a",))
    with pytest.raises(InterpretError):
        FailClosedInterpreter(_Answer({"answer": "a"})).choose("s", "q", ("a",))
    for bad in (0, -1, float("nan"), True):
        with pytest.raises(InterpretError):
            FailClosedInterpreter(keyword(), bad)


def test_make_interpreter():
    assert available_interpreters() == ("laya", "mock")
    result = interpret("카드번호 있는 파일은 거부", make_interpreter())
    assert result.entry == Entry(settings={"numbers.card.action": "deny"}, said="카드번호 있는 파일은 거부")
    with pytest.raises(InterpretError):
        make_interpreter("openai")
    with pytest.raises(InterpretError):
        make_interpreter(colour="red")
    with pytest.raises(InterpretError):
        make_interpreter(mode="fixed")


# --- 문장·질문·답 형식 ---

@pytest.mark.parametrize("sentence", ["", "   \n", None, 123])
def test_empty_sentence_is_unclear_without_asking(sentence):
    mock = keyword()
    assert interpret(sentence, mock).outcome == "unclear"
    assert mock.calls == []


def test_too_long_sentence_is_unclear_without_asking():
    mock = keyword()
    assert interpret("병원 기록은 보여주지 마" + "가" * MAX_SENTENCE_CHARS, mock).outcome == "unclear"
    assert mock.calls == []


@pytest.mark.parametrize("answer, confidence", [("", 0.9), (None, 0.9), ("a", 1.1), ("a", -0.1), ("a", float("nan")),
                                                ("a", True), ("a", "0.9")])
def test_choice_validation(answer, confidence):
    with pytest.raises(InterpretError):
        Choice(answer, confidence)


def test_choice_confidence_is_float():
    assert isinstance(Choice("a", 1).confidence, float)


def test_questions_have_fixed_first_line_and_list_options():
    for key in QUESTION_TITLES:
        options = ("none", "birth") if key == "visible" else ("deny", "mask") if key == "action" else TYPE_OPTIONS
        question = build_question(key, options, "번호 종류: rrn (주민등록번호)")
        assert question_key(question) == key
        for option in options:
            assert f"- {option}:" in question
    assert question_key("다른 질문") is None


# --- Phase 4 보완: 비밀값 문장 ---

@pytest.mark.parametrize("sentence, settings", [
    ("비밀번호가 있는 파일은 거부해줘", {"numbers.secret.action": "deny"}),
    ("토큰이 있으면 물어보고 보여줘", {"numbers.secret.action": "ask_human"}),
    ("API 키는 가려서 보여줘", {"numbers.secret.action": "mask"}),
    ("패스워드 들어간 파일은 차단", {"numbers.secret.action": "deny"}),
])
def test_keyword_mock_understands_secret_sentences(sentence, settings):
    result = interpret(sentence, MockPolicyInterpreter())
    assert result.outcome == "setting"
    assert dict(result.entry.settings) == settings
