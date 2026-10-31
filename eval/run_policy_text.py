"""정책 문장 해석기(gate.policy_text)를 문장 평가 세트로 잰다 (3d).

    uv run python -m eval.run_policy_text --interpreter mock [--details]
    JEV_BASE_URL=http://127.0.0.1:8000 JEV_MODEL=gate-laya-v3 uv run python -m eval.run_policy_text --interpreter laya

- 평가 세트: eval/policy_sentences.csv (id, sentence, expect, setting, group, note). 작업자 B가 해석기 키워드 표를 보지 않고 썼다.
  expect는 setting | context | unsupported | path | unclear, setting은 `키=값`이고 정답이 여럿이면 `|`로 잇는다.
- 판정 (문장마다 하나)
  - 맞음: 저장할 문장(setting·context)은 정답과 같은 것을 저장하려 했다. 저장하지 않을 문장(unsupported·path·unclear)은
    아무것도 저장하지 않았다. 저장하지 않는 결과끼리 이름까지 같은지(예: path를 path로)는 '결과 일치'로 따로 센다.
  - 안전한 실패: 저장할 문장을 저장하지 않았다(다시 말해 달라고 함, 해석기 오류 포함).
  - 잘못 저장: 정답과 다른 설정·맥락 규칙을 저장하려 했다. 가장 나쁜 경우다(사용자가 "이렇게 이해했어요"를 보고 거절할 수는 있다).
    그중 기본 정책보다 느슨해지는 것('느슨해짐')을 따로 센다(gate.policy.loosened. 저장 전 한 번 더 확인하긴 한다).
- 해석기 오류(연결 실패 등)는 '오류'로도 따로 센다. 오류가 있으면 해석기 성능이 아니라 실행 환경 문제일 수 있다.
- Mock 결과는 실제 해석 성능을 대변하지 않는다. 키워드 표와 같은 사람이 문장을 쓰지 않게 평가 세트는 따로 만들었다.
"""

import argparse
import csv
import sys
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from eval.run_eval import _ratio, _table
from gate.policy import DEFAULT_POLICY, PolicyError, build_policy, loosened, validate_setting
from gate.policy_text import (
    CONFIDENCE_THRESHOLD,
    DEFAULT_TIMEOUT_SECONDS,
    InterpretError,
    Interpretation,
    PolicyInterpreter,
    available_interpreters,
    interpret,
    make_interpreter,
)

DEFAULT_SENTENCES = Path(__file__).resolve().parent / "policy_sentences.csv"
EXPECTS = ("setting", "context", "unsupported", "path", "unclear")
SAVING = frozenset({"setting", "context"})
GROUP_ORDER = ("entity", "number", "context", "unsupported", "path", "unclear")
MOCK_NOTICE = "Mock(키워드 규칙) 결과는 실제 해석 성능을 대변하지 않습니다."

Verdict = Literal["correct", "safe_miss", "wrong_save"]


@dataclass(frozen=True)
class SentenceCase:
    id: str
    sentence: str
    expect: str
    accepted: frozenset[tuple[str, str]]  # expect가 setting일 때 정답 (키, 값)들
    group: str
    note: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: SentenceCase
    result: Interpretation
    verdict: Verdict
    exact: bool          # 결과 이름까지 기대와 같다
    loosening: bool      # 잘못 저장한 것이 기본 정책보다 느슨하다
    seconds: float = 0.0


@dataclass
class Tally:
    total: int = 0
    counts: Counter = field(default_factory=Counter)
    loosening: int = 0

    def add(self, r: CaseResult) -> None:
        self.total += 1
        self.counts[r.verdict] += 1
        self.loosening += r.loosening


def _parse_setting(text: str, where: str) -> frozenset[tuple[str, str]]:
    pairs = set()
    for part in text.split("|"):
        key, sep, value = part.strip().partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            raise ValueError(f"{where}: setting은 `키=값`(여럿이면 |로 잇기)이어야 합니다: {text!r}")
        try:
            validate_setting(key, value)
        except PolicyError as exc:
            raise ValueError(f"{where}: {exc}") from None
        pairs.add((key, value))
    return frozenset(pairs)


