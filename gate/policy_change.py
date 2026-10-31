"""정책 바꾸기의 계산·검사·쓰기. `gate policy`(gate.policy_cli)와 설정 화면(gate ui)이 같은 함수를 쓴다.

이 모듈은 묻지 않는다. plan_*가 바꿈(Change)을 계산하고 검사하면, 묻는 쪽이 보고 정한 뒤 apply로 쓴다.
  - gate policy: 터미널에서 y/N으로 묻는다(gate.policy_cli._Prompt).
  - gate ui: 화면에서 한 번 더 확인받는다. 확인 값은 Change.confirm_token이다(gate.ui_api).

규칙 (두 쪽이 같다)
  - 느슨해짐: gate.policy.loosened(before, after)가 비어 있지 않음. 느슨해지는 바꿈은 한 번 더 확인한 뒤에만 쓴다
    (Change.needs_confirm). 지금 정책 파일이 잘못된 채 되돌리는 것(before가 None)도 확인을 받는다.
  - 되돌리기 기록: 바꾸기 직전의 항목을 정책 파일 옆 기록 폴더(policy.yaml → policy-history/, 폴더 0700)에
    000001.yaml처럼 번호를 늘려 쌓는다(write_entries로 쓰므로 파일 0600). 정책 파일이 없었으면 빈 항목으로 남긴다
    (되돌리면 기본값). 최근 MAX_HISTORY개만 둔다. 쓰기에 실패하면 방금 쌓은 기록을 지운다.
  - undo는 가장 최근 기록을 정책 파일에 쓰고 그 기록을 지운다. undo 자체는 기록하지 않는다(다시 실행하기는 없다).
  - 정책 파일은 write_entries로 원자적으로 쓴다(임시 파일 → rename, 0600).
  - 뺀 키(gate.policy.is_retired: entities.date·event.action)는 새 항목으로 더하지 못한다. 예전 정책 파일에 있던
    항목은 그대로 두고(지우기·되돌리기는 된다) 효과만 없다.
"""

import hashlib
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from gate.ner_client import RETIRED_ENTITY_NAMES, RULE_GROUPS
from gate.policy import (
    Entry,
    PathRule,
    Policy,
    PolicyError,
    build_policy,
    default_settings,
    dump_entries,
    is_retired,
    loosened,
    read_entries,
    setting_keys,
    validate_setting,
    write_entries,
)

POLICY_ENV = "GATE_POLICY"
MAX_HISTORY = 20
_HISTORY_NAME = re.compile(r"^(\d{6,})\.yaml$")

ChangeAction = Literal["add", "set", "remove", "undo"]
# 저장 전에 알려 줄 점. 글은 보여 주는 쪽(gate policy, gate ui)이 따로 쓴다.
#   context_mock: 맥락 규칙인데 지금 Jev가 Mock이다(Mock은 맥락 규칙을 판단에 쓰지 않는다)
#   ner_off:      NER 묶음 설정인데 지금 NER이 꺼져 있다(효과 없음). 규칙이 찾는 host 묶음은 NER을 꺼도 적용되어 빼고 본다
#   no_change:    지금 정책과 같은 값이라 동작이 바뀌지 않는다
Note = Literal["context_mock", "ner_off", "no_change"]


class ChangeError(Exception):
    """바꿀 수 없다(잘못된 입력, 정책 파일·기록 문제, 쓰기 실패). 메시지는 사용자에게 그대로 보여 준다.

    problem: undo에서 지금 정책 파일도 잘못되어 있었으면 그 설명(gate policy가 먼저 보여 준다).
    """

    def __init__(self, message: str, *, problem: str | None = None) -> None:
        super().__init__(message)
        self.problem = problem


