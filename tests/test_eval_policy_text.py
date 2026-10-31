"""eval.run_policy_text: 정책 문장 해석기 평가 (3d)."""

import pytest

from eval.run_policy_text import (
    DEFAULT_SENTENCES,
    SentenceCase,
    evaluate,
    format_details,
    format_report,
    judge,
    load_sentences,
    main,
)
from gate.policy_text import MockPolicyInterpreter, interpret

HEADER = "id,sentence,expect,setting,group,note\n"


def write_csv(tmp_path, body: str):
    path = tmp_path / "sentences.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def case(expect, setting=(), sentence="문장", group="entity"):
    return SentenceCase("P1", sentence, expect, frozenset(setting), group)


def test_load_sentences_parses_alternatives(tmp_path):
    path = write_csv(tmp_path, 'P1,이메일은 가려줘,setting,"numbers.email.action=mask|numbers.email.visible=none",number,\n'
                               "P2,병원 기록은 보여주지 마,context,,context,주제\n")
    first, second = load_sentences(path)
    assert first.accepted == {("numbers.email.action", "mask"), ("numbers.email.visible", "none")}
    assert second.expect == "context" and second.accepted == frozenset() and second.note == "주제"


@pytest.mark.parametrize("body", [
    "P1,문장,maybe,,entity,\n",                                   # 모르는 expect
    "P1,문장,setting,,entity,\n",                                 # setting인데 정답 없음
    "P1,문장,unclear,entities.name.action=mask,entity,\n",       # setting이 아닌데 정답 있음
    "P1,문장,setting,entities.name.action=show,entity,\n",       # 없는 값
    "P1,문장,setting,entities.nobody.action=mask,entity,\n",     # 없는 키
    "P1,문장,setting,entities.name.action,entity,\n",            # = 없음
    "P1,문장,unclear,,entity,\nP1,문장,unclear,,entity,\n",       # id 겹침
])
def test_load_sentences_rejects_bad_rows(tmp_path, body):
    with pytest.raises(ValueError):
        load_sentences(write_csv(tmp_path, body))


def test_judge_categories():
    mock = MockPolicyInterpreter()
    name_mask = {("entities.name.action", "mask")}
    assert judge(case("setting", name_mask), interpret("이름은 가려줘", mock)).verdict == "correct"
    assert judge(case("setting", {("entities.name.action", "deny")}),
                 interpret("이름은 가려줘", mock)).verdict == "wrong_save"
    assert judge(case("setting", name_mask), interpret("아무거나", mock)).verdict == "safe_miss"
    # 묶음 문장을 맥락 규칙으로 저장하면 잘못 저장이다(3d 전의 동작)
    assert judge(case("setting", name_mask), interpret("가족 이야기는 보여주지 마", mock)).verdict == "wrong_save"
    assert judge(case("context"), interpret("병원 기록은 보여주지 마", mock)).verdict == "correct"
    assert judge(case("context"), interpret("이름은 가려줘", mock)).verdict == "wrong_save"
    unclear = judge(case("unclear"), interpret("공개 폴더는 그냥 보여줘", mock))
    assert unclear.verdict == "correct" and not unclear.exact  # 저장 안 함은 맞음, 결과 이름은 path로 다름
    assert judge(case("path"), interpret("공개 폴더는 그냥 보여줘", mock)).exact
    assert judge(case("unclear"), interpret("이름은 가려줘", mock)).verdict == "wrong_save"


def test_wrong_save_that_loosens_is_flagged():
    mock = MockPolicyInterpreter()
    loose = judge(case("unclear"), interpret("주소는 보여줘도 돼", mock))
    assert loose.verdict == "wrong_save" and loose.loosening
    strict = judge(case("unclear"), interpret("이름 있는 파일은 거부", mock))
    assert strict.verdict == "wrong_save" and not strict.loosening


def test_report_and_details(tmp_path):
    path = write_csv(tmp_path, "P1,이름은 가려줘,setting,entities.name.action=mask,entity,\n"
                               "P2,주소는 보여줘도 돼,unclear,,unclear,일부러 틀린 정답\n"
                               "P3,아무거나,setting,entities.name.action=mask,entity,\n")
    results = evaluate(load_sentences(path), MockPolicyInterpreter())
    report = format_report(results, name="mock", threshold=0.7, notice="주의 문구")
    assert "문장 3개" in report and "주의 문구" in report
    assert "잘못 저장:" in report and "P2" in report and "[느슨해짐]" in report
    details = format_details(results)
    assert "P3" in details and "안전한 실패" in details and "질문: type=" in details
    assert "P1" not in details
    assert "일부러 틀린 정답" in details


def test_main_runs_mock(tmp_path, capsys):
    path = write_csv(tmp_path, "P1,이름은 가려줘,setting,entities.name.action=mask,entity,\n")
    assert main(["--interpreter", "mock", "--sentences", str(path), "--details"]) == 0
    out = capsys.readouterr().out
    assert "해석기 mock" in out and "Mock" in out


def test_main_stops_on_bad_input(tmp_path, capsys):
    assert main(["--interpreter", "mock", "--sentences", str(tmp_path / "없음.csv")]) == 2
    assert main(["--interpreter", "laya", "--sentences", str(write_csv(tmp_path, ""))]) == 2  # JEV_BASE_URL 없음
    assert "중단" in capsys.readouterr().err


@pytest.mark.skipif(not DEFAULT_SENTENCES.exists(), reason="평가 세트가 아직 없다")
def test_default_sentences_are_valid():
    cases = load_sentences()
    assert len(cases) >= 60
