import re
import sys
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import run_eval
from eval.run_eval import (
    CASES,
    DEFAULT_PURPOSE,
    DOC_TYPES,
    EXPECTED_BY_CASE,
    MOCK_NOTICE,
    LabeledSample,
    SampleResult,
    compute_stats,
    evaluate,
    format_report,
    gate_outcome,
    load_labels,
    main,
    policy_outcome,
    summarize,
    virtual_path,
)
from gate.jev_client import JevDecision, JevError, MockJevClient, make_client
from gate.policy import Entry, build_policy, write_entries

ROOT = Path(__file__).resolve().parent.parent


def fake_masker(text: str, visible=None) -> SimpleNamespace:
    """gate.detectors 없이 쓰는 가짜 마스킹: 숫자를 전부 '*'로 바꾼다."""
    return SimpleNamespace(text=re.sub(r"\d", "*", text), findings=tuple(re.findall(r"\d{3,}", text)))


def _result(expected, predicted, confidence=0.99, *, doc_type="resume", latency=1.0, kinds=()):
    case = {v: k for k, v in EXPECTED_BY_CASE.items()}[expected]
    sample = LabeledSample(f"samples/{doc_type}/{case}_1.txt", expected, doc_type, case)
    decision = None if predicted is None else JevDecision(predicted, confidence)
    return SampleResult(sample, decision, None if decision else "Jev 실패", latency, len(kinds), tuple(kinds))


@pytest.fixture
def fixed_results():
    return [
        _result("safe", "safe", doc_type="cover_letter", latency=1),               # 맞음, 허용
        _result("safe", "sensitive", doc_type="cover_letter", latency=2),          # 오탐, 차단
        _result("safe", None, doc_type="cover_letter", latency=3),                 # 오류 → 오탐, 차단
        _result("sensitive", "sensitive", 0.9, latency=4),                         # 맞음, 사람 승인
        _result("sensitive", "safe", latency=5),                                   # 미탐, 허용
        _result("highly_sensitive", "highly_sensitive", latency=6),                # 맞음, 차단
        _result("highly_sensitive", None, latency=7),                              # 오류(미탐 아님), 차단
    ]


# --- 지표 계산 ---

def test_compute_stats(fixed_results):
    s = compute_stats(fixed_results)
    assert (s.total, s.correct, s.errors) == (7, 3, 2)
    assert (s.safe_total, s.false_positives) == (3, 2)
    assert (s.unsafe_total, s.false_negatives) == (4, 1)
    assert s.accuracy == pytest.approx(3 / 7)
    assert s.safe_correct == 4  # 오류는 safe가 아님(차단)으로 센다: highly_sensitive 오류는 맞힌 쪽
    assert s.false_positive_rate == pytest.approx(2 / 3)
    assert s.false_negative_rate == pytest.approx(1 / 4)


def test_compute_stats_empty():
    s = compute_stats([])
    assert s.accuracy is None and s.false_positive_rate is None and s.false_negative_rate is None


def test_summarize(fixed_results):
    report = summarize(fixed_results)
    assert report.confusion["safe"] == {"safe": 1, "sensitive": 1, "error": 1}
    assert report.confusion["highly_sensitive"] == {"highly_sensitive": 1, "error": 1}
    assert report.outcomes["safe"] == {"allow": 1, "deny": 2}
    assert report.outcomes["sensitive"] == {"ask_human": 1, "allow": 1}
    assert report.outcomes["highly_sensitive"] == {"deny": 2}
    assert report.by_doc_type["cover_letter"].total == 3
    assert report.by_doc_type["resume"].total == 4
    assert set(report.by_case) == {"pii", "quasi", "none"}
    assert report.mean_latency_ms == pytest.approx(4.0)
    assert report.max_latency_ms == 7


@pytest.mark.parametrize("decision, outcome", [
    (None, "deny"),
    (JevDecision("safe", 0.95), "allow"),
    (JevDecision("safe", 0.949), "ask_human"),
    (JevDecision("sensitive", 0.95), "deny"),
    (JevDecision("highly_sensitive", 0.99), "deny"),
    (JevDecision("highly_sensitive", 0.5), "ask_human"),
])
def test_policy_outcome(decision, outcome):
    assert policy_outcome(decision) == outcome


def test_gate_outcome_combines_number_rules_and_jev():
    masked = _result("safe", "safe", kinds=("mobile",))
    assert gate_outcome(masked) == "mask"
    assert gate_outcome(_result("safe", "safe")) == "allow"
    card_deny = build_policy([Entry(settings={"numbers.card.action": "deny"})])
    assert gate_outcome(_result("safe", "safe", kinds=("card", "mobile")), card_deny) == "deny"
    assert gate_outcome(_result("safe", "safe", kinds=("mobile",)), card_deny) == "mask"
    assert gate_outcome(_result("sensitive", "sensitive", 0.9, kinds=("mobile",))) == "ask_human"
    lenient = build_policy([Entry(settings={"thresholds.allow_if_safe_confidence_gte": 0.5})])
    assert gate_outcome(_result("safe", "safe", 0.6), lenient) == "allow"


