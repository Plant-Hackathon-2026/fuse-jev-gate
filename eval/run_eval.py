"""합성 한국어 샘플로 Jev 판단을 평가한다.

    uv run python -m eval.run_eval --client mock [--policy gate-data/policy.yaml] [--details]

- 샘플은 정규식 마스킹(기본 gate.detectors.mask, 공개 범위는 정책)을 거친 뒤 Jev에 보낸다. 마스킹 함수를 불러올 수 없거나
  마스킹 중 오류가 나면 평가를 멈춘다. 마스킹하지 않은 내용은 Jev에 보내지 않는다.
- 게이트 결과는 실제 정책 엔진(gate.policy)으로 낸다: 번호 종류별 동작과 Jev 답(임계값 두 개)을 합쳐
  허용·가림·사람 승인·차단 중 하나. --policy를 주지 않으면 기본 정책이다. 샘플은 .txt라 경로 규칙은 적용되지 않는다.
- Jev에 넘기는 가상 경로에는 경우(pii 등)가 드러나지 않게 중립 이름을 쓴다. 파일 이름이 답을 알려 주지 않게 하기 위해서다.
- JevError(및 그 밖의 예외)가 난 샘플은 '오류'로 따로 세고, 정책 결과는 차단으로 집계한다.
- Mock 결과는 Jev의 실제 성능을 대변하지 않는다.
"""

import argparse
import csv
import hashlib
import os
import sys
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, Protocol

from gate.detectors import KINDS
from gate.jev_client import (
    DEFAULT_TIMEOUT_SECONDS,
    LABELS,
    JevClient,
    JevDecision,
    JevError,
    RequestContext,
    available_backends,
    make_client,
)
from gate.policy import DEFAULT_POLICY, Policy, PolicyError, jev_action, load_policy, number_action, strictest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LABELS = PROJECT_ROOT / "eval" / "labels.csv"
DEFAULT_PURPOSE = "문서 내용을 요약하기 위해"

DOC_TYPES = {"cover_letter": "자기소개서", "consult_chat": "상담채팅", "medical_note": "진료메모", "resume": "이력서"}
CASES = {"pii": "개인정보 있음", "quasi": "준식별자만", "none": "없음"}
EXPECTED_BY_CASE = {"pii": "highly_sensitive", "quasi": "sensitive", "none": "safe"}
ERROR = "error"

Outcome = Literal["allow", "mask", "ask_human", "deny"]
OUTCOMES: tuple[Outcome, ...] = ("allow", "mask", "ask_human", "deny")
OUTCOME_NAMES = {"allow": "허용", "mask": "가림", "ask_human": "사람 승인", "deny": "차단"}

MOCK_NOTICE = (
    "주의: MockJevClient(단순 키워드 규칙) 결과다. Jev의 실제 성능(한국어 포함)을 대변하지 않는다.\n"
    "      규칙과 샘플을 같은 작업에서 만들었으므로 점수 자체에는 의미가 없다. 파이프라인 동작 확인용이다."
)


class Masked(Protocol):
    @property
    def text(self) -> str: ...

    @property
    def findings(self) -> Sequence[object]: ...


Masker = Callable[..., Masked]  # masker(text, visible=...)


class MaskerUnavailable(RuntimeError):
    """마스킹 함수를 쓸 수 없다. 이때는 평가를 멈춘다."""


@dataclass(frozen=True)
class LabeledSample:
    path: str      # 프로젝트 루트 기준 상대 경로
    expected: str  # safe | sensitive | highly_sensitive
    doc_type: str
    case: str


@dataclass(frozen=True)
class SampleResult:
    sample: LabeledSample
    decision: JevDecision | None  # 오류면 None
    error: str | None
    latency_ms: float
    findings: int  # 정규식 탐지 건수
    kinds: tuple[str, ...] = ()  # 탐지한 종류 (게이트 결과에 쓴다)

    @property
    def predicted(self) -> str:
        return self.decision.label if self.decision is not None else ERROR


