"""gate.policy: 기본값, 정책 파일, 판단 규칙, 완화 확인."""

import os
import stat
import threading
import unicodedata
from pathlib import Path

import pytest
import yaml

from gate.detectors import KINDS, VISIBLE_CHOICES, Finding
from gate.jev_client import JevDecision
from gate.policy import (
    DEFAULT_PATH_RULES,
    DEFAULT_POLICY,
    MAX_POLICY_FILE_BYTES,
    SETTING_NAMES,
    Entry,
    PathRule,
    PolicyError,
    PolicySource,
    build_policy,
    default_settings,
    describe_setting,
    jev_action,
    load_policy,
    loosened,
    match_path,
    number_action,
    path_action,
    read_entries,
    setting_choices,
    setting_keys,
    strictest,
    validate_setting,
    write_entries,
)

CONFIG = Path(__file__).resolve().parent.parent / "config" / "policy.yaml"


def setting(**kv) -> Entry:
    return Entry(settings={k.replace("__", "."): v for k, v in kv.items()})


# --- 기본값 ---

def test_defaults_mask_every_kind_fully():
    for kind in KINDS:
        assert DEFAULT_POLICY.number_rule(kind) == "mask"
    assert DEFAULT_POLICY.visible == {kind: "none" for kind in KINDS}
    assert DEFAULT_POLICY.allow_threshold == 0.95
    assert DEFAULT_POLICY.deny_threshold == 0.95
    assert DEFAULT_POLICY.jev_backend == "mock"
    assert DEFAULT_POLICY.jev_timeout_seconds == 3.0
    assert DEFAULT_POLICY.max_inspect_bytes == 200_000
    assert (DEFAULT_POLICY.approval_timeout_seconds, DEFAULT_POLICY.approval_session_minutes) == (30.0, 60)
    assert DEFAULT_POLICY.path_rules == DEFAULT_PATH_RULES
    assert DEFAULT_POLICY.context_rules == ()
    assert DEFAULT_POLICY.entries == ()


def test_config_file_documents_the_code_defaults():
    data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    flat = {f"numbers.{kind}.{what}": value
            for kind, rule in data["numbers"].items() for what, value in rule.items()}
    flat |= {f"thresholds.{k}": v for k, v in data["thresholds"].items()}
    flat |= {f"entities.{group}.{what}": value
             for group, rule in data["entities"].items() for what, value in rule.items()}
    flat |= {k: data[k] for k in ("jev_backend", "jev_timeout_seconds", "max_inspect_bytes", "ner_backend",
                                  "ner_timeout_seconds")}
    flat |= {f"approval.{k}": v for k, v in data["approval"].items()}
    assert flat == default_settings()
    assert tuple(PathRule(r["pattern"], r["action"]) for r in data["path_rules"]) == DEFAULT_PATH_RULES
    assert data["context_rules"] == []


def test_setting_keys_cover_every_kind():
    keys = setting_keys()
    for kind in KINDS:
        assert f"numbers.{kind}.action" in keys
        assert setting_choices(f"numbers.{kind}.visible") == VISIBLE_CHOICES[kind]
        assert setting_choices(f"numbers.{kind}.action") == ("deny", "ask_human", "mask")
    assert setting_choices("jev_backend") == ("laya", "mock")
    assert setting_choices("jev_timeout_seconds") is None


def test_policy_is_read_only():
    with pytest.raises(TypeError):
        DEFAULT_POLICY.settings["jev_backend"] = "x"  # type: ignore[index]


# --- 설정 검사 ---

@pytest.mark.parametrize("key, value", [
    ("numbers.rrn.visible", "birth"),
    ("numbers.card.visible", "last4"),
    ("numbers.card.action", "deny"),
    ("numbers.email.action", "ask_human"),
    ("thresholds.allow_if_safe_confidence_gte", 1),
    ("thresholds.deny_if_sensitive_confidence_gte", 0.5),
    ("jev_timeout_seconds", 10),
    ("max_inspect_bytes", 1000),
    ("jev_backend", "mock"),
    ("approval.timeout_seconds", 1.0),
    ("approval.timeout_seconds", 600.0),
    ("approval.session_minutes", 0),
    ("approval.session_minutes", 1440),
])
def test_validate_setting_accepts(key, value):
    assert validate_setting(key, value) == value


