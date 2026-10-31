"""gate.pipeline + NER (3c): 정규식 → NER → Jev → 합치기. NER·Jev는 가짜다. 모든 값은 가짜 예시다.

사용자 결정(2026-10-31, ②): NER은 항상 가린다. 가린 것(정규식·NER)이 있으면 Jev는 가린 결과(보여 줄 내용)를 판단해
안전이면 가린 채 보여 주고, 아니면 사람에게 묻는다. 가린 것이 없으면 지금처럼 Jev 결과(허용·승인·차단)를 따른다.
NER이 꺼져 있으면(기본) 3b와 똑같다.
"""

import pytest

from gate.jev_client import JevError, MockJevClient
from gate.ner_client import Entity, NerError, NerResult
from gate.peer import Peer
from gate.pipeline import Inspector, search
from gate.policy import Entry, PolicySource, write_entries
from gate.store import PlainStore

PEER = Peer(pid=4321, uid=501, exe="/usr/local/bin/claude")
INTRO = "지원자 홍길동은 가온대학교를 졸업했습니다.\n연락처는 010-1234-5678입니다.\n"
INTRO_SHOWN = "지원자 [이름]은 [학교]를 졸업했습니다.\n연락처는 ***-****-****입니다.\n"
NAMES = {"홍길동": "NAME", "가온대학교": "SCHOOL", "30대": "AGE", "+1 415 555 0100": "PHONE",
         "010-1234-5678": "PHONE", "MIIEvgIBADAN": "PRIVATE_KEY", "김": "NAME"}


class FakeNer:
    """words의 글자가 나오는 곳마다 개체 하나를 돌려준다(겹치면 앞의 것)."""

    def __init__(self, words=NAMES, tag="ner:test0001", error=None, tag_error=None):
        self.words, self.tag, self.error, self.tag_error = dict(words), tag, error, tag_error
        self.texts: list[str] = []

    def cache_tag(self):
        if self.tag_error:
            raise self.tag_error
        return self.tag

    def find(self, text):
        self.texts.append(text)
        if self.error:
            raise self.error
        found = []
        for word, label in self.words.items():
            start = text.find(word)
            while start != -1:
                found.append(Entity(label, start, start + len(word), 0.9))
                start = text.find(word, start + 1)
        kept = []
        for e in sorted(found, key=lambda e: (e.start, -e.end)):
            if not kept or e.start >= kept[-1].end:
                kept.append(e)
        return NerResult(tuple(kept), self.tag)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "자소서.txt").write_text(INTRO, encoding="utf-8")
    (root / "이름만.txt").write_text("담당자는 홍길동입니다.\n", encoding="utf-8")
    (root / "나이만.txt").write_text("환자는 30대입니다.\n", encoding="utf-8")
    (root / "회의.txt").write_text("회의는 화요일입니다.\n", encoding="utf-8")
    (root / "해외.txt").write_text("연락처 +1 415 555 0100\n", encoding="utf-8")
    (root / "키.txt").write_text("값 MIIEvgIBADAN 끝\n", encoding="utf-8")
    (root / "깨짐.txt").write_bytes(b"\xff\xfe " + "김".encode() + b" \x80\n")
    return root


def make(source, tmp_path, *, jev=None, ner=None, settings=None):
    policy_path = tmp_path / "policy.yaml"
    entries = [Entry(settings={"ner_backend": "server", **(settings or {})})] if ner is not None else []
    if settings and ner is None:
        entries = [Entry(settings=settings)]
    if entries:
        write_entries(policy_path, entries)
    jev = jev or MockJevClient("fixed", label="safe", confidence=0.99)
    ner = ner if ner is not None else FakeNer()
    inspector = Inspector(PlainStore(source), PolicySource(policy_path), jev_factory=lambda policy: jev,
                          ner_factory=lambda policy: ner if policy.ner_backend == "server" else None)
    return inspector, jev, ner


