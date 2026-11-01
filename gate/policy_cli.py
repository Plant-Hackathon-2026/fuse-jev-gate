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

계산과 묻기
  바꿈 계산·검사(완화 판단), 되돌리기 기록 쌓기, 0600 쓰기, undo는 gate.policy_change가 한다. 설정 화면(gate ui)도
  같은 함수를 쓴다. 이 모듈은 터미널에서 묻고(_Prompt) 결과를 글로 보여 주기만 한다.

되돌리기 기록 (gate.policy_change)
  바꾸기 직전의 항목을 정책 파일 옆 기록 폴더(policy.yaml → policy-history/, 폴더 0700)에 000001.yaml처럼 번호를 늘려
  쌓는다(write_entries로 쓰므로 파일 0600). 정책 파일이 없었으면 빈 항목으로 남긴다(되돌리면 기본값). 최근 MAX_HISTORY개만 둔다.
  undo는 가장 최근 기록을 정책 파일에 쓰고 그 기록을 지운다. undo 자체는 기록하지 않는다(다시 실행하기는 없다).

출력은 모두 stdout으로 낸다(run이 stdout만 받는다).
"""

import argparse
import os
import sys
from pathlib import Path
from typing import TextIO

from gate.detectors import KINDS
from gate.ner_client import ENTITY_GROUPS, ENTITY_NAMES, RULE_GROUPS
from gate.policy import (
    ACTION_NAMES,
    DEFAULT_PATH_RULES,
    KIND_NAMES,
    SETTING_NAMES,
    VISIBLE_NAMES,
    Entry,
    Policy,
    build_policy,
    describe_setting,
)
from gate.policy_change import (  # noqa: F401  (MAX_HISTORY·history_dir는 이 모듈 이름으로도 쓴다)
    MAX_HISTORY,
    POLICY_ENV,
    Change,
    ChangeError,
    Note,
    apply,
    history,
    history_dir,
    plan_add,
    plan_remove,
    plan_undo,
    read_current,
    setting_entry,
    stamped,
)
from gate.policy_text import InterpretError, PolicyInterpreter, interpret, make_interpreter

CHANGE_COMMANDS = ("add", "remove", "undo", "set")
_YES = frozenset({"y", "yes", "예", "네", "ㅛ"})  # ㅛ: 한글 자판 상태에서 y를 누른 경우
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
            raise _Stop("정책을 바꾸는 명령은 사람이 터미널에서 직접 실행해야 해요(입력이 터미널이 아님). "
                        "보기는 `plant policy show`로 할 수 있어요.")
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
        print(f"plant policy: {exc}", file=out)
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
        return read_current(path)
    except ChangeError as exc:
        raise _Stop(str(exc)) from exc


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


_NOTE_TEXT: dict[Note, str] = {
    "context_mock": "  주의: 지금 판단 모델은 Mock(키워드 규칙)이라 맥락 규칙을 판단에 쓰지 않아요. 실제 판단 모델을 붙여야 효과가 있어요.",
    "ner_off": "  주의: 지금 NER이 꺼져 있어(ner_backend: none) 이 설정은 효과가 없어요. "
               "켜려면: plant policy set ner_backend server",
    "no_change": "  (지금 정책과 같은 값이라 동작은 바뀌지 않아요)",
}


def _commit(change: Change, io: _Prompt) -> int:
    """느슨해지면 한 번 더 묻고, 기록을 쌓은 뒤 쓴다(gate.policy_change.apply). 취소하면 1."""
    if not _confirm_loosening(change, io):
        return 1
    _saved(change.path, _apply(change), io)
    return 0


def _apply(change: Change) -> Policy:
    try:
        return apply(change)
    except ChangeError as exc:
        raise _Stop(str(exc)) from exc


def _confirm_loosening(change: Change, io: _Prompt) -> bool:
    if not change.loosening:
        return True
    io.say("이 변경은 정책을 느슨하게 해요(더 많이 보여 주게 돼요):")
    for line in change.loosening:
        io.say(f"  - {line}")
    if io.confirm("정말 느슨하게 바꿀까요?"):
        return True
    io.say("취소했어요. 정책은 바뀌지 않았어요.")
    return False


def _saved(path: Path, policy: Policy, io: _Prompt) -> None:
    io.say(f"저장했어요: {path}")
    io.say("실행 중인 PLANT 데몬은 다음 요청부터 바뀐 정책을 써요(다시 시작하지 않아도 돼요).")
    io.say(f"되돌리려면 `plant policy undo`. 정책 버전 {policy.version}")


def _plan(make, *args) -> Change:
    try:
        return make(*args)
    except ChangeError as exc:
        raise _Stop(str(exc)) from exc


# --- 명령 ---

def _add(path: Path, entries: tuple[Entry, ...], sentence: str, io: _Prompt,
         interpreter: PolicyInterpreter | None) -> int:
    if interpreter is None:
        try:
            interpreter = make_interpreter()
        except InterpretError as exc:
            raise _Stop(f"문장 해석기를 준비하지 못했어요: {exc}") from exc
    result = interpret(sentence, interpreter)
    io.say(result.message)
    if result.entry is None:
        return 1
    change = _plan(plan_add, path, entries, stamped(result.entry))
    for note in change.notes():
        io.say(_NOTE_TEXT[note])
    if not io.confirm("이대로 저장할까요?"):
        io.say("저장하지 않았어요.")
        return 1
    return _commit(change, io)


def _remove(path: Path, entries: tuple[Entry, ...], number: str, io: _Prompt) -> int:
    change = _plan(plan_remove, path, entries, number)
    removed = change.entry
    io.say(f"{number}번 항목을 지워요: {_entry_title(removed)} → {_describe_entry(removed)}")
    return _commit(change, io)


def _undo(path: Path, io: _Prompt) -> int:
    """정책 파일이 잘못되어 있어도 되돌릴 수 있다. 그동안 데몬은 모두 차단하므로 되돌리는 것은 느슨해지는 변경으로 본다."""
    try:
        change = plan_undo(path)
    except ChangeError as exc:
        if exc.problem is not None:
            _say_problem(exc.problem, io)
        raise _Stop(str(exc)) from exc
    if change.before is None:
        _say_problem(change.problem, io)
        io.say(f"직전 기록으로 되돌려요 (지정한 항목 {len(change.after)}개).")
        if not io.confirm("되돌릴까요?"):
            io.say("취소했어요. 정책은 바뀌지 않았어요.")
            return 1
    else:
        io.say(f"직전 변경을 되돌려요 (지정한 항목 {len(change.before)}개 → {len(change.after)}개).")
        if not _confirm_loosening(change, io):
            return 1
    _saved(path, _apply(change), io)
    io.say(f"더 되돌릴 수 있는 변경: {change.history_left}개")
    return 0


def _say_problem(problem: str, io: _Prompt) -> None:
    io.say(f"지금 정책 파일에 문제가 있어요: {problem}")
    io.say("PLANT 데몬은 지금 내용 읽기를 모두 차단하고 있어요. 되돌리면 잘못된 파일은 기록의 내용으로 바뀌어요.")


def _set(path: Path, entries: tuple[Entry, ...], key: str, values: list[str], io: _Prompt) -> int:
    try:
        entry = setting_entry(key, values)
    except ChangeError as exc:
        raise _Stop(str(exc)) from exc
    if entry.path_rule is not None:
        io.say(f"지정해요: {_describe_entry(entry)}")
    else:
        io.say(f"지정해요: {describe_setting(key, entry.settings[key])}")
    return _commit(_plan(plan_add, path, entries, entry, "set"), io)


# --- show ---

def _entry_title(entry: Entry) -> str:
    return f"\"{' '.join(entry.said.split())}\"" if entry.said else "(plant policy set으로 지정)"


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
    lines.append("내가 지정한 항목 (지우기: plant policy remove <번호>)")
    if not entries:
        lines.append("  없음. 아래는 전부 기본값이에요.")
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
    lines.append("  이름·주소·소속 등 종류별 처리 (NER·규칙이 찾은 것, `[이름]`처럼 종류 이름으로 가림)"
                 + ("" if ner_on else " — 지금 NER이 꺼져 있어 IP·내부 주소(규칙)만 적용"))
    for group in ENTITY_GROUPS:
        key = f"entities.{group}.action"
        how = " (규칙으로 찾음, NER을 꺼도 적용)" if group in RULE_GROUPS else ""
        lines.append(f"    {ENTITY_NAMES[group]}: {ACTION_NAMES[policy.entity_rule(group)]} {mark(key)}{how}")
    if ner_on:
        lines.append("    가린 것이 있으면 가린 결과를 판단 모델에 다시 물어, 안전하면 보여주고 아니면 사람에게 물어봄")

    allow_key, deny_key = "thresholds.allow_if_safe_confidence_gte", "thresholds.deny_if_sensitive_confidence_gte"
    timeout_key, session_key = "approval.timeout_seconds", "approval.session_minutes"
    lines += [
        "  판단 모델 기준",
        f"    판단 모델이 '안전'이라고 확신도 {policy.allow_threshold:g} 이상으로 답하면 보여줌 {mark(allow_key)}",
        f"    판단 모델이 '민감'이라고 확신도 {policy.deny_threshold:g} 이상으로 답하면 거부 {mark(deny_key)}",
        "    그 사이는 사람에게 물어봄",
        "  사람 승인 (맥 대화상자 또는 내 터미널의 plant approve / plant deny. 허용해도 가린 정보는 가린 채로 보여줌)",
        f"    답을 기다리는 시간: {policy.approval_timeout_seconds:g}초, 넘으면 거부 {mark(timeout_key)}",
        f"    '이 폴더 허용'을 기억하는 시간: {policy.approval_session_minutes}분 {mark(session_key)}"
        if policy.approval_session_minutes > 0 else
        f"    '이 폴더 허용'을 쓰지 않음 (0분) {mark(session_key)}",
        "  경로 규칙 (내용을 보기 전에 경로로 정함)",
    ]
    for i, rule in enumerate(policy.path_rules):
        origin = "[기본]" if i < len(DEFAULT_PATH_RULES) else "[지정]"
        lines.append(f"    {rule.pattern} → {ACTION_NAMES[rule.action]} {origin}")

    lines.append("  맥락 규칙 (판단 모델에게 함께 알려 주는 내 문장)")
    if not policy.context_rules:
        lines.append("    없음 [기본]")
    for rule in policy.context_rules:
        lines.append(f"    - {' '.join(rule.split())} [지정]")
    if policy.context_rules and policy.jev_backend == "mock":
        lines.append("    주의: 지금 판단 모델은 Mock(키워드 규칙)이라 맥락 규칙을 판단에 쓰지 않아요.")

    lines.append("  기타")
    for key in _OTHER_KEYS:
        name, value = SETTING_NAMES[key], policy.settings[key]
        lines.append(f"    {name}: {value:g} {mark(key)}" if isinstance(value, float)
                     else f"    {name}: {value} {mark(key)}")

    lines += ["", f"정책 버전 {policy.version} · 되돌릴 수 있는 변경 {len(history(path))}개"]
    print("\n".join(lines), file=out)