@pytest.mark.parametrize("key, value", [
    ("numbers.rrn.visible", "last4"),
    ("numbers.account.visible", "last4"),
    ("numbers.card.action", "allow"),       # 번호가 있는데 원본 그대로는 선택지에 없다
    ("numbers.card.action", "Deny"),
    ("numbers.unknown.action", "mask"),
    ("regex_hit_action", "mask"),            # 명세의 단일 설정은 종류별 설정으로 바뀌었다
    ("thresholds.allow_if_safe_confidence_gte", 1.5),
    ("thresholds.allow_if_safe_confidence_gte", -0.1),
    ("thresholds.allow_if_safe_confidence_gte", True),
    ("thresholds.allow_if_safe_confidence_gte", "0.9"),
    ("thresholds.allow_if_safe_confidence_gte", float("nan")),
    ("jev_timeout_seconds", 0),
    ("jev_timeout_seconds", 10_000),
    ("max_inspect_bytes", 999),
    ("max_inspect_bytes", 2000.0),
    ("jev_backend", "openai"),
    ("approval.timeout_seconds", 0.5),
    ("approval.timeout_seconds", 601),
    ("approval.session_minutes", -1),
    ("approval.session_minutes", 1441),
    ("approval.session_minutes", 30.0),
    (1, "mask"),
])
def test_validate_setting_rejects(key, value):
    with pytest.raises(PolicyError):
        validate_setting(key, value)


def test_float_settings_are_normalized():
    assert isinstance(validate_setting("jev_timeout_seconds", 3), float)


# --- 항목과 정책 만들기 ---

def test_entry_requires_exactly_one_kind():
    with pytest.raises(PolicyError):
        Entry()
    with pytest.raises(PolicyError):
        Entry(settings={"numbers.rrn.visible": "birth"}, context="x")
    with pytest.raises(PolicyError):
        Entry(settings={})
    with pytest.raises(PolicyError):
        Entry(context="   ")
    with pytest.raises(PolicyError):
        Entry(settings={"numbers.rrn.visible": "all"})


@pytest.mark.parametrize("pattern, action", [
    ("", "deny"), ("/", "deny"), ("a/../b", "deny"), ("a\x00", "deny"), ("*.txt", "mask"), (3, "deny"),
])
def test_path_rule_validation(pattern, action):
    with pytest.raises(PolicyError):
        PathRule(pattern, action)


def test_build_policy_applies_entries_in_order():
    policy = build_policy([
        setting(numbers__rrn__visible="birth"),
        Entry(context="병원 기록은 보여주지 마", said="병원 기록은 보여주지 마"),
        setting(numbers__rrn__visible="birth_gender", numbers__card__action="deny"),
        Entry(path_rule=PathRule("공개/**", "allow")),
    ])
    assert policy.visible["rrn"] == "birth_gender"
    assert policy.number_rule("card") == "deny"
    assert policy.number_rule("rrn") == "mask"
    assert policy.context_rules == ("병원 기록은 보여주지 마",)
    assert policy.path_rules == (*DEFAULT_PATH_RULES, PathRule("공개/**", "allow"))
    assert len(policy.entries) == 4


def test_version_tracks_behavior_not_wording():
    a = build_policy([Entry(settings={"numbers.rrn.visible": "birth"}, said="생년월일까지")])
    b = build_policy([Entry(settings={"numbers.rrn.visible": "birth"}, said="앞 6자리까지", at="2026-10-31T00:00:00")])
    c = build_policy([Entry(settings={"numbers.rrn.visible": "birth_gender"})])
    d = build_policy([Entry(context="병원 기록은 보여주지 마")])
    assert a.version == b.version
    assert len({DEFAULT_POLICY.version, a.version, c.version, d.version}) == 4
    assert build_policy().version == DEFAULT_POLICY.version


def test_entry_dict_round_trip():
    entries = [
        Entry(settings={"numbers.rrn.visible": "birth", "jev_timeout_seconds": 5.0}, said="문장", at="2026-10-31T10:00:00"),
        Entry(context="가족 이야기는 빼 줘"),
        Entry(path_rule=PathRule("**/진료/**", "ask_human")),
    ]
    assert [Entry.from_dict(e.to_dict()) for e in entries] == entries


