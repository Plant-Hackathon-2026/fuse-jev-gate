"""gate.policy_cli: `gate policy` 하위 명령 (show, add, remove, undo, set)."""

import argparse
import io
import os
import stat

import pytest

from gate.policy import (
    DEFAULT_POLICY,
    Entry,
    PathRule,
    PolicySource,
    build_policy,
    read_entries,
    write_entries,
)
from gate.policy_cli import MAX_HISTORY, history_dir, register, run
from gate.policy_text import MockPolicyInterpreter

PROMPT = "[y/N]"


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GATE_POLICY", raising=False)
    return tmp_path / "gate-data" / "policy.yaml"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="gate")
    register(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def gate_policy(path, *argv, answers="", interpreter=None, require_tty=False, stdin=None):
    """gate policy --policy <path> <argv...>를 실행해 (exit code, 출력)을 돌려준다."""
    out = io.StringIO()
    prefix = ["policy"] if path is None else ["policy", "--policy", str(path)]
    code = run(parse([*prefix, *argv]), stdin=stdin if stdin is not None else io.StringIO(answers), stdout=out,
               interpreter=interpreter, require_tty=require_tty)
    return code, out.getvalue()


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


# --- show ---

def test_show_without_file_shows_defaults(policy_file):
    code, out = gate_policy(policy_file, "show")
    assert code == 0
    assert "아직 없음" in out and "없음. 아래는 전부 기본값" in out
    assert out.count("[지정]") == 1  # 범례 한 번뿐
    assert "주민등록번호: 가려서 보여줌 [기본] · 가릴 때 전부 가림 [기본]" in out
    assert "*.env → 거부 [기본]" in out
    assert DEFAULT_POLICY.version in out
    assert not policy_file.exists()


def test_show_separates_user_values_from_defaults(policy_file):
    write_entries(policy_file, [
        Entry(settings={"numbers.rrn.visible": "birth"}, said="주민번호는 생년월일까지만 보여줘", at="2026-10-31T14:00:00+09:00"),
        Entry(settings={"numbers.card.action": "deny"}),
        Entry(context="병원 기록은 보여주지 마", said="병원 기록은 보여주지 마"),
        Entry(path_rule=PathRule("공개/**", "allow")),
        Entry(settings={"numbers.rrn.visible": "birth_gender", "thresholds.allow_if_safe_confidence_gte": 0.99}),
    ])
    code, out = gate_policy(policy_file, "show")
    assert code == 0
    lines = out.splitlines()
    first = lines.index('  1. "주민번호는 생년월일까지만 보여줘"  (2026-10-31 14:00)')
    assert "덮어써서 지금은 적용되지 않음" in lines[first + 1]  # 1번은 5번이 덮어썼다
    assert sum("덮어써서" in line for line in lines) == 1
    assert "2. (plant policy set으로 지정)" in out
    assert "주민등록번호: 가려서 보여줌 [기본] · 가릴 때 성별 자리(앞 7자리)까지 공개 [지정]" in out
    assert "카드번호: 거부 [지정] · 가릴 때 전부 가림 [기본]" in out
    assert "이메일: 가려서 보여줌 [기본]" in out
    assert "확신도 0.99 이상으로 답하면 보여줌 [지정]" in out
    assert "확신도 0.95 이상으로 답하면 거부 [기본]" in out
    assert "공개/** → 그대로 보여줌 [지정]" in out and "*.pem → 거부 [기본]" in out
    assert "- 병원 기록은 보여주지 마 [지정]" in out
    assert "Mock" in out  # 맥락 규칙을 Mock Jev가 쓰지 않는다는 주의
    assert build_policy(read_entries(policy_file)).version in out


def test_show_lists_approval_settings(policy_file):
    _, out = gate_policy(policy_file, "show")
    assert "답을 기다리는 시간: 30초, 넘으면 거부 [기본]" in out
    assert "'이 폴더 허용'을 기억하는 시간: 60분 [기본]" in out
    assert "기능이 생기기 전까지" not in out
    write_entries(policy_file, [Entry(settings={"approval.timeout_seconds": 45.0, "approval.session_minutes": 0})])
    _, out = gate_policy(policy_file, "show")
    assert "답을 기다리는 시간: 45초, 넘으면 거부 [지정]" in out
    assert "'이 폴더 허용'을 쓰지 않음 (0분) [지정]" in out


def test_set_approval_settings(policy_file):
    code, out = gate_policy(policy_file, "set", "approval.session_minutes", "0", answers="y\n")
    assert code == 0, out
    assert read_entries(policy_file)[-1].settings == {"approval.session_minutes": 0}
    code, out = gate_policy(policy_file, "set", "approval.timeout_seconds", "abc", answers="y\n")
    assert code == 1 and "숫자" in out


def test_show_does_not_need_terminal(policy_file):
    assert gate_policy(policy_file, "show", require_tty=True)[0] == 0


def test_policy_option_after_subcommand_and_env(policy_file, monkeypatch):
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"})])
    out = io.StringIO()
    assert run(parse(["policy", "show", "--policy", str(policy_file)]), stdin=io.StringIO(), stdout=out) == 0
    assert "카드번호: 거부 [지정]" in out.getvalue()
    monkeypatch.setenv("GATE_POLICY", str(policy_file))
    code, out = gate_policy(None, "show")
    assert code == 0 and "카드번호: 거부 [지정]" in out


def test_missing_policy_path_exits_1(policy_file):
    for argv in (["show"], ["set", "numbers.card.action", "deny"]):
        code, out = gate_policy(None, *argv)
        assert code == 1 and "GATE_POLICY" in out


# --- add ---

def test_add_approved(policy_file):
    code, out = gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers="y\n")
    assert code == 0
    assert "이렇게 이해했어요" in out and "카드번호: 거부" in out
    assert out.count(PROMPT) == 1  # 엄격해지는 변경은 해석 확인 한 번
    assert "다음 요청부터" in out  # 데몬 자동 반영 안내
    [entry] = read_entries(policy_file)
    assert dict(entry.settings) == {"numbers.card.action": "deny"}
    assert entry.said == "카드번호 있는 파일은 거부" and entry.at
    assert mode(policy_file) == 0o600


