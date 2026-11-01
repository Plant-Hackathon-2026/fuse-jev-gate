"""gate.pipeline: 경로 규칙 → 정규식 → 캐시 → Jev → 정책. Jev는 Mock이다. 모든 값은 가짜 예시다."""

import errno

import pytest

from gate.approval import Approvals, SessionApprovals
from gate.cache import CacheKey
from gate.detectors import mask
from gate.jev_client import JevDecision, JevError, MockJevClient, make_client
from gate.peer import Peer
from gate.pipeline import MAX_LINE_CHARS, Blocked, Inspector, search, split_utf8
from gate.policy import Entry, PathRule, PolicySource, write_entries
from gate.protocol import GateError
from gate.store import PlainStore

PEER = Peer(pid=4321, uid=501, exe="/usr/local/bin/claude")
PLAIN = "회의는 다음 주 화요일에 합니다.\n안건은 예산입니다.\n"
WITH_PHONE = "연락처는 010-1234-5678입니다.\n"
WITH_CARD = "결제 카드 4111 1111 1111 1111\n"
WITH_RRN = "주민번호 900101-1234567\n"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "공개").mkdir(parents=True)
    (root / "비공개").mkdir()
    (root / "진료").mkdir()
    (root / "회의.txt").write_text(PLAIN, encoding="utf-8")
    (root / "연락처.txt").write_text(WITH_PHONE, encoding="utf-8")
    (root / "카드.txt").write_text(WITH_CARD, encoding="utf-8")
    (root / "주민.txt").write_text(WITH_RRN, encoding="utf-8")
    (root / "빈.txt").write_bytes(b"")
    (root / "설정.env").write_text("TOKEN=not-a-real-token\n", encoding="utf-8")
    (root / "공개" / "안내.txt").write_text(WITH_PHONE, encoding="utf-8")
    (root / "비공개" / "메모.txt").write_text(PLAIN, encoding="utf-8")
    (root / "진료" / "메모.txt").write_text(PLAIN, encoding="utf-8")
    (root / "공개" / "링크.txt").symlink_to(root / "비공개" / "메모.txt")
    (root / "회의-링크.txt").symlink_to(root / "설정.env")
    return root


def make(source, tmp_path, jev=None, entries=None, **kwargs):
    """이 파일은 경로 규칙·정규식·Jev를 본다. NER은 명시적으로 끈다(기본은 켜짐, tests/test_ner_pipeline.py가 따로 본다)."""
    policy_path = tmp_path / "policy.yaml"
    if entries is not None:
        write_entries(policy_path, entries)
    jev = jev or MockJevClient("fixed", label="safe", confidence=0.99)
    inspector = Inspector(PlainStore(source), PolicySource(policy_path), jev_factory=lambda policy: jev,
                          ner_factory=lambda policy: None, **kwargs)
    return inspector, jev, policy_path


# --- 허용·마스킹·차단 ---