@pytest.mark.parametrize("data", [
    "x", [], {"set": {"numbers.rrn.visible": "birth"}, "extra": 1},
    {"path_rule": {"pattern": "a"}}, {"path_rule": {"pattern": "a", "action": "deny", "x": 1}},
    {"set": ["numbers.rrn.visible"]}, {"context": 3}, {"context": "x", "said": 3},
])
def test_entry_from_dict_rejects_bad_shapes(data):
    with pytest.raises(PolicyError):
        Entry.from_dict(data)


# --- 정책 파일 ---

def test_missing_or_unset_policy_file_means_defaults(tmp_path):
    assert read_entries(None) == ()
    assert read_entries(tmp_path / "없음.yaml") == ()
    assert load_policy(tmp_path / "없음.yaml").version == DEFAULT_POLICY.version
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    assert read_entries(empty) == ()


def test_write_then_read_round_trip(tmp_path):
    path = tmp_path / "sub" / "policy.yaml"
    entries = (
        Entry(settings={"numbers.card.action": "deny"}, said="카드번호 있는 파일은 거부"),
        Entry(context="병원 기록은 보여주지 마", said="병원 기록은 보여주지 마"),
    )
    policy = write_entries(path, entries)
    assert read_entries(path) == entries
    assert load_policy(path).version == policy.version
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# gate 정책 파일")
    assert "카드번호 있는 파일은 거부" in text  # 한글을 그대로 쓴다
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".policy-")]  # 임시 파일이 남지 않는다


@pytest.mark.parametrize("content", [
    "version: 1\nentries: [",                                   # YAML 오류
    "- 1\n- 2\n",                                               # 최상위가 목록
    "version: 2\nentries: []\n",                                # 모르는 버전
    "entries: []\n",                                            # 버전 없음
    "version: 1\nentries: {}\n",                                # entries가 목록이 아님
    "version: 1\nentries: []\nthresholds: {}\n",                # 모르는 최상위 키
    "version: 1\nentries:\n  - set: {numbers.rrn.visible: all}\n",
    "version: 1\nentries:\n  - set: {numbers.rrn.visible: birth}\n    context: x\n",
    "version: 1\nentries:\n  - !!python/object/apply:os.system [echo]\n",  # safe_load만 쓴다
])
def test_bad_policy_files_raise(tmp_path, content):
    path = tmp_path / "policy.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(path)


def test_policy_file_must_be_utf8_and_small(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_bytes("version: 1\nentries:\n  - context: 병원\n".encode("euc-kr"))
    with pytest.raises(PolicyError):
        load_policy(path)
    path.write_bytes(b"#" * (MAX_POLICY_FILE_BYTES + 1))
    with pytest.raises(PolicyError):
        load_policy(path)


def test_unreadable_policy_path_raises(tmp_path):
    with pytest.raises(PolicyError):
        load_policy(tmp_path)  # 폴더


def test_write_entries_validates_before_writing(tmp_path):
    path = tmp_path / "policy.yaml"
    write_entries(path, [Entry(context="원래 규칙")])
    with pytest.raises(PolicyError):
        write_entries(path, [Entry.from_dict({"set": {"numbers.rrn.visible": "all"}})])
    assert read_entries(path) == (Entry(context="원래 규칙"),)


# --- 데몬용 정책 원본: 요청마다 변경 확인 ---

def test_policy_source_follows_file_changes(tmp_path):
    path = tmp_path / "policy.yaml"
    source = PolicySource(path)
    assert source.current().version == DEFAULT_POLICY.version

    write_entries(path, [setting(numbers__card__action="deny")])
    assert source.current().number_rule("card") == "deny"
    first = source.current()
    assert source.current() is first  # 바뀌지 않으면 다시 읽지 않는다

    path.write_text("version: 1\nentries: [", encoding="utf-8")
    for _ in range(2):
        with pytest.raises(PolicyError):
            source.current()

    write_entries(path, [setting(numbers__card__action="ask_human")])
    assert source.current().number_rule("card") == "ask_human"

    path.unlink()
    assert source.current().version == DEFAULT_POLICY.version


def test_policy_source_without_path_uses_defaults():
    assert PolicySource(None).current() is not None
    assert PolicySource(None).current().version == DEFAULT_POLICY.version


def test_policy_source_is_thread_safe(tmp_path):
    path = tmp_path / "policy.yaml"
    write_entries(path, [setting(numbers__rrn__visible="birth")])
    source = PolicySource(path)
    seen, errors = [], []

    def worker():
        try:
            for _ in range(50):
                seen.append(source.current().version)
        except Exception as exc:  # pragma: no cover - 실패 시 원인 확인용
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(seen)) == 1


