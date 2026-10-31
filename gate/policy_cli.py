"""`gate policy` 명령: 사용자 정책 보기와 바꾸기.

정책은 사람(사용자)만 자기 터미널에서 바꾼다. 이 명령은 데몬 소켓을 거치지 않고 정책 파일을 직접 쓴다(gate.policy.write_entries).
실행 중인 데몬은 다음 요청에서 파일이 바뀐 것을 보고 다시 읽는다(gate.policy.PolicySource). 재시작은 필요 없다.

정책 파일 경로: --policy 또는 GATE_POLICY. 둘 다 없으면 안내하고 exit 1.

명령
  show                      내가 지정한 항목(번호 붙임)과 지금 적용 중인 정책(기본값·지정값 구분)
  add "문장"                 해석(gate.policy_text) → "이렇게 이해했어요" → y/N → 저장
  remove <번호>              show의 번호로 항목을 지운다
  undo                      직전 변경을 되돌린다. 여러 번 할 수 있다
  set <키> <값>              해석 없이 바로 지정한다. 숫자 설정은 문자열에서 바꾼 뒤 validate_setting으로 검사한다
  set path <패턴> <동작>      경로 규칙(allow | ask_human | deny)

확인 규칙 (느슨해짐 = gate.policy.loosened(before, after)가 비어 있지 않음)
  | 명령   | 같거나 엄격해짐            | 느슨해짐                                        |
  |--------|---------------------------|-------------------------------------------------|
  | add    | "이렇게 이해했어요" y/N 1번 | "이렇게 이해했어요" y/N + 느슨해지는 점 y/N (2번) |
  | set    | 확인 없이 저장              | 느슨해지는 점 y/N                                |
  | remove | 확인 없이 저장              | 느슨해지는 점 y/N                                |
  | undo   | 확인 없이 저장              | 느슨해지는 점 y/N                                |
  기본 답은 N이고 입력이 끝나면(EOF) N으로 본다. 저장하지 않으면 exit 1.

require_tty
  True이면 정책을 바꾸는 명령(add, remove, undo, set)은 stdin이 터미널일 때만 받는다. 에이전트의 Bash 실행처럼 터미널이
  없는 곳에서 `y`를 흘려 넣는 것을 한 겹 더 막을 뿐 **보안 경계가 아니다**(script 등으로 가짜 터미널을 만들 수 있다).
  진짜 경계는 정책 파일과 기록 폴더 쓰기를 샌드박스로 막는 것이다(Phase 6). show는 터미널이 없어도 된다.

되돌리기 기록
  바꾸기 직전의 항목을 정책 파일 옆 기록 폴더(policy.yaml → policy-history/, 폴더 0700)에 000001.yaml처럼 번호를 늘려
  쌓는다(write_entries로 쓰므로 파일 0600). 정책 파일이 없었으면 빈 항목으로 남긴다(되돌리면 기본값). 최근 MAX_HISTORY개만 둔다.
  undo는 가장 최근 기록을 정책 파일에 쓰고 그 기록을 지운다. undo 자체는 기록하지 않는다(다시 실행하기는 없다).

출력은 모두 stdout으로 낸다(run이 stdout만 받는다).
"""

import argparse
import os
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import TextIO

from gate.detectors import KINDS
from gate.ner_client import ENTITY_GROUPS, ENTITY_NAMES
from gate.policy import (
    ACTION_NAMES,
    DEFAULT_PATH_RULES,
    KIND_NAMES,
    SETTING_NAMES,
    VISIBLE_NAMES,
    Entry,
    PathRule,
    Policy,
    PolicyError,
    build_policy,
    default_settings,
    describe_setting,
    loosened,
    read_entries,
    setting_keys,
    validate_setting,
    write_entries,
)
from gate.policy_text import InterpretError, PolicyInterpreter, interpret, make_interpreter

POLICY_ENV = "GATE_POLICY"
MAX_HISTORY = 20
CHANGE_COMMANDS = ("add", "remove", "undo", "set")
_YES = frozenset({"y", "yes", "예", "네", "ㅛ"})  # ㅛ: 한글 자판 상태에서 y를 누른 경우
_HISTORY_NAME = re.compile(r"^(\d{6,})\.yaml$")
_OTHER_KEYS = ("jev_backend", "jev_timeout_seconds", "max_inspect_bytes", "ner_backend", "ner_timeout_seconds")


class _Stop(Exception):
    """명령을 멈추고 exit 1. 메시지는 사용자에게 보여 준다."""