def test_add_accepts_unquoted_words(policy_file):
    assert gate_policy(policy_file, "add", "카드번호", "있는", "파일은", "거부", answers="y\n")[0] == 0
    assert read_entries(policy_file)[0].said == "카드번호 있는 파일은 거부"


@pytest.mark.parametrize("answers", ["n\n", "\n", "아니\n", ""])  # 거부, 빈 답(기본 N), 다른 말, EOF
def test_add_declined_or_eof_does_not_save(policy_file, answers):
    code, out = gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers=answers)
    assert code == 1 and "저장하지 않았어요" in out
    assert not policy_file.exists()


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "예", "네", "ㅛ"])
def test_add_yes_variants(policy_file, answer):
    assert gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers=f"{answer}\n")[0] == 0


def test_add_loosening_asks_twice(policy_file):
    code, out = gate_policy(policy_file, "add", "주민번호는 생년월일까지만 보여줘", answers="y\ny\n")
    assert code == 0
    assert out.count(PROMPT) == 2
    assert "느슨하게" in out and "주민등록번호: 전부 가림 → 주민등록번호: 생년월일(앞 6자리)까지 공개" in out
    assert dict(read_entries(policy_file)[0].settings) == {"numbers.rrn.visible": "birth"}


@pytest.mark.parametrize("answers", ["y\nn\n", "y\n"])  # 두 번째에서 거부, 두 번째에서 EOF
def test_add_loosening_declined_keeps_file(policy_file, answers):
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"})])
    before = policy_file.read_bytes()
    code, out = gate_policy(policy_file, "add", "주민번호는 생년월일까지만 보여줘", answers=answers)
    assert code == 1 and "바뀌지 않았어요" in out
    assert policy_file.read_bytes() == before
    assert not history_dir(policy_file).exists()


