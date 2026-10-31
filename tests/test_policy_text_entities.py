"""gate.policy_text 3d·3e: 정책 문장으로 NER 묶음(entities.<묶음>.action) 바꾸기, Laya 해석기(가짜 laya-serve).

키워드 Mock의 결과는 실제 해석 성능을 대변하지 않는다. 표현 다양성은 eval/policy_sentences.csv로 따로 잰다.
"""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from gate.ner_client import ENTITY_GROUPS
from gate.policy import DEFAULT_POLICY, Entry, build_policy, describe_setting, loosened, read_entries, setting_choices
from gate.policy_text import (
    ENTITY_ACTION_OPTIONS,
    ENTITY_OPTIONS,
    LAYA_POLICY_PREFIX,
    QUESTION_TITLES,
    TYPE_OPTIONS,
    Choice,
    InterpretError,
    LayaPolicyInterpreter,
    MockPolicyInterpreter,
    build_question,
    interpret,
    make_interpreter,
    question_key,
    split_question,
)


def keyword() -> MockPolicyInterpreter:
    return MockPolicyInterpreter()


def fixed(**answers) -> MockPolicyInterpreter:
    return MockPolicyInterpreter("fixed", answers=answers)


def saved(sentence: str) -> dict | None:
    result = interpret(sentence, keyword())
    return dict(result.entry.settings) if result.entry is not None and result.entry.settings else None


# --- 질문 구성 ---

def test_entity_is_a_sentence_type_and_actions_match_policy_choices():
    assert "entity" in TYPE_OPTIONS
    for group in ENTITY_GROUPS:
        assert setting_choices(f"entities.{group}.action") == ENTITY_ACTION_OPTIONS


def test_entity_questions_list_every_group_and_action():
    assert ENTITY_OPTIONS == (*ENTITY_GROUPS, "unsupported")
    assert "date" not in ENTITY_OPTIONS and "event" not in ENTITY_OPTIONS
    question = build_question("entity", ENTITY_OPTIONS)
    assert question_key(question) == "entity"
    for group in ENTITY_OPTIONS:
        assert f"- {group}:" in question
    assert "- plain: 나이\n" in question and "날짜·사건·시간·연도·직함·과목" in question
    question = build_question("entity_action", ENTITY_ACTION_OPTIONS, "정보 종류: name (사람 이름)")
    assert question_key(question) == "entity_action"
    assert "정보 종류: name" in question
    for action in ENTITY_ACTION_OPTIONS:
        assert f"- {action}:" in question


# --- 키워드 Mock: 묶음 문장 ---

@pytest.mark.parametrize("sentence, key, value", [
    ("이름은 가려줘", "entities.name.action", "mask"),
    ("성함은 감춰 주세요", "entities.name.action", "mask"),
    ("이름은 보여주지 마", "entities.name.action", "mask"),            # 이름을 안 보이게 = 가림
    ("이름이 있는 파일은 보여주지 마", "entities.name.action", "deny"),  # 파일을 안 보이게 = 거부
    ("이름 있는 파일은 물어보고 보여줘", "entities.name.action", "ask_human"),
    ("이름은 가리지 말고 그대로 보여줘", "entities.name.action", "allow"),
    ("주소는 보여줘도 돼", "entities.address.action", "allow"),
    ("사는 곳은 숨겨줘", "entities.address.action", "mask"),
    ("생년월일은 가려줘", "entities.birth.action", "mask"),
    ("학번 있는 파일은 거부", "entities.student_id.action", "deny"),
    ("사번은 가려줘", "entities.student_id.action", "mask"),               # 식별번호로 넓혔다(3e)
    ("여권번호 있는 파일은 거부", "entities.student_id.action", "deny"),
    ("직원 번호는 물어보고 보여줘", "entities.student_id.action", "ask_human"),
    ("면허번호는 숨겨줘", "entities.student_id.action", "mask"),
    ("차량번호는 그대로 보여줘", "entities.student_id.action", "allow"),
    ("회원번호는 가려줘", "entities.student_id.action", "mask"),
    ("식별번호는 가려줘", "entities.student_id.action", "mask"),
    ("메신저 아이디는 가려줘", "entities.etc.action", "mask"),
    ("카톡 아이디가 있는 파일은 거부", "entities.etc.action", "deny"),
    ("SNS 아이디는 그대로 보여줘", "entities.etc.action", "allow"),
    ("출생 연도는 가려줘", "entities.birth.action", "mask"),               # "연도"보다 먼저 생년월일로 본다
    ("학교 이름은 가려줘", "entities.org.action", "mask"),            # "이름"이 name으로 다시 잡히지 않는다
    ("병원 이름은 가려줘", "entities.org.action", "mask"),
    ("장소는 그대로 보여줘", "entities.place.action", "allow"),
    ("IP 주소는 가려줘", "entities.host.action", "mask"),            # "주소"가 address로 다시 잡히지 않는다
    ("아이피 있는 파일은 거부", "entities.host.action", "deny"),
    ("나이는 가려줘", "entities.plain.action", "mask"),
    ("몇 살인지는 물어보고 보여줘", "entities.plain.action", "ask_human"),
    ("연령은 그대로 보여줘", "entities.plain.action", "allow"),
])
def test_entity_sentences(sentence, key, value):
    result = interpret(sentence, keyword())
    assert result.outcome == "setting"
    assert result.entry == Entry(settings={key: value}, said=sentence)
    assert [s.key for s in result.steps] == ["type", "entity", "entity_action"]
    assert "이렇게 이해했어요" in result.message and describe_setting(key, value) in result.message
    assert f"(설정 {key} = {value})" in result.message
    build_policy([result.entry])