# --- 판단 규칙 ---

def test_strictest_order():
    assert strictest() is None
    assert strictest(None, None) is None
    assert strictest("allow", "mask") == "mask"
    assert strictest("mask", "ask_human", None) == "ask_human"
    assert strictest("allow", "deny", "ask_human") == "deny"


@pytest.mark.parametrize("pattern, path, expected", [
    ("*.env", "/.env", True),
    ("*.env", "/a/b/prod.env", True),
    ("*.env", "/a/b/prod.env.txt", False),
    ("*.env", "/x.env/inner.txt", True),          # 폴더 이름이 맞으면 그 아래도 맞는다
    ("**/*.env", "/a/prod.env", True),
    ("**/*.env", "/prod.env", True),               # **는 0단계도 된다
    ("공개/**", "/공개/a.txt", True),
    ("공개/**", "/b/공개/a.txt", False),           # /가 있는 패턴은 뿌리부터 맞춘다
    ("/공개/*.txt", "/공개/a.txt", True),
    ("/공개/*.txt", "/공개/sub/a.txt", False),      # *는 한 단계만
    ("**/진료/**", "/2026/진료/메모.txt", True),
    ("id_rsa*", "/home/.ssh/id_rsa.pub", True),
])
def test_match_path(pattern, path, expected):
    assert match_path(pattern, path, fold=False) is expected


def test_match_path_fold_ignores_case_and_normalization():
    nfd = unicodedata.normalize("NFD", "/진료/메모.txt")
    assert nfd != "/진료/메모.txt"
    assert match_path("진료/**", nfd, fold=False)  # NFC로 맞춘 뒤 비교
    assert match_path("*.env", "/PROD.ENV", fold=True)
    assert not match_path("*.env", "/PROD.ENV", fold=False)


def test_path_action_defaults_deny_secret_files():
    assert path_action(DEFAULT_POLICY, ["/상담/2026-03.txt"]) is None
    assert path_action(DEFAULT_POLICY, ["/설정/.env"]) == "deny"
    assert path_action(DEFAULT_POLICY, ["/설정/.env.local"]) == "deny"
    assert path_action(DEFAULT_POLICY, ["/Keys/Server.PEM"]) == "deny"   # 대소문자 무시 (맥 파일 시스템)
    with pytest.raises(ValueError):
        path_action(DEFAULT_POLICY, [])


def test_path_action_strictest_rule_wins_regardless_of_order():
    policy = build_policy([Entry(path_rule=PathRule("공개/**", "allow")),
                           Entry(path_rule=PathRule("**/진료/**", "ask_human"))])
    assert path_action(policy, ["/공개/안내.txt"]) == "allow"
    assert path_action(policy, ["/공개/a.env"]) == "deny"
    assert path_action(policy, ["/공개/진료/a.txt"]) == "ask_human"


def test_path_allow_needs_every_candidate_and_exact_case():
    policy = build_policy([Entry(path_rule=PathRule("공개/**", "allow"))])
    # 요청 경로는 공개 폴더지만 심볼릭 링크를 따라간 실제 경로는 다른 폴더
    assert path_action(policy, ["/공개/link.txt", "/비공개/secret.txt"]) is None
    assert path_action(policy, ["/공개/a.txt", "/공개/a.txt"]) == "allow"
    deny = build_policy([Entry(path_rule=PathRule("비공개/**", "deny"))])
    assert path_action(deny, ["/공개/link.txt", "/비공개/secret.txt"]) == "deny"
    upper = build_policy([Entry(path_rule=PathRule("public/**", "allow"))])
    assert path_action(upper, ["/PUBLIC/a.txt"]) is None


def test_number_action_uses_per_kind_rules():
    policy = build_policy([setting(numbers__card__action="deny", numbers__email__action="ask_human")])
    assert number_action(policy, []) is None
    assert number_action(policy, [Finding("mobile", 0, 1)]) == "mask"
    assert number_action(policy, [Finding("mobile", 0, 1), Finding("email", 2, 3)]) == "ask_human"
    assert number_action(policy, [Finding("email", 0, 1), Finding("card", 2, 3)]) == "deny"
    assert number_action(DEFAULT_POLICY, [Finding("card", 0, 1)]) == "mask"