def load_sentences(path: Path = DEFAULT_SENTENCES) -> list[SentenceCase]:
    """평가 세트를 읽는다. 형식이 틀리면 ValueError(어느 줄인지 함께)."""
    cases: list[SentenceCase] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            where = f"{path.name} {n}번째 줄"
            missing = {"id", "sentence", "expect", "setting", "group"} - {k for k, v in row.items() if v is not None}
            if missing:
                raise ValueError(f"{where}: 칸이 없습니다: {', '.join(sorted(missing))}")
            case_id, expect, setting = row["id"].strip(), row["expect"].strip(), row["setting"].strip()
            if not case_id or case_id in seen:
                raise ValueError(f"{where}: id가 비었거나 겹칩니다: {case_id!r}")
            seen.add(case_id)
            if expect not in EXPECTS:
                raise ValueError(f"{where}: expect는 {', '.join(EXPECTS)} 중 하나여야 합니다: {expect!r}")
            if (expect == "setting") != bool(setting):
                raise ValueError(f"{where}: setting 칸은 expect가 setting일 때만 채웁니다")
            accepted = _parse_setting(setting, where) if setting else frozenset()
            cases.append(SentenceCase(case_id, row["sentence"], expect, accepted, row["group"].strip(),
                                      (row.get("note") or "").strip()))
    return cases


def judge(case: SentenceCase, result: Interpretation) -> CaseResult:
    entry = result.entry
    if entry is None:
        verdict: Verdict = "safe_miss" if case.expect in SAVING else "correct"
    elif case.expect == "setting":
        got = set(entry.settings.items()) if entry.settings else set()
        verdict = "correct" if len(got) == 1 and got <= case.accepted else "wrong_save"
    elif case.expect == "context":
        verdict = "correct" if entry.context is not None else "wrong_save"
    else:
        verdict = "wrong_save"
    loose = verdict == "wrong_save" and bool(loosened(DEFAULT_POLICY, build_policy([entry])))
    return CaseResult(case, result, verdict, result.outcome == case.expect, loose)


def evaluate(cases: Sequence[SentenceCase], interpreter: PolicyInterpreter, *,
             threshold: float = CONFIDENCE_THRESHOLD) -> list[CaseResult]:
    out = []
    for case in cases:
        start = time.perf_counter()
        result = interpret(case.sentence, interpreter, threshold=threshold)
        elapsed = time.perf_counter() - start
        r = judge(case, result)
        out.append(CaseResult(r.case, r.result, r.verdict, r.exact, r.loosening, elapsed))
    return out


def _saved_text(result: Interpretation) -> str:
    entry = result.entry
    if entry is None:
        return f"저장 안 함({result.outcome})"
    if entry.settings:
        return ", ".join(f"{k}={v}" for k, v in entry.settings.items())
    return f"맥락 규칙 \"{entry.context}\""


def _expected_text(case: SentenceCase) -> str:
    if case.expect == "setting":
        return " | ".join(sorted(f"{k}={v}" for k, v in case.accepted))
    return case.expect


def _steps(result: Interpretation) -> str:
    return " ".join(f"{s.key}={s.choice.answer}/{s.choice.confidence:.2f}" for s in result.steps)