def test_add_context_rule_warns_mock_jev(policy_file):
    code, out = gate_policy(policy_file, "add", "병원 기록은 보여주지 마", answers="y\n")
    assert code == 0 and "Mock" in out and out.count(PROMPT) == 1
    assert build_policy(read_entries(policy_file)).context_rules == ("병원 기록은 보여주지 마",)


@pytest.mark.parametrize("sentence, hint", [
    ("아무거나", "다시 말해 주세요"),
    ("공개 폴더는 그냥 보여줘", "plant policy set path"),
    ("병원 기록은 보여줘도 돼", "더 막는 쪽"),
])
def test_add_not_understood_does_not_ask_or_save(policy_file, sentence, hint):
    code, out = gate_policy(policy_file, "add", sentence, answers="y\ny\n")
    assert code == 1 and hint in out
    assert PROMPT not in out
    assert not policy_file.exists()


def test_add_interpreter_error_does_not_save(policy_file):
    broken = MockPolicyInterpreter(error=RuntimeError("down"))
    code, out = gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers="y\n", interpreter=broken)
    assert code == 1 and "해석기" in out
    assert not policy_file.exists()


def test_add_uses_given_interpreter(policy_file):
    mock = MockPolicyInterpreter("fixed", answers={"type": ("number", 0.9), "kind": ("email", 0.9),
                                                   "target": ("action", 0.9), "action": ("deny", 0.9)})
    assert gate_policy(policy_file, "add", "무엇이든", answers="y\n", interpreter=mock)[0] == 0
    assert len(mock.calls) == 4
    assert dict(read_entries(policy_file)[0].settings) == {"numbers.email.action": "deny"}


# --- set ---

def test_set_stricter_saves_without_asking(policy_file):
    code, out = gate_policy(policy_file, "set", "numbers.card.action", "deny")
    assert code == 0 and PROMPT not in out
    [entry] = read_entries(policy_file)
    assert dict(entry.settings) == {"numbers.card.action": "deny"} and entry.said is None
    assert mode(policy_file) == 0o600


def test_set_threshold_converts_number(policy_file):
    code, out = gate_policy(policy_file, "set", "thresholds.allow_if_safe_confidence_gte", "0.97")
    assert code == 0 and PROMPT not in out  # 허용 기준을 올리는 것은 엄격해지는 변경
    value = read_entries(policy_file)[0].settings["thresholds.allow_if_safe_confidence_gte"]
    assert value == 0.97 and isinstance(value, float)


def test_set_int_setting(policy_file):
    code, out = gate_policy(policy_file, "set", "max_inspect_bytes", "100000", answers="y\n")
    assert code == 0 and out.count(PROMPT) == 1  # 방향을 모르는 설정은 완화로 보고 묻는다
    assert read_entries(policy_file)[0].settings["max_inspect_bytes"] == 100000


@pytest.mark.parametrize("argv", [
    ["numbers.rrn.visible", "birth"],
    ["thresholds.allow_if_safe_confidence_gte", "0.9"],
    ["path", "공개/**", "allow"],
])
def test_set_loosening_asks_and_refusal_keeps_file(policy_file, argv):
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"})])
    before = policy_file.read_bytes()
    code, out = gate_policy(policy_file, "set", *argv, answers="n\n")
    assert code == 1 and out.count(PROMPT) == 1 and "느슨하게" in out
    assert policy_file.read_bytes() == before
    code, out = gate_policy(policy_file, "set", *argv, answers="y\n")
    assert code == 0 and len(read_entries(policy_file)) == 2


def test_set_path_stricter_saves_without_asking(policy_file):
    code, out = gate_policy(policy_file, "set", "path", "가족/**", "deny")
    assert code == 0 and PROMPT not in out
    assert read_entries(policy_file)[0].path_rule == PathRule("가족/**", "deny")