def test_format_report_shows_errors_and_notice(fixed_results):
    text = format_report(summarize(fixed_results), client_name="mock", notice=MOCK_NOTICE)
    assert "대변하지 않는다" in text
    assert "2건" in text
    assert "안전 여부 정확도" in text and "4/7 (57.1%)" in text
    for heading in ("[전체]", "[혼동행렬]", "[문서 종류별]", "[게이트 결과]", "가림"):
        assert heading in text


# --- 샘플과 라벨 파일 ---

def _sample_files() -> set[str]:
    return {unicodedata.normalize("NFC", p.relative_to(ROOT).as_posix()) for p in (ROOT / "samples").rglob("*.txt")}


def test_labels_cover_all_samples():
    samples = load_labels()
    paths = [unicodedata.normalize("NFC", s.path) for s in samples]
    assert len(paths) == len(set(paths))
    assert set(paths) == _sample_files()


def test_at_least_30_samples():
    assert len(_sample_files()) >= 30
    assert len(load_labels()) >= 30


def test_labels_are_consistent():
    samples = load_labels()
    for s in samples:
        assert s.expected == EXPECTED_BY_CASE[s.case]
        assert s.path.startswith(f"samples/{s.doc_type}/{s.case}_")
        assert (ROOT / s.path).read_text(encoding="utf-8").strip()
    combos = {(s.doc_type, s.case) for s in samples}
    assert combos == {(t, c) for t in DOC_TYPES for c in CASES}


def test_virtual_path_hides_case():
    samples = load_labels()
    vpaths = [virtual_path(s) for s in samples]
    assert len(set(vpaths)) == len(vpaths)
    for s, vpath in zip(samples, vpaths):
        assert vpath.startswith(f"/{DOC_TYPES[s.doc_type]}/")
        assert not any(case in vpath for case in CASES)


# --- 평가 실행 (gate.detectors 없이 가짜 마스킹 주입) ---

def test_evaluate_sends_masked_text_with_context():
    samples = [s for s in load_labels() if s.case == "pii"][:2]
    client = MockJevClient("fixed", label="highly_sensitive", confidence=0.99)
    results = evaluate(samples, client, masker=fake_masker)
    assert [r.predicted for r in results] == ["highly_sensitive", "highly_sensitive"]
    assert all(r.findings > 0 for r in results)
    for call, sample in zip(client.calls, samples):
        assert not re.search(r"\d", call.text)
        assert call.context.path == virtual_path(sample)
        assert call.context.purpose == DEFAULT_PURPOSE
        assert call.context.pid is not None


@pytest.mark.parametrize("client", [
    MockJevClient(error=JevError("연결 실패")),
    MockJevClient(error=RuntimeError("감싸지 않은 클라이언트의 예외")),
    make_client("mock", timeout=0.01, delay=0.5),
])
def test_evaluate_counts_failures_as_deny(client):
    samples = [s for s in load_labels() if s.case == "none"][:1]
    results = evaluate(samples, client, masker=fake_masker)
    assert results[0].decision is None and results[0].error
    report = summarize(results)
    assert report.overall.errors == 1
    assert report.outcomes["safe"] == {"deny": 1}


def test_evaluate_stops_when_masker_fails():
    def broken_masker(text, visible=None):
        raise ValueError("마스킹 실패")

    client = MockJevClient()
    with pytest.raises(ValueError):
        evaluate(load_labels()[:1], client, masker=broken_masker)
    assert client.calls == []


# --- CLI ---

def test_main_aborts_without_detectors(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "gate.detectors", None)  # import가 실패하게 만든다
    monkeypatch.setattr(run_eval, "evaluate", lambda *a, **k: pytest.fail("마스킹 없이 평가가 진행됨"))
    assert main(["--client", "mock"]) == 2
    assert "중단" in capsys.readouterr().err


def test_main_runs_with_injected_masker(monkeypatch, capsys):
    monkeypatch.setattr(run_eval, "load_default_masker", lambda: fake_masker)
    assert main(["--client", "mock", "--details"]) == 0
    out = capsys.readouterr().out
    assert MOCK_NOTICE in out
    assert f"샘플 {len(load_labels())}개" in out
    assert "[샘플별]" in out


