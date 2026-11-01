"""gate audit: 감사 로그(gate.audit)를 사람이 읽는 표로 보여 준다 (Phase 7).

사용
  gate audit                    최근 20건 (-n 0이면 전부)
  gate audit --blocked          막힌 요청만: 차단·승인 필요, 그리고 판단 전에 거부된 요청(EACCES. 원본 밖 경로 등)
  gate audit --decision mask    결정으로 거른다(여러 번 쓸 수 있다): allow mask deny ask_human ok error
  gate audit --path 상담         경로에 이 글자가 든 기록만 (대소문자·NFC/NFD 차이 무시)
  gate audit --since 1h         최근 30m·2h·1d, 또는 그 시각 이후(2026-10-31, 2026-10-31T17:00. 시간대가 없으면 이 컴퓨터 기준)
  gate audit --file <파일>       이 파일과 내용이 같은 파일을 읽은 기록만 (content_hmac 비교)
  gate audit --json             고른 기록을 JSONL 그대로 낸다 (스크립트용, 요약 줄 없음)

로그 경로는 --audit 또는 GATE_AUDIT(데몬과 같다). 조건은 모두 함께 적용한다(그리고).

규칙
  - 읽기만 한다. 로그와 키 파일을 만들거나 바꾸지 않는다. --file은 로그와 같은 폴더의 audit.key가 있어야 한다.
  - 기록은 파일 순서(오래된 것 → 최근)로 보여 주고, -n은 조건에 맞는 것 중 마지막 N건이다.
  - 깨진 줄(UTF-8·JSON이 아니거나 객체가 아닌 줄)은 건너뛰고 stderr에 줄 번호를 알린다. 보여 주기만 하는 명령이라
    멈추지 않는다. 엄격하게 읽으려면 gate.audit.read_records를 쓴다.
  - 경로·이유·프로그램 이름은 에이전트가 정한 글자일 수 있어서 clean_text로 제어 문자를 지우고 보여 준다(--json은 그대로).
  - 에이전트 세션은 감사 로그 폴더를 읽지 못한다(Phase 6 샌드박스·권한 규칙). 사용자 터미널에서 쓰는 명령이다.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, TextIO

from gate.approval import clean_text, kinds_text
from gate.audit import KEY_FILE_NAME, content_hmac, load_key

DECISIONS = ("allow", "mask", "deny", "ask_human", "ok", "error")
DECISION_NAMES = {"allow": "허용", "mask": "가림", "deny": "차단", "ask_human": "승인 필요", "ok": "성공", "error": "오류"}
APPROVAL_NAMES = {"once": "이번만 허용", "session": "이 폴더 허용", "remembered": "폴더 승인 기억",
                  "deny": "거부", "timeout": "무응답(시간 초과)"}
ANSWERED_BY_NAMES = {"dialog": "대화상자", "terminal": "터미널"}
ERRNO_NAMES = {"ENOENT": "없는 경로", "EACCES": "접근 거부", "EROFS": "쓰기 불가", "ENOTDIR": "폴더가 아님",
               "EISDIR": "폴더임", "EINVAL": "잘못된 요청", "ENOSYS": "없는 연산"}
DEFAULT_LAST = 20
_RELATIVE = re.compile(r"(\d+)([mhd])")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}
_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class Entry:
    line: int
    raw: str            # 파일의 줄 그대로(줄바꿈 제외). --json이 낸다
    record: dict


@dataclass
class Selection:
    entries: list[Entry]  # 보여 줄 기록 (-n 적용 뒤)
    total: int            # 읽은 기록 수 (깨진 줄 제외)
    matched: Counter      # 조건에 맞는 기록의 결정별 수 (-n 적용 전)
    broken: list[int]     # 깨진 줄 번호


def register(sub: argparse._SubParsersAction) -> None:
    au = sub.add_parser("audit", help="감사 로그 보기 (최근 요청, 막힌 요청, 파일별 기록)")
    au.add_argument("--audit", default=os.environ.get("GATE_AUDIT"), help="감사 로그 파일 (기본: $GATE_AUDIT)")
    au.add_argument("-n", "--last", type=_non_negative, default=DEFAULT_LAST,
                    help=f"조건에 맞는 것 중 최근 몇 건 (기본 {DEFAULT_LAST}, 0이면 전부)")
    au.add_argument("--blocked", action="store_true", help="막힌 요청만 (차단, 승인 필요, EACCES)")
    au.add_argument("--decision", action="append", choices=DECISIONS, help="이 결정만 (여러 번 쓸 수 있음)")
    au.add_argument("--path", help="경로에 이 글자가 든 기록만")
    au.add_argument("--since", type=parse_since, help="이 시각 이후만 (30m, 2h, 1d, 2026-10-31, 2026-10-31T17:00)")
    au.add_argument("--file", help="이 파일과 내용이 같은 파일을 읽은 기록만 (같은 폴더의 audit.key로 비교)")
    au.add_argument("--json", action="store_true", help="고른 기록을 JSONL 그대로")


def _non_negative(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("0 이상이어야 합니다")
    return number


def parse_since(value: str, *, now: datetime | None = None) -> datetime:
    """30m·2h·1d(지금부터 거슬러), 또는 ISO 날짜·시각. 시간대가 없으면 이 컴퓨터 시간대로 본다."""
    now = now if now is not None else datetime.now().astimezone()
    match = _RELATIVE.fullmatch(value.strip())
    if match:
        return now - timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        raise argparse.ArgumentTypeError(f"알 수 없는 시각입니다: {value} (예: 30m, 2h, 1d, 2026-10-31T17:00)") from None
    return moment if moment.utcoffset() is not None else moment.astimezone()


def run(args: argparse.Namespace, out: TextIO | None = None) -> int:
    out = sys.stdout if out is None else out
    if not args.audit:
        _err("--audit 또는 GATE_AUDIT 환경변수로 감사 로그 파일을 지정하세요")
        return 1
    log = Path(args.audit)
    digest = None
    if args.file:
        try:
            digest = file_hmac(args.file, log.parent / KEY_FILE_NAME)
        except (OSError, ValueError) as exc:
            _err(f"--file을 비교할 수 없습니다: {exc}")
            return 1
    try:
        selection = select(log, last=args.last, blocked=args.blocked, decisions=args.decision, path=args.path,
                           since=args.since, hmac_digest=digest)
    except FileNotFoundError:
        _err(f"감사 로그가 없습니다: {log} (데몬을 --audit 또는 GATE_AUDIT로 띄우면 남깁니다)")
        return 1
    except OSError as exc:
        _err(f"감사 로그를 읽을 수 없습니다: {exc}")
        return 1
    if selection.broken:
        shown = ", ".join(map(str, selection.broken[:5])) + (" …" if len(selection.broken) > 5 else "")
        _err(f"감사 로그의 깨진 줄 {len(selection.broken)}개를 건너뛰었습니다 (줄 {shown})")
    if args.json:
        for entry in selection.entries:
            print(entry.raw, file=out)
        return 0
    for entry in selection.entries:
        print(format_record(entry.record), file=out)
    print(summary(selection), file=out)
    return 0


def file_hmac(file: str | os.PathLike, key_path: str | os.PathLike) -> str:
    """파일 내용의 content_hmac. 키는 읽기만 한다(없으면 FileNotFoundError)."""
    key = load_key(key_path)
    sha = hashlib.sha256()
    with open(file, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            sha.update(chunk)
    return content_hmac(key, sha.hexdigest())


def select(log: str | os.PathLike, *, last: int = DEFAULT_LAST, blocked: bool = False,
           decisions: list[str] | None = None, path: str | None = None, since: datetime | None = None,
           hmac_digest: str | None = None) -> Selection:
    """조건에 맞는 기록을 고른다. last가 0이면 전부. 로그를 열 수 없으면 OSError."""
    kept: deque[Entry] = deque(maxlen=last or None)
    matched: Counter = Counter()
    broken: list[int] = []
    total = 0
    needle = _fold(path) if path else None
    for item in _entries(log):
        if isinstance(item, int):
            broken.append(item)
            continue
        total += 1
        r = item.record
        if blocked and not is_blocked(r):
            continue
        if decisions and r.get("decision") not in decisions:
            continue
        if needle is not None and (not isinstance(r.get("path"), str) or needle not in _fold(r["path"])):
            continue
        if since is not None and ((moment := _moment(r)) is None or moment < since):
            continue
        if hmac_digest is not None and r.get("content_hmac") != hmac_digest:
            continue
        matched[r.get("decision")] += 1
        kept.append(item)
    return Selection(list(kept), total, matched, broken)


def _entries(log: str | os.PathLike) -> Iterator[Entry | int]:
    """기록을 하나씩 낸다. 깨진 줄은 줄 번호(int)를 낸다."""
    with open(log, "rb") as file:
        for number, raw in enumerate(file, start=1):
            try:
                text = raw.decode("utf-8").rstrip("\n")
                record = json.loads(text)
            except ValueError:  # UnicodeDecodeError, json.JSONDecodeError
                yield number
                continue
            yield Entry(number, text, record) if isinstance(record, dict) else number


def is_blocked(record: dict) -> bool:
    return record.get("decision") in ("deny", "ask_human") or record.get("errno") == "EACCES"


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _moment(record: dict) -> datetime | None:
    ts = record.get("ts")
    if not isinstance(ts, str):
        return None
    try:
        moment = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return moment if moment.utcoffset() is not None else None


def format_record(record: dict) -> str:
    """기록 한 줄: 시각, 연산, 결정, 경로 — 세부."""
    moment = _moment(record)
    when = moment.astimezone().strftime("%Y-%m-%d %H:%M:%S") if moment else "시각 없음".ljust(19)
    op = clean_text(_str(record.get("op"))) or "?"
    decision = record.get("decision")
    label = DECISION_NAMES.get(decision, clean_text(_str(decision)) or "?")
    path = clean_text(_str(record.get("path"))) or "?"
    details = _details(record)
    return f"{when}  {op:<6}  {_pad(label, 9)}  {path}" + (f"  — {' · '.join(details)}" if details else "")


def _details(record: dict) -> list[str]:
    parts = []
    decision = record.get("decision")
    errno = record.get("errno")
    if decision == "error" and isinstance(errno, str):
        parts.append(f"{ERRNO_NAMES.get(errno, '오류')} ({clean_text(errno)})")
    if decision in ("deny", "ask_human") and record.get("reason"):
        parts.append(clean_text(_str(record["reason"])))
    findings = record.get("findings")
    if isinstance(findings, dict) and findings:
        prefix = "가림" if decision in ("allow", "mask") else "찾음"
        parts.append(f"{prefix}: {clean_text(kinds_text(findings))}")
    if isinstance(record.get("jev_label"), str):
        jev = f"판단 모델 {clean_text(record['jev_label'])}"
        if isinstance(record.get("jev_confidence"), (int, float)):
            jev += f" {record['jev_confidence']:.2f}"
        parts.append(jev + (" (캐시)" if record.get("cached") else ""))
    approval = record.get("approval")
    if isinstance(approval, str):
        by = ANSWERED_BY_NAMES.get(record.get("approved_by"))
        parts.append(f"승인: {APPROVAL_NAMES.get(approval, clean_text(approval))}" + (f"({by})" if by else ""))
    process = clean_text(_str(record.get("process"))) or "?"
    pid = record.get("pid")
    parts.append(f"{process} pid {pid}" if isinstance(pid, int) else process)
    return parts


def summary(selection: Selection) -> str:
    matched = sum(selection.matched.values())
    if matched == 0:
        return f"기록 {selection.total}건 중 조건에 맞는 기록이 없습니다."
    text = f"기록 {selection.total}건 중 조건에 맞는 {matched}건"
    if len(selection.entries) < matched:
        text += f", 그중 최근 {len(selection.entries)}건을 보여 줍니다 (-n 0이면 전부)"
    counts = ", ".join(f"{DECISION_NAMES.get(d, clean_text(_str(d)) or '?')} {n}"
                       for d, n in sorted(selection.matched.items(), key=_decision_order))
    return f"{text} · {counts}"


def _decision_order(item: tuple[object, int]) -> int:
    decision = item[0]
    return DECISIONS.index(decision) if decision in DECISIONS else len(DECISIONS)


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _err(text: str) -> None:
    print(f"plant: {text}", file=sys.stderr, flush=True)
