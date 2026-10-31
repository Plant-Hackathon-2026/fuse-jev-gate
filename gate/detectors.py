"""1차 정규식 탐지와 같은 길이 마스킹.

형식이 정해진 개인정보와 비밀값(비밀번호·키·토큰)을 정규식으로 찾아, UTF-8 바이트 길이가 원본과 같도록 가린다.
정규식이 가린 부분은 원본과 바이트 수가 같다(자릿수가 보존된다). 처음에는 게이트가 원본 크기를 그대로 알려 주므로 필수였다.
3c부터 NER 개체는 replacements로 받아 `[이름]`처럼 종류 이름으로 바꾸므로, NER이 켜져 있으면 전체 길이는 달라질 수 있다.

같은 길이 규칙
  - 가릴 문자 하나를 그 문자의 UTF-8 바이트 수만큼의 `*`로 바꾼다.
    숫자·영문(1바이트) → `*`, 한글·전각 숫자(3바이트) → `***`, 이모지(4바이트) → `****`.
  - 탐지 범위 안의 구분자(`-`, 공백, `@`, `.`, 줄바꿈)와 종류별로 남기는 부분은 그대로 둔다.
  - 탐지 범위 밖은 한 글자도 건드리지 않는다.
  - 전각 숫자(`０１０`)와 전각 하이픈(`－`)은 글자 수가 같은 ASCII로 바꾼 사본에서 찾고(위치가 그대로라 원문에 바로 적용된다),
    가리기는 원문에 한다. 그래서 전각 숫자 한 자리는 `***`가 되고 전각 하이픈은 구분자로 남는다.

종류별 규칙 (공개 범위의 첫 값 none이 기본이다. 구분자만 남기고 전부 가린다)
  | kind          | 대상       | 탐지 조건                                                         | 공개 범위 선택지(visible)    | 예 (기본 none → 공개했을 때)                         |
  |---------------|------------|-------------------------------------------------------------------|------------------------------|------------------------------------------------------|
  | rrn           | 주민등록번호 | 6자리 + (`-`·공백·없음) + 7자리. 앞 6자리가 실제 날짜이고 오늘 이후가 아님. 7번째 1~4·9·0 | none, birth(앞 6자리), birth_gender(앞 7자리) | `900101-1234567` → `******-*******` → `900101-1******` |
  | foreigner_rrn | 외국인등록번호 | 위와 같고 7번째 5~8                                              | 같음                         | `900101-5234567` → `******-*******`                  |
  | mobile        | 휴대폰     | 010·011·016~019(또는 `+82 10`) + 3~4자리 + 4자리, 구분자 `-` `.` 공백은 선택 | none, prefix(앞자리)         | `010-1234-5678` → `***-****-****` → `010-****-****`  |
  | phone         | 일반 전화  | 지역번호(02, 031~033, 041~044, 051~055, 061~064)·070(또는 `+82 2`) + 3~4 + 4자리, `(02)` 괄호 허용 | none, area(지역번호, 괄호 포함) | `02-123-4567` → `**-***-****` → `02-***-****`        |
  | email         | 이메일     | ASCII 주소. 최상위 도메인은 영문 2자 이상                          | none, tld(최상위 도메인)     | `hong@example.com` → `****@*******.***` → `****@*******.com` |
  | card          | 카드번호   | 13~19자리. 붙여쓰기, 4-4-4-4 계열, 4-6-5·4-6-4(구분자 `-`·공백 하나로 통일). Luhn 통과 필수 | none, last4(끝 4자리)        | `4111 1111 1111 1111` → `**** **** **** ****` → `**** **** **** 1111` |
  | account       | 계좌번호   | 10~14자리(붙여쓰기 또는 `-`로 2~4묶음) + 앞 20자·뒤 10자 안에 계좌 키워드 | none만                       | `110-123-456789` → `***-***-******`                  |
  | secret        | 비밀값     | 아래 "비밀값" 참고. 값 부분만 가린다                                | none만                       | `password: hunter2` → `password: *******`            |

비밀값 (secret, Phase 4 보완: 설정 파일의 비밀번호·키·토큰. 파일 형식을 파싱하지 않고 글자 패턴으로 찾는다)
  - 형식이 정해진 키: AWS 액세스 키(`AKIA`·`ASIA` + 16자), `sk-…`(OpenAI 등), `sk_live_`·`rk_test_` 등(Stripe),
    GitHub(`ghp_` 등 36자 이상, `github_pat_`), Slack(`xox?-`), Google API 키(`AIza` + 35자), JWT(`eyJ….eyJ….서명`).
  - 개인 키 블록: `-----BEGIN … PRIVATE KEY-----`와 `-----END … PRIVATE KEY-----` 사이 본문. 머리·꼬리 줄과 줄바꿈은 남긴다.
  - URL 속 비밀번호: `scheme://사용자:비밀번호@호스트`의 비밀번호. 이 부분은 이메일로 잡지 않는다.
  - 이름으로 알아보는 값: 키 이름이 password·passwd·passphrase·secret·token·credential(s)·authorization이나
    api_key·access_key·secret_key·private_key·signing_key·master_key·encryption_key·auth_key(`_`·`-`·붙여쓰기·대소문자 무관)로
    끝나면, 그 뒤의 `:`·`=`·`=>` 다음 값을 가린다. 한글 키 비밀번호·비번·암호·패스워드도 같다.
    yaml(`key: value`), toml·ini(`key = value`), json(`"key": "value"`), env(`KEY=value`)를 같은 규칙으로 본다.
    - 따옴표 값은 따옴표 안쪽 전체, 따옴표 없는 값은 쉼표·`#` 주석·괄호 전까지(영문 키는 공백으로 이어진 낱말까지, 한글 키는 한 낱말).
    - 키 이름이 이 낱말로 끝나야 한다(max_tokens, tokenizer, password_policy는 아님).
    - 건너뛰는 값: 빈 값, null·none·true·false 같은 값, 타입 이름(str 등), `$`·`${…}`·`%(`·`{{`·`<`로 시작하는 참조·자리표시자,
      YAML 블록 표시(`|`, `>`), 이미 가린 값(`*`뿐), 키 이름과 같은 값, 바로 뒤에 `(`·`[`가 오는 값(함수 호출·코드).
  - 놓치는 것: YAML 블록(`password: |` 다음 줄), 여러 줄 값, 위 목록에 없는 키 이름과 형식(예: 일반 단어로 된 토큰).
    코드 안의 참조(`password = settings.DB_PASSWORD`)는 값으로 보고 가린다.

오탐 줄이기
  - 모든 숫자 패턴은 앞뒤가 숫자이면 매칭하지 않는다(더 긴 숫자열의 일부는 잡지 않음).
    계좌는 `-숫자`로 더 이어지는 묶음의 일부도 잡지 않는다.
  - 주민·외국인등록번호: 7번째 자리로 세기를 정해(1·2·5·6 → 1900년대, 3·4·7·8 → 2000년대, 9·0 → 1800년대)
    윤년까지 따져 날짜를 확인한다. 7번째 자리는 0~9가 모두 둘 중 하나에 속하므로 거르는 효과는 없고 종류 구분에만 쓴다.
    끝자리 검증(체크섬)은 쓰지 않는다. 최근 발급분에 맞지 않는다는 이야기가 있으나 확인하지 못했기 때문이다.
  - 카드·계좌: 숫자가 YYYYMMDD 날짜(1900~2099년)로 시작하면 주문번호·일시로 보고 뺀다.
  - 계좌: 뒤에 `원`·`KRW`가 붙거나 앞에 `₩`·`$`가 있으면 금액으로 보고 뺀다.
  - 대표번호(1588·1544·1600 등 15xx·16xx·18xx), 080(수신자 부담), 050x(안심번호)는 잡지 않는다.
    개인이 아니라 업체·중계용 번호라서 가리면 쓸모 있는 정보만 사라지고, 정규식 탐지 건수가 정책 판단(마스킹·차단)에
    쓰이므로 오탐이 문서 전체 결정을 흔들 수 있다.

공개 범위 (visible, 사용자 설정)
  mask(text, visible={"rrn": "birth", "card": "last4"})처럼 종류별로 준다. 빠진 종류는 none(전부 가림)이다.
  기본값은 모든 종류가 전부 가림이다(2026-10-31 사용자 결정: 주민번호를 먼저 정하고, 같은 원칙을 다른 번호에도 적용).
  처음에는 휴대폰 앞자리·지역번호·최상위 도메인·카드 끝 4자리를 남겼으나 이제는 선택이다. 명세 예시 모양(`900101-1******`)은
  rrn이 birth_gender일 때 나온다. 모르는 종류나 값은 ValueError다.

겹칠 때 고르는 규칙 (결정적)
  1. 더 긴 범위를 고른다.
  2. 길이가 같으면 secret > rrn > foreigner_rrn > card > mobile > phone > email > account 순서를 따른다.
     예: `신한카드 4111 1111 1111 1111`은 계좌 키워드(신한)가 있어도 카드, `계좌 010-1234-5678`은 휴대폰,
     `password: 010-1234-5678`은 비밀값.
  3. 그래도 같으면 앞에 있는 것을 고른다. 이미 고른 범위와 겹치는 후보는 버린다.

TODO: 한글로 쓴 숫자("공일공 일이삼사 오육칠팔", "구공공일공일")는 1차 범위 밖이다. 숫자로 바꾼 뒤 같은 규칙을 적용하는 식으로 추가한다.
TODO: 전각 하이픈 외의 비ASCII 구분자(en dash `–`, 마이너스 `−` 등), 구분자 앞뒤 공백(`010 - 1234 - 5678`), +82 외 국가번호는 잡지 않는다.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

KINDS = ("rrn", "foreigner_rrn", "card", "mobile", "phone", "email", "account", "secret")
# 겹치는 범위의 길이가 같을 때 고르는 순서. 비밀값 자리의 번호는 비밀값으로 본다.
_PRIORITY = {kind: i for i, kind in enumerate(("secret", "rrn", "foreigner_rrn", "card", "mobile", "phone", "email",
                                               "account"))}
# 종류별 공개 범위 선택지. 첫 값(none, 전부 가림)이 기본이다.
VISIBLE_CHOICES: dict[str, tuple[str, ...]] = {
    "rrn": ("none", "birth", "birth_gender"),
    "foreigner_rrn": ("none", "birth", "birth_gender"),
    "card": ("none", "last4"),
    "mobile": ("none", "prefix"),
    "phone": ("none", "area"),
    "email": ("none", "tld"),
    "account": ("none",),
    "secret": ("none",),
}
DEFAULT_VISIBLE = "none"
SEPARATORS = frozenset("- .@\n\r")  # 줄바꿈은 여러 줄에 걸친 개인 키 블록에만 들어간다
ACCOUNT_WINDOW_BEFORE = 20
ACCOUNT_WINDOW_AFTER = 10


@dataclass(frozen=True)
class Finding:
    kind: str   # KINDS 중 하나
    start: int  # 문자(str 인덱스) 단위, end는 포함하지 않는다
    end: int


@dataclass(frozen=True)
class MaskResult:
    text: str
    findings: tuple[Finding, ...]


@dataclass(frozen=True)
class _Hit:
    kind: str
    start: int
    end: int
    # 공개 범위 이름 → 원문 그대로 둘 범위(절대 인덱스). 구분자는 이와 무관하게 남긴다
    keeps: tuple[tuple[str, tuple[int, int]], ...] = ()

    def finding(self) -> Finding:
        return Finding(self.kind, self.start, self.end)


# 전각 숫자·하이픈을 ASCII로 바꾼 사본에서 찾는다. 글자 하나를 글자 하나로만 바꿔야 위치가 유지된다.
_NORMALIZE = {0xFF10 + i: ord("0") + i for i in range(10)} | {0xFF0D: ord("-")}

_RRN = re.compile(r"(?<!\d)(?P<keep>(?P<birth>\d{6})[- ]?(?P<gender>\d))\d{6}(?!\d)")
_RRN_CENTURY = {1: 1900, 2: 1900, 5: 1900, 6: 1900, 3: 2000, 4: 2000, 7: 2000, 8: 2000, 9: 1800, 0: 1800}

_PHONE_PREFIX = r"(?:\+82[-. ]?0?|0)"  # +82 뒤에서는 앞자리 0을 빼고 쓰는 경우가 많다
_PHONE_REST = r"[-. ]?\d{3,4}[-. ]?\d{4}(?!\d)"
_MOBILE = re.compile(r"(?<![\d+])(?P<keep>" + _PHONE_PREFIX + r"1[016789])" + _PHONE_REST)
_AREA = r"(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4]|70)"
_PHONE = re.compile(r"(?<![\d+])(?P<keep>\(?" + _PHONE_PREFIX + _AREA + r"\)?)" + _PHONE_REST)

# 한국어 조사가 바로 붙는 경우("hong@example.com으로")가 많아서 경계는 ASCII 문자로만 본다.
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@"
    r"(?:[A-Za-z0-9-]{1,63}\.){1,8}(?P<tld>[A-Za-z]{2,24})(?![A-Za-z0-9-])"
)

_CARD = re.compile(
    r"(?<!\d)(?:"
    r"\d{13,19}"
    r"|\d{4}(?P<s1>[ -])\d{4}(?P=s1)\d{4}(?P=s1)\d{1,4}(?:(?P=s1)\d{1,3})?"
    r"|\d{4}(?P<s2>[ -])\d{6}(?P=s2)\d{4,5}"
    r")(?!\d)"
)

_ACCOUNT = re.compile(r"(?<!\d)(?<!\d-)(?:\d{10,14}|\d{2,6}(?:-\d{2,7}){1,3})(?!\d)(?!-\d)")
# 은행 이름 중 '우리', '하나', '국민', '기업'처럼 일상어와 겹치는 것은 '은행'이 붙을 때만 잡힌다.
_ACCOUNT_KEYWORD = re.compile(
    r"계좌|입금|송금|이체|출금|예금주|은행|뱅크|농협|신협|수협|새마을금고|우체국|신한|KB|NH|IBK|KDB|(?i:account|bank)"
)
_AMOUNT_AFTER = re.compile(r"\s?(?:원|KRW)")
_AMOUNT_BEFORE = re.compile(r"(?:₩|￦|\$|KRW)\s?$")

# 비밀값. 형식이 공개된 키·토큰 모양 (gitleaks 같은 도구가 쓰는 널리 알려진 접두어)
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{36,}"
    r"|github_pat_[A-Za-z0-9_]{22,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r"|eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r")(?![A-Za-z0-9_-])"
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----(?P<body>[\s\S]{1,20000}?)-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----"
)
_URL_PASSWORD = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s/@:]*:(?P<value>[^\s/@]+)@")
_IN_URL_USERINFO = re.compile(r"://[^\s/@:]*:[^\s/@]*\Z")
# 키 이름이 이 낱말로 끝나야 한다. 영문 키 앞에는 영문·숫자·_가 오지 않는다.
_SECRET_KEY = re.compile(
    r"(?i:(?<![A-Za-z0-9_])(?P<key>[A-Za-z0-9_.-]*?(?:password|passwd|passphrase|secret|token|credentials?|authorization"
    r"|(?:api|access|secret|private|signing|master|encryption|auth)[_-]?key))(?![A-Za-z0-9_]))"
    r"|(?P<ko>비밀\s?번호|비번|암호|패스워드)"
)
_VALUE_CHARS = r"[^\s,;#\"'`}\]\[()<>]"
_SEPARATOR = r"[\"']?[ \t]*(?:=>|[:=])(?![:=>])[ \t]*"
_QUOTED = r"\"(?P<dq>(?:[^\"\\\n]|\\.)*)\"|'(?P<sq>[^'\n]*)'"
_SECRET_VALUE = re.compile(_SEPARATOR + "(?:" + _QUOTED + "|(?P<bare>" + _VALUE_CHARS + "+(?:[ \t]+" + _VALUE_CHARS + "+)*))")
_SECRET_VALUE_ONE_WORD = re.compile(_SEPARATOR + "(?:" + _QUOTED + "|(?P<bare>" + _VALUE_CHARS + "+))")
_NOT_SECRET = frozenset({
    "null", "none", "nil", "~", "true", "false", "yes", "no", "undefined", "|", ">", "|-", ">-", "|+", ">+",
    "str", "string", "bytes", "int", "optional", "required",
})
_PLACEHOLDER_START = ("$", "%(", "{{", "<")

# 종류별로 쓰는 정규식(이 모듈의 변수 이름, 패턴 객체). 설정 화면의 "찾는 규칙 보기"(gate.ui_api)가 여기서 읽어 보여 준다.
# 패턴 글자를 따로 적지 않고 객체를 그대로 넘기므로 화면과 코드가 어긋나지 않는다. 날짜·Luhn·키워드 같은 추가 확인은 코드에 있다.
PATTERNS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "rrn": (("_RRN", _RRN),),
    "foreigner_rrn": (("_RRN", _RRN),),
    "card": (("_CARD", _CARD),),
    "mobile": (("_MOBILE", _MOBILE),),
    "phone": (("_PHONE", _PHONE),),
    "email": (("_EMAIL", _EMAIL),),
    "account": (("_ACCOUNT", _ACCOUNT), ("_ACCOUNT_KEYWORD", _ACCOUNT_KEYWORD)),
    "secret": (("_TOKEN", _TOKEN), ("_PRIVATE_KEY", _PRIVATE_KEY), ("_URL_PASSWORD", _URL_PASSWORD),
               ("_SECRET_KEY", _SECRET_KEY), ("_SECRET_VALUE", _SECRET_VALUE)),
}


def detect(text: str) -> list[Finding]:
    """text에서 개인정보 위치를 찾는다. 겹치지 않고, start 순으로 정렬된다."""
    return [hit.finding() for hit in _scan(text.translate(_NORMALIZE))]


def check_visible(visible: Mapping[str, str] | None = None) -> dict[str, str]:
    """종류별 공개 범위를 검사하고 빠진 종류를 none으로 채워 돌려준다. 모르는 종류나 값이면 ValueError."""
    if visible is None:
        visible = {}
    if not isinstance(visible, Mapping):
        raise ValueError(f"visible은 종류 → 공개 범위 mapping이어야 합니다: {type(visible).__name__}")
    for kind, value in visible.items():
        if kind not in VISIBLE_CHOICES:
            raise ValueError(f"알 수 없는 종류: {kind!r} ({', '.join(KINDS)} 중 하나)")
        if not isinstance(value, str) or value not in VISIBLE_CHOICES[kind]:
            raise ValueError(f"{kind}의 공개 범위는 {', '.join(VISIBLE_CHOICES[kind])} 중 하나여야 합니다: {value!r}")
    return {kind: visible.get(kind, DEFAULT_VISIBLE) for kind in KINDS}


def mask(
    text: str, *, visible: Mapping[str, str] | None = None, replacements: Sequence[tuple[int, int, str]] = ()
) -> MaskResult:
    """탐지한 범위를 같은 UTF-8 바이트 길이로 가린다.

    `len(결과.text.encode("utf-8")) == len(text.encode("utf-8"))`이다. findings의 위치는 원문(text)의 문자 인덱스다.
    전각 숫자처럼 문자 하나가 `*` 여러 개가 되면 결과 문자열에서는 위치가 밀린다(바이트 위치는 원문과 같다).
    visible은 종류별 공개 범위다(모듈 docstring의 표). 기본은 전부 가림이고, 모르는 종류나 값이면 ValueError.

    replacements는 정규식 밖에서 찾은 범위(NER 개체, 3c)를 `[(start, end, 바꿀 글자)]`로 받아 함께 바꾼다.
    위치는 원문 문자 인덱스이고, 서로 겹치거나 정규식 범위와 겹치면 ValueError다(겹치는 부분은 미리 빼서 넘긴다).
    replacements를 주면 결과 길이가 원문과 달라질 수 있다(`[이름]`처럼 종류 이름으로 바꾸기 때문). findings는 정규식 것만이다.
    """
    shown = check_visible(visible)
    view = text.translate(_NORMALIZE)
    hits = _scan(view)
    extra = sorted(replacements)
    for i, (start, end, new) in enumerate(extra):
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) \
                or not 0 <= start < end <= len(text) or not isinstance(new, str):
            raise ValueError(f"replacements의 범위가 올바르지 않습니다: {(start, end)}")
        if i and start < extra[i - 1][1]:
            raise ValueError("replacements끼리 겹칩니다")
        if any(start < hit.end and hit.start < end for hit in hits):
            raise ValueError("replacements가 정규식 범위와 겹칩니다")
    if not hits and not extra:
        return MaskResult(text, ())
    spans: list[tuple[int, int, str | _Hit]] = [(hit.start, hit.end, hit) for hit in hits] + list(extra)
    parts = []
    pos = 0
    for start, end, what in sorted(spans, key=lambda s: s[0]):
        parts.append(text[pos:start])
        if isinstance(what, _Hit):
            parts.append(_mask_hit(text, view, what, dict(what.keeps).get(shown[what.kind])))
        else:
            parts.append(what)
        pos = end
    parts.append(text[pos:])
    return MaskResult("".join(parts), tuple(hit.finding() for hit in hits))


def mask_bytes(
    data: bytes, *, visible: Mapping[str, str] | None = None, replacements: Sequence[tuple[int, int, str]] = ()
) -> tuple[bytes, tuple[Finding, ...]]:
    """바이트를 같은 길이로 가린다. UTF-8이 아닌 바이트도 그대로 보존한다.

    - `data.decode("utf-8", "surrogateescape")`로 푼다. UTF-8로 풀 수 없는 바이트는 한 바이트씩
      U+DC80~U+DCFF의 대리 문자 하나가 된다. 정규식은 이 문자를 숫자·영문으로 보지 않으므로 탐지 범위에 들어가지 않고,
      `encode("utf-8", "surrogateescape")`로 되돌리면 원래 바이트가 그대로 나온다.
    - 돌려주는 Finding의 start·end는 바이트 위치가 아니라 위 방식으로 푼 문자열의 문자 인덱스다.
      바이트 위치가 필요하면 `len(decoded[:start].encode("utf-8", "surrogateescape"))`로 바꾼다.
    - UTF-8이 아닌 인코딩(EUC-KR 등)은 공식 지원이 아니다. ASCII 숫자·이메일은 그대로 잡히지만
      한글 키워드가 필요한 계좌는 놓친다. UTF-16은 아무것도 잡지 못한다.
    - 파일을 나눠서 부르면 경계에 걸친 번호를 놓친다. 파일 전체를 한 번에 넘기고, 부분 읽기는 결과를 잘라서 쓴다.
    - replacements(NER 개체)는 mask와 같다. 위치는 위 방식으로 푼 문자열의 문자 인덱스이고, 주면 바이트 길이가 달라질 수 있다.
    """
    result = mask(data.decode("utf-8", "surrogateescape"), visible=visible, replacements=replacements)
    return result.text.encode("utf-8", "surrogateescape"), result.findings


def same_length_stars(s: str) -> str:
    """s를 UTF-8 바이트 수가 같은 `*` 문자열로 바꾼다. UTF-8이 아닌 바이트(surrogateescape)는 1바이트로 센다."""
    return "*" * len(s.encode("utf-8", "surrogateescape"))


def _mask_hit(text: str, view: str, hit: _Hit, keep: tuple[int, int] | None) -> str:
    keep_start, keep_end = keep or (hit.start, hit.start)
    out = []
    for i in range(hit.start, hit.end):
        ch = text[i]
        out.append(ch if keep_start <= i < keep_end or view[i] in SEPARATORS else same_length_stars(ch))
    return "".join(out)


def _scan(text: str) -> list[_Hit]:
    """text는 _NORMALIZE를 적용한 사본이다. 위치는 원문과 같다."""
    candidates = [
        *_find_secret(text),
        *_find_rrn(text),
        *_find_card(text),
        *_find_phone(text),
        *_find_email(text),
        *_find_account(text),
    ]
    return _select(candidates)


def _select(candidates: list[_Hit]) -> list[_Hit]:
    """겹치는 후보 중 하나만 남긴다. 결과는 start 순이다.

    이어서 겹치는 후보끼리 묶고 묶음 안에서만 고른다. 다른 묶음끼리는 겹칠 수 없어서 전체를 한 번에 고른 것과 같다.
    """
    chosen: list[_Hit] = []
    group: list[_Hit] = []
    group_end = 0
    for hit in sorted(candidates, key=lambda h: (h.start, h.end)):
        if group and hit.start >= group_end:
            chosen.extend(_pick(group))
            group = []
        if not group:
            group_end = hit.end
        group.append(hit)
        group_end = max(group_end, hit.end)
    if group:
        chosen.extend(_pick(group))
    return chosen


def _pick(group: list[_Hit]) -> list[_Hit]:
    """긴 것 → KINDS 순서 → 앞에 있는 것 순으로, 이미 고른 것과 겹치지 않으면 고른다."""
    picked: list[_Hit] = []
    for hit in sorted(group, key=lambda h: (h.start - h.end, _PRIORITY[h.kind], h.start)):
        if all(hit.end <= p.start or p.end <= hit.start for p in picked):
            picked.append(hit)
    return sorted(picked, key=lambda h: h.start)


def _digits(s: str) -> str:
    return "".join(ch for ch in s if ch.isdecimal())


def _find_rrn(text: str) -> Iterator[_Hit]:
    for m in _RRN.finditer(text):
        gender = int(m["gender"])
        birth = m["birth"]
        try:
            born = date(_RRN_CENTURY[gender] + int(birth[:2]), int(birth[2:4]), int(birth[4:]))
        except ValueError:
            continue
        if born > date.today():
            continue
        kind = "foreigner_rrn" if 5 <= gender <= 8 else "rrn"
        yield _Hit(kind, m.start(), m.end(), (("birth", m.span("birth")), ("birth_gender", m.span("keep"))))


def _find_phone(text: str) -> Iterator[_Hit]:
    for kind, pattern, option in (("mobile", _MOBILE, "prefix"), ("phone", _PHONE, "area")):
        for m in pattern.finditer(text):
            yield _Hit(kind, m.start(), m.end(), ((option, m.span("keep")),))


def _find_email(text: str) -> Iterator[_Hit]:
    for m in _EMAIL.finditer(text):
        if _IN_URL_USERINFO.search(text, max(0, m.start() - 300), m.start()):
            continue  # scheme://user:비밀번호@host의 '비밀번호@host'는 이메일이 아니다(비밀값으로 잡는다)
        yield _Hit("email", m.start(), m.end(), (("tld", m.span("tld")),))


def _find_secret(text: str) -> Iterator[_Hit]:
    for m in _TOKEN.finditer(text):
        yield _Hit("secret", m.start(), m.end())
    for m in _PRIVATE_KEY.finditer(text):
        yield _Hit("secret", *m.span("body"))
    for m in _URL_PASSWORD.finditer(text):
        yield _Hit("secret", *m.span("value"))
    for m in _SECRET_KEY.finditer(text):
        korean = m.group("ko") is not None
        v = (_SECRET_VALUE_ONE_WORD if korean else _SECRET_VALUE).match(text, m.end())
        if v is None:
            continue
        group = next(g for g in ("dq", "sq", "bare") if v.group(g) is not None)
        value = v.group(group)
        start, end = v.span(group)
        if not value.strip() or value.lower() in _NOT_SECRET or value.startswith(_PLACEHOLDER_START):
            continue
        if set(value) <= SEPARATORS | {"*"} or (not korean and value.lower() == m.group("key").lower()):
            continue  # 이미 가린 값, password=password 같은 코드
        if group == "bare" and text[end:end + 1] in ("(", "["):
            continue  # 함수 호출·인덱싱 같은 코드
        yield _Hit("secret", start, end)


def _find_card(text: str) -> Iterator[_Hit]:
    for m in _CARD.finditer(text):
        digits = _digits(m.group())
        if not 13 <= len(digits) <= 19 or not _luhn_ok(digits) or _starts_with_date(digits):
            continue
        yield _Hit("card", m.start(), m.end(), (("last4", (_nth_digit_from_end(text, m.start(), m.end(), 4), m.end())),))


def _find_account(text: str) -> Iterator[_Hit]:
    for m in _ACCOUNT.finditer(text):
        start, end = m.span()
        digits = _digits(m.group())
        if not 10 <= len(digits) <= 14 or _starts_with_date(digits):
            continue
        if _AMOUNT_AFTER.match(text, end) or _AMOUNT_BEFORE.search(text[max(0, start - 4):start]):
            continue
        before = text[max(0, start - ACCOUNT_WINDOW_BEFORE):start]
        after = text[end:end + ACCOUNT_WINDOW_AFTER]
        if _ACCOUNT_KEYWORD.search(before) or _ACCOUNT_KEYWORD.search(after):
            yield _Hit("account", start, end)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _starts_with_date(digits: str) -> bool:
    """YYYYMMDD(1900~2099년)로 시작하는지. 주문번호·일시(20261031…)를 카드·계좌에서 빼는 데 쓴다."""
    if len(digits) < 8:
        return False
    year = int(digits[:4])
    if not 1900 <= year <= 2099:
        return False
    try:
        date(year, int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return False
    return True


def _nth_digit_from_end(text: str, start: int, end: int, n: int) -> int:
    """[start, end)에서 뒤에서 n번째 숫자의 인덱스."""
    count = 0
    for i in range(end - 1, start - 1, -1):
        if text[i].isdecimal():
            count += 1
            if count == n:
                return i
    return start