@pytest.mark.parametrize("argv, hint", [
    (["numbers.rrn.visible", "last4"], "numbers.rrn.visible"),
    (["numbers.card.action", "allow"], "numbers.card.action"),
    (["thresholds.allow_if_safe_confidence_gte", "높게"], "숫자"),
    (["thresholds.allow_if_safe_confidence_gte", "1.5"], "0~1"),
    (["max_inspect_bytes", "2.5"], "정수"),
    (["regex_hit_action", "mask"], "알 수 없는 설정 키"),
    (["numbers.rrn.visible", "birth", "extra"], "하나만"),
    (["path", "공개/**"], "set path"),
    (["path", "공개/**", "open"], "allow"),
    (["path", "../밖/**", "deny"], ".."),
])
def test_set_invalid_exits_1_without_writing(policy_file, argv, hint):
    code, out = gate_policy(policy_file, "set", *argv, answers="y\n")
    assert code == 1 and hint in out
    assert not policy_file.exists()


# --- remove ---

def test_remove_stricter_without_asking(policy_file):
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"}),
                                Entry(path_rule=PathRule("공개/**", "allow"))])
    code, out = gate_policy(policy_file, "remove", "2")
    assert code == 0 and PROMPT not in out
    assert read_entries(policy_file) == (Entry(settings={"numbers.card.action": "deny"}),)


@pytest.mark.parametrize("entry", [
    Entry(settings={"numbers.card.action": "deny"}),
    Entry(context="병원 기록은 보여주지 마"),
    Entry(path_rule=PathRule("가족/**", "deny")),
])
def test_remove_loosening_asks(policy_file, entry):
    write_entries(policy_file, [entry])
    code, out = gate_policy(policy_file, "remove", "1", answers="n\n")
    assert code == 1 and out.count(PROMPT) == 1
    assert read_entries(policy_file) == (entry,)
    assert gate_policy(policy_file, "remove", "1", answers="y\n")[0] == 0
    assert read_entries(policy_file) == ()


@pytest.mark.parametrize("number", ["0", "2", "99", "-1", "abc", "1.0"])
def test_remove_unknown_number_exits_1(policy_file, number):
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"})])
    code, out = gate_policy(policy_file, "remove", number, answers="y\n")
    assert code == 1 and "없는 번호" in out
    assert len(read_entries(policy_file)) == 1


def test_remove_on_empty_policy_exits_1(policy_file):
    code, out = gate_policy(policy_file, "remove", "1")
    assert code == 1 and "지정한 항목이 없음" in out


# --- undo ---

def test_undo_multiple_times_then_nothing_left(policy_file):
    gate_policy(policy_file, "set", "numbers.card.action", "deny")
    gate_policy(policy_file, "set", "numbers.email.action", "deny")
    gate_policy(policy_file, "set", "numbers.mobile.action", "ask_human")
    assert len(read_entries(policy_file)) == 3

    for remaining in (2, 1, 0):
        code, out = gate_policy(policy_file, "undo", answers="y\n")
        assert code == 0
        assert len(read_entries(policy_file)) == remaining
    assert build_policy(read_entries(policy_file)).version == DEFAULT_POLICY.version  # 파일이 없던 상태 = 기본값

    code, out = gate_policy(policy_file, "undo")
    assert code == 1 and "되돌릴 기록이 없어요" in out


def test_undo_without_any_history(policy_file):
    code, out = gate_policy(policy_file, "undo")
    assert code == 1 and "되돌릴 기록이 없어요" in out


def test_undo_of_stricter_change_is_loosening_and_asks(policy_file):
    gate_policy(policy_file, "set", "numbers.card.action", "deny")
    code, out = gate_policy(policy_file, "undo", answers="n\n")
    assert code == 1 and out.count(PROMPT) == 1 and "카드번호: 거부 → 카드번호: 가려서 보여줌" in out
    assert len(read_entries(policy_file)) == 1


def test_undo_of_loosening_change_is_immediate(policy_file):
    gate_policy(policy_file, "set", "numbers.rrn.visible", "birth", answers="y\n")
    code, out = gate_policy(policy_file, "undo")
    assert code == 0 and PROMPT not in out
    assert read_entries(policy_file) == ()