@pytest.mark.parametrize("sentence, settings", [
    ("이메일 주소는 가려서 보여줘", {"numbers.email.action": "mask"}),   # 이메일이지 주소(address)가 아니다
    ("주민번호는 생년월일까지만 보여줘", {"numbers.rrn.visible": "birth"}),  # 생년월일은 번호의 공개 범위
    ("휴대폰 번호는 앞자리만 보여줘", {"numbers.mobile.visible": "prefix"}),
])
def test_number_sentences_stay_numbers(sentence, settings):
    assert saved(sentence) == settings


@pytest.mark.parametrize("sentence, outcome", [
    ("병원 기록은 보여주지 마", "context"),         # 주제는 맥락 규칙, "병원 이름"과 다르다
    ("회사 이야기는 보여주지 마", "context"),
    ("파일 이름에 비밀이 들어간 건 거부", "path"),  # "파일 이름"의 "이름"은 묶음이 아니다
    ("zip 파일은 거부", "context"),                 # "zip"의 "ip"는 IP가 아니다
])
def test_boundary_sentences_are_not_entities(sentence, outcome):
    result = interpret(sentence, keyword())
    assert result.outcome == outcome
    assert "entity" not in [s.choice.answer for s in result.steps]


@pytest.mark.parametrize("sentence", [
    "이름",                            # 어떻게 처리할지 없음
    "이름이랑 주소는 가려줘",          # 묶음이 둘
    "나이는 가리고 연도는 보여줘",     # 같은 묶음에 다른 동작 둘
    "주민번호랑 이름은 가려줘",        # 번호와 묶음이 섞임
    "이름 폴더는 거부",                # 묶음과 경로가 섞임
])
def test_ambiguous_entity_sentences_are_not_saved(sentence):
    result = interpret(sentence, keyword())
    assert result.outcome == "unclear" and result.entry is None


# --- 평가 세트에서 드러난 Mock 결함 (3d, 첫 Mock 평가에서 잘못 저장) ---

@pytest.mark.parametrize("sentence, settings", [
    ("학번은 가리지 않으면 안 돼", {"entities.student_id.action": "mask"}),           # 이중 부정은 allow가 아니다
    ("상담사 이름이 나오면 [이름]으로 바꿔서 보여줘", {"entities.name.action": "mask"}),  # "보여줘"가 allow로 잡히지 않는다
    ("이름이 나오면 보여주기 전에 물어봐 줘", {"entities.name.action": "ask_human"}),
    ("회사 이름은 내 허락 없이 보여주지 마", {"entities.org.action": "ask_human"}),
    ("주소는 굳이 가릴 필요 없어", {"entities.address.action": "allow"}),
    ("이메일 주소는 가려줘", {"numbers.email.action": "mask"}),
    # 과거형 "가렸"을 못 읽고 이유 부분의 "그대로"를 allow로 읽었다(검증 세트 H001, 느슨해짐)
    ("친구 이름이 그대로 넘어가는 게 싫어서, 이름은 가렸으면 좋겠어", None),
    ("이름은 가렸으면 좋겠어", {"entities.name.action": "mask"}),
    ("주민번호는 생년월일까지만 보여주고 나머지는 가려줘", {"numbers.rrn.visible": "birth"}),
])
def test_mock_phrase_fixes(sentence, settings):
    assert saved(sentence) == settings