@dataclass(frozen=True)
class Stats:
    total: int
    correct: int
    errors: int
    safe_total: int
    false_positives: int  # 기대 safe → safe가 아닌 답 또는 오류(차단)
    unsafe_total: int
    false_negatives: int  # 기대 safe 아님 → safe

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.total if self.total else None

    @property
    def safe_correct(self) -> int:
        """safe인지 아닌지만 맞힌 수. 오류는 safe가 아님(차단)으로 센다."""
        return self.total - self.false_positives - self.false_negatives

    @property
    def false_positive_rate(self) -> float | None:
        return self.false_positives / self.safe_total if self.safe_total else None

    @property
    def false_negative_rate(self) -> float | None:
        return self.false_negatives / self.unsafe_total if self.unsafe_total else None


@dataclass(frozen=True)
class Report:
    policy: Policy
    overall: Stats
    by_doc_type: dict[str, Stats]
    by_case: dict[str, Stats]
    confusion: dict[str, Counter]  # 기대 라벨 → 답(라벨 또는 error) 건수
    outcomes: dict[str, Counter]   # 기대 라벨 → 게이트 결과 건수
    findings_by_case: dict[str, int]
    mean_latency_ms: float | None
    max_latency_ms: float | None


def load_labels(path: Path = DEFAULT_LABELS) -> list[LabeledSample]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    samples = []
    for line_no, row in enumerate(rows, start=2):
        sample = LabeledSample(row["path"], row["expected"], row["doc_type"], row["case"])
        if sample.expected not in LABELS or sample.doc_type not in DOC_TYPES or sample.case not in CASES:
            raise ValueError(f"{path}:{line_no}: 라벨 값이 올바르지 않습니다: {row}")
        samples.append(sample)
    return samples


def virtual_path(sample: LabeledSample) -> str:
    """Jev에 보여 줄 가상 경로. 문서 종류는 드러내되 경우(pii 등)는 숨긴다."""
    digest = hashlib.sha256(sample.path.encode("utf-8")).hexdigest()[:8]
    return f"/{DOC_TYPES[sample.doc_type]}/{digest}.txt"


def load_default_masker() -> Masker:
    try:
        from gate.detectors import mask
    except Exception as exc:
        raise MaskerUnavailable(
            f"정규식 마스킹(gate.detectors.mask)을 불러올 수 없습니다 ({type(exc).__name__}: {exc}). "
            "마스킹하지 않은 내용은 Jev에 보내지 않습니다."
        ) from exc
    return mask


def evaluate(
    samples: Sequence[LabeledSample],
    client: JevClient,
    *,
    masker: Masker,
    root: Path = PROJECT_ROOT,
    purpose: str = DEFAULT_PURPOSE,
    policy: Policy = DEFAULT_POLICY,
) -> list[SampleResult]:
    """샘플마다 마스킹한 뒤 Jev에 묻는다. 마스킹이 실패하면 예외를 그대로 올려 평가를 멈춘다."""
    results = []
    for sample in samples:
        text = (root / sample.path).read_text(encoding="utf-8")
        masked = masker(text, visible=policy.visible)
        kinds = tuple(k for k in (getattr(f, "kind", None) for f in masked.findings) if k in KINDS)
        context = RequestContext(sys.executable, os.getpid(), virtual_path(sample), purpose, policy.context_rules)
        started = time.perf_counter()
        try:
            decision = client.judge(masked.text, context)
            if not isinstance(decision, JevDecision):
                raise JevError(f"Jev 응답 형식이 올바르지 않습니다: {type(decision).__name__}")
            error = None
        except JevError as exc:
            decision, error = None, str(exc)
        except Exception as exc:  # JevError로 감싸지 않은 클라이언트라도 실패는 차단으로 센다
            decision, error = None, f"{type(exc).__name__}: {exc}"
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(SampleResult(sample, decision, error, latency_ms, len(masked.findings), kinds))
    return results


def policy_outcome(decision: JevDecision | None, policy: Policy = DEFAULT_POLICY) -> Outcome:
    """Jev 답만 보고 낸 정책 결과(gate.policy.jev_action). Jev 오류(decision이 None)는 차단이다."""
    return jev_action(policy, decision)