def now() -> str:
    """항목에 남기는 시각(ISO 8601, 이 컴퓨터 시간대, 초 단위). 테스트가 바꿔 끼운다."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stamped(entry: Entry) -> Entry:
    """같은 항목에 지금 시각(at)을 붙인다. 문장 해석 결과를 저장하기 직전에 쓴다(gate policy add와 화면이 같다)."""
    return Entry(entry.settings, entry.context, entry.path_rule, entry.said, now())


def read_current(path: Path) -> tuple[Entry, ...]:
    """지금 정책 파일의 항목. 파일이 없으면 빈 묶음(기본값), 잘못되었으면 ChangeError."""
    try:
        return read_entries(path)
    except PolicyError as exc:
        raise ChangeError(f"정책 파일에 문제가 있어 읽을 수 없습니다: {exc}\n"
                          f"  파일: {path}\n"
                          "  게이트 데몬은 이 파일이 고쳐질 때까지 내용 읽기를 모두 차단합니다. "
                          "`gate policy undo`로 이전 상태를 되살리거나, 파일을 지우면 기본값으로 돌아갑니다.") from exc


# --- 되돌리기 기록 ---

def history_dir(path: Path) -> Path:
    return path.parent / f"{path.stem}-history"


def history(path: Path) -> list[Path]:
    """기록 파일, 오래된 것 → 최근 것."""
    folder = history_dir(path)
    if not folder.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in folder.iterdir() if (m := _HISTORY_NAME.match(p.name)) and p.is_file()]
    return [p for _, p in sorted(found)]


def push_history(path: Path, entries: Sequence[Entry]) -> Path:
    folder = history_dir(path)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    existing = history(path)
    number = int(_HISTORY_NAME.match(existing[-1].name).group(1)) + 1 if existing else 1
    snapshot = folder / f"{number:06d}.yaml"
    write_entries(snapshot, entries)
    for old in history(path)[:-MAX_HISTORY]:
        old.unlink(missing_ok=True)
    return snapshot


# --- 바꿈 ---

@dataclass(frozen=True)
class Change:
    """계산하고 검사한 바꿈 하나. apply로 쓰기 전까지 파일은 그대로다."""

    action: ChangeAction
    path: Path
    before: tuple[Entry, ...] | None   # None: 지금 정책 파일이 잘못됨(undo만)
    after: tuple[Entry, ...]
    before_policy: Policy | None
    after_policy: Policy
    loosening: tuple[str, ...]         # gate.policy.loosened 설명. before가 None이면 빈 묶음
    entry: Entry | None = None         # add·set: 더하는 항목, remove: 지우는 항목
    snapshot: Path | None = None       # undo: 되돌릴 기록 파일
    history_left: int = 0              # undo: 되돌린 뒤 남는 기록 수
    problem: str | None = None         # undo: before가 None일 때 지금 정책 파일의 문제

    @property
    def needs_confirm(self) -> bool:
        """한 번 더 확인해야 쓰는 바꿈인가. 느슨해지거나, 잘못된 정책 파일을 기록으로 덮는 경우다."""
        return self.before is None or bool(self.loosening)

    @property
    def confirm_token(self) -> str:
        """화면의 확인 값. 같은 파일 상태에서 같은 결과로 가는 바꿈이면 같다(새 항목의 시각은 넣지 않는다).

        확인을 받는 사이에 정책 파일이 바뀌면 값이 달라져서, 화면은 바뀐 내용으로 다시 확인받는다.
        """
        payload = json.dumps([
            self.action, str(self.path),
            None if self.before is None else dump_entries(self.before),
            self.after_policy.version, list(self.loosening),
        ], ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def notes(self) -> tuple[Note, ...]:
        """add·set에서 저장 전에 알려 줄 점(Note)."""
        out: list[Note] = []
        entry = self.entry
        if self.action in ("add", "set") and entry is not None:
            if entry.context is not None and self.after_policy.jev_backend == "mock":
                out.append("context_mock")
            if entry.settings and any(needs_ner(key) for key in entry.settings) \
                    and self.after_policy.ner_backend == "none":
                out.append("ner_off")
            if self.before_policy is not None and self.after_policy.version == self.before_policy.version:
                out.append("no_change")
        return tuple(out)


def needs_ner(key: str) -> bool:
    """NER이 켜져 있어야 효과가 있는 설정인가. 규칙이 찾는 host 묶음과 뺀 키는 아니다."""
    if not key.startswith("entities.") or is_retired(key):
        return False
    return key.split(".")[1] not in RULE_GROUPS


def _build(entries: Sequence[Entry]) -> Policy:
    try:
        return build_policy(entries)
    except PolicyError as exc:
        raise ChangeError(f"정책을 만들 수 없습니다: {exc}") from exc


def plan_add(path: Path, entries: Sequence[Entry], entry: Entry, action: ChangeAction = "add") -> Change:
    """entries 뒤에 entry를 더하는 바꿈. 문장으로 더하기(add)와 바로 지정(set)이 같이 쓴다.
    entry에 뺀 키가 있으면 ChangeError(새로 저장하지 않는다)."""
    retired = [key for key in (entry.settings or {}) if is_retired(key)]
    if retired:
        raise ChangeError(retired_message(retired[0]))
    before = tuple(entries)
    after = (*before, entry)
    before_policy, after_policy = build_policy(before), _build(after)
    return Change(action, path, before, after, before_policy, after_policy,
                  tuple(loosened(before_policy, after_policy)), entry)


def setting_entry(key: str, values: Sequence[str]) -> Entry:
    """`gate policy set <키> <값>`·`set path <패턴> <동작>`의 항목. 숫자 설정은 기본값과 같은 형으로 바꾼 뒤 검사한다."""
    if key == "path":
        if len(values) != 2:
            raise ChangeError("경로 규칙은 `gate policy set path <패턴> <allow|ask_human|deny>` 모양으로 씁니다.")
        try:
            return Entry(path_rule=PathRule(values[0], values[1]), at=now())
        except PolicyError as exc:
            raise ChangeError(str(exc)) from exc
    if is_retired(key):
        raise ChangeError(retired_message(key))
    if key not in setting_keys():
        raise ChangeError(f"알 수 없는 설정 키: {key}\n  쓸 수 있는 키: {', '.join(setting_keys())}, path")
    if len(values) != 1:
        raise ChangeError(f"값은 하나만 씁니다: gate policy set {key} <값>")
    return Entry(settings={key: parse_value(key, values[0])}, at=now())


def retired_message(key: str) -> str:
    """뺀 키를 지정하려 할 때의 안내."""
    name = RETIRED_ENTITY_NAMES[key.split(".")[1]]
    return (f"{key}는 지정할 수 없습니다. 지금은 {name}을(를) 따로 찾지 않습니다(이름·주소를 찾는 NER 모델을 바꾸면서 뺐습니다).\n"
            "  예전 정책 파일에 있는 항목은 그대로 읽히지만 효과가 없습니다(`gate policy remove <번호>`로 지울 수 있습니다).")


def parse_value(key: str, raw: str) -> object:
    """문자열 값을 그 설정의 기본값과 같은 형(float, int, str)으로 바꾼 뒤 validate_setting으로 검사한다."""
    kind = type(default_settings()[key])
    try:
        value = kind(raw) if kind in (int, float) else raw
    except ValueError as exc:
        raise ChangeError(f"{key}에는 {'정수' if kind is int else '숫자'}를 씁니다: {raw!r}") from exc
    try:
        return validate_setting(key, value)
    except PolicyError as exc:
        raise ChangeError(str(exc)) from exc


def plan_remove(path: Path, entries: Sequence[Entry], number: str) -> Change:
    """show의 번호(1부터)로 항목 하나를 지우는 바꿈."""
    entries = tuple(entries)
    if not number.isdecimal() or not 1 <= int(number) <= len(entries):
        where = f"1~{len(entries)}" if entries else "지정한 항목이 없음"
        raise ChangeError(f"없는 번호입니다: {number} ({where}). `gate policy show`로 번호를 확인하세요.")
    index = int(number) - 1
    after = entries[:index] + entries[index + 1:]
    before_policy, after_policy = build_policy(entries), _build(after)
    return Change("remove", path, entries, after, before_policy, after_policy,
                  tuple(loosened(before_policy, after_policy)), entries[index])


def plan_undo(path: Path) -> Change:
    """직전 변경을 되돌리는 바꿈. 정책 파일이 잘못되어 있어도 된다(그때 before는 None이고 확인을 받는다)."""
    found = history(path)
    if not found:
        read_current(path)  # 정책 파일이 잘못되었으면 그 안내가 먼저다
        raise ChangeError("되돌릴 기록이 없습니다.")
    problem = None
    try:
        before: tuple[Entry, ...] | None = read_entries(path)
    except PolicyError as exc:
        before, problem = None, str(exc)
    snapshot = found[-1]
    try:
        previous = read_entries(snapshot)
    except PolicyError as exc:
        raise ChangeError(f"되돌리기 기록을 읽을 수 없습니다: {exc}\n  기록 파일: {snapshot}\n"
                          "  이 파일을 지우면 그 전 기록으로 되돌릴 수 있습니다.", problem=problem) from exc
    after_policy = _build(previous)
    if before is None:
        return Change("undo", path, None, previous, None, after_policy, (), None, snapshot, len(found) - 1, problem)
    before_policy = build_policy(before)
    return Change("undo", path, before, previous, before_policy, after_policy,
                  tuple(loosened(before_policy, after_policy)), None, snapshot, len(found) - 1)


def apply(change: Change) -> Policy:
    """바꿈을 쓴다. undo가 아니면 바꾸기 전 항목을 기록에 쌓고 정책 파일을 쓴다(실패하면 쌓은 기록을 지운다).
    undo는 기록의 항목을 정책 파일에 쓰고 그 기록을 지운다. 쓰기 실패는 ChangeError다."""
    try:
        if change.action == "undo":
            policy = write_entries(change.path, change.after)
            change.snapshot.unlink(missing_ok=True)
            return policy
        snapshot = push_history(change.path, change.before)
        try:
            return write_entries(change.path, change.after)
        except BaseException:
            snapshot.unlink(missing_ok=True)
            raise
    except OSError as exc:
        raise ChangeError(f"정책 파일을 쓸 수 없습니다: {exc.strerror or type(exc).__name__}") from exc