@pytest.mark.parametrize("label, confidence, expected", [
    ("safe", 0.95, "allow"),
    ("safe", 0.99, "allow"),
    ("safe", 0.94, "ask_human"),
    ("sensitive", 0.95, "deny"),
    ("highly_sensitive", 0.97, "deny"),
    ("sensitive", 0.8, "ask_human"),
    ("highly_sensitive", 0.5, "ask_human"),
])
def test_jev_action_thresholds(label, confidence, expected):
    assert jev_action(DEFAULT_POLICY, JevDecision(label, confidence)) == expected


def test_jev_action_error_is_deny_and_thresholds_follow_policy():
    assert jev_action(DEFAULT_POLICY, None) == "deny"
    lenient = build_policy([setting(thresholds__allow_if_safe_confidence_gte=0.7,
                                    thresholds__deny_if_sensitive_confidence_gte=0.99)])
    assert jev_action(lenient, JevDecision("safe", 0.8)) == "allow"
    assert jev_action(lenient, JevDecision("sensitive", 0.97)) == "ask_human"


# --- 완화 확인 ---

def test_loosened_detects_relaxing_changes():
    base = build_policy([setting(numbers__card__action="deny")])
    assert loosened(base, base) == []
    assert loosened(DEFAULT_POLICY, build_policy([setting(numbers__rrn__visible="birth")]))
    assert loosened(base, DEFAULT_POLICY) == ["카드번호: 거부 → 카드번호: 가려서 보여줌"]
    assert loosened(DEFAULT_POLICY, build_policy([setting(thresholds__allow_if_safe_confidence_gte=0.9)]))
    assert loosened(DEFAULT_POLICY, build_policy([setting(thresholds__deny_if_sensitive_confidence_gte=0.99)]))
    assert loosened(DEFAULT_POLICY, build_policy([setting(jev_timeout_seconds=10)]))  # 방향을 모르면 완화로 본다
    assert loosened(DEFAULT_POLICY, build_policy([Entry(path_rule=PathRule("공개/**", "allow"))]))
    ctx = build_policy([Entry(context="병원 기록은 보여주지 마")])
    assert loosened(ctx, DEFAULT_POLICY) == ["맥락 규칙 삭제: 병원 기록은 보여주지 마"]
    # 사람 승인: 기다리는 시간·폴더 승인을 기억하는 시간이 길어지면 승인될 여지가 커진다
    assert loosened(DEFAULT_POLICY, build_policy([setting(approval__session_minutes=120)])) == [
        "'이 폴더 허용'을 기억하는 시간(분, 0이면 기억하지 않음): 60 → '이 폴더 허용'을 기억하는 시간(분, 0이면 기억하지 않음): 120"]
    assert loosened(DEFAULT_POLICY, build_policy([setting(approval__timeout_seconds=60.0)]))


def test_loosened_is_empty_for_stricter_changes():
    stricter = [
        setting(numbers__card__action="deny"),
        setting(numbers__mobile__action="ask_human"),
        setting(thresholds__allow_if_safe_confidence_gte=0.99),
        setting(thresholds__deny_if_sensitive_confidence_gte=0.9),
        Entry(path_rule=PathRule("**/진료/**", "deny")),
        Entry(context="병원 기록은 보여주지 마"),
        setting(approval__session_minutes=0),
        setting(approval__timeout_seconds=10.0),
    ]
    for entry in stricter:
        assert loosened(DEFAULT_POLICY, build_policy([entry])) == [], entry
    relaxed = build_policy([setting(numbers__rrn__visible="birth_gender")])
    assert loosened(relaxed, build_policy([setting(numbers__rrn__visible="birth")])) == []


def test_describe_setting_is_korean():
    assert describe_setting("numbers.rrn.visible", "birth") == "주민등록번호: 생년월일(앞 6자리)까지 공개"
    assert describe_setting("numbers.card.action", "deny") == "카드번호: 거부"
    assert describe_setting("jev_timeout_seconds", 3.0) == "Jev 응답 제한 시간(초): 3"
    assert describe_setting("thresholds.allow_if_safe_confidence_gte", 0.97) == \
        "Jev가 '안전'이라고 답할 때 보여 주는 최소 확신도: 0.97"
    assert set(SETTING_NAMES) == {k for k in setting_keys() if not k.startswith(("numbers.", "entities."))}


