"""gate.policy_change: 정책 바꾸기의 계산·검사·쓰기(gate policy와 gate ui가 같이 쓴다)."""

import os
import stat

import pytest

from gate import policy_change as pc
from gate.policy import Entry, read_entries, write_entries
from tests.test_policy_cli import gate_policy


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    monkeypatch.delenv("GATE_POLICY", raising=False)
    return tmp_path / "gate-data" / "policy.yaml"


def test_plan_does_not_write_and_apply_keeps_history(policy_file):
    entry = pc.setting_entry("numbers.card.action", ["deny"])
    change = pc.plan_add(policy_file, (), entry, "set")
    assert not policy_file.exists() and not change.needs_confirm and change.loosening == ()
    policy = pc.apply(change)
    assert policy.number_rule("card") == "deny"
    assert stat.S_IMODE(os.stat(policy_file).st_mode) == 0o600
    [snapshot] = pc.history(policy_file)
    assert read_entries(snapshot) == ()  # 파일이 없던 상태(기본값)를 남긴다
    assert stat.S_IMODE(os.stat(pc.history_dir(policy_file)).st_mode) == 0o700


def test_loosening_needs_confirm_and_token_is_stable_across_new_entry_times(policy_file, monkeypatch):
    times = iter(["2026-10-31T10:00:00+09:00", "2026-10-31T10:05:00+09:00"])
    monkeypatch.setattr(pc, "now", lambda: next(times))
    first = pc.plan_add(policy_file, (), pc.setting_entry("numbers.rrn.visible", ["birth"]), "set")
    second = pc.plan_add(policy_file, (), pc.setting_entry("numbers.rrn.visible", ["birth"]), "set")
    assert first.needs_confirm and first.loosening
    assert first.after[0].at != second.after[0].at
    assert first.confirm_token == second.confirm_token  # 같은 파일 상태 → 같은 결과면 같은 확인 값


def test_token_changes_when_the_file_changes_in_between(policy_file):
    entry = pc.setting_entry("numbers.rrn.visible", ["birth"])
    before = pc.plan_add(policy_file, pc.read_current(policy_file), entry, "set")
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"})])
    after = pc.plan_add(policy_file, pc.read_current(policy_file), entry, "set")
    assert before.confirm_token != after.confirm_token


def test_notes(policy_file):
    off = (Entry(settings={"ner_backend": "none"}),)
    ner = pc.plan_add(policy_file, off, Entry(settings={"entities.name.action": "deny"}, said="이름 있는 파일은 거부"))
    assert ner.notes() == ("ner_off",)
    on = pc.plan_add(policy_file, (), Entry(settings={"entities.name.action": "deny"}))
    assert on.notes() == ()  # 기본은 NER 켜짐이라 안내하지 않는다
    context = pc.plan_add(policy_file, (), Entry(context="병원 기록은 보여주지 마", said="병원 기록은 보여주지 마"))
    assert context.notes() == ("context_mock",)
    same = pc.plan_add(policy_file, (), Entry(settings={"numbers.card.action": "mask"}))
    assert same.notes() == ("no_change",)


def test_remove_and_undo_plans(policy_file):
    write_entries(policy_file, [Entry(settings={"numbers.card.action": "deny"})])
    with pytest.raises(pc.ChangeError, match="없는 번호"):
        pc.plan_remove(policy_file, pc.read_current(policy_file), "2")
    change = pc.plan_remove(policy_file, pc.read_current(policy_file), "1")
    assert change.entry == Entry(settings={"numbers.card.action": "deny"}) and change.needs_confirm
    pc.apply(change)
    undo = pc.plan_undo(policy_file)
    assert undo.history_left == 0 and not undo.needs_confirm  # 지운 것을 되살리면 엄격해진다
    pc.apply(undo)
    assert read_entries(policy_file) == (Entry(settings={"numbers.card.action": "deny"}),)
    with pytest.raises(pc.ChangeError, match="되돌릴 기록이 없습니다"):
        pc.plan_undo(policy_file)


def test_undo_over_invalid_file_needs_confirm(policy_file):
    pc.apply(pc.plan_add(policy_file, (), pc.setting_entry("numbers.card.action", ["deny"]), "set"))
    policy_file.write_text("version: 1\nentries: [\n", encoding="utf-8")
    change = pc.plan_undo(policy_file)
    assert change.before is None and change.problem and change.needs_confirm