SENSITIVE = dict(label="sensitive", confidence=0.99)


# --- NER을 끄면 3b와 같다 ---

def test_ner_off_by_default_keeps_names_and_jev_still_denies(source, tmp_path):
    inspector, _, ner = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/자소서.txt")
    assert verdict.judgment.action == "mask"
    assert verdict.data.decode() == "지원자 홍길동은 가온대학교를 졸업했습니다.\n연락처는 ***-****-****입니다.\n"
    assert ner.texts == []
    inspector, _, _ = make(source, tmp_path, jev=MockJevClient("fixed", **SENSITIVE))
    assert inspector.inspect(PEER, "/자소서.txt").judgment.action == "deny"  # 번호를 가렸어도 3b처럼 차단


# --- NER을 켜면 ---

def test_names_are_replaced_with_kind_names_and_numbers_keep_stars(source, tmp_path):
    inspector, jev, ner = make(source, tmp_path, ner=FakeNer())
    verdict = inspector.inspect(PEER, "/자소서.txt")
    j = verdict.judgment
    assert (j.action, j.stage) == ("mask", "jev")
    assert verdict.data.decode() == INTRO_SHOWN
    assert dict(j.findings) == {"mobile": 1, "name": 1, "org": 1}  # 정규식과 겹친 NER 전화번호는 세지 않는다
    assert "이름" in j.reason and "소속" in j.reason
    assert ner.texts == [INTRO]                              # NER은 원문을 본다
    assert jev.calls[0].text == INTRO_SHOWN                  # Jev는 가린 결과(보여 줄 내용)를 본다
    assert "가린 뒤 Jev 판단 safe" in j.reason


@pytest.mark.parametrize("label, confidence, action", [
    ("safe", 0.99, "mask"), ("safe", 0.6, "mask"),           # 가린 결과가 안전하면 확신도와 상관없이 가린 채 보여 준다
    ("sensitive", 0.99, "ask_human"), ("highly_sensitive", 0.97, "ask_human"), ("sensitive", 0.6, "ask_human"),
])
def test_masked_result_is_rechecked_by_jev(source, tmp_path, label, confidence, action):
    inspector, _, _ = make(source, tmp_path, jev=MockJevClient("fixed", label=label, confidence=confidence),
                           ner=FakeNer())
    verdict = inspector.inspect(PEER, "/자소서.txt")
    j = verdict.judgment
    assert (j.action, j.stage, j.jev_label) == (action, "jev", label)
    if action == "mask":
        assert verdict.data.decode() == INTRO_SHOWN
    else:
        assert verdict.data is None and "가린 뒤에도" in j.reason  # 승인 창구가 없으면 묻지 않고 막힌다