def register(subparsers) -> argparse.ArgumentParser:
    """gate 명령의 subparsers에 policy 하위 명령을 붙인다. --policy는 policy 바로 뒤나 하위 명령 뒤 어디에 써도 된다."""
    help_text = f"정책 파일 (기본: ${POLICY_ENV})"
    parser = subparsers.add_parser("policy", help="사용자 정책 보기·바꾸기 (사람만 터미널에서)")
    parser.add_argument("--policy", dest="policy_path", default=None, help=help_text)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--policy", dest="policy_path", default=argparse.SUPPRESS, help=help_text)
    sub = parser.add_subparsers(dest="policy_command", required=True)
    sub.add_parser("show", parents=[common], help="지정한 항목과 지금 적용 중인 정책")
    add = sub.add_parser("add", parents=[common], help="정책 문장 추가 (해석 후 확인)")
    add.add_argument("sentence", nargs="+", help='예: "주민번호는 생년월일까지만 보여줘"')
    remove = sub.add_parser("remove", parents=[common], help="show의 번호로 항목 지우기")
    remove.add_argument("number")
    sub.add_parser("undo", parents=[common], help="직전 변경 되돌리기")
    set_ = sub.add_parser("set", parents=[common], help="설정을 바로 지정 (set <키> <값> 또는 set path <패턴> <동작>)")
    set_.add_argument("key")
    set_.add_argument("values", nargs="+")
    return parser


def run(
    args: argparse.Namespace,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    interpreter: PolicyInterpreter | None = None,
    require_tty: bool = True,
) -> int:
    """policy 하위 명령을 실행한다. 성공 0, 실패·취소 1."""
    stdin = sys.stdin if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    command = args.policy_command
    try:
        path = _policy_path(args)
        if command in CHANGE_COMMANDS and require_tty and not _isatty(stdin):
            raise _Stop("정책을 바꾸는 명령은 사람이 터미널에서 직접 실행해야 합니다(입력이 터미널이 아님). "
                        "보기는 `gate policy show`로 할 수 있습니다.")
        if command == "undo":
            return _undo(path, _Prompt(stdin, out))
        entries = _read(path)
        if command == "show":
            _show(path, entries, out)
            return 0
        io = _Prompt(stdin, out)
        if command == "add":
            return _add(path, entries, " ".join(args.sentence), io, interpreter)
        if command == "remove":
            return _remove(path, entries, args.number, io)
        return _set(path, entries, args.key, args.values, io)
    except _Stop as exc:
        print(f"gate policy: {exc}", file=out)
        return 1


# --- 공통 ---

def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def _policy_path(args: argparse.Namespace) -> Path:
    raw = getattr(args, "policy_path", None) or os.environ.get(POLICY_ENV)
    if not raw:
        raise _Stop(f"정책 파일 경로를 --policy 또는 {POLICY_ENV} 환경변수로 지정하세요 "
                    "(예: --policy <hackathon 폴더>/gate-data/policy.yaml).")
    return Path(raw).expanduser().absolute()


def _read(path: Path) -> tuple[Entry, ...]:
    try:
        return read_entries(path)
    except PolicyError as exc:
        raise _Stop(f"정책 파일에 문제가 있어 읽을 수 없습니다: {exc}\n"
                    f"  파일: {path}\n"
                    "  게이트 데몬은 이 파일이 고쳐질 때까지 내용 읽기를 모두 차단합니다. "
                    "`gate policy undo`로 이전 상태를 되살리거나, 파일을 지우면 기본값으로 돌아갑니다.") from exc


class _Prompt:
    def __init__(self, stdin: TextIO, out: TextIO) -> None:
        self.stdin = stdin
        self.out = out

    def say(self, text: str = "") -> None:
        print(text, file=self.out)

    def confirm(self, question: str) -> bool:
        """기본 N. 입력이 끝나면(EOF) N."""
        self.out.write(f"{question} [y/N] ")
        self.out.flush()
        line = self.stdin.readline()
        if not line:
            self.out.write("\n")
            return False
        return line.strip().lower() in _YES


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _commit(path: Path, before: Sequence[Entry], after: Sequence[Entry], io: _Prompt) -> Policy | None:
    """after로 바꾼다. 느슨해지면 한 번 더 묻는다. 저장하기 전에 before를 기록에 쌓는다. 취소하면 None."""
    try:
        new_policy = build_policy(after)
    except PolicyError as exc:
        raise _Stop(f"정책을 만들 수 없습니다: {exc}") from exc
    if not _confirm_loosening(build_policy(before), new_policy, io):
        return None
    snapshot = _push_history(path, before)
    try:
        policy = write_entries(path, after)
    except BaseException:
        snapshot.unlink(missing_ok=True)
        raise
    _saved(path, policy, io)
    return policy