def test_safe_document_is_allowed_unchanged(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/회의.txt", "요약")
    assert verdict.judgment.action == "allow"
    assert verdict.data == PLAIN.encode()
    assert (verdict.judgment.stage, verdict.judgment.jev_label, verdict.judgment.jev_calls) == ("jev", "safe", 1)
    call = jev.calls[0]
    assert (call.context.process, call.context.pid, call.context.path, call.context.purpose) == \
        ("/usr/local/bin/claude", 4321, "/회의.txt", "요약")


def test_numbers_are_masked_by_default_and_jev_sees_masked_text(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/연락처.txt")
    assert verdict.judgment.action == "mask"
    assert verdict.data == "연락처는 ***-****-****입니다.\n".encode()
    assert len(verdict.data) == len(WITH_PHONE.encode())
    assert dict(verdict.judgment.findings) == {"mobile": 1}
    assert "1234" not in jev.calls[0].text and "010" not in jev.calls[0].text


def test_visible_settings_come_from_policy(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, entries=[Entry(settings={"numbers.rrn.visible": "birth",
                                                                       "numbers.mobile.visible": "prefix"})])
    assert inspector.inspect(PEER, "/주민.txt").data == "주민번호 900101-*******\n".encode()
    assert inspector.inspect(PEER, "/연락처.txt").data == "연락처는 010-****-****입니다.\n".encode()


@pytest.mark.parametrize("label, confidence, action", [
    ("sensitive", 0.97, "deny"),
    ("highly_sensitive", 0.95, "deny"),
    ("safe", 0.8, "ask_human"),
    ("sensitive", 0.6, "ask_human"),
])
def test_jev_answer_decides(source, tmp_path, label, confidence, action):
    inspector, _, _ = make(source, tmp_path, jev=MockJevClient("fixed", label=label, confidence=confidence))
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert verdict.judgment.action == action
    assert verdict.data is None and not verdict.readable
    blocked = Blocked(verdict.judgment)
    assert blocked.errno == errno.EACCES
    assert ("사람 승인" in blocked.message) == (action == "ask_human")


def test_thresholds_come_from_policy(source, tmp_path):
    jev = MockJevClient("fixed", label="safe", confidence=0.9)
    inspector, _, _ = make(source, tmp_path, jev=jev,
                           entries=[Entry(settings={"thresholds.allow_if_safe_confidence_gte": 0.85})])
    assert inspector.inspect(PEER, "/회의.txt").judgment.action == "allow"


def test_number_rule_deny_skips_jev(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path, entries=[Entry(settings={"numbers.card.action": "deny"})])
    verdict = inspector.inspect(PEER, "/카드.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "number")
    assert "카드번호" in verdict.judgment.reason
    assert jev.calls == []
    assert inspector.inspect(PEER, "/연락처.txt").judgment.action == "mask"  # 다른 종류는 그대로


def test_number_rule_ask_human_still_lets_jev_deny(source, tmp_path):
    entries = [Entry(settings={"numbers.mobile.action": "ask_human"})]
    inspector, _, _ = make(source, tmp_path, entries=entries)
    assert inspector.inspect(PEER, "/연락처.txt").judgment.action == "ask_human"
    strict = MockJevClient("fixed", label="highly_sensitive", confidence=0.99)
    inspector, _, _ = make(source, tmp_path, jev=strict, entries=entries)
    assert inspector.inspect(PEER, "/연락처.txt").judgment.action == "deny"


# --- 실패하면 차단 ---

def test_jev_error_denies_and_is_not_cached(source, tmp_path):
    jev = MockJevClient("fixed", label="safe", confidence=0.99, error=JevError("Jev 응답이 3초 안에 오지 않았습니다"))
    inspector, _, _ = make(source, tmp_path, jev=jev)
    for expected_calls in (1, 2):
        verdict = inspector.inspect(PEER, "/회의.txt")
        assert verdict.judgment.action == "deny"
        assert "3초" in verdict.judgment.reason
        assert len(jev.calls) == expected_calls
    assert len(inspector.cache) == 0


def test_unexpected_exception_and_bad_answer_deny(source, tmp_path):
    class Broken:
        def judge(self, text, context):
            raise RuntimeError("secret 010-1234-5678")

    class Wrong:
        def judge(self, text, context):
            return {"label": "safe", "confidence": 0.99}

    inspector, _, _ = make(source, tmp_path, jev=Broken())
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert verdict.judgment.action == "deny"
    assert "RuntimeError" in verdict.judgment.reason and "010" not in verdict.judgment.reason
    inspector, _, _ = make(source, tmp_path, jev=Wrong())
    assert inspector.inspect(PEER, "/회의.txt").judgment.action == "deny"


def test_jev_that_cannot_be_built_denies(source, tmp_path):
    def factory(policy):
        return make_client("없는-백엔드")

    inspector = Inspector(PlainStore(source), PolicySource(None), jev_factory=factory, ner_factory=lambda p: None)
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert verdict.judgment.action == "deny"
    assert "없는-백엔드" in verdict.judgment.reason


def test_broken_policy_file_denies_until_fixed(source, tmp_path):
    inspector, jev, policy_path = make(source, tmp_path)
    policy_path.write_text("version: 1\nentries: [", encoding="utf-8")
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "policy")
    assert jev.calls == []
    write_entries(policy_path, [])
    assert inspector.inspect(PEER, "/회의.txt").judgment.action == "allow"


def test_default_backend_without_policy_file_is_keyword_mock(source):
    inspector = Inspector(PlainStore(source), ner_factory=lambda p: None)  # Jev 기본값만 본다(NER 기본값은 test_ner_pipeline)
    assert inspector.inspect(PEER, "/회의.txt").judgment.action == "allow"
    assert inspector.inspect(PEER, "/연락처.txt").judgment.action in ("mask", "ask_human", "deny")


# --- 경로 규칙 ---

def test_default_path_rule_denies_env_without_reading_or_asking(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/설정.env")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "path")
    assert verdict.judgment.content_sha256 is None  # 읽지 않았다
    assert jev.calls == []


def test_path_rules_follow_symlink_targets(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, entries=[Entry(path_rule=PathRule("공개/**", "allow"))])
    assert inspector.inspect(PEER, "/회의-링크.txt").judgment.action == "deny"   # 이름은 평범해도 실제는 .env
    allowed = inspector.inspect(PEER, "/공개/안내.txt")
    assert (allowed.judgment.action, allowed.judgment.stage) == ("allow", "path")
    assert allowed.data == WITH_PHONE.encode()  # allow 경로는 검사 없이 원본
    linked = inspector.inspect(PEER, "/공개/링크.txt")  # 공개 폴더 안 링크지만 실제는 비공개
    assert linked.judgment.stage == "jev"


def test_path_ask_human_rule_inspects_first_then_asks(source, tmp_path):
    # Phase 5: 번호 deny·Jev deny로 막힐 파일은 묻지 않도록 검사를 먼저 한다. 승인 창구가 없으면 ask_human 그대로다.
    inspector, jev, _ = make(source, tmp_path, entries=[Entry(path_rule=PathRule("진료/**", "ask_human"))])
    verdict = inspector.inspect(PEER, "/진료/메모.txt")
    assert (verdict.judgment.action, verdict.judgment.stage, verdict.data) == ("ask_human", "path", None)
    assert verdict.judgment.reason == "경로 규칙에 따라 사람에게 물어봅니다"
    assert len(jev.calls) == 1


def test_path_ask_human_rule_still_denied_by_jev(source, tmp_path):
    jev = MockJevClient("fixed", label="highly_sensitive", confidence=0.99)
    inspector, _, _ = make(source, tmp_path, jev=jev, entries=[Entry(path_rule=PathRule("진료/**", "ask_human"))])
    assert inspector.inspect(PEER, "/진료/메모.txt").judgment.action == "deny"


def test_missing_and_outside_paths_raise(source, tmp_path):
    inspector, _, _ = make(source, tmp_path)
    for path, err in (("/없음.txt", errno.ENOENT), ("/../x", errno.EACCES), ("/공개", errno.EISDIR)):
        with pytest.raises(GateError) as exc:
            inspector.inspect(PEER, path)
        assert exc.value.errno == err


# --- 캐시 (완료 조건: 다시 읽으면 Jev를 호출하지 않는다) ---

def test_rereading_does_not_call_jev_again(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path)
    first = inspector.inspect(PEER, "/연락처.txt", "요약")
    second = inspector.inspect(PEER, "/연락처.txt", "요약")
    assert len(jev.calls) == 1
    assert (first.judgment.cached, second.judgment.cached) == (False, True)
    assert second.data == first.data
    other_pid = Peer(pid=9999, uid=501, exe="/usr/local/bin/claude")  # gate cat은 부를 때마다 새 프로세스
    assert inspector.inspect(other_pid, "/연락처.txt", "요약").judgment.cached
    assert len(jev.calls) == 1


def test_cache_misses_when_context_policy_or_content_changes(source, tmp_path):
    inspector, jev, policy_path = make(source, tmp_path)
    inspector.inspect(PEER, "/회의.txt", "요약")
    inspector.inspect(PEER, "/회의.txt", "다른 목적")
    inspector.inspect(Peer(1, 501, "/usr/bin/python3"), "/회의.txt", "요약")
    inspector.inspect(PEER, "/비공개/메모.txt", "요약")              # 같은 내용, 다른 경로
    assert len(jev.calls) == 4
    write_entries(policy_path, [Entry(context="병원 기록은 보여주지 마")])
    inspector.inspect(PEER, "/회의.txt", "요약")
    assert len(jev.calls) == 5
    assert "[사용자 정책]" in jev.calls[-1].question and "병원 기록은 보여주지 마" in jev.calls[-1].question
    (source / "회의.txt").write_text(PLAIN + "추가\n", encoding="utf-8")
    inspector.inspect(PEER, "/회의.txt", "요약")
    assert len(jev.calls) == 6


class TaggedJev(MockJevClient):
    """모델 식별값(cache_tag)이 있는 Mock. tag를 바꾸면 체크포인트를 바꾼 것과 같다."""
    tag = "v1"

    def cache_tag(self):
        if isinstance(self.tag, Exception):
            raise self.tag
        return self.tag


def test_cache_misses_when_model_changes(source, tmp_path):
    jev = TaggedJev("fixed", label="safe", confidence=0.99)
    inspector, _, _ = make(source, tmp_path, jev=jev)
    inspector.inspect(PEER, "/회의.txt", "요약")
    assert inspector.inspect(PEER, "/회의.txt", "요약").judgment.cached
    assert len(jev.calls) == 1
    jev.tag = "v2"
    verdict = inspector.inspect(PEER, "/회의.txt", "요약")
    assert not verdict.judgment.cached and len(jev.calls) == 2


def test_model_tag_failure_denies_without_calling_jev(source, tmp_path):
    jev = TaggedJev("fixed", label="safe", confidence=0.99)
    jev.tag = JevError("Laya 모델 폴더에서 questions.json·model.safetensors를 찾을 수 없습니다: FileNotFoundError")
    inspector, _, _ = make(source, tmp_path, jev=jev)
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert verdict.judgment.action == "deny" and "모델 폴더" in verdict.judgment.reason
    assert jev.calls == []


# --- 그 밖 ---

def test_empty_file_is_allowed_without_jev(source, tmp_path):
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/빈.txt")
    assert (verdict.judgment.action, verdict.data) == ("allow", b"")
    assert jev.calls == []


def test_large_file_is_judged_in_chunks_and_strictest_wins(source, tmp_path):
    lines = [f"{i}번째 줄입니다. 평범한 내용입니다.\n" for i in range(200)]
    lines[150] = "이 줄은 위험합니다.\n"
    (source / "긴글.txt").write_text("".join(lines), encoding="utf-8")

    class ByMarker:
        def __init__(self):
            self.chunks = []

        def judge(self, text, context):
            self.chunks.append(text)
            return JevDecision("highly_sensitive" if "위험" in text else "safe", 0.99)

    jev = ByMarker()
    inspector, _, _ = make(source, tmp_path, jev=jev, entries=[Entry(settings={"max_inspect_bytes": 1000})])
    verdict = inspector.inspect(PEER, "/긴글.txt")
    assert verdict.judgment.action == "deny"
    assert verdict.judgment.jev_calls == len(jev.chunks) > 1
    assert all(len(c.encode()) <= 1000 for c in jev.chunks)
    assert "위험" in jev.chunks[-1]  # deny가 나오면 더 묻지 않는다
    assert "".join(jev.chunks) in "".join(lines)


def test_unknown_encoding_is_denied_without_calling_jev(source, tmp_path):
    # UTF-16 BOM처럼 시작하지만 UTF-8 글 + 잘못된 바이트다. 어느 인코딩으로도 풀리지 않는다(2026-11-01 사용자 결정: 차단).
    # 예전에는 UTF-8로 보고 깨진 글자를 Jev에 넘겼다
    data = b"\xff\xfe" + "연락처 010-1234-5678".encode() + b"\x80"
    (source / "깨진.txt").write_bytes(data)
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/깨진.txt")
    assert (verdict.judgment.action, verdict.judgment.stage, verdict.data) == ("deny", "encoding", None)
    assert "인코딩" in verdict.judgment.reason
    assert verdict.judgment.content_sha256 is not None and jev.calls == []


# --- 인코딩 (2026-11-01 사용자 결정): UTF-16·EUC-KR은 글자로 풀어 검사하고 원래 인코딩으로 돌려준다 ---

SECRETS = "password: hunter2-real\nAPI_TOKEN=sk-live-abcdefghijklmnopqrstuvwx\n"
SECRETS_SHOWN = "password: *******-****\nAPI_TOKEN=**-****-************************\n"


@pytest.mark.parametrize("codec", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_secrets_are_masked_and_returned_in_utf16(source, tmp_path, codec):
    # 예전에는 UTF-8로 보고 풀어서 아무것도 잡지 못했고 Jev가 safe면 원문이 그대로 나갔다(plans/next-tasks-plan.md 1.5)
    data = SECRETS.encode(codec)
    (source / "설정.txt").write_bytes(data)
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/설정.txt")
    assert verdict.judgment.action == "mask"
    assert dict(verdict.judgment.findings) == {"secret": 2}
    assert verdict.judgment.reason.endswith("(UTF-16 파일)")
    assert verdict.data == SECRETS_SHOWN.encode(codec)        # BOM·바이트 순서 그대로
    assert jev.calls[0].text == SECRETS_SHOWN                 # Jev는 0 바이트 없는 글자를 본다
    assert verdict.codec in ("utf-16-le", "utf-16-be")


def test_utf16_card_number_is_masked(source, tmp_path):
    (source / "카드16.txt").write_bytes(WITH_CARD.encode("utf-16"))
    inspector, _, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/카드16.txt")
    assert verdict.data == "결제 카드 **** **** **** ****\n".encode("utf-16")


def test_euc_kr_document_is_checked_and_returned_in_euc_kr(source, tmp_path):
    doc = "상담 기록\n연락처 010-1234-5678\n비밀번호: 사과나무\n"
    (source / "상담.txt").write_bytes(doc.encode("cp949"))
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/상담.txt")
    assert dict(verdict.judgment.findings) == {"mobile": 1, "secret": 1}
    shown = verdict.data.decode("cp949")
    assert "010" not in shown and "사과나무" not in shown and shown.startswith("상담 기록\n")
    assert "(EUC-KR 파일)" in verdict.judgment.reason
    assert "상담 기록" in jev.calls[0].text


def test_unmasked_utf16_document_is_returned_unchanged(source, tmp_path):
    data = PLAIN.encode("utf-16")
    (source / "회의16.txt").write_bytes(data)
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/회의16.txt")
    assert (verdict.judgment.action, verdict.data) == ("allow", data)
    assert jev.calls[0].text == PLAIN


def test_number_rules_apply_to_utf16(source, tmp_path):
    (source / "카드16.txt").write_bytes(WITH_CARD.encode("utf-16"))
    inspector, jev, _ = make(source, tmp_path, entries=[Entry(settings={"numbers.card.action": "deny"})])
    verdict = inspector.inspect(PEER, "/카드16.txt")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "number") and jev.calls == []


def test_search_reads_utf16_content(source, tmp_path):
    (source / "회의16.txt").write_bytes(PLAIN.encode("utf-16"))
    (source / "연락처16.txt").write_bytes(WITH_PHONE.encode("utf-16-be"))
    inspector, _, _ = make(source, tmp_path)
    result = search(inspector, PEER, "/", "안건")
    assert {(m["path"], m["text"]) for m in result["matches"]} >= {("/회의16.txt", "안건은 예산입니다.")}
    found = search(inspector, PEER, "/연락처16.txt", "연락처")["matches"]
    assert [m["text"] for m in found] == ["연락처는 ***-****-****입니다."]


BINARY = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256))


def test_binary_is_not_judged_and_asks_a_human(source, tmp_path):
    (source / "사진.png").write_bytes(BINARY)
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/사진.png")
    j = verdict.judgment
    assert (j.action, j.stage, verdict.data) == ("ask_human", "encoding", None)
    assert "바이너리" in j.reason and jev.calls == []
    assert "사람 승인이 필요합니다" in Blocked(j).message


def test_binary_approved_returns_original_bytes(source, tmp_path):
    (source / "사진.png").write_bytes(BINARY)
    inspector, channel, jev, _ = asking(source, tmp_path, "once")
    verdict = inspector.inspect(PEER, "/사진.png")
    assert (verdict.judgment.action, verdict.judgment.approval, verdict.data) == ("allow", "once", BINARY)
    assert "바이너리" in channel.requests[0].reason and jev.calls == []


def test_binary_denied_by_user(source, tmp_path):
    (source / "사진.png").write_bytes(BINARY)
    inspector, _, _, _ = asking(source, tmp_path, "deny")
    verdict = inspector.inspect(PEER, "/사진.png")
    assert (verdict.judgment.action, verdict.data) == ("deny", None)


def test_path_rules_come_before_encoding(source, tmp_path):
    (source / "공개" / "사진.png").write_bytes(BINARY)
    (source / "비공개" / "깨진.txt").write_bytes(b"\xff\xfe\x80")
    inspector, _, _ = make(source, tmp_path, entries=[Entry(path_rule=PathRule("공개/**", "allow")),
                                                      Entry(path_rule=PathRule("비공개/**", "deny"))])
    assert inspector.inspect(PEER, "/공개/사진.png").data == BINARY
    assert inspector.inspect(PEER, "/비공개/깨진.txt").judgment.stage == "path"


@pytest.mark.parametrize("text", ["", "a" * 10, "가" * 1000, "😀a" * 700, ("짧은 줄\n" * 400)])
def test_split_utf8(text):
    data = text.encode()
    chunks = split_utf8(data, 1000)
    assert b"".join(chunks) == data
    assert all(len(c) <= 1000 for c in chunks)
    for c in chunks:
        c.decode("utf-8")  # 글자 중간에서 자르지 않는다


# --- search ---

def test_search_only_sees_allowed_or_masked_content(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, entries=[Entry(settings={"numbers.card.action": "deny"})])
    result = search(inspector, PEER, "/", "입니다")
    paths = {m["path"] for m in result["matches"]}
    assert "/회의.txt" in paths and "/연락처.txt" in paths
    assert {s["path"] for s in result["skipped"]} >= {"/설정.env", "/카드.txt", "/회의-링크.txt"}
    assert all(s["errno"] == "EACCES" for s in result["skipped"])
    assert result["truncated"] is False

    assert search(inspector, PEER, "/", "1234")["matches"] == []       # 가려진 값은 찾을 수 없다
    assert search(inspector, PEER, "/", "4111")["matches"] == []       # 차단된 파일은 보지 않는다
    assert search(inspector, PEER, "/", "TOKEN")["matches"] == []


def test_search_options_and_limits(source, tmp_path):
    (source / "영문.txt").write_text("Hello\nhello\n" + "x" * 1000 + "hello\n", encoding="utf-8")
    inspector, _, _ = make(source, tmp_path)
    assert len(search(inspector, PEER, "/영문.txt", "hello")["matches"]) == 2
    assert len(search(inspector, PEER, "/영문.txt", "HELLO", ignore_case=True)["matches"]) == 3
    long_line = search(inspector, PEER, "/영문.txt", "xxx")["matches"][0]["text"]
    assert len(long_line) == MAX_LINE_CHARS + 1 and long_line.endswith("…")
    limited = search(inspector, PEER, "/", "입니다", max_results=1)
    assert len(limited["matches"]) == 1 and limited["truncated"] is True


@pytest.mark.parametrize("pattern, max_results", [("", 10), ("a\nb", 10), (None, 10), ("x" * 1001, 10),
                                                  ("a", 0), ("a", 1001), ("a", True)])
def test_search_rejects_bad_arguments(source, tmp_path, pattern, max_results):
    inspector, _, _ = make(source, tmp_path)
    with pytest.raises(GateError) as exc:
        search(inspector, PEER, "/", pattern, max_results=max_results)
    assert exc.value.errno == errno.EINVAL


def test_search_survives_directory_symlink_loops(source, tmp_path):
    (source / "공개" / "위로").symlink_to(source)
    inspector, _, _ = make(source, tmp_path)
    result = search(inspector, PEER, "/", "예산")
    assert result["files_searched"] < 50
    assert any(m["path"] == "/회의.txt" for m in result["matches"])


def test_masked_text_shape_matches_detectors(source, tmp_path):
    inspector, _, _ = make(source, tmp_path)
    assert inspector.inspect(PEER, "/카드.txt").data.decode() == mask(WITH_CARD).text


# --- Phase 4 보완: 설정 파일의 비밀값 ---

CONFIG = (
    "database:\n"
    "  url: postgres://admin:S3cretPass@db.example.com:5432/app\n"
    "  password: hunter2-example\n"
    "api_key: sk-test-0000000000000000000000000000\n"
    "owner_email: hong@example.com\n"
)


def test_config_secrets_are_masked_and_structure_kept(source, tmp_path):
    (source / "app.yaml").write_text(CONFIG, encoding="utf-8")
    inspector, jev, _ = make(source, tmp_path)
    verdict = inspector.inspect(PEER, "/app.yaml")
    assert verdict.judgment.action == "mask"
    assert dict(verdict.judgment.findings) == {"secret": 3, "email": 1}
    text = verdict.data.decode()
    for secret in ("S3cretPass", "hunter2", "sk-test-0000", "hong@"):
        assert secret not in text
    assert "postgres://admin:**********@db.example.com:5432/app" in text
    assert len(verdict.data) == len(CONFIG.encode())
    assert "hunter2" not in jev.calls[0].text  # Jev도 가린 내용만 본다
    assert "비밀값(비밀번호·키·토큰)" in verdict.judgment.reason


def test_secret_rule_deny_and_credential_paths(source, tmp_path):
    (source / "app.yaml").write_text(CONFIG, encoding="utf-8")
    (source / ".aws").mkdir()
    (source / ".aws" / "credentials").write_text("[default]\naws_access_key_id = x\n", encoding="utf-8")
    inspector, jev, _ = make(source, tmp_path, entries=[Entry(settings={"numbers.secret.action": "deny"})])
    verdict = inspector.inspect(PEER, "/app.yaml")
    assert (verdict.judgment.action, verdict.judgment.stage) == ("deny", "number")
    assert "비밀값" in verdict.judgment.reason
    assert inspector.inspect(PEER, "/.aws/credentials").judgment.stage == "path"
    assert jev.calls == []
    assert inspector.inspect(PEER, "/연락처.txt").judgment.action == "mask"  # 비밀값이 없는 파일은 그대로


# --- Phase 5: 사람 승인 ---

UNSURE = MockJevClient("fixed", label="sensitive", confidence=0.7)  # 임계값 사이 → ask_human
FAST = Entry(settings={"approval.timeout_seconds": 1.0})


class Scripted:
    """요청을 기록하고 정해 둔 답을 바로 하는 창구. None이면 답하지 않는다(무응답)."""
    name = "dialog"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def present(self, pending):
        self.requests.append(pending.request)
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            pending.answer(answer, self.name)


def asking(source, tmp_path, *answers, jev=None, entries=(), clock=None):
    channel = Scripted(*answers)
    sessions = SessionApprovals(clock) if clock else None
    approvals = Approvals([channel], sessions=sessions)
    inspector, jev, policy_path = make(source, tmp_path, jev=jev or MockJevClient("fixed", label="sensitive",
                                                                                   confidence=0.7),
                                       entries=[FAST, *entries], approvals=approvals)
    return inspector, channel, jev, policy_path


def test_approved_once_returns_content_and_records_who(source, tmp_path):
    inspector, channel, _, _ = asking(source, tmp_path, "once")
    verdict = inspector.inspect(PEER, "/회의.txt", "요약")
    j = verdict.judgment
    assert (j.action, j.stage, j.approval, j.approved_by) == ("allow", "human", "once", "dialog")
    assert verdict.data == PLAIN.encode()
    assert j.reason == "사용자가 승인했습니다 (Jev 판단(sensitive, 확신도 0.70)이 확실하지 않습니다)"
    assert (j.jev_label, j.jev_confidence) == ("sensitive", 0.7)
    [req] = channel.requests
    assert (req.path, req.folder, req.exe, req.pid, req.purpose) == ("/회의.txt", "/", PEER.exe, PEER.pid, "요약")
    assert (req.timeout_seconds, req.session_minutes, dict(req.findings)) == (1.0, 60, {})


def test_approval_still_masks_numbers(source, tmp_path):
    inspector, channel, _, _ = asking(source, tmp_path, "once")
    verdict = inspector.inspect(PEER, "/연락처.txt")
    assert (verdict.judgment.action, verdict.judgment.approval) == ("mask", "once")
    assert verdict.data == "연락처는 ***-****-****입니다.\n".encode()
    assert dict(channel.requests[0].findings) == {"mobile": 1}


def test_denied_by_user(source, tmp_path):
    inspector, _, _, _ = asking(source, tmp_path, "deny")
    verdict = inspector.inspect(PEER, "/회의.txt")
    j = verdict.judgment
    assert (j.action, j.stage, j.approval, j.approved_by, j.reason) == \
        ("deny", "human", "deny", "dialog", "사용자가 거부했습니다")
    assert verdict.data is None and Blocked(j).message == "사용자가 거부했습니다"


def test_no_answer_times_out_and_denies(source, tmp_path):
    inspector, _, _, _ = asking(source, tmp_path, None)
    verdict = inspector.inspect(PEER, "/회의.txt")
    j = verdict.judgment
    assert (j.action, j.stage, j.approval, j.approved_by) == ("deny", "human", "timeout", None)
    assert j.reason.startswith("1초 안에 승인이 없어 차단했습니다")
    assert verdict.data is None


def test_once_asks_again_but_jev_is_cached_without_the_approval(source, tmp_path):
    inspector, channel, jev, _ = asking(source, tmp_path, "once", "deny")
    assert inspector.inspect(PEER, "/회의.txt").judgment.action == "allow"
    second = inspector.inspect(PEER, "/회의.txt").judgment
    assert (second.action, second.approval, second.cached) == ("deny", "deny", True)
    assert len(channel.requests) == 2 and len(jev.calls) == 1
    # 캐시에는 Jev 판단만 있다(승인 결과를 섞지 않는다)
    policy = inspector.policy_source.current()
    digest = second.content_sha256
    assert inspector.cache.get(CacheKey.build(digest, policy.version, "/회의.txt", PEER.exe, None)).action == "ask_human"


def test_folder_approval_is_remembered_for_that_folder_program_and_policy(source, tmp_path):
    now = [0.0]
    inspector, channel, _, policy_path = asking(source, tmp_path, "session", "once", "once", "once",
                                                clock=lambda: now[0])
    (source / "진료" / "다른.txt").write_text(PLAIN, encoding="utf-8")
    assert inspector.inspect(PEER, "/진료/메모.txt").judgment.approval == "session"
    again = inspector.inspect(PEER, "/진료/다른.txt")  # 같은 폴더의 다른 파일
    assert (again.judgment.action, again.judgment.stage, again.judgment.approval) == ("allow", "human", "remembered")
    assert again.data == PLAIN.encode() and len(channel.requests) == 1

    assert inspector.inspect(PEER, "/비공개/메모.txt").judgment.approval == "once"  # 다른 폴더
    other = Peer(pid=1, uid=PEER.uid, exe="/usr/bin/other")
    assert inspector.inspect(other, "/진료/메모.txt").judgment.approval == "once"   # 다른 프로그램
    assert inspector.inspect(PEER, "/회의.txt").judgment.approval == "once"          # 상위 폴더도 다시 묻는다
    assert len(channel.requests) == 4

    now[0] += 60 * 60  # 1시간이 지나면 다시 묻는다
    channel.answers = ["once"]
    assert inspector.inspect(PEER, "/진료/메모.txt").judgment.approval == "once"


def test_folder_approval_is_forgotten_when_policy_changes(source, tmp_path):
    inspector, channel, _, policy_path = asking(source, tmp_path, "session", "deny")
    assert inspector.inspect(PEER, "/진료/메모.txt").judgment.approval == "session"
    assert inspector.inspect(PEER, "/진료/메모.txt").judgment.approval == "remembered"
    write_entries(policy_path, [FAST, Entry(context="병원 기록은 보여주지 마")])
    assert inspector.inspect(PEER, "/진료/메모.txt").judgment.approval == "deny"


def test_session_minutes_zero_does_not_offer_folder_approval(source, tmp_path):
    inspector, channel, _, _ = asking(source, tmp_path, "once",
                                      entries=[Entry(settings={"approval.session_minutes": 0})])
    inspector.inspect(PEER, "/회의.txt")
    assert channel.requests[0].session_minutes == 0


def test_path_and_number_ask_human_rules_are_asked(source, tmp_path):
    safe = MockJevClient("fixed", label="safe", confidence=0.99)
    inspector, channel, _, _ = asking(source, tmp_path, "once", "once", jev=safe, entries=[
        Entry(path_rule=PathRule("진료/**", "ask_human")), Entry(settings={"numbers.card.action": "ask_human"})])
    by_path = inspector.inspect(PEER, "/진료/메모.txt")
    assert (by_path.judgment.action, by_path.judgment.approval, by_path.data) == ("allow", "once", PLAIN.encode())
    assert channel.requests[0].reason == "경로 규칙에 따라 사람에게 물어봅니다"
    card = inspector.inspect(PEER, "/카드.txt")
    assert (card.judgment.action, card.data) == ("mask", mask(WITH_CARD).text.encode())
    assert channel.requests[1].reason == "정책에 따라 카드번호이(가) 있는 파일은 사람에게 물어봅니다"


def test_files_that_would_be_denied_anyway_are_not_asked(source, tmp_path):
    inspector, channel, _, _ = asking(source, tmp_path, "once", entries=[
        Entry(path_rule=PathRule("진료/**", "ask_human")), Entry(settings={"numbers.card.action": "deny"})])
    assert inspector.inspect(PEER, "/카드.txt").judgment.action == "deny"      # 번호 deny
    assert inspector.inspect(PEER, "/설정.env").judgment.action == "deny"      # 경로 deny
    sure = MockJevClient("fixed", label="highly_sensitive", confidence=0.99)
    inspector2, channel2, _, _ = asking(source, tmp_path / "b", "once", jev=sure,
                                        entries=[Entry(path_rule=PathRule("진료/**", "ask_human"))])
    assert inspector2.inspect(PEER, "/진료/메모.txt").judgment.action == "deny"  # Jev deny
    assert channel.requests == [] and channel2.requests == []


def test_empty_file_under_ask_human_path_is_still_asked(source, tmp_path):
    (source / "진료" / "빈.txt").write_bytes(b"")
    inspector, channel, jev, _ = asking(source, tmp_path, "once",
                                        entries=[Entry(path_rule=PathRule("진료/**", "ask_human"))])
    verdict = inspector.inspect(PEER, "/진료/빈.txt")
    assert (verdict.judgment.action, verdict.data) == ("allow", b"")
    assert len(channel.requests) == 1 and jev.calls == []


def test_ask_false_never_asks_but_uses_folder_approval(source, tmp_path):
    inspector, channel, _, _ = asking(source, tmp_path, "session")
    assert inspector.inspect(PEER, "/진료/메모.txt", ask=False).judgment.action == "ask_human"
    assert channel.requests == []
    inspector.inspect(PEER, "/진료/메모.txt")
    assert inspector.inspect(PEER, "/진료/메모.txt", ask=False).judgment.approval == "remembered"


def test_search_does_not_ask_and_says_how_to_get_approval(source, tmp_path):
    inspector, channel, _, _ = asking(source, tmp_path, "session")
    result = search(inspector, PEER, "/", "회의")
    assert result["matches"] == [] and channel.requests == []
    skipped = {s["path"]: s["message"] for s in result["skipped"]}
    assert skipped["/진료/메모.txt"].endswith("search는 묻지 않습니다. gate cat으로 열면 사용자에게 묻습니다")
    inspector.inspect(PEER, "/진료/메모.txt")  # 이 폴더 허용
    after = search(inspector, PEER, "/", "회의")
    assert [m["path"] for m in after["matches"]] == ["/진료/메모.txt"]


def test_without_approvals_ask_human_is_blocked_as_before(source, tmp_path):
    inspector, _, _ = make(source, tmp_path, jev=UNSURE)
    verdict = inspector.inspect(PEER, "/회의.txt")
    assert (verdict.judgment.action, verdict.judgment.approval) == ("ask_human", None)
    assert Blocked(verdict.judgment).message == "사람 승인이 필요합니다 (Jev 판단(sensitive, 확신도 0.70)이 확실하지 않습니다)"
    skipped = search(inspector, PEER, "/회의.txt", "회의")["skipped"]
    assert "gate cat" not in skipped[0]["message"]