def test_undo_restores_said_and_order_after_remove(policy_file):
    gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers="y\n")
    gate_policy(policy_file, "add", "병원 기록은 보여주지 마", answers="y\n")
    before = read_entries(policy_file)
    gate_policy(policy_file, "remove", "1", answers="y\n")
    assert gate_policy(policy_file, "undo")[0] == 0  # 지운 것을 되살리는 것은 엄격해지는 변경
    assert read_entries(policy_file) == before


def test_declined_change_leaves_no_history(policy_file):
    gate_policy(policy_file, "set", "numbers.rrn.visible", "birth", answers="n\n")
    gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers="n\n")
    assert not history_dir(policy_file).exists()


def test_history_permissions_and_limit(policy_file):
    for i in range(MAX_HISTORY + 5):
        assert gate_policy(policy_file, "set", "numbers.card.action", ("deny", "ask_human")[i % 2],
                           answers="y\n")[0] == 0
    folder = history_dir(policy_file)
    assert folder == policy_file.parent / "policy-history"
    assert mode(folder) == 0o700
    files = sorted(p for p in folder.iterdir())
    assert len(files) == MAX_HISTORY and all(p.suffix == ".yaml" for p in files)
    assert all(mode(p) == 0o600 for p in files)
    assert mode(policy_file) == 0o600
    for _ in range(MAX_HISTORY):
        assert gate_policy(policy_file, "undo", answers="y\n")[0] == 0
    assert len(read_entries(policy_file)) == 5  # 오래된 기록 5개는 지워져 그 이전으로는 못 돌아간다
    assert gate_policy(policy_file, "undo")[0] == 1


# --- 터미널 확인 ---

@pytest.mark.parametrize("argv", [
    ["add", "카드번호 있는 파일은 거부"],
    ["set", "numbers.card.action", "deny"],
    ["remove", "1"],
    ["undo"],
])
def test_change_commands_require_terminal(policy_file, argv):
    write_entries(policy_file, [Entry(settings={"numbers.email.action": "deny"})])
    gate_policy(policy_file, "set", "numbers.card.action", "deny")  # undo할 기록을 하나 만든다
    before = policy_file.read_bytes()
    code, out = gate_policy(policy_file, *argv, answers="y\ny\n", require_tty=True)
    assert code == 1 and "터미널" in out
    assert policy_file.read_bytes() == before


def test_change_commands_accept_terminal(policy_file):
    code, _ = gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", stdin=FakeTty("y\n"), require_tty=True)
    assert code == 0 and len(read_entries(policy_file)) == 1


# --- 잘못된 정책 파일 ---

@pytest.mark.parametrize("content", [
    "version: 2\nentries: []\n",
    "version: 1\nentries:\n  - set: {numbers.rrn.visible: everything}\n",
    "version: 1\nentries: [\n",
    "- just a list\n",
])
@pytest.mark.parametrize("argv", [["show"], ["set", "numbers.card.action", "deny"], ["remove", "1"],
                                  ["add", "카드번호 있는 파일은 거부"]])
def test_invalid_policy_file_is_reported(policy_file, content, argv):
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text(content, encoding="utf-8")
    code, out = gate_policy(policy_file, *argv, answers="y\ny\n")
    assert code == 1
    assert "정책 파일에 문제가 있어 읽을 수 없어요" in out and "Traceback" not in out
    assert policy_file.read_text(encoding="utf-8") == content


def test_undo_recovers_from_invalid_policy_file(policy_file):
    gate_policy(policy_file, "set", "numbers.card.action", "deny")
    gate_policy(policy_file, "set", "numbers.email.action", "deny")
    policy_file.write_text("version: 1\nentries: [\n", encoding="utf-8")
    code, out = gate_policy(policy_file, "undo", answers="n\n")
    assert code == 1 and "문제가 있어요" in out and out.count(PROMPT) == 1
    code, out = gate_policy(policy_file, "undo", answers="y\n")
    assert code == 0
    [entry] = read_entries(policy_file)
    assert dict(entry.settings) == {"numbers.card.action": "deny"}