def _confirm_loosening(before: Policy, after: Policy, io: _Prompt) -> bool:
    changes = loosened(before, after)
    if not changes:
        return True
    io.say("이 변경은 정책을 느슨하게 합니다(더 많이 보여 주게 됩니다):")
    for line in changes:
        io.say(f"  - {line}")
    if io.confirm("정말 느슨하게 바꿀까요?"):
        return True
    io.say("취소했습니다. 정책은 바뀌지 않았습니다.")
    return False


def _saved(path: Path, policy: Policy, io: _Prompt) -> None:
    io.say(f"저장했습니다: {path}")
    io.say("실행 중인 게이트 데몬은 다음 요청부터 바뀐 정책을 씁니다(다시 시작하지 않아도 됩니다).")
    io.say(f"되돌리려면 `gate policy undo`. 정책 버전 {policy.version}")


def _write_failed(exc: OSError) -> _Stop:
    return _Stop(f"정책 파일을 쓸 수 없습니다: {exc.strerror or type(exc).__name__}")


# --- 되돌리기 기록 ---

def history_dir(path: Path) -> Path:
    return path.parent / f"{path.stem}-history"


def _history(path: Path) -> list[Path]:
    """기록 파일, 오래된 것 → 최근 것."""
    folder = history_dir(path)
    if not folder.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in folder.iterdir() if (m := _HISTORY_NAME.match(p.name)) and p.is_file()]
    return [p for _, p in sorted(found)]


def _push_history(path: Path, entries: Sequence[Entry]) -> Path:
    folder = history_dir(path)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    existing = _history(path)
    number = int(_HISTORY_NAME.match(existing[-1].name).group(1)) + 1 if existing else 1
    snapshot = folder / f"{number:06d}.yaml"
    write_entries(snapshot, entries)
    for old in _history(path)[:-MAX_HISTORY]:
        old.unlink(missing_ok=True)
    return snapshot


# --- 명령 ---

def _add(path: Path, entries: tuple[Entry, ...], sentence: str, io: _Prompt,
         interpreter: PolicyInterpreter | None) -> int:
    if interpreter is None:
        try:
            interpreter = make_interpreter()
        except InterpretError as exc:
            raise _Stop(f"문장 해석기를 준비하지 못했습니다: {exc}") from exc
    result = interpret(sentence, interpreter)
    io.say(result.message)
    if result.entry is None:
        return 1
    entry = Entry(result.entry.settings, result.entry.context, result.entry.path_rule, result.entry.said, _now())
    after = (*entries, entry)
    before_policy, after_policy = build_policy(entries), build_policy(after)
    if entry.context is not None and after_policy.jev_backend == "mock":
        io.say("  주의: 지금 Jev는 Mock(키워드 규칙)이라 맥락 규칙을 판단에 쓰지 않습니다. 실제 Jev를 붙여야 효과가 있습니다.")
    if after_policy.version == before_policy.version:
        io.say("  (지금 정책과 같은 값이라 동작은 바뀌지 않습니다)")
    if not io.confirm("이대로 저장할까요?"):
        io.say("저장하지 않았습니다.")
        return 1
    try:
        return 0 if _commit(path, entries, after, io) else 1
    except OSError as exc:
        raise _write_failed(exc) from exc


def _remove(path: Path, entries: tuple[Entry, ...], number: str, io: _Prompt) -> int:
    if not number.isdecimal() or not 1 <= int(number) <= len(entries):
        where = f"1~{len(entries)}" if entries else "지정한 항목이 없음"
        raise _Stop(f"없는 번호입니다: {number} ({where}). `gate policy show`로 번호를 확인하세요.")
    index = int(number) - 1
    removed = entries[index]
    after = entries[:index] + entries[index + 1:]
    io.say(f"{number}번 항목을 지웁니다: {_entry_title(removed)} → {_describe_entry(removed)}")
    try:
        return 0 if _commit(path, entries, after, io) else 1
    except OSError as exc:
        raise _write_failed(exc) from exc