@pytest.mark.parametrize("sentence, outcome", [
    ("주소는 시·도까지만 보여줘", "unclear"),                       # 묶음에는 공개 범위가 없다(allow로 저장하지 않음)
    ("이름은 성만 보이게 가려줘", "unclear"),
    ("주민번호는 생년월일까지만 보여주고 생년월일은 가려줘", "unclear"),  # 번호 공개 범위 + 묶음 = 바꿈 둘
    ("개인정보는 다 가려줘", "unclear"),                            # 넓은 말은 맥락 규칙으로 저장하지 않는다
    ("연락처는 안 보이게 해 줘", "unclear"),
    ("기타 개인정보는 가려줘", "setting"),                          # 묶음 이름(etc)은 넓은 말이 아니다
    ("주소록.xlsx 파일은 열지 마", "path"),                         # 확장자가 붙은 파일 이름
    ("이름에 '이력서'가 들어간 파일은 확인받고 열어줘", "path"),
    ("메일 주소는 .com 같은 끝부분만 보여줘", "setting"),            # ".com"은 파일 이름이 아니다
    ("돈 문제 얘기가 나오는 글은 읽지 못하게 해줘", "context"),
])
def test_mock_boundary_fixes(sentence, outcome):
    assert interpret(sentence, keyword()).outcome == outcome


# --- 안내 문구 ---

def test_plain_group_is_only_age_now():
    result = interpret("나이는 가려줘", keyword())
    assert "나이(NER): 가려서 보여줌" in result.message
    assert "다섯 가지" not in result.message and "같이 바뀝니다" not in result.message


# --- 지금 찾지 않는 정보 (3e, 2026-11-01 NER 교체) ---

@pytest.mark.parametrize("sentence", [
    "날짜는 가려줘", "약속 날짜가 나오면 물어봐", "몇 월 며칠인지는 거부",
    "사건 이름은 물어보고 보여줘", "행사명은 가려줘",
    "시간은 그대로 보여줘", "연도가 있는 파일은 거부", "직함은 가려줘", "직급은 숨겨줘", "직책은 보여줘",
    "과목 이름은 그대로 보여줘", "수업 이름은 가려줘", "강의명은 물어보고 보여줘",
])
def test_retired_kinds_are_not_saved(sentence):
    result = interpret(sentence, keyword())
    assert (result.outcome, result.entry) == ("unsupported", None)
    assert "지금은 날짜·사건·시간·연도·직함·과목을 따로 찾지 않아요" in result.message
    assert "2026" not in result.message and "교체" not in result.message  # 일반 사용자에게 내부 이력은 보이지 않는다
    assert "습니다" not in result.message                                  # 설정 화면 말투(~해요)에 맞춘다
    assert [s.key for s in result.steps] == ["type", "entity"]          # 처리 방식은 묻지 않는다
    assert result.steps[1].choice.answer == "unsupported"


def test_retired_kind_mixed_with_a_group_is_unclear():
    assert interpret("나이는 가리고 연도는 보여줘", keyword()).outcome == "unclear"
    assert interpret("이름이랑 날짜는 가려줘", keyword()).outcome == "unclear"


def test_fixed_unsupported_answer_stops_without_saving():
    mock = fixed(type=("entity", 0.9), entity=("unsupported", 0.9))
    result = interpret("아무 문장", mock)
    assert (result.outcome, result.entry) == ("unsupported", None)
    assert [c.key for c in mock.calls] == ["type", "entity"]


@pytest.mark.parametrize("sentence, words", [
    ("이름 있는 파일은 거부", "읽기를 거부"),
    ("이름 있는 파일은 물어보고 보여줘", "사람에게 물어본 뒤"),
    ("이름은 가려줘", "NER이 찾은 부분을 [이름]처럼"),
    ("이름은 그대로 보여줘", "가리지 않아요"),
    ("IP 주소는 가려줘", "규칙이 찾은 부분을 [IP]처럼"),        # IP·내부 주소는 규칙이 찾는다
    ("IP 주소는 가려줘", "IP·내부 주소(규칙): 가려서 보여줌"),
])
def test_entity_action_message_explains_effect(sentence, words):
    assert words in interpret(sentence, keyword()).message


def test_entity_allow_is_loosening_and_stricter_is_not():
    allow = build_policy([Entry(settings={"entities.name.action": "allow"})])
    deny = build_policy([Entry(settings={"entities.name.action": "deny"})])
    assert loosened(DEFAULT_POLICY, allow) == ["이름(NER): 가려서 보여줌 → 이름(NER): 그대로 보여줌"]
    assert loosened(DEFAULT_POLICY, deny) == []


# --- 확신도 ---

ENTITY_ANSWERS = dict(type=("entity", 0.9), entity=("name", 0.9), entity_action=("deny", 0.9))


