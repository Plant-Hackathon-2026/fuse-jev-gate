"""gate.detectors: 정규식 탐지와 같은 길이(UTF-8 바이트) 마스킹. 모든 값은 가짜 예시다."""

import dataclasses
import random
import time

import pytest

from gate.detectors import (
    ACCOUNT_WINDOW_BEFORE,
    DEFAULT_VISIBLE,
    KINDS,
    VISIBLE_CHOICES,
    Finding,
    MaskResult,
    check_visible,
    detect,
    mask,
    mask_bytes,
    same_length_stars,
)


def kinds(text: str) -> list[str]:
    return [f.kind for f in detect(text)]


def byte_len(s: str) -> int:
    return len(s.encode("utf-8"))


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch) * (2 if i % 2 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


# --- 종류별 마스킹 모양 ---

@pytest.mark.parametrize("text, expected, kind", [
    # 기본은 모든 종류를 전부 가린다(구분자만 남김). 공개 범위는 test_visible_options
    # 주민·외국인등록번호
    ("900101-1234567", "******-*******", "rrn"),
    ("9001011234567", "*************", "rrn"),
    ("900101 1234567", "****** *******", "rrn"),
    ("900101-5234567", "******-*******", "foreigner_rrn"),
    # 휴대폰 (+, 괄호는 구분자가 아니라서 가린다)
    ("010-1234-5678", "***-****-****", "mobile"),
    ("01012345678", "***********", "mobile"),
    ("010.1234.5678", "***.****.****", "mobile"),
    ("010 1234 5678", "*** **** ****", "mobile"),
    ("011-123-4567", "***-***-****", "mobile"),
    ("+82 10-1234-5678", "*** **-****-****", "mobile"),
    ("+821012345678", "*************", "mobile"),
    # 일반 전화
    ("02-123-4567", "**-***-****", "phone"),
    ("02-1234-5678", "**-****-****", "phone"),
    ("031-123-4567", "***-***-****", "phone"),
    ("(02) 123-4567", "**** ***-****", "phone"),
    ("02)123-4567", "******-****", "phone"),
    ("064-123-4567", "***-***-****", "phone"),
    ("070-1234-5678", "***-****-****", "phone"),
    ("0212345678", "**********", "phone"),
    # 이메일 (@, .은 구분자라 남는다)
    ("hong@example.com", "****@*******.***", "email"),
    ("hong.gildong@mail.example.co.kr", "****.*******@****.*******.**.**", "email"),
    ("kim_01+tag@example.org", "**********@*******.***", "email"),
    # 카드
    ("4111 1111 1111 1111", "**** **** **** ****", "card"),
    ("4111-1111-1111-1111", "****-****-****-****", "card"),
    ("4111111111111111", "****************", "card"),
    ("5555555555554444", "****************", "card"),
    ("3782 822463 10005", "**** ****** *****", "card"),
    ("30569309025904", "**************", "card"),
    # 계좌 (근처 키워드 필요)
    ("계좌번호: 110-123-456789", "계좌번호: ***-***-******", "account"),
    ("국민은행 123456-01-123456", "국민은행 ******-**-******", "account"),
    ("입금 계좌 3333-01-1234567", "입금 계좌 ****-**-*******", "account"),
    ("농협 352-1234-5678-93", "농협 ***-****-****-**", "account"),
    ("카카오뱅크 3333011234567", "카카오뱅크 *************", "account"),
    ("110123456789 (신한)", "************ (신한)", "account"),
])
def test_mask_shape_per_kind(text, expected, kind):
    result = mask(text)
    assert result.text == expected
    assert [f.kind for f in result.findings] == [kind]
    assert byte_len(result.text) == byte_len(text)


def test_kinds_cover_interface():
    assert set(KINDS) == {"rrn", "foreigner_rrn", "mobile", "phone", "email", "card", "account", "secret"}


@pytest.mark.parametrize("text, visible, expected", [
    # 주민·외국인등록번호
    ("900101-1234567", {"rrn": "none"}, "******-*******"),
    ("900101-1234567", {"rrn": "birth"}, "900101-*******"),
    ("900101-1234567", {"rrn": "birth_gender"}, "900101-1******"),
    ("9001011234567", {"rrn": "birth"}, "900101*******"),
    ("9001011234567", {"rrn": "birth_gender"}, "9001011******"),
    ("900101 1234567", {"rrn": "birth_gender"}, "900101 1******"),
    ("900101-5234567", {"foreigner_rrn": "birth"}, "900101-*******"),
    ("900101-5234567", {"foreigner_rrn": "birth_gender"}, "900101-5******"),
    ("900101-5234567", {"rrn": "birth_gender"}, "******-*******"),     # 외국인등록번호는 따로 정한다
    # 휴대폰: 앞자리
    ("010-1234-5678", {"mobile": "prefix"}, "010-****-****"),
    ("01012345678", {"mobile": "prefix"}, "010********"),
    ("010.1234.5678", {"mobile": "prefix"}, "010.****.****"),
    ("011-123-4567", {"mobile": "prefix"}, "011-***-****"),
    ("+82 10-1234-5678", {"mobile": "prefix"}, "+82 10-****-****"),
    ("+821012345678", {"mobile": "prefix"}, "+8210********"),
    # 일반 전화: 지역번호(괄호 포함)
    ("02-123-4567", {"phone": "area"}, "02-***-****"),
    ("031-123-4567", {"phone": "area"}, "031-***-****"),
    ("(02) 123-4567", {"phone": "area"}, "(02) ***-****"),
    ("02)123-4567", {"phone": "area"}, "02)***-****"),
    ("070-1234-5678", {"phone": "area"}, "070-****-****"),
    ("0212345678", {"phone": "area"}, "02********"),
    # 이메일: 최상위 도메인
    ("hong@example.com", {"email": "tld"}, "****@*******.com"),
    ("hong.gildong@mail.example.co.kr", {"email": "tld"}, "****.*******@****.*******.**.kr"),
    # 카드: 끝 4자리
    ("4111 1111 1111 1111", {"card": "last4"}, "**** **** **** 1111"),
    ("4111-1111-1111-1111", {"card": "last4"}, "****-****-****-1111"),
    ("4111111111111111", {"card": "last4"}, "************1111"),
    ("3782 822463 10005", {"card": "last4"}, "**** ****** *0005"),
    # 계좌는 선택지가 none뿐이다
    ("계좌번호: 110-123-456789", {"account": "none"}, "계좌번호: ***-***-******"),
])
def test_visible_options(text, visible, expected):
    result = mask(text, visible=visible)
    assert result.text == expected
    assert byte_len(result.text) == byte_len(text)


def test_visible_default_is_none_for_every_kind():
    assert DEFAULT_VISIBLE == "none"
    assert set(VISIBLE_CHOICES) == set(KINDS)
    assert all(choices[0] == "none" for choices in VISIBLE_CHOICES.values())
    assert check_visible() == {kind: "none" for kind in KINDS}
    assert check_visible({"card": "last4"}) == {**{kind: "none" for kind in KINDS}, "card": "last4"}
    text = SENTENCES[1]
    assert mask(text).text == mask(text, visible={kind: "none" for kind in KINDS}).text


def test_visible_does_not_change_other_kinds():
    text = "900101-1234567 010-1234-5678 hong@example.com"
    assert mask(text, visible={"rrn": "birth_gender"}).text == "900101-1****** ***-****-**** ****@*******.***"
    assert mask(text, visible={"mobile": "prefix", "email": "tld"}).text == "******-******* 010-****-**** ****@*******.com"


@pytest.mark.parametrize("visible", [
    {"rrn": "full"}, {"rrn": ""}, {"rrn": "BIRTH"}, {"rrn": None},
    {"card": "birth"}, {"account": "last4"}, {"mobile": "area"},
    {"unknown": "none"}, ["rrn"], "none",
])
def test_visible_rejects_unknown_kinds_and_values(visible):
    with pytest.raises(ValueError):
        mask("900101-1234567", visible=visible)
    with pytest.raises(ValueError):
        mask_bytes(b"no pii", visible=visible)


def test_finding_is_frozen_and_positions_are_char_indices():
    text = "상담자 연락처는 010-1234-5678, 메일은 hong@example.com입니다."
    findings = detect(text)
    assert [text[f.start:f.end] for f in findings] == ["010-1234-5678", "hong@example.com"]
    assert findings == sorted(findings, key=lambda f: f.start)
    with pytest.raises(dataclasses.FrozenInstanceError):
        findings[0].start = 0  # type: ignore[misc]
    assert isinstance(mask(text), MaskResult)
    assert mask(text).findings == tuple(findings)


def test_korean_particles_attached_to_values():
    text = "010-1234-5678로 연락하거나 hong@example.com으로 보내 주세요"
    assert kinds(text) == ["mobile", "email"]
    assert mask(text).text == "***-****-****로 연락하거나 ****@*******.***으로 보내 주세요"


# --- 주민·외국인등록번호 ---

@pytest.mark.parametrize("text, kind", [
    ("900101-1234567", "rrn"),      # 1900년대 남
    ("900101-2234567", "rrn"),      # 1900년대 여
    ("000101-3234567", "rrn"),      # 2000년대
    ("000101-4234567", "rrn"),
    ("850101-9234567", "rrn"),      # 1800년대
    ("850101-0234567", "rrn"),
    ("900101-5234567", "foreigner_rrn"),
    ("900101-6234567", "foreigner_rrn"),
    ("000101-7234567", "foreigner_rrn"),
    ("000101-8234567", "foreigner_rrn"),
])
def test_rrn_seventh_digit_decides_kind(text, kind):
    assert kinds(text) == [kind]


@pytest.mark.parametrize("text", [
    "901301-1234567",  # 13월
    "900100-1234567",  # 0일
    "900230-1234567",  # 2월 30일
    "000229-1234567",  # 1900년은 윤년이 아니다
    "010229-3234567",  # 2001년은 윤년이 아니다
    "991231-4234567",  # 2099년, 미래
    "991231-8234567",  # 외국인 2099년, 미래
])
def test_rrn_rejects_invalid_or_future_birth_dates(text):
    assert detect(text) == []


@pytest.mark.parametrize("text", ["000229-3234567", "960229-1234567"])
def test_rrn_accepts_leap_days_by_century(text):
    assert kinds(text) == ["rrn"]


# --- 경계: 더 긴 숫자열의 일부는 잡지 않는다 ---

@pytest.mark.parametrize("text", [
    "19001011234567",        # 14자리 안의 주민번호 모양
    "1900101-1234567",
    "900101-12345678",
    "9010-1234-5678",        # 앞에 숫자가 붙은 휴대폰 모양
    "12010-1234-5678",
    "010-1234-56789",
    "02-123-45678",
    "41111111111111111111",  # 20자리
    "411111111111",          # 12자리
])
def test_no_match_inside_longer_digit_runs(text):
    assert detect(text) == []


# --- 전화: 대표번호 등은 일부러 뺀다 ---

@pytest.mark.parametrize("text", [
    "고객센터 1588-1234",
    "1544 1234",
    "1600-1234",
    "080-123-4567",    # 수신자 부담
    "0505-123-4567",   # 안심번호
    "039-123-4567",    # 없는 지역번호
    "012-1234-5678",   # 휴대폰 앞자리가 아님
])
def test_representative_and_special_numbers_are_not_phones(text):
    assert detect(text) == []


# --- 개인정보가 아닌 숫자·문자열 ---

@pytest.mark.parametrize("text", [
    "2026-10-31",
    "2026.10.31",
    "20261031",
    "2026-10-31 14:30",
    "금액 1,234,567원",
    "주문번호 2026103112345",
    "주문번호 ORD-2026-1031-0001",
    "주문번호 110-123-456789",        # 계좌 모양이지만 근처에 계좌 키워드가 없다
    "운송장 123456789012",
    "4111 1111 1111 1112",            # Luhn 실패
    "user@localhost",
    "pkg@1.2.3",
    "@example.com",
    "공일공 일이삼사 오육칠팔",          # 한글로 쓴 숫자는 범위 밖 (TODO)
    "",
])
def test_non_pii_is_not_detected(text):
    assert detect(text) == []
    assert mask(text) == MaskResult(text, ())


@pytest.mark.parametrize("number", ["2026103100000007", "2026 1031 0000 0007", "2026103100007"])
def test_card_skips_date_prefixed_order_numbers(number):
    assert luhn_ok(number.replace(" ", ""))  # Luhn은 통과하지만 날짜로 시작하므로 주문번호로 본다
    assert detect(f"주문번호 {number}") == []


# --- 계좌: 근처 키워드 필요 ---

def test_account_requires_keyword_within_window():
    near = "신한은행 110-123-456789"
    far = "신한은행 지점 안내입니다. 오늘 접수된 문의 번호는 110-123-456789"
    assert far.index("110-") - (far.index("은행") + len("은행")) > ACCOUNT_WINDOW_BEFORE
    assert kinds(near) == ["account"]
    assert detect(far) == []


@pytest.mark.parametrize("text", [
    "입금액 12345678901원",           # 금액
    "입금 ₩12345678901",
    "입금일시 20261031143000",        # 일시
    "입금 확인 주문번호 2026103100007",  # 날짜로 시작하는 주문번호
    "입금일 2026-10-31",
    "계좌 123-45-678",                # 10자리 미만
])
def test_account_false_positive_guards(text):
    assert detect(text) == []


# --- 겹칠 때 고르는 규칙 ---

@pytest.mark.parametrize("text, expected", [
    ("신한카드 4111 1111 1111 1111", ["card"]),   # 계좌 키워드(신한)가 있어도 카드
    ("계좌 30569309025904", ["card"]),            # 같은 범위면 카드 > 계좌
    ("계좌 010-1234-5678", ["mobile"]),           # 휴대폰 > 계좌
    ("계좌 02-1234-5678", ["phone"]),
    ("계좌 900101-1234567", ["rrn"]),             # 주민번호 > 계좌
    ("01012345678@example.com", ["email"]),       # 더 긴 범위
])
def test_overlap_resolution(text, expected):
    assert kinds(text) == expected


def test_select_handles_chained_overlaps_and_adjacent_spans():
    from gate.detectors import _Hit, _select

    # A·B·C가 사슬처럼 이어서 겹친다 → 가장 긴 C, 그다음 C와 겹치지 않는 A
    a, b, c = _Hit("account", 0, 10), _Hit("card", 8, 12), _Hit("email", 11, 30)
    assert _select([c, b, a]) == [a, c]
    # 길이가 같으면 KINDS 순서: card > account
    assert _select([_Hit("account", 0, 10), _Hit("card", 5, 15)]) == [_Hit("card", 5, 15)]
    # 맞닿기만 한 범위는 둘 다 남는다
    assert _select([_Hit("phone", 5, 10), _Hit("mobile", 0, 5)]) == [_Hit("mobile", 0, 5), _Hit("phone", 5, 10)]


def test_findings_never_overlap():
    text = "계좌 010-1234-5678 / 신한카드 4111-1111-1111-1111 / 01012345678@example.com / 900101-1234567"
    findings = detect(text)
    for a, b in zip(findings, findings[1:]):
        assert a.end <= b.start


# --- 같은 바이트 길이 ---

SENTENCES = [
    "안녕하세요. 저는 홍길동(가명)이고 주민번호는 900101-1234567, 연락처는 010-1234-5678입니다.",
    "📞 010-1234-5678 🙂 hong@example.com 💳 4111 1111 1111 1111 🏦 계좌: 110-123-456789 끝👍",
    "Name: Hong / Tel: (02) 123-4567 / Mail: hong.gildong@mail.example.co.kr / 외국인등록번호 900101-5234567",
    "표\t02-1234-5678\t070-1234-5678\n신한은행\t110123456789\n카드\t5555-5555-5555-4444\n",
    "개인정보가 없는 평범한 문장입니다. 2026-10-31에 회의가 있습니다. 🙂",
    "",
    'db:\n  password: "비밀🙂-1234"\n  url: postgres://admin:pa ss@db.example.com/app\napi_key: sk-test-AAAAAAAAAAAAAAAAAAAAAAAA\n',
]


@pytest.mark.parametrize("text", SENTENCES)
def test_mask_preserves_utf8_byte_length(text):
    result = mask(text)
    assert byte_len(result.text) == byte_len(text)


@pytest.mark.parametrize("text", SENTENCES)
def test_mask_is_idempotent(text):
    once = mask(text).text
    assert mask(once).text == once
    assert detect(once) == []


def test_masked_text_hides_values_and_keeps_surroundings():
    text = SENTENCES[1]
    masked = mask(text).text
    assert masked == "📞 ***-****-**** 🙂 ****@*******.*** 💳 **** **** **** **** 🏦 계좌: ***-***-****** 끝👍"
    for secret in ("010", "1234-5678", "hong", ".com", "4111", "1111", "456789"):
        assert secret not in masked


def test_fullwidth_digits_become_three_stars_each():
    text = "연락처 ０１０-１２３４-５６７８ 입니다"
    result = mask(text)
    assert [f.kind for f in result.findings] == ["mobile"]
    assert result.text == "연락처 " + "*" * 9 + "-" + "*" * 12 + "-" + "*" * 12 + " 입니다"
    assert mask(text, visible={"mobile": "prefix"}).text == "연락처 ０１０-" + "*" * 12 + "-" + "*" * 12 + " 입니다"
    assert byte_len(result.text) == byte_len(text)


def test_fullwidth_hyphen_is_kept_as_separator():
    text = "９００１０１－１２３４５６７"
    result = mask(text)
    assert [f.kind for f in result.findings] == ["rrn"]
    assert result.text == "*" * 18 + "－" + "*" * 21
    assert byte_len(result.text) == byte_len(text)
    assert mask(text, visible={"rrn": "birth_gender"}).text == "９００１０１－１" + "*" * 18


@pytest.mark.parametrize("s, n", [("a1", 2), ("홍길동", 9), ("😀", 4), ("é", 2), ("\udcff", 1)])
def test_same_length_stars(s, n):
    assert same_length_stars(s) == "*" * n


# --- mask_bytes ---

def test_mask_bytes_preserves_invalid_utf8_bytes():
    data = b"\xff\xfe" + "주민번호 900101-1234567".encode() + b"\x80\xc3\x28" + " hong@example.com\n".encode()
    out, findings = mask_bytes(data)
    assert len(out) == len(data)
    assert out == b"\xff\xfe" + "주민번호 ******-*******".encode() + b"\x80\xc3\x28" + " ****@*******.***\n".encode()
    assert [f.kind for f in findings] == ["rrn", "email"]
    out, _ = mask_bytes(data, visible={"rrn": "birth", "email": "tld"})
    assert out == b"\xff\xfe" + "주민번호 900101-*******".encode() + b"\x80\xc3\x28" + " ****@*******.com\n".encode()


def test_mask_bytes_finding_indices_follow_surrogateescape_decoding():
    data = b"\xff" + "연락처 010-1234-5678".encode()
    _, findings = mask_bytes(data)
    decoded = data.decode("utf-8", "surrogateescape")
    assert [decoded[f.start:f.end] for f in findings] == ["010-1234-5678"]
    assert findings[0].start == 5  # \xff 1개 + "연락처 " 4개


def test_mask_bytes_euc_kr_text_keeps_korean_bytes():
    data = "연락처 010-1234-5678, 메일 hong@example.com".encode("euc-kr")
    out, findings = mask_bytes(data)
    assert len(out) == len(data)
    assert out == "연락처 ***-****-****, 메일 ****@*******.***".encode("euc-kr")
    assert [f.kind for f in findings] == ["mobile", "email"]


def test_mask_bytes_matches_mask_for_valid_utf8():
    for text in SENTENCES:
        out, findings = mask_bytes(text.encode())
        assert out == mask(text).text.encode()
        assert findings == mask(text).findings


PIECES = [
    "900101-1234567", "010-1234-5678", "02-123-4567", "hong@example.com", "4111 1111 1111 1111",
    "계좌 110-123-456789", "홍길동", "상담 기록", "😀", "０１０-１２３４-５６７８", " ", "\n", "-", "1", "@",
]


def test_mask_bytes_random_mix_preserves_length_and_untouched_bytes():
    rng = random.Random(20261031)
    for _ in range(300):
        chunks = []
        for _ in range(rng.randint(1, 12)):
            if rng.random() < 0.3:
                chunks.append(rng.randbytes(rng.randint(1, 6)))
            else:
                chunks.append(rng.choice(PIECES).encode())
        data = b"".join(chunks)
        out, findings = mask_bytes(data)
        assert len(out) == len(data)

        decoded = data.decode("utf-8", "surrogateescape")
        masked_bytes = set()
        for f in findings:
            b_start = len(decoded[:f.start].encode("utf-8", "surrogateescape"))
            b_end = len(decoded[:f.end].encode("utf-8", "surrogateescape"))
            masked_bytes.update(range(b_start, b_end))
        for i, (before, after) in enumerate(zip(data, out)):
            if i not in masked_bytes:
                assert before == after
            else:
                assert after in (before, ord("*"))


def test_large_input_finishes_quickly():
    unit = "상담 메모: 연락처 010-1234-5678, 계좌 110-123-456789, 메일 hong@example.com. " + "1-" * 20 + "a" * 70 + "\n"
    text = unit * (200_000 // byte_len(unit) + 1) + "1" * 5000 + "12-" * 2000
    started = time.perf_counter()
    result = mask(text)
    elapsed = time.perf_counter() - started
    assert byte_len(result.text) == byte_len(text)
    assert elapsed < 3.0


# --- 비밀값 (secret, Phase 4 보완) ---
# 모든 키·토큰은 형식만 흉내 낸 가짜 값이다.

@pytest.mark.parametrize("text, expected", [
    # 이름으로 알아보는 값: yaml, toml·ini, json, env, 한글 키
    ("password: hunter2-example", "password: *******-*******"),
    ("  db_password: 'correct horse'", "  db_password: '******* *****'"),
    ("password: correct horse battery  # 주석", "password: ******* ***** *******  # 주석"),
    ('secret_key = "django-insecure-0000000000000000"', 'secret_key = "******-********-****************"'),
    ('{"client_secret": "abc123XYZ", "user": "hong"}', '{"client_secret": "*********", "user": "hong"}'),
    ('{"apiKey":"k-1"}', '{"apiKey":"*-*"}'),
    ("export DB_PASSWORD=correct-horse", "export DB_PASSWORD=*******-*****"),
    ("spring.datasource.password=abcd", "spring.datasource.password=****"),
    ("access_token => 'tok'", "access_token => '***'"),
    ("Authorization: Bearer abc.def", "Authorization: ****** ***.***"),
    ("비밀번호: 1234 입니다", "비밀번호: **** 입니다"),
    ("새 비밀번호 : qwer!234", "새 비밀번호 : ********"),
    ("비번=0000", "비번=****"),
    # 형식이 정해진 키·토큰 (키 이름이 없어도)
    ("키는 AKIAEXAMPLEEXAMPLE00 입니다", "키는 ******************** 입니다"),
    ("sk-proj-abcdefEXAMPLEghijklmnopqrstuv", "**-****-" + "*" * 29),
    ("sk_live_0000EXAMPLE0000abcd", "*" * 27),            # _는 구분자가 아니라 가린다
    ("ghp_" + "a" * 36, "*" * 40),
    ("github_pat_" + "A1" * 15, "*" * 41),
    ("xoxb-0000-EXAMPLE", "****-****-*******"),
    ("AIza" + "B" * 35, "*" * 39),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefg123", "********************.***************.**********"),
    # URL 속 비밀번호만 (사용자·호스트는 남김)
    ("postgres://admin:S3cretPass@db.example.com:5432/app", "postgres://admin:**********@db.example.com:5432/app"),
    ("redis://:p4ss@localhost:6379", "redis://:****@localhost:6379"),
])
def test_secret_values_are_masked(text, expected):
    result = mask(text)
    assert result.text == expected
    assert {f.kind for f in result.findings} == {"secret"}
    assert byte_len(result.text) == byte_len(text)


@pytest.mark.parametrize("text", [
    "max_tokens: 100",                # 키 이름이 token으로 끝나지 않는다
    "tokenizer: bert",
    "password_policy: strong",
    "token_type: bearer",
    "password: ${DB_PASSWORD}",       # 참조·자리표시자
    "password: $DB_PASSWORD",
    "password: <비밀번호를 넣으세요>",
    "password: {{ vault_password }}",
    "password: ''",
    "password: null",
    "password: |",                    # YAML 블록 (다음 줄 값은 놓친다)
    "def login(password: str) -> None:",
    "password = get_password()",
    "password = os.environ['DB_PASSWORD']",
    "login(password=password)",
    "password: ********",             # 이미 가린 값
    "암호화: AES-256",                 # '암호'로 시작하는 다른 낱말
    "비밀번호는 문자로 보내드렸습니다",  # 구분자가 없다
    "password-file: /etc/app/pw",     # 키 이름이 password로 끝나지 않는다
    "sk-short",                       # 형식에 비해 짧다
])
def test_non_secrets_are_left_alone(text):
    assert [f for f in detect(text) if f.kind == "secret"] == []


def test_private_key_block_masks_only_the_body():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow0000\nAAAA+/==\n-----END RSA PRIVATE KEY-----\n다음 줄"
    result = mask(text)
    assert result.text == "-----BEGIN RSA PRIVATE KEY-----\n**********\n********\n-----END RSA PRIVATE KEY-----\n다음 줄"
    escaped = '{"private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEv0000\\n-----END PRIVATE KEY-----\\n"}'
    masked = mask(escaped).text
    assert "MIIEv0000" not in masked and len(masked) == len(escaped)


def test_url_password_is_not_reported_as_email():
    text = "url: postgres://admin:S3cret@db.example.com/app, owner: hong@example.com"
    assert [(f.kind, text[f.start:f.end]) for f in detect(text)] == [("secret", "S3cret"), ("email", "hong@example.com")]


def test_secret_wins_ties_and_longer_spans():
    assert kinds("password: 010-1234-5678") == ["secret"]                       # 같은 범위면 비밀값
    assert kinds('secret_key = "django-insecure-0000000000000000"') == ["secret"]  # 0 16개(Luhn 통과)보다 길다
    assert kinds("api_key: sk-test-0000000000000000000000000000") == ["secret"]


def test_secret_visible_only_none():
    assert VISIBLE_CHOICES["secret"] == ("none",)
    with pytest.raises(ValueError):
        mask("password: x", visible={"secret": "prefix"})


def test_config_files_keep_structure():
    yaml_text = (
        "database:\n"
        "  host: db.example.com\n"
        "  password: hunter2-example\n"
        "api_key: sk-test-0000000000000000000000000000\n"
        "github_token: ghp_000000000000000000000000000000000000\n"
        "owner_email: hong@example.com\n"
    )
    masked = mask(yaml_text).text
    assert masked.splitlines() == [
        "database:",
        "  host: db.example.com",
        "  password: *******-*******",
        "api_key: **-****-****************************",
        "github_token: ****************************************",
        "owner_email: ****@*******.***",
    ]