def _undo(path: Path, io: _Prompt) -> int:
    """정책 파일이 잘못되어 있어도 되돌릴 수 있다. 그동안 데몬은 모두 차단하므로 되돌리는 것은 느슨해지는 변경으로 본다."""
    history = _history(path)
    if not history:
        _read(path)  # 정책 파일이 잘못되었으면 그 안내가 먼저다
        raise _Stop("되돌릴 기록이 없습니다.")
    try:
        entries: tuple[Entry, ...] | None = read_entries(path)
    except PolicyError as exc:
        entries = None
        io.say(f"지금 정책 파일에 문제가 있습니다: {exc}")
        io.say("게이트 데몬은 지금 내용 읽기를 모두 차단하고 있습니다. 되돌리면 잘못된 파일은 기록의 내용으로 바뀝니다.")
    snapshot = history[-1]
    try:
        previous = read_entries(snapshot)
    except PolicyError as exc:
        raise _Stop(f"되돌리기 기록을 읽을 수 없습니다: {exc}\n  기록 파일: {snapshot}\n"
                    "  이 파일을 지우면 그 전 기록으로 되돌릴 수 있습니다.") from exc
    if entries is None:
        io.say(f"직전 기록으로 되돌립니다 (지정한 항목 {len(previous)}개).")
        if not io.confirm("되돌릴까요?"):
            io.say("취소했습니다. 정책은 바뀌지 않았습니다.")
            return 1
    else:
        io.say(f"직전 변경을 되돌립니다 (지정한 항목 {len(entries)}개 → {len(previous)}개).")
        if not _confirm_loosening(build_policy(entries), build_policy(previous), io):
            return 1
    try:
        policy = write_entries(path, previous)
    except OSError as exc:
        raise _write_failed(exc) from exc
    snapshot.unlink(missing_ok=True)
    _saved(path, policy, io)
    io.say(f"더 되돌릴 수 있는 변경: {len(history) - 1}개")
    return 0


def _set(path: Path, entries: tuple[Entry, ...], key: str, values: list[str], io: _Prompt) -> int:
    if key == "path":
        if len(values) != 2:
            raise _Stop("경로 규칙은 `gate policy set path <패턴> <allow|ask_human|deny>` 모양으로 씁니다.")
        try:
            entry = Entry(path_rule=PathRule(values[0], values[1]), at=_now())
        except PolicyError as exc:
            raise _Stop(str(exc)) from exc
        io.say(f"지정합니다: {_describe_entry(entry)}")
    else:
        if key not in setting_keys():
            raise _Stop(f"알 수 없는 설정 키: {key}\n  쓸 수 있는 키: {', '.join(setting_keys())}, path")
        if len(values) != 1:
            raise _Stop(f"값은 하나만 씁니다: gate policy set {key} <값>")
        value = _parse_value(key, values[0])
        entry = Entry(settings={key: value}, at=_now())
        io.say(f"지정합니다: {describe_setting(key, entry.settings[key])}")
    try:
        return 0 if _commit(path, entries, (*entries, entry), io) else 1
    except OSError as exc:
        raise _write_failed(exc) from exc


def _parse_value(key: str, raw: str) -> object:
    """문자열 값을 그 설정의 기본값과 같은 형(float, int, str)으로 바꾼 뒤 validate_setting으로 검사한다."""
    kind = type(default_settings()[key])
    try:
        value = kind(raw) if kind in (int, float) else raw
    except ValueError as exc:
        raise _Stop(f"{key}에는 {'정수' if kind is int else '숫자'}를 씁니다: {raw!r}") from exc
    try:
        return validate_setting(key, value)
    except PolicyError as exc:
        raise _Stop(str(exc)) from exc


# --- show ---

def _entry_title(entry: Entry) -> str:
    return f"\"{' '.join(entry.said.split())}\"" if entry.said else "(gate policy set으로 지정)"


def _describe_entry(entry: Entry) -> str:
    if entry.settings is not None:
        return ", ".join(describe_setting(k, v) for k, v in entry.settings.items())
    if entry.context is not None:
        return f"맥락 규칙: {' '.join(entry.context.split())}"
    rule = entry.path_rule
    return f"경로 규칙: {rule.pattern} → {ACTION_NAMES[rule.action]}"