def test_fixed_entity_flow_records_questions():
    mock = fixed(**ENTITY_ANSWERS)
    result = interpret("아무 문장", mock)
    assert result.entry == Entry(settings={"entities.name.action": "deny"}, said="아무 문장")
    assert [c.key for c in mock.calls] == ["type", "entity", "entity_action"]
    assert mock.calls[1].options == ENTITY_OPTIONS
    assert mock.calls[2].options == ENTITY_ACTION_OPTIONS
    assert "정보 종류: name" in mock.calls[2].question


@pytest.mark.parametrize("low", ["type", "entity", "entity_action"])
def test_low_confidence_at_entity_steps_asks_again_and_stops(low):
    answers = dict(ENTITY_ANSWERS)
    answers[low] = (answers[low][0], 0.69)
    mock = fixed(**answers)
    result = interpret("아무 문장", mock)
    assert result.outcome == "unclear" and result.entry is None
    order = ["type", "entity", "entity_action"]
    assert [c.key for c in mock.calls] == order[:order.index(low) + 1]


def test_entity_answer_outside_options_is_error():
    result = interpret("아무 문장", fixed(type=("entity", 0.9), entity=("phone", 0.9)))
    assert result.outcome == "error" and result.entry is None


# --- gate policy add ---

def gate_policy_add(path, sentence, answers):
    import argparse

    from gate.policy_cli import register, run
    parser = argparse.ArgumentParser(prog="gate")
    register(parser.add_subparsers(dest="command", required=True))
    out = io.StringIO()
    code = run(parser.parse_args(["policy", "--policy", str(path), "add", sentence]), stdin=io.StringIO(answers),
               stdout=out, interpreter=None, require_tty=False)
    return code, out.getvalue()


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GATE_POLICY", raising=False)
    return tmp_path / "gate-data" / "policy.yaml"


def test_add_entity_sentence_warns_when_ner_is_off(policy_file):
    from gate.policy import write_entries
    write_entries(policy_file, [Entry(settings={"ner_backend": "none"})])
    code, out = gate_policy_add(policy_file, "이름 있는 파일은 거부", "y\n")
    assert code == 0 and "NER이 꺼져 있어" in out and out.count("[y/N]") == 1
    assert dict(read_entries(policy_file)[-1].settings) == {"entities.name.action": "deny"}


def test_add_entity_sentence_no_warning_by_default(policy_file):
    code, out = gate_policy_add(policy_file, "이름 있는 파일은 거부", "y\n")  # 기본은 NER 켜짐
    assert code == 0 and "NER이 꺼져 있어" not in out


def test_add_entity_sentence_no_warning_when_ner_is_on(policy_file):
    from gate.policy import write_entries
    write_entries(policy_file, [Entry(settings={"ner_backend": "server"})])
    code, out = gate_policy_add(policy_file, "이름 있는 파일은 거부", "y\n")
    assert code == 0 and "NER이 꺼져 있어" not in out


def test_add_entity_allow_asks_twice(policy_file):
    code, out = gate_policy_add(policy_file, "주소는 보여줘도 돼", "y\ny\n")
    assert code == 0 and out.count("[y/N]") == 2 and "느슨하게" in out
    assert dict(read_entries(policy_file)[0].settings) == {"entities.address.action": "allow"}


# --- Laya 해석기 (가짜 laya-serve) ---