def test_main_uses_policy_file(tmp_path, capsys):
    policy_path = tmp_path / "policy.yaml"
    write_entries(policy_path, [Entry(settings={"numbers.rrn.action": "deny"}), Entry(context="병원 기록은 보여주지 마")])
    assert main(["--client", "mock", "--policy", str(policy_path), "--details"]) == 0
    out = capsys.readouterr().out
    assert build_policy([Entry(settings={"numbers.rrn.action": "deny"}),
                         Entry(context="병원 기록은 보여주지 마")]).version in out


def test_main_aborts_on_bad_policy_file(tmp_path, capsys):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("version: 1\nentries: [", encoding="utf-8")
    assert main(["--client", "mock", "--policy", str(policy_path)]) == 2
    assert "정책 파일" in capsys.readouterr().err


def test_evaluate_passes_policy_visibility_and_context():
    samples = [s for s in load_labels() if s.case == "pii"][:1]
    seen = []

    def recording_masker(text, visible=None):
        seen.append(visible)
        return fake_masker(text)

    policy = build_policy([Entry(settings={"numbers.rrn.visible": "birth"}), Entry(context="병원 기록은 보여주지 마")])
    client = MockJevClient("fixed", label="safe", confidence=0.99)
    evaluate(samples, client, masker=recording_masker, policy=policy)
    assert seen[0]["rrn"] == "birth"
    assert client.calls[0].context.user_policy == ("병원 기록은 보여주지 마",)


# --- 게이트 흐름 (3c) ---

class PerfectNer:
    """가려져야 할 글자(must_hide.csv)를 모두 이름으로 찾는 가짜 NER. 흐름과 누출 세기 확인용이다."""

    def __init__(self, words):
        self.words = sorted(set(words), key=len, reverse=True)

    def cache_tag(self):
        return "ner:perfect"

    def find(self, text):
        from gate.ner_client import Entity, NerResult
        taken: list[tuple[int, int]] = []
        for word in self.words:
            start = text.find(word)
            while start != -1:
                end = start + len(word)
                if all(end <= s or e <= start for s, e in taken):
                    taken.append((start, end))
                start = text.find(word, start + 1)
        return NerResult(tuple(Entity("NAME", s, e, 0.9) for s, e in sorted(taken)), "ner:perfect")


def test_must_hide_file_matches_samples():
    must_hide = run_eval.load_must_hide()
    samples = {s.path: s for s in load_labels()}
    assert {p for p in must_hide} == {p for p, s in samples.items() if s.case != "none"}
    for path, rows in must_hide.items():
        text = (run_eval.PROJECT_ROOT / path).read_text(encoding="utf-8")
        assert all(m.text in text for m in rows), path


def test_gate_run_without_ner_leaks_names_when_jev_says_safe():
    must_hide = run_eval.load_must_hide()
    samples = load_labels()
    safe = MockJevClient("fixed", label="safe", confidence=0.99)
    runs = run_eval.run_gate(samples, safe, None, must_hide)
    leaked = {m.group for r in runs for m in r.leaked}
    assert "name" in leaked and not leaked & {"phone", "email", "rrn", "card"}  # 번호는 정규식이 가린다
    sensitive = MockJevClient("fixed", label="sensitive", confidence=0.99)
    runs = run_eval.run_gate(samples, sensitive, None, must_hide)
    assert all(r.outcome == "deny" and r.shown is None for r in runs)  # NER이 없으면 3b처럼 차단


def test_gate_run_with_ner_rechecks_masked_result_and_counts_leaks():
    must_hide = run_eval.load_must_hide()
    samples = load_labels()
    ner = PerfectNer(m.text for ms in must_hide.values() for m in ms)
    safe = MockJevClient("fixed", label="safe", confidence=0.99)
    runs = run_eval.run_gate(samples, safe, ner, must_hide)
    assert {r.outcome for r in runs if r.sample.case != "none"} == {"mask"}
    assert {r.outcome for r in runs if r.sample.case == "none"} == {"allow"}
    sensitive = MockJevClient("fixed", label="sensitive", confidence=0.99)
    runs = run_eval.run_gate(samples, sensitive, ner, must_hide)
    by_case = {(r.sample.case, r.outcome) for r in runs}
    assert {o for c, o in by_case if c != "none"} == {"ask_human"}  # 가린 결과도 민감하면 사람에게
    assert {o for c, o in by_case if c == "none"} == {"deny"}       # 가린 것이 없으면 Jev대로
    assert all(not r.leaked for r in runs)
    assert all(r.shown is not None for r in runs if r.outcome == "ask_human")  # 승인하면 보일 내용
    report = run_eval.format_gate_runs(runs, must_hide, ner_name="server")
    assert "식별자" in report and "준식별자" in report
    details = run_eval.format_gate_details(runs, show=True)
    assert "[이름]" in details and "(보이지 않음)" in details