def test_cli_shows_file_problem_before_broken_snapshot_error(policy_file):
    """정책 파일과 기록이 둘 다 잘못되면, 나누기 전처럼 파일 문제를 먼저 알리고 기록 오류로 멈춘다."""
    pc.apply(pc.plan_add(policy_file, (), pc.setting_entry("numbers.card.action", ["deny"]), "set"))
    policy_file.write_text("version: 1\nentries: [\n", encoding="utf-8")
    pc.history(policy_file)[-1].write_text("version: 9\n", encoding="utf-8")
    code, out = gate_policy(policy_file, "undo", answers="y\n")
    assert code == 1
    assert out.index("지금 정책 파일에 문제가 있습니다") < out.index("되돌리기 기록을 읽을 수 없습니다")


def test_write_failure_is_change_error_and_leaves_no_history(policy_file, monkeypatch):
    change = pc.plan_add(policy_file, (), pc.setting_entry("numbers.card.action", ["deny"]), "set")

    def broken(path, entries):
        if path == policy_file:
            raise PermissionError(13, "Permission denied")
        return original(path, entries)

    original = pc.write_entries
    monkeypatch.setattr(pc, "write_entries", broken)
    with pytest.raises(pc.ChangeError, match="정책 파일을 쓸 수 없습니다"):
        pc.apply(change)
    assert pc.history(policy_file) == []


# --- 뺀 묶음과 규칙 묶음 (3e, 2026-11-01 NER 교체) ---

@pytest.mark.parametrize("key", ["entities.date.action", "entities.event.action"])
def test_retired_keys_cannot_be_added(policy_file, key):
    with pytest.raises(pc.ChangeError, match="지정할 수 없습니다"):
        pc.setting_entry(key, ["mask"])
    with pytest.raises(pc.ChangeError, match="따로 찾지 않습니다"):
        pc.plan_add(policy_file, (), Entry(settings={key: "mask"}))   # 코드가 만든 항목도 새로 더하지 않는다
    assert not policy_file.exists()
    assert "2026" not in pc.retired_message(key)   # 터미널 안내에도 내부 날짜는 넣지 않는다


def test_old_entries_with_retired_keys_stay_and_can_be_removed(policy_file):
    old = Entry.from_dict({"set": {"entities.date.action": "deny"}, "said": "날짜는 거부"})
    write_entries(policy_file, [old])
    change = pc.plan_add(policy_file, pc.read_current(policy_file), pc.setting_entry("numbers.card.action", ["deny"]))
    pc.apply(change)
    assert [dict(e.settings) for e in read_entries(policy_file)] == [{"entities.date.action": "deny"},
                                                                     {"numbers.card.action": "deny"}]
    pc.apply(pc.plan_remove(policy_file, read_entries(policy_file), "1"))
    assert [dict(e.settings) for e in read_entries(policy_file)] == [{"numbers.card.action": "deny"}]


def test_host_setting_has_no_ner_off_note(policy_file):
    write_entries(policy_file, [Entry(settings={"ner_backend": "none"})])
    entries = pc.read_current(policy_file)
    host = pc.plan_add(policy_file, entries, Entry(settings={"entities.host.action": "deny"}))
    assert host.notes() == ()                                   # IP·내부 주소는 규칙이라 NER을 꺼도 적용
    name = pc.plan_add(policy_file, entries, Entry(settings={"entities.name.action": "deny"}))
    assert name.notes() == ("ner_off",)
    assert pc.needs_ner("entities.name.action") and not pc.needs_ner("entities.host.action")
    assert not pc.needs_ner("entities.date.action") and not pc.needs_ner("numbers.card.action")


def test_turning_ner_off_is_loosening_and_needs_confirm(policy_file):
    change = pc.plan_add(policy_file, (), pc.setting_entry("ner_backend", ["none"]), "set")
    assert change.needs_confirm
    assert change.loosening == ("NER(이름·주소·소속 가리기) 백엔드: server → NER(이름·주소·소속 가리기) 백엔드: none",)
    back_on = pc.plan_add(policy_file, (Entry(settings={"ner_backend": "none"}),),
                          pc.setting_entry("ner_backend", ["server"]), "set")
    assert not back_on.needs_confirm