def format_report(results: Sequence[CaseResult], *, name: str, threshold: float, notice: str | None = None) -> str:
    groups: dict[str, Tally] = {}
    overall = Tally()
    for r in results:
        groups.setdefault(r.case.group, Tally()).add(r)
        overall.add(r)
    order = [g for g in GROUP_ORDER if g in groups] + sorted(set(groups) - set(GROUP_ORDER))

    def row(label: str, t: Tally) -> list[str]:
        return [label, str(t.total), _ratio(t.counts["correct"], t.total), _ratio(t.counts["safe_miss"], t.total),
                _ratio(t.counts["wrong_save"], t.total), str(t.loosening)]

    header = ["분류", "문장", "맞음", "안전한 실패", "잘못 저장", "그중 느슨해짐"]
    lines = [f"정책 문장 해석 평가: 해석기 {name}, 문장 {overall.total}개, 확신도 임계값 {threshold:g}"]
    if notice:
        lines.append(f"주의: {notice}")
    lines.append("")
    lines += _table(header, [row(g, groups[g]) for g in order] + [row("전체", overall)])
    errors = sum(r.result.outcome == "error" for r in results)
    non_saving = [r for r in results if r.case.expect not in SAVING]
    lines.append("")
    lines.append(f"해석기 오류: {errors}")
    if non_saving:
        lines.append(f"저장하지 않을 문장의 결과 일치(path·unsupported·unclear 구분): "
                     f"{_ratio(sum(r.exact for r in non_saving), len(non_saving))}")
    if results:
        avg = sum(r.seconds for r in results) / len(results)
        lines.append(f"문장당 평균 {avg * 1000:.0f}ms")
    wrong = [r for r in results if r.verdict == "wrong_save"]
    if wrong:
        lines.append("")
        lines.append("잘못 저장:")
        for r in wrong:
            mark = " [느슨해짐]" if r.loosening else ""
            lines.append(f"  {r.case.id} \"{r.case.sentence}\"{mark}")
            lines.append(f"      기대 {_expected_text(r.case)} → {_saved_text(r.result)}")
    return "\n".join(lines)


def format_details(results: Sequence[CaseResult]) -> str:
    """맞지 않은 문장(안전한 실패 포함)과 결과 이름이 다른 문장을 질문 단계와 함께 보여 준다."""
    lines = ["문장별 (맞지 않거나 결과 이름이 다른 것만):"]
    shown = 0
    for r in results:
        if r.verdict == "correct" and r.exact:
            continue
        shown += 1
        tag = {"correct": "맞음(결과 이름 다름)", "safe_miss": "안전한 실패", "wrong_save": "잘못 저장"}[r.verdict]
        lines.append(f"  {r.case.id} [{r.case.group}] {tag}: \"{r.case.sentence}\"")
        lines.append(f"      기대 {_expected_text(r.case)} → {_saved_text(r.result)}")
        if r.result.steps:
            lines.append(f"      질문: {_steps(r.result)}")
        if r.case.note:
            lines.append(f"      메모: {r.case.note}")
    if not shown:
        lines.append("  (없음)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run_policy_text", description="정책 문장 해석기를 평가한다")
    parser.add_argument("--interpreter", required=True, choices=available_interpreters(), help="해석기 이름")
    parser.add_argument("--sentences", type=Path, default=DEFAULT_SENTENCES, help="평가 세트 CSV")
    parser.add_argument("--threshold", type=float, default=CONFIDENCE_THRESHOLD, help="확신도 임계값")
    parser.add_argument("--timeout", type=float, default=max(DEFAULT_TIMEOUT_SECONDS, 30.0),
                        help="해석기 질문 하나의 타임아웃(초)")
    parser.add_argument("--details", action="store_true", help="맞지 않은 문장을 질문 단계와 함께 출력")
    args = parser.parse_args(argv)
    try:
        cases = load_sentences(args.sentences)
        interpreter = make_interpreter(args.interpreter, timeout=args.timeout)
    except (OSError, ValueError, InterpretError) as exc:
        print(f"eval: 평가를 중단합니다. {exc}", file=sys.stderr)
        return 2
    results = evaluate(cases, interpreter, threshold=args.threshold)
    notice = MOCK_NOTICE if args.interpreter == "mock" else None
    print(format_report(results, name=args.interpreter, threshold=args.threshold, notice=notice))
    if args.details:
        print()
        print(format_details(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