def test_rechecked_document_shows_masked_content_when_approved(source, tmp_path):
    from gate.approval import ApprovalResult, SessionApprovals

    class Approve:
        sessions = SessionApprovals()

        def request(self, request, key):
            self.asked = request
            return ApprovalResult("once")

    approvals = Approve()
    policy_path = tmp_path / "policy.yaml"
    write_entries(policy_path, [Entry(settings={"ner_backend": "server"})])
    inspector = Inspector(PlainStore(source), PolicySource(policy_path),
                          jev_factory=lambda p: MockJevClient("fixed", **SENSITIVE),
                          ner_factory=lambda p: FakeNer(), approvals=approvals)
    verdict = inspector.inspect(PEER, "/이름만.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("mask", "human")
    assert verdict.data.decode() == "담당자는 [이름]입니다.\n"
    assert dict(approvals.asked.findings) == {"name": 1}


def test_regex_only_masking_is_also_rechecked_when_ner_is_on(source, tmp_path):
    (source / "번호만.txt").write_text("연락처 010-1234-5678\n", encoding="utf-8")
    inspector, jev, _ = make(source, tmp_path, jev=MockJevClient("fixed", **SENSITIVE), ner=FakeNer(words={}))
    verdict = inspector.inspect(PEER, "/번호만.txt")
    assert verdict.judgment.action == "ask_human"           # NER을 끄면 3b처럼 차단(아래 테스트)
    assert jev.calls[0].text == "연락처 ***-****-****\n"
    (tmp_path / "off").mkdir()
    inspector, _, _ = make(source, tmp_path / "off", jev=MockJevClient("fixed", **SENSITIVE))
    assert inspector.inspect(PEER, "/번호만.txt").judgment.action == "deny"


@pytest.mark.parametrize("label, confidence, action", [("sensitive", 0.99, "deny"), ("sensitive", 0.6, "ask_human"),
                                                       ("safe", 0.99, "allow")])
def test_nothing_to_mask_follows_jev(source, tmp_path, label, confidence, action):
    inspector, _, _ = make(source, tmp_path, jev=MockJevClient("fixed", label=label, confidence=confidence),
                           ner=FakeNer())
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert verdict.judgment.action == action


def test_plain_group_is_shown_and_does_not_count_as_something_to_mask(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, ner=FakeNer())
    verdict = inspector.inspect(PEER, "/나이만.txt")
    assert (verdict.judgment.action, verdict.data.decode()) == ("allow", "환자는 30대입니다.\n")
    inspector, _, _ = make(source, tmp_path, jev=MockJevClient("fixed", **SENSITIVE), ner=FakeNer())
    assert inspector.inspect(PEER, "/나이만.txt").judgment.action == "deny"


def test_jev_error_still_denies_even_with_something_to_mask(source, tmp_path):
    jev = MockJevClient("fixed", label="safe", confidence=0.99, error=JevError("Laya 서버에 연결하지 못했습니다"))
    inspector, _, _ = make(source, tmp_path, jev=jev, ner=FakeNer())
    verdict = inspector.inspect(PEER, "/자소서.txt")
    assert (verdict.judgment.action, verdict.data) == ("deny", None)
    assert "연결하지 못했습니다" in verdict.judgment.reason


@pytest.mark.parametrize("ner", [
    FakeNer(error=NerError("NER 서버에 연결하지 못했습니다: ConnectionRefusedError")),
    FakeNer(error=RuntimeError("홍길동 boom")),
    FakeNer(tag_error=NerError("NER 서버가 준비되지 않았습니다")),
])
def test_ner_failure_denies_without_calling_jev(source, tmp_path, ner):
    inspector, jev, _ = make(source, tmp_path, ner=ner)
    verdict = inspector.inspect(PEER, "/자소서.txt")
    j = verdict.judgment
    assert (j.action, j.stage, verdict.data) == ("deny", "entity", None)
    assert "NER 판단에 실패해 차단했습니다" in j.reason and "홍길동" not in j.reason
    assert jev.calls == []


def test_ner_that_cannot_be_built_denies(source, tmp_path):
    def broken(policy):
        raise NerError("NER 서버 주소 NER_BASE_URL(예: http://127.0.0.1:8001)이 필요합니다")
    policy_path = tmp_path / "policy.yaml"
    write_entries(policy_path, [Entry(settings={"ner_backend": "server"})])
    inspector = Inspector(PlainStore(source), PolicySource(policy_path),
                          jev_factory=lambda p: MockJevClient("fixed", label="safe", confidence=0.99),
                          ner_factory=broken)
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "entity")
    assert "NER_BASE_URL" in verdict.judgment.reason


def test_bad_ner_result_type_denies(source, tmp_path):
    class Bad(FakeNer):
        def find(self, text):
            return [Entity("NAME", 0, 1, 0.9)]
    inspector, _, _ = make(source, tmp_path, ner=Bad())
    assert inspector.inspect(PEER, "/자소서.txt").judgment.action == "deny"


# --- 정책 ---