class FakeLayaChoice:
    """laya-serve POST /v1/systemone/batch의 choice 답 흉내. answer(문장, 질문 정의) → (선택지, 확률표)."""

    def __init__(self):
        self.answer = lambda state, question: (next(iter(question["criteria"])), None)
        self.reply: tuple[int, bytes] | None = None
        self.requests: list[tuple[str, dict, dict]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                fake.requests.append((self.path, dict(self.headers), body))
                if fake.reply is not None:
                    status, data = fake.reply
                else:
                    qid, question = next(iter(body["questions"].items()))
                    results = []
                    for state in body["states"]:
                        choice, probs = fake.answer(state, question)
                        if probs is None:
                            probs = {label: (0.9 if label == choice else 0.1 / (len(question["criteria"]) - 1))
                                     for label in question["criteria"]}
                        results.append({"answers": {qid: {"type": "choice", "choice": choice, "probabilities": probs,
                                                          "confidence": 0.5}}})
                    status, data = 200, json.dumps({"results": results}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake():
    server = FakeLayaChoice()
    yield server
    server.close()


def laya(fake, **kwargs) -> LayaPolicyInterpreter:
    return LayaPolicyInterpreter(base_url=fake.url, env={}, **kwargs)


def test_split_question_round_trips_build_question():
    question = build_question("entity_action", ENTITY_ACTION_OPTIONS, "정보 종류: name (사람 이름)")
    instructions, criteria = split_question(question, ENTITY_ACTION_OPTIONS)
    assert instructions == f"{QUESTION_TITLES['entity_action']}\n정보 종류: name (사람 이름)"
    assert list(criteria) == list(ENTITY_ACTION_OPTIONS)
    assert criteria["allow"] == "이 정보를 가리지 않고 그대로 보여줌"
    with pytest.raises(InterpretError):
        split_question(question, ("deny", "mask"))
    with pytest.raises(InterpretError):
        split_question("선택지 없는 질문", ("a",))


def test_laya_sends_choice_question_and_uses_chosen_probability(fake):
    fake.answer = lambda state, q: ("mask", {"deny": 0.05, "ask_human": 0.05, "mask": 0.8, "allow": 0.1})
    question = build_question("entity_action", ENTITY_ACTION_OPTIONS, "정보 종류: name (사람 이름)")
    choice = laya(fake, model="gate-laya-v3", api_key="k-123").choose("이름은 가려줘", question, ENTITY_ACTION_OPTIONS)
    assert choice == Choice("mask", 0.8)
    [(path, headers, body)] = fake.requests
    assert path == "/v1/systemone/batch"
    assert headers["Authorization"] == "Bearer k-123"
    assert body["model"] == "gate-laya-v3" and body["states"] == ["이름은 가려줘"]
    [q] = body["questions"].values()
    assert q["type"] == "choice"
    assert q["instructions"].startswith(LAYA_POLICY_PREFIX) and QUESTION_TITLES["entity_action"] in q["instructions"]
    assert list(q["criteria"]) == list(ENTITY_ACTION_OPTIONS)


def test_laya_model_from_env(fake):
    assert LayaPolicyInterpreter(base_url=fake.url, env={"JEV_MODEL": "a"}).model == "a"
    assert LayaPolicyInterpreter(base_url=fake.url, env={"JEV_MODEL": "a", "POLICY_LAYA_MODEL": "b"}).model == "b"
    assert LayaPolicyInterpreter(base_url=fake.url, env={}).model == "gate-laya"


def test_laya_full_interpretation(fake):
    picks = {"type": "entity", "entity": "address", "entity_action": "allow"}
    fake.answer = lambda state, q: (picks[question_key(q["instructions"].split("\n", 1)[1])], None)
    result = interpret("사는 곳은 보여줘도 괜찮아", make_interpreter("laya", base_url=fake.url, env={}))
    assert result.entry == Entry(settings={"entities.address.action": "allow"}, said="사는 곳은 보여줘도 괜찮아")
    assert len(fake.requests) == 3


def test_laya_low_probability_is_unclear(fake):
    fake.answer = lambda state, q: ("entity", {k: 1 / len(q["criteria"]) for k in q["criteria"]})
    result = interpret("이름은 가려줘", laya(fake))
    assert result.outcome == "unclear" and result.entry is None


@pytest.mark.parametrize("reply", [
    (500, b"{}"),
    (200, b"not json"),
    (200, json.dumps({"results": []}).encode()),
    (200, json.dumps({"results": [{"answers": {}}]}).encode()),
    (200, json.dumps({"results": [{"answers": {"answer": {"choice": "number", "probabilities": {}}}}]}).encode()),
    (200, json.dumps({"results": [{"answers": {"answer": {"choice": "number",
                                                          "probabilities": {"number": 1.5}}}}]}).encode()),
    (200, json.dumps({"results": [{"answers": {"answer": {"choice": "allow_all",
                                                          "probabilities": {"allow_all": 0.9}}}}]}).encode()),
])
def test_laya_bad_replies_are_errors_and_not_saved(fake, reply):
    fake.reply = reply
    with pytest.raises(InterpretError):
        laya(fake).choose("이름은 가려줘", build_question("type", TYPE_OPTIONS), TYPE_OPTIONS)
    result = interpret("이름은 가려줘", make_interpreter("laya", base_url=fake.url, env={}))
    assert result.outcome == "error" and result.entry is None


def test_laya_requires_local_address():
    with pytest.raises(InterpretError, match="JEV_BASE_URL"):
        LayaPolicyInterpreter(env={})
    with pytest.raises(InterpretError):
        make_interpreter("laya", env={})
    with pytest.raises(InterpretError):
        LayaPolicyInterpreter(base_url="http://192.0.2.1:8000", env={})


def test_laya_unreachable_server_is_error():
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    server.server_close()
    result = interpret("이름은 가려줘", make_interpreter("laya", base_url=url, env={}))
    assert result.outcome == "error" and result.entry is None