# --- 3e: 묶음 정리 (2026-11-01 NER 교체) ---

OLD_POLICY = """version: 1
entries:
- said: 날짜는 거부
  set: {entities.date.action: deny}
- set: {entities.event.action: allow, entities.name.action: ask_human}
"""


def test_entity_groups_are_nine_and_retired_keys_are_not_settings():
    groups = [k.split(".")[1] for k in setting_keys() if k.startswith("entities.")]
    assert groups == ["name", "address", "birth", "student_id", "org", "place", "etc", "host", "plain"]
    assert DEFAULT_POLICY.entity_rule("plain") == "allow" and DEFAULT_POLICY.entity_rule("host") == "mask"
    assert "entities.date.action" not in DEFAULT_POLICY.settings
    assert "entities.event.action" not in DEFAULT_POLICY.settings


def test_old_policy_file_with_retired_keys_is_read_and_has_no_effect(tmp_path):
    """예전 키가 든 정책 파일도 읽힌다(그렇지 않으면 데몬이 모든 읽기를 막는다). 효과는 없다."""
    path = tmp_path / "policy.yaml"
    path.write_text(OLD_POLICY, encoding="utf-8")
    entries = read_entries(path)
    assert dict(entries[0].settings) == {"entities.date.action": "deny"}   # 항목에는 남는다(보여 주고 지울 수 있게)
    policy = PolicySource(path).current()                                   # PolicyError가 아니다
    assert policy.entity_rule("name") == "ask_human"
    assert "entities.date.action" not in policy.settings and "entities.event.action" not in policy.settings
    assert policy.version == build_policy([setting(entities__name__action="ask_human")]).version
    write_entries(tmp_path / "copy.yaml", entries)                          # 다른 항목과 함께 다시 쓸 수 있다
    assert read_entries(tmp_path / "copy.yaml") == entries


@pytest.mark.parametrize("key", ["entities.date.action", "entities.event.action"])
def test_retired_keys_are_refused_for_new_settings(key):
    with pytest.raises(PolicyError, match="지정할 수 없습니다"):
        validate_setting(key, "mask")
    assert key not in setting_keys() and setting_choices(key) is None


def test_retired_key_with_bad_value_still_breaks_the_file(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("version: 1\nentries:\n- set: {entities.date.action: hide}\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="entities.date.action"):
        read_entries(path)


def test_describe_retired_setting_says_not_found_now():
    assert describe_setting("entities.date.action", "deny") == "날짜(NER): 거부 (지금은 찾지 않음)"
    assert describe_setting("entities.event.action", "allow") == "사건(NER): 그대로 보여줌 (지금은 찾지 않음)"
    assert describe_setting("entities.plain.action", "allow") == "나이(NER): 그대로 보여줌"
    assert describe_setting("entities.student_id.action", "mask") == "식별번호(학번·사번·여권번호 등)(NER): 가려서 보여줌"


# --- Phase 4 보완: 비밀값과 자격 증명 파일 ---

def test_secret_kind_has_settings():
    assert DEFAULT_POLICY.number_rule("secret") == "mask"
    assert DEFAULT_POLICY.visible["secret"] == "none"
    assert setting_choices("numbers.secret.visible") == ("none",)
    assert describe_setting("numbers.secret.action", "deny") == "비밀값(비밀번호·키·토큰): 거부"
    with pytest.raises(PolicyError):
        validate_setting("numbers.secret.visible", "prefix")


@pytest.mark.parametrize("path, expected", [
    ("/.netrc", "deny"),
    ("/홈/.npmrc", "deny"),
    ("/a/.pypirc", "deny"),
    ("/.pgpass", "deny"),
    ("/.git-credentials", "deny"),
    ("/gcp/credentials.json", "deny"),
    ("/home/me/.aws/credentials", "deny"),
    ("/.aws/credentials", "deny"),
    ("/certs/client.P12", "deny"),
    ("/certs/a.pfx", "deny"),
    ("/android/release.keystore", "deny"),
    ("/java/trust.jks", "deny"),
    ("/설정/app.yaml", None),               # 설정 파일은 열어 보고 비밀값을 가린다
    ("/설정/settings.toml", None),
    ("/메모/credentials.md", None),
    ("/.aws/config", None),
])
def test_default_rules_for_credential_files(path, expected):
    assert path_action(DEFAULT_POLICY, [path]) == expected