def gate_outcome(result: SampleResult, policy: Policy = DEFAULT_POLICY) -> Outcome:
    """게이트가 실제로 낼 결과. 번호 종류별 동작과 Jev 결과 중 엄격한 쪽이고, 허용인데 가린 번호가 있으면 가림이다."""
    final = strictest(number_action(policy, [SimpleNamespace(kind=k) for k in result.kinds]),
                      jev_action(policy, result.decision))
    return "mask" if final == "allow" and result.findings else final


def compute_stats(results: Sequence[SampleResult]) -> Stats:
    safe = [r for r in results if r.sample.expected == "safe"]
    unsafe = [r for r in results if r.sample.expected != "safe"]
    return Stats(
        total=len(results),
        correct=sum(r.predicted == r.sample.expected for r in results),
        errors=sum(r.decision is None for r in results),
        safe_total=len(safe),
        false_positives=sum(r.predicted != "safe" for r in safe),
        unsafe_total=len(unsafe),
        false_negatives=sum(r.predicted == "safe" for r in unsafe),
    )


def summarize(results: Sequence[SampleResult], policy: Policy = DEFAULT_POLICY) -> Report:
    def grouped(key: Callable[[SampleResult], str], order: Sequence[str]) -> dict[str, Stats]:
        return {k: compute_stats([r for r in results if key(r) == k])
                for k in order if any(key(r) == k for r in results)}

    confusion = {label: Counter() for label in LABELS}
    outcomes = {label: Counter() for label in LABELS}
    findings_by_case: Counter = Counter()
    for r in results:
        confusion[r.sample.expected][r.predicted] += 1
        outcomes[r.sample.expected][gate_outcome(r, policy)] += 1
        findings_by_case[r.sample.case] += r.findings
    latencies = [r.latency_ms for r in results]
    return Report(
        policy=policy,
        overall=compute_stats(results),
        by_doc_type=grouped(lambda r: r.sample.doc_type, list(DOC_TYPES)),
        by_case=grouped(lambda r: r.sample.case, list(CASES)),
        confusion=confusion,
        outcomes=outcomes,
        findings_by_case={case: findings_by_case[case] for case in CASES},
        mean_latency_ms=sum(latencies) / len(latencies) if latencies else None,
        max_latency_ms=max(latencies) if latencies else None,
    )


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]], align: str | None = None) -> list[str]:
    """align은 열마다 'l' 또는 'r'. 기본은 첫 열만 왼쪽 정렬. 한글은 두 칸으로 센다."""
    align = align or "l" + "r" * (len(header) - 1)
    widths = [max(_width(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = []
    for row in [header, *rows]:
        cells = [cell + " " * (widths[i] - _width(cell)) if align[i] == "l" else " " * (widths[i] - _width(cell)) + cell
                 for i, cell in enumerate(row)]
        lines.append("  ".join(cells).rstrip())
    return lines


def _ratio(n: int, d: int) -> str:
    return f"{n}/{d} ({n / d:.1%})" if d else "-"


def _stats_row(name: str, s: Stats) -> list[str]:
    return [name, str(s.total), _ratio(s.correct, s.total), _ratio(s.false_positives, s.safe_total),
            _ratio(s.false_negatives, s.unsafe_total), str(s.errors)]


def format_report(report: Report, *, client_name: str, notice: str | None = None) -> str:
    o = report.overall
    p = report.policy
    lines = [f"Jev 평가: 백엔드 {client_name}, 샘플 {o.total}개, 정책 {p.version} "
             f"(허용 임계값 {p.allow_threshold:g}, 차단 임계값 {p.deny_threshold:g})"]
    if notice:
        lines.append(notice)
    if report.mean_latency_ms is None:
        latency, latency_note = "-", ""
    else:
        latency = f"{report.mean_latency_ms:.2f} ms"
        latency_note = f"최대 {report.max_latency_ms:.2f} ms. Jev 호출만 잼 (마스킹 제외)"
    lines += [
        "",
        "[전체]",
        *_table(["항목", "값", "설명"], [
            ["정확도", _ratio(o.correct, o.total), "답 = 기대 라벨 (오류는 틀림)"],
            ["안전 여부 정확도", _ratio(o.safe_correct, o.total), "safe인지 아닌지만 비교. 예/아니오 모델(laya)은 이 값을 본다"],
            ["오탐률", _ratio(o.false_positives, o.safe_total), "기대 safe → safe 아님 (오류 포함)"],
            ["미탐률", _ratio(o.false_negatives, o.unsafe_total), "기대 safe 아님 → safe"],
            ["Jev 오류", f"{o.errors}건", "JevError 등. 정책 결과는 차단으로 집계"],
            ["평균 지연", latency, latency_note],
        ], align="lrl"),
        "",
        "[혼동행렬] 행: 기대 라벨, 열: Jev 답",
        *_table(["기대 \\ 답", *LABELS, ERROR], [
            [label, *(str(report.confusion[label][p]) for p in (*LABELS, ERROR))] for label in LABELS
        ]),
        "",
        "[문서 종류별]",
        *_table(["종류", "n", "정확도", "오탐", "미탐", "오류"],
                [_stats_row(DOC_TYPES[k], s) for k, s in report.by_doc_type.items()]),
        "",
        "[경우별]",
        *_table(["경우", "n", "정확도", "오탐", "미탐", "오류"],
                [_stats_row(CASES[k], s) for k, s in report.by_case.items()]),
        "",
        "[게이트 결과] gate.policy: 번호 종류별 동작 + Jev 답(임계값 두 개). Jev 오류는 차단, 사람 승인은 묻지 않고 그대로 셈",
        *_table(["기대 라벨", *(OUTCOME_NAMES[x] for x in OUTCOMES)], [
            *([label, *(str(report.outcomes[label][x]) for x in OUTCOMES)] for label in LABELS),
            ["합계", *(str(sum(report.outcomes[label][x] for label in LABELS)) for x in OUTCOMES)],
        ]),
        "",
        "[정규식 탐지] Jev에 보내기 전에 가린 건수 (경우별 합계)",
        *_table(["경우", "건수"], [[CASES[k], str(v)] for k, v in report.findings_by_case.items()]),
    ]
    return "\n".join(lines)


def format_details(results: Sequence[SampleResult], policy: Policy = DEFAULT_POLICY) -> str:
    rows = []
    for r in results:
        answer = ERROR if r.decision is None else f"{r.decision.label} ({r.decision.confidence:.2f})"
        rows.append([r.sample.path, r.sample.expected, answer, OUTCOME_NAMES[gate_outcome(r, policy)],
                     str(r.findings), f"{r.latency_ms:.2f}"])
    lines = ["[샘플별]", *_table(["파일", "기대", "답 (확신도)", "게이트", "탐지", "ms"], rows, align="llllrr")]
    lines += [f"오류 {r.sample.path}: {r.error}" for r in results if r.error]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval.run_eval", description="합성 한국어 샘플로 Jev 판단을 평가한다")
    parser.add_argument("--client", required=True, choices=available_backends(), help="Jev 백엔드 이름")
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS, help="라벨 CSV (경로는 프로젝트 루트 기준)")
    parser.add_argument("--policy", type=Path, help="정책 파일 (gate policy로 만든 것). 주지 않으면 기본 정책")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="Jev 호출 타임아웃(초)")
    parser.add_argument("--purpose", default=DEFAULT_PURPOSE, help="Jev 질문에 넣을 요청 목적")
    parser.add_argument("--details", action="store_true", help="샘플별 결과도 출력")
    args = parser.parse_args(argv)

    try:
        policy = load_policy(args.policy)
        masker = load_default_masker()
        client = make_client(args.client, timeout=args.timeout)
    except (MaskerUnavailable, JevError, PolicyError) as exc:
        print(f"eval: 평가를 중단합니다. {exc}", file=sys.stderr)
        return 2

    results = evaluate(load_labels(args.labels), client, masker=masker, purpose=args.purpose, policy=policy)
    notice = MOCK_NOTICE if args.client == "mock" else None
    print(format_report(summarize(results, policy), client_name=args.client, notice=notice))
    if args.details:
        print()
        print(format_details(results, policy))
    return 0


if __name__ == "__main__":
    sys.exit(main())