def test_entity_rules(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path, ner=FakeNer(), settings={"entities.name.action": "deny"})
    verdict = inspector.inspect(PEER, "/자소서.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "entity")
    assert "이름" in verdict.judgment.reason and jev.calls == []

    inspector, _, _ = make(source, tmp_path, ner=FakeNer(), settings={"entities.org.action": "ask_human"})
    verdict = inspector.inspect(PEER, "/자소서.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("ask_human", "entity")
    assert "소속" in verdict.judgment.reason

    inspector, _, _ = make(source, tmp_path, ner=FakeNer(), settings={"entities.name.action": "allow"})
    verdict = inspector.inspect(PEER, "/자소서.txt")
    assert verdict.data.decode() == "지원자 홍길동은 [학교]를 졸업했습니다.\n연락처는 ***-****-****입니다.\n"
    assert dict(verdict.judgment.findings) == {"mobile": 1, "org": 1}

    inspector, _, _ = make(source, tmp_path, ner=FakeNer(), settings={"entities.plain.action": "mask"})
    assert inspector.inspect(PEER, "/나이만.txt").data.decode() == "환자는 [나이]입니다.\n"


def test_number_ask_human_rule_still_asks_when_masked_result_is_safe(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, ner=FakeNer(), settings={"numbers.mobile.action": "ask_human"})
    verdict = inspector.inspect(PEER, "/자소서.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("ask_human", "number")


def test_ner_phone_that_regex_missed_follows_number_rule(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, ner=FakeNer())
    verdict = inspector.inspect(PEER, "/해외.txt")
    assert (verdict.data.decode(), dict(verdict.judgment.findings)) == ("연락처 [전화번호]\n", {"phone": 1})
    inspector, _, _ = make(source, tmp_path, ner=FakeNer(), settings={"numbers.phone.action": "deny"})
    verdict = inspector.inspect(PEER, "/해외.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "entity")


def test_ner_private_key_label_is_ignored(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, ner=FakeNer())
    verdict = inspector.inspect(PEER, "/키.txt")
    assert (verdict.judgment.action, verdict.data.decode()) == ("allow", "값 MIIEvgIBADAN 끝\n")


def test_non_utf8_bytes_are_kept_and_positions_match(source, tmp_path):
    inspector, _, ner = make(source, tmp_path, ner=FakeNer())
    verdict = inspector.inspect(PEER, "/깨짐.txt")
    assert verdict.data == b"\xff\xfe " + "[이름]".encode() + b" \x80\n"


# --- 캐시 ---

def test_ner_result_is_cached_by_content_and_model(source, tmp_path):
    ner = FakeNer()
    inspector, jev, _ = make(source, tmp_path, ner=ner)
    first = inspector.inspect(PEER, "/자소서.txt")
    second = inspector.inspect(PEER, "/자소서.txt", "다른 목적")
    assert first.data == second.data and len(ner.texts) == 1
    inspector.inspect(PEER, "/자소서.txt", "다른 목적")
    assert len(jev.calls) == 2                                # 같은 요청이면 Jev 판단도 캐시
    ner.tag = "ner:test0002"  # 체크포인트를 바꿔 서버를 다시 띄운 경우
    inspector.inspect(PEER, "/자소서.txt")
    assert len(ner.texts) == 2
    assert len(jev.calls) == 3                                # 가린 결과가 달라질 수 있으니 Jev도 다시 묻는다
    (source / "자소서.txt").write_text(INTRO + "끝.\n", encoding="utf-8")
    inspector.inspect(PEER, "/자소서.txt")
    assert len(ner.texts) == 3


def test_search_sees_ner_masked_content(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, ner=FakeNer())
    result = search(inspector, PEER, "/", "[이름]")
    assert {m["path"] for m in result["matches"]} == {"/자소서.txt", "/이름만.txt", "/깨짐.txt"}
    assert search(inspector, PEER, "/", "홍길동")["matches"] == []