def _show(path: Path, entries: tuple[Entry, ...], out: TextIO) -> None:
    policy = build_policy(entries)
    last_set = {key: i for i, entry in enumerate(entries) for key in (entry.settings or {})}

    def mark(key: str) -> str:
        return "[지정]" if key in last_set else "[기본]"

    lines = [f"정책 파일: {path}" + ("" if path.exists() else " (아직 없음, 전부 기본값)"), ""]
    lines.append("내가 지정한 항목 (지우기: gate policy remove <번호>)")
    if not entries:
        lines.append("  없음. 아래는 전부 기본값입니다.")
    for i, entry in enumerate(entries):
        at = f"  ({entry.at[:16].replace('T', ' ')})" if entry.at else ""
        lines.append(f"  {i + 1}. {_entry_title(entry)}{at}")
        overridden = [k for k in (entry.settings or {}) if last_set[k] != i]
        note = "  (뒤 항목이 덮어써서 지금은 적용되지 않음)" if overridden else ""
        lines.append(f"     → {_describe_entry(entry)}{note}")

    lines += ["", "지금 적용 중인 정책 ([기본] 기본값, [지정] 내가 지정한 값)", "  번호·비밀값 종류별 처리 (정규식이 찾은 것)"]
    for kind in KINDS:
        action_key, visible_key = f"numbers.{kind}.action", f"numbers.{kind}.visible"
        lines.append(f"    {KIND_NAMES[kind]}: {ACTION_NAMES[policy.number_rule(kind)]} {mark(action_key)}"
                     f" · 가릴 때 {VISIBLE_NAMES[policy.visible[kind]]} {mark(visible_key)}")

    ner_on = policy.ner_backend != "none"
    lines.append("  이름·주소·소속 등 종류별 처리 (NER이 찾은 것, `[이름]`처럼 종류 이름으로 가림)"
                 + ("" if ner_on else " — 지금 NER이 꺼져 있어 쓰지 않음"))
    for group in ENTITY_GROUPS:
        key = f"entities.{group}.action"
        lines.append(f"    {ENTITY_NAMES[group]}: {ACTION_NAMES[policy.entity_rule(group)]} {mark(key)}")
    if ner_on:
        lines.append("    가린 것이 있으면 가린 결과를 Jev에 다시 물어, 안전하면 보여주고 아니면 사람에게 물어봄")

    allow_key, deny_key = "thresholds.allow_if_safe_confidence_gte", "thresholds.deny_if_sensitive_confidence_gte"
    timeout_key, session_key = "approval.timeout_seconds", "approval.session_minutes"
    lines += [
        "  Jev 판단 기준",
        f"    Jev가 '안전'이라고 확신도 {policy.allow_threshold:g} 이상으로 답하면 보여줌 {mark(allow_key)}",
        f"    Jev가 '민감'이라고 확신도 {policy.deny_threshold:g} 이상으로 답하면 거부 {mark(deny_key)}",
        "    그 사이는 사람에게 물어봄",
        "  사람 승인 (맥 대화상자 또는 내 터미널의 gate approve / gate deny. 허용해도 번호는 가린 채로 보여줌)",
        f"    답을 기다리는 시간: {policy.approval_timeout_seconds:g}초, 넘으면 거부 {mark(timeout_key)}",
        f"    '이 폴더 허용'을 기억하는 시간: {policy.approval_session_minutes}분 {mark(session_key)}"
        if policy.approval_session_minutes > 0 else
        f"    '이 폴더 허용'을 쓰지 않음 (0분) {mark(session_key)}",
        "  경로 규칙 (내용을 보기 전에 경로로 정함)",
    ]
    for i, rule in enumerate(policy.path_rules):
        origin = "[기본]" if i < len(DEFAULT_PATH_RULES) else "[지정]"
        lines.append(f"    {rule.pattern} → {ACTION_NAMES[rule.action]} {origin}")

    lines.append("  맥락 규칙 (Jev에게 함께 알려 주는 내 문장)")
    if not policy.context_rules:
        lines.append("    없음 [기본]")
    for rule in policy.context_rules:
        lines.append(f"    - {' '.join(rule.split())} [지정]")
    if policy.context_rules and policy.jev_backend == "mock":
        lines.append("    주의: 지금 Jev는 Mock(키워드 규칙)이라 맥락 규칙을 판단에 쓰지 않습니다.")

    lines.append("  기타")
    for key in _OTHER_KEYS:
        name, value = SETTING_NAMES[key], policy.settings[key]
        lines.append(f"    {name}: {value:g} {mark(key)}" if isinstance(value, float)
                     else f"    {name}: {value} {mark(key)}")

    lines += ["", f"정책 버전 {policy.version} · 되돌릴 수 있는 변경 {len(_history(path))}개"]
    print("\n".join(lines), file=out)