def test_undo_with_invalid_policy_and_no_history_reports_file_problem(policy_file):
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text("version: 9\n", encoding="utf-8")
    code, out = gate_policy(policy_file, "undo")
    assert code == 1 and "정책 파일에 문제가 있어" in out


# --- 데몬 반영 ---

def test_running_daemon_source_sees_change(policy_file):
    source = PolicySource(policy_file)
    assert source.current().version == DEFAULT_POLICY.version
    gate_policy(policy_file, "set", "numbers.card.action", "deny")
    policy = source.current()
    assert policy.number_rule("card") == "deny"
    _, out = gate_policy(policy_file, "show")
    assert policy.version in out


# --- 3e: 묶음 정리 (2026-11-01 NER 교체) ---

def test_show_lists_nine_groups_and_host_is_rule_based(policy_file):
    write_entries(policy_file, [Entry(settings={"ner_backend": "none"})])
    _, out = gate_policy(policy_file, "show")
    assert "지금 NER이 꺼져 있어 IP·내부 주소(규칙)만 적용" in out
    assert "IP·내부 주소: 가려서 보여줌 [기본] (규칙으로 찾음, NER을 꺼도 적용)" in out
    assert "    나이: 그대로 보여줌 [기본]" in out and "식별번호(학번·사번·여권번호 등): 가려서 보여줌" in out
    assert "    날짜:" not in out and "    사건:" not in out and "시간·연도" not in out


def test_old_retired_entries_are_shown_and_removable(policy_file):
    policy_file.parent.mkdir(parents=True)
    policy_file.write_text("version: 1\nentries:\n- said: 날짜는 거부\n  set: {entities.date.action: deny}\n",
                           encoding="utf-8")
    code, out = gate_policy(policy_file, "show")
    assert code == 0 and "날짜(NER): 거부 (지금은 찾지 않음)" in out
    assert gate_policy(policy_file, "remove", "1")[0] == 0
    assert read_entries(policy_file) == ()


def test_change_flow_uses_one_polite_tone(policy_file):
    """gate policy의 안내는 설정 화면과 같은 말투(~해요)로 쓴다."""
    outputs = [
        gate_policy(policy_file, "show")[1],
        gate_policy(policy_file, "add", "카드번호 있는 파일은 거부", answers="y\n")[1],
        gate_policy(policy_file, "add", "병원 기록은 보여주지 마", answers="n\n")[1],
        gate_policy(policy_file, "add", "주민번호는 생년월일까지만 보여줘", answers="y\nn\n")[1],
        gate_policy(policy_file, "set", "numbers.card.action", "deny")[1],          # 같은 값(바뀌지 않음)
        gate_policy(policy_file, "set", "numbers.email.action", "deny")[1],
        gate_policy(policy_file, "set", "entities.date.action", "mask")[1],
        gate_policy(policy_file, "remove", "9")[1],
        gate_policy(policy_file, "remove", "2", answers="y\n")[1],
        gate_policy(policy_file, "undo", answers="y\n")[1],
        gate_policy(policy_file, "show")[1],
    ]
    for out in outputs:
        assert "니다" not in out, out


@pytest.mark.parametrize("key", ["entities.date.action", "entities.event.action"])
def test_set_refuses_retired_keys(policy_file, key):
    code, out = gate_policy(policy_file, "set", key, "mask")
    assert code == 1 and "지정할 수 없어요" in out and "따로 찾지 않아요" in out
    assert not policy_file.exists()


def test_add_retired_kind_sentence_is_not_saved(policy_file):
    code, out = gate_policy(policy_file, "add", "날짜는 가려줘", answers="y\n", interpreter=MockPolicyInterpreter())
    assert code == 1 and "따로 찾지 않아요" in out and PROMPT not in out
    assert not policy_file.exists()
