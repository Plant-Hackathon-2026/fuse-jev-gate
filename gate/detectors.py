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
    - 마크다운 굵게(`**비밀번호**: 값`, `**password:** 값`)도 같은 키로 본다.
  - 보강(2026-11-01 사용자 결정: 실제 모델로 잰 누출 사례, plans/next-tasks-plan.md 1.4). 모두 비밀값(secret)이고 값만 가린다.
    - 짧은 키 pw·pwd·pass·pin·passcode(낱말 전체, 대소문자 무관)와 `_PASS`·`_PWD`·`_PIN`으로 끝나는 키(`DB_PASS=…`,
      `MYSQL_PWD=…`) 뒤의 `:`·`=` 값. 짧은 키는 한 낱말만, 한글뿐인 값("PIN: 번호를")과 경로(`PWD=/Users/…`)는 뺀다.
      pass·pin은 흔한 말이라(`PASS: 12`, `pin: GPIO13`) 값이 비밀번호처럼 보일 때만(아래 "비밀번호 같은 값") 가린다.
    - 구분자 없는 문장: password·passwd·passcode·pwd·pw·pin, 비밀번호·비번·암호·패스워드·핀번호 바로 뒤의 값.
      조사(는·은·가·이·를·을·로), 띄어쓰기, `is`·`was`, 화살표(`→`·`->`), 표 칸(`|`)을 지나 다음 낱말 하나를 본다.
      "비밀번호는 qwer1234! 입니다", "비번 7741 이야", "공유기 암호 → 1q2w3e4r", netrc `password X`. 값은 비밀번호 같은 값일 때만.
    - netrc `machine 호스트 login 이름 password 값`(여러 줄도)은 값 모양과 상관없이 가린다.
    - OTP·백업·복구·인증 코드: "백업 코드", "복구 코드", "인증 코드·번호", "보안 코드", "OTP", "backup code(s)",
      "recovery code(s)" 등 뒤의 코드 목록(쉼표·띄어쓰기·줄바꿈으로 나뉜 `8312-4471, 9902-1187`). 코드마다 숫자가 있고
      영문·숫자 4자 이상이어야 한다(연도·날짜는 아님). 다음 줄로 이어지는 목록은 그 줄이 코드로만 되어 있을 때만 본다.
    - `admin / P@ssw0rd`처럼 흔한 계정 이름(admin·root·user·guest·test 등) 뒤 `/`의 비밀번호 같은 값,
      `ID/PW: 이름 / 값`의 값, `mysql … -p값`·`sshpass -p 값`의 값.
    - YAML 블록: `password: |`(또는 `>`) 다음 줄부터 더 들여 쓴 줄 전체, `password:` 다음 줄에 더 들여 쓴 비밀번호 같은 값 하나.
  - 비밀번호 같은 값(credential-like, 위 보강과 gate.pipeline의 "남은 비밀번호" 확인이 같이 쓴다): 4자 이상이고 숫자나 기호
    (`!@#$%^&*+=?~|`, 역슬래시)가 있다. 날짜·시각·버전·금액·비율·주소·경로·파일 이름·도메인·자리표시자, `AES-256`·`WPA2`·`GPIO13`
    같은 기술 이름, `8bit` 같은 단위는 아니다. 값 바로 뒤에 한글이 붙으면 조사·어미(이·입니다·야·로·를 등)일 때만 값으로 본다
    ("8~16자로", "5회", "1234원"은 아님).
  - 놓치는 것: 숫자·기호가 없는 문장 속 비밀번호("비밀번호는 sunshine"), 키 이름 없이 혼자 있는 값, 여러 줄 값,
    위 목록에 없는 키 이름과 형식(예: 일반 단어로 된 토큰). 코드 안의 참조(`password = settings.DB_PASSWORD`)는 값으로 보고 가린다.

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
# 키 글자(영문·숫자·_ . -)가 이어진 곳의 첫 글자에서만 찾기 시작한다(`a-a-a-…`처럼 긴 줄에서 시작점마다 끝까지 훑지 않게).
_SECRET_KEY = re.compile(
    r"(?i:(?<![A-Za-z0-9_.-])(?P<key>[A-Za-z0-9_.-]*?(?:password|passwd|passphrase|secret|token|credentials?|authorization"
    r"|(?:api|access|secret|private|signing|master|encryption|auth)[_-]?key))(?![A-Za-z0-9_]))"
    r"|(?P<ko>비밀\s?번호|비번|암호|패스워드)"
)
_VALUE_CHARS = r"[^\s,;#\"'`}\]\[()<>]"
_BOLD = r"(?:\*\*|__)?"  # 마크다운 굵게: **비밀번호**: 값, **password:** 값
_SEPARATOR = r"[\"']?" + _BOLD + r"[ \t]*(?:=>|[:=])(?![:=>])" + _BOLD + r"[ \t]*"
_QUOTED = r"\"(?P<dq>(?:[^\"\\\n]|\\.)*)\"|'(?P<sq>[^'\n]*)'"
_SECRET_VALUE = re.compile(_SEPARATOR + "(?:" + _QUOTED + "|(?P<bare>" + _VALUE_CHARS + "+(?:[ \t]+" + _VALUE_CHARS + "+)*))")
_SECRET_VALUE_ONE_WORD = re.compile(_SEPARATOR + "(?:" + _QUOTED + "|(?P<bare>" + _VALUE_CHARS + "+))")
_NOT_SECRET = frozenset({
    "null", "none", "nil", "~", "true", "false", "yes", "no", "undefined", "|", ">", "|-", ">-", "|+", ">+",
    "str", "string", "bytes", "int", "optional", "required",
})
_PLACEHOLDER_START = ("$", "%(", "{{", "<")

# --- 비밀값 보강 (2026-11-01, 모듈 docstring "보강") ---
_HANGUL = "가-힣ㄱ-ㅎㅏ-ㅣ"
_HANGUL_CHAR = re.compile(f"[{_HANGUL}]")
_TEXT_PUNCT = "·・‧…–—→←⇒"  # 한국어 글에 흔한 가운뎃점·말줄임표·대시·화살표. 값의 일부로 보지 않는다("테스트·e2e")
_JOSA_AFTER_KEY = r"(?:는|은|가|이|를|을|로)?"
# 짧은 키와 _PASS·_PWD·_PIN으로 끝나는 키. 앞뒤에 영문·숫자·_가 붙지 않는다(bypass, spin, pwdhash, DB_PASSWORD는 아님)
_SHORT_KEY = re.compile(
    r"(?i:(?<![A-Za-z0-9_.-])(?P<suffix>[A-Za-z0-9_.-]*?[_.-](?:pass|pwd|pin))(?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])(?P<short>passcode|pass|pwd|pw|pin)(?![A-Za-z0-9_]))"
)
# 구분자 없이 값이 오는 키(문장, netrc)
_LOOSE_KEY = re.compile(
    r"(?i:(?<![A-Za-z0-9_])(?:password|passwd|passcode|pwd|pw|pin)(?![A-Za-z0-9_]))"
    f"|비밀[ \\t]?번호|비번|암호|패스워드|핀[ \\t]?번호"
)
_LOOSE_GAP = re.compile(
    _BOLD + _JOSA_AFTER_KEY + r"(?:[ \t]*(?:→|->|⇒|=>|\|)[ \t]*|[ \t]+(?:(?i:is|was)[ \t]+)?)"
)
_LOOSE_VALUE = re.compile(
    r"\"(?P<dq>[^\"\n]+)\"|'(?P<sq>[^'\n]+)'|“(?P<cq>[^”\n]+)”|‘(?P<rq>[^’\n]+)’"
    f"|(?P<bare>[^\\s{_HANGUL}{_TEXT_PUNCT}\"'`“”‘’()\\[\\]{{}}<>,;|]+)"
)
# netrc: machine 호스트 login 이름 [account·port …] password 값 (한 줄이나 여러 줄)
_NETRC = re.compile(
    r"(?i)(?<![A-Za-z0-9_])machine[ \t]+\S+[ \t\r\n]+login[ \t]+\S+(?:[ \t\r\n]+(?:account|port)[ \t]+\S+)*"
    f"[ \\t\\r\\n]+password[ \\t]+(?P<value>[^\\s`{_HANGUL}]+)"
)
# 명령줄의 비밀번호: mysql … -p값(붙여 씀), sshpass -p 값
_CLI_PASSWORD = re.compile(
    r"(?<![A-Za-z0-9_-])(?:(?:mysql|mysqldump|mysqladmin|mysqlimport|mysqlcheck|mysqlsh|mariadb)(?![A-Za-z0-9_-])"
    r"[^\n]{0,300}?[ \t]-p|sshpass(?:[ \t]+-[A-Za-z]\S*){0,10}?[ \t]+-p[ \t]*)"
    f"(?:'(?P<sq>[^'\\n]+)'|\"(?P<dq>[^\"\\n]+)\"|(?P<bare>[^\\s'\"`,;{_HANGUL}]+))"
)
# 계정 / 비밀번호: 흔한 계정 이름 뒤 `/`의 값, `ID/PW: 이름 / 값`의 값. 경로(/home/user/…)의 일부는 아니다
_PAIR_VALUE = f"(?P<value>[^\\s/,;\"'`()\\[\\]{{}}<>{_HANGUL}{_TEXT_PUNCT}]++)(?!/)"
_ACCOUNT_PAIR = re.compile(
    r"(?i:(?<![A-Za-z0-9_./\\-])(?:admin|administrator|root|user|guest|test|sa|postgres|ubuntu|pi|oracle|demo|ec2-user)"
    r"(?![A-Za-z0-9_.-]))[ \t]*/[ \t]*" + _PAIR_VALUE
)
_ID_PW = re.compile(
    r"(?:(?i:(?<![A-Za-z0-9_])id[ \t]*/[ \t]*(?:pw|pwd|pass|password)(?![A-Za-z0-9_]))"
    r"|아이디[ \t]*/[ \t]*(?:비밀번호|비번|패스워드|암호)|계정[ \t]*/[ \t]*(?:비밀번호|비번|암호))"
    + _BOLD + r"[ \t]*[:=：]?" + _BOLD + r"[ \t]*[^\s/]+[ \t]*/[ \t]*" + _PAIR_VALUE
)
# OTP·백업·복구·인증 코드. 이름 뒤 코드 목록을 하나씩 가린다(쉼표는 남김)
_CODE_LABEL = re.compile(
    r"(?i:(?<![A-Za-z0-9_])(?:backup|recovery|verification|security|otp|2fa|mfa|one[- ]time)[ \t]?(?:codes?|코드|번호)"
    r"(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])otp(?![A-Za-z0-9_]))"
    r"|(?:백업|복구|복원|인증|보안|일회용)[ \t]?(?:코드|번호)"
)
_CODE_GAP = re.compile(_BOLD + _JOSA_AFTER_KEY + r"[ \t]*(?:[:=：]|→|->|⇒|=>)?" + _BOLD + r"[ \t]*")
_CODE = re.compile(r"(?<![A-Za-z0-9])(?:\d{3,4}(?: \d{3,4})+|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)(?![A-Za-z0-9])")
_CODE_SEP = re.compile(r"[ \t]*[,;/][ \t]*|[ \t]+")
_CODE_NEWLINE = re.compile(r"[ \t]*[,;]?[ \t]*\r?\n[ \t]*(?:(?:[-*•·]|\d{1,2}[.)])[ \t]+)?")
_CODE_LINE = re.compile(r"[A-Za-z0-9]+(?:[- ][A-Za-z0-9]+)*(?:[ \t]*[,;/][ \t]*[A-Za-z0-9]+(?:[- ][A-Za-z0-9]+)*)*[ \t,;]*")
MAX_CODES = 50
MAX_CODE_LINE_CHARS = 300
# YAML 블록: `key: |`(·`>`·`|-` 등) 또는 값 없는 `key:` 뒤 줄바꿈. 다음 줄들은 _yaml_block이 본다
_YAML_OPEN = re.compile(
    r"[\"']?" + _BOLD + r"[ \t]*:" + _BOLD + r"[ \t]*(?P<indicator>[|>][-+]?\d*[-+]?)?[ \t]*(?:#[^\n]*)?(?=\r?\n)"
)

# 비밀번호 같은 값(credential-like). 보강 규칙과 gate.pipeline의 "남은 비밀번호" 확인이 같이 쓴다
_CREDENTIAL_SYMBOL = re.compile(r"[!@#$%^&+=?~|\\]")  # `*`는 마크다운 강조(*중요*)라 뺀다
_NOT_CREDENTIAL = re.compile(
    r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{2,4}"         # 날짜
    r"|\d{1,2}:\d{2}(?::\d{2})?"                                             # 시각
    r"|v?\d+(?:\.\d+)+"                                                      # 버전·소수·IP
    r"|[$₩￦€£¥]\d[\d,.]*|\d[\d,.]*%"                                          # 금액·비율
    r"|(?:https?|ftp|file)://\S*|www\.\S*|(?:~|\.{1,2})?/\S*|[A-Za-z]:\\\S*"   # 주소·경로
    r"|[\w.-]+\.(?:txt|md|pdf|zip|png|jpe?g|gif|csv|xlsx?|docx?|pptx?|hwpx?|json|ya?ml|toml|ini|conf|cfg|log|py|js|ts"
    r"|sh|html?)"                                                            # 파일 이름
    r"|(?:[\w-]+\.)+(?:com|net|org|io|kr|jp|cn|us|uk|co|dev|app|ai|me|info|biz|edu|gov|local|internal|lan)"  # 도메인
    r"|(?:AES|SHA|RSA|3?DES|MD|HMAC|ECDSA|ED|TLS|SSL|UTF|ISO|RFC|IPv|HTTP|PBKDF|WPA|WEP|GPIO)[-_]?\d*(?:-[A-Za-z0-9]+)?"
    r"|\d+(?:bits?|bytes?|[kmgt]b|ms|px|chars?|characters?|digits?)",          # 단위
    re.IGNORECASE,
)
# 값 바로 뒤에 붙어도 되는 한글(조사·어미의 첫 글자). "qwer1234!입니다", "7741이야"는 되고 "8~16자로", "5회", "1234원"은 아니다
_VALUE_ENDINGS = frozenset("이입야예에요로으를을는은가고라다였랑와과하니네지죠래란임")
_YEAR = re.compile(r"(?:19|20)\d\d")

# 남은 비밀번호(gate.pipeline ②): 비밀번호를 뜻하는 낱말. 한글 낱말 뒤에는 한글이 바로 오지 않거나 조사만 온다(암호화는 아님)
_HINT_WORD = re.compile(
    r"(?i:(?<![A-Za-z0-9_.])(?:password|passwd|passphrase|passcode|pwd|pw|pin|secret|otp|(?:backup|recovery)[ \t]?codes?)"
    r"(?![A-Za-z0-9_]|\.[A-Za-z0-9_]))"  # numbers.secret.action 같은 점 이름의 일부는 아니다
    r"|(?:비밀[ \t]?번호|비번|암호|패스워드|핀[ \t]?번호|(?:백업|복구|인증)[ \t]?(?:코드|번호))"
    r"(?=[^가-힣]|$|(?:는|은|가|이|를|을|도|로|와|과|의|만|에)(?![가-힣]))"
)
_HINT_TOKEN = re.compile(f"[^\\s{_HANGUL}{_TEXT_PUNCT}\"'`“”‘’()\\[\\]{{}}<>,;|=]+")  # `KEY=`의 =는 낱말을 나눈다
_HINT_STOP = re.compile(r"[,;}]")  # `]`는 NER 표시([이름])에도 있어 멈추지 않는다
HINT_WINDOW = 40   # 낱말 뒤 이 글자 수 안에서 시작하는 낱말만 본다(같은 줄, 쉼표·세미콜론 전까지)
HINT_TOKENS = 5    # 낱말 뒤 몇 낱말까지 볼지
HINT_TOKEN_CHARS = 200  # 한 낱말을 이 글자 수까지만 본다(긴 줄에서 낱말마다 줄 끝까지 훑지 않게)
# ID/PW 이름표의 PW 부분. 이 뒤 `이름 / 값`은 _ID_PW가 값만 가린다
_ID_PW_HEAD = re.compile(r"(?i:(?<![A-Za-z0-9_])id|아이디|계정)[ \t]*/[ \t]*\Z")

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
               ("_SECRET_KEY", _SECRET_KEY), ("_SECRET_VALUE", _SECRET_VALUE), ("_SHORT_KEY", _SHORT_KEY),
               ("_LOOSE_KEY", _LOOSE_KEY), ("_LOOSE_GAP", _LOOSE_GAP), ("_LOOSE_VALUE", _LOOSE_VALUE),
               ("_NETRC", _NETRC), ("_CLI_PASSWORD", _CLI_PASSWORD), ("_ACCOUNT_PAIR", _ACCOUNT_PAIR), ("_ID_PW", _ID_PW),
               ("_CODE_LABEL", _CODE_LABEL), ("_CODE", _CODE)),
}


def detect(text: str) -> list[Finding]:
    """text에서 개인정보 위치를 찾는다. 겹치지 않고, start 순으로 정렬된다."""
    return [hit.finding() for hit in _scan(text.translate(_NORMALIZE))]


def leftover_secret(text: str) -> bool:
    """가린 결과(text)에 비밀번호를 뜻하는 낱말이 있고, 같은 줄 가까이(HINT_WINDOW자·HINT_TOKENS낱말 안, 쉼표·세미콜론 전)에
    가려지지 않은 비밀번호 같은 값이 남았는지. gate.pipeline이 판단 모델의 safe를 믿지 않고 사람에게 물을 때 쓴다(②).

    낱말: password·passwd·passphrase·passcode·pwd·pw·pin·secret·OTP·backup·recovery code, 비밀번호·비번·암호·패스워드·핀번호·
    백업·복구·인증 코드(번호). 비밀번호 같은 값은 정규식 보강과 같은 기준이고(모듈 docstring), 여기서는 연도(19xx·20xx)도 뺀다
    (낱말과 값 사이가 떨어져 있어 "secret santa 2024" 같은 문장이 걸리지 않게). 낱말 뒤 가까이에 `*`(이미 가린 값)가 있으면
    정규식이 처리한 것으로 보고 넘어간다. 값은 돌려주지 않는다(이유·로그에 원문을 남기지 않게).
    """
    view = text.translate(_NORMALIZE)
    for m in _HINT_WORD.finditer(view):
        start = m.end() + (2 if view.startswith(("**", "__"), m.end()) else 0)  # **비밀번호**의 닫는 굵게
        limit = min(len(view), start + HINT_WINDOW + HINT_TOKEN_CHARS)
        line_end = view.find("\n", start, limit)
        line_end = limit if line_end == -1 else line_end
        stop = _HINT_STOP.search(view, start, line_end)
        end = stop.start() if stop else line_end
        if "*" in view[start:min(end, start + HINT_WINDOW)]:
            continue  # 가린 값이 가까이 있다
        for i, token in enumerate(_HINT_TOKEN.finditer(view, start, end)):
            if i >= HINT_TOKENS or token.start() >= start + HINT_WINDOW:
                break
            core = token.group().lstrip(":>-@#").rstrip(".:")  # 앞뒤 부호(->, @이름, 문장 끝 .)는 값이 아니다
            if (_credential_like(core) and not _YEAR.fullmatch(core) and _ends_cleanly(view, token.end())
                    and not _HINT_WORD.fullmatch(core)):
                return True
    return False


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
    - UTF-8이 아닌 인코딩(EUC-KR 등)은 이 함수에서 지원하지 않는다. ASCII 숫자·이메일은 그대로 잡히지만
      한글 키워드가 필요한 계좌는 놓친다. UTF-16은 아무것도 잡지 못한다. 게이트(gate.pipeline)는 이 함수를 쓰지 않고
      gate.encoding으로 인코딩을 알아본 글자를 mask에 넘긴다.
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
        yield from _key_value(text, m, one_word=korean, key=None if korean else m.group("key"))
    for m in _SHORT_KEY.finditer(text):
        short = m.group("short")
        yield from _key_value(text, m, one_word=short is not None, key=m.group(), short=short.lower() if short else None)
    yield from _find_loose_secret(text)
    yield from _find_codes(text)
    for m in _NETRC.finditer(text):
        yield _Hit("secret", *m.span("value"))
    for m in _CLI_PASSWORD.finditer(text):
        group = next(g for g in ("sq", "dq", "bare") if m.group(g) is not None)
        if not m.group(group).startswith(_PLACEHOLDER_START):
            yield _Hit("secret", *m.span(group))
    for pattern, check in ((_ACCOUNT_PAIR, True), (_ID_PW, False)):
        for m in pattern.finditer(text):
            value = m.group("value")
            if value.startswith(_PLACEHOLDER_START) or set(value) <= SEPARATORS | {"*"}:
                continue
            if not check or (_credential_like(value) and _ends_cleanly(text, m.end("value"))):
                yield _Hit("secret", *m.span("value"))


def _key_value(text: str, m: re.Match[str], *, one_word: bool, key: str | None,
               short: str | None = None) -> Iterator[_Hit]:
    """키(m) 뒤 `:`·`=` 값을 가린다. key는 영문 키 이름(password=password 같은 코드를 거른다), 한글 키면 None.
    short는 짧은 키(pw·pwd·pass·pin·passcode)일 때 그 이름이다. 값이 없고 YAML 블록이면 다음 줄들을 본다."""
    head = _ID_PW_HEAD.search(text, max(0, m.start() - 16), m.start())
    if head is not None and _ID_PW.match(text, head.start()):
        return  # ID/PW: 이름 / 값 — 이름이 아니라 / 뒤의 값만 가린다(_ID_PW)
    y = _YAML_OPEN.match(text, m.end())
    if y is not None:
        yield from _yaml_block(text, m.start(), y.end(), block=y.group("indicator") is not None)
        return
    v = (_SECRET_VALUE_ONE_WORD if one_word else _SECRET_VALUE).match(text, m.end())
    if v is None:
        return
    group = next(g for g in ("dq", "sq", "bare") if v.group(g) is not None)
    value = v.group(group)
    start, end = v.span(group)
    if not value.strip() or value.lower() in _NOT_SECRET or value.startswith(_PLACEHOLDER_START):
        return
    if set(value) <= SEPARATORS | {"*"} or (key is not None and value.lower() == key.lower()):
        return  # 이미 가린 값, password=password 같은 코드
    if group == "bare" and text[end:end + 1] in ("(", "["):
        return  # 함수 호출·인덱싱 같은 코드
    if m.re is _SHORT_KEY:
        if not any(ch.isascii() and ch.isalnum() for ch in _HANGUL_CHAR.sub("", value)) \
                or (value[:1] in "/~." and _NOT_CREDENTIAL.fullmatch(value)):
            return  # "PIN: 번호를 입력하세요" 같은 문장, DB_PASS=… 같은 자리표시, PWD=/Users/… 같은 경로
        if short in ("pass", "pin") and not _credential_like(value):
            return  # PASS: 12, pin: GPIO13 같은 흔한 말
    yield _Hit("secret", start, end)


def _yaml_block(text: str, key_start: int, pos: int, *, block: bool) -> Iterator[_Hit]:
    """`key: |`(block) 다음 줄부터 키 줄보다 더 들여 쓴 줄을 모두 가린다. `key:`만 있으면 다음 줄 하나가 더 들여 쓴
    비밀번호 같은 값 한 낱말일 때만 가린다(`key:` 아래 `min_length: 8` 같은 묶음·목록은 아님)."""
    line_start = text.rfind("\n", 0, key_start) + 1
    head = text[line_start:key_start]
    indent = len(head) - len(head.lstrip(" \t"))
    while pos < len(text):
        begin = pos + 1  # pos는 줄바꿈 자리다(\r\n이면 \r)
        if text[pos] == "\r":
            begin += 1
        if begin > len(text):
            return
        end = text.find("\n", begin)
        end = len(text) if end == -1 else end
        line = text[begin:end].rstrip("\r")
        content = line.strip()
        if not content:
            if not block:
                return
            pos = end
            continue
        lead = len(line) - len(line.lstrip(" \t"))
        if lead <= indent:
            return
        start = begin + lead
        if not block:
            if (content.startswith(("-", "#")) or re.search(r":(?:\s|$)", content) or " " in content
                    or not _credential_like(content)):
                return
            yield _Hit("secret", start, start + len(content))
            return
        yield _Hit("secret", start, start + len(content))
        pos = end


def _find_loose_secret(text: str) -> Iterator[_Hit]:
    """구분자 없는 값: "비밀번호는 qwer1234! 입니다", "비번 7741", "암호 → 1q2w3e4r", `password Sup3rS3cret`."""
    for m in _LOOSE_KEY.finditer(text):
        gap = _LOOSE_GAP.match(text, m.end())
        if gap is None:
            continue
        v = _LOOSE_VALUE.match(text, gap.end())
        if v is None:
            continue
        group = next(g for g in ("dq", "sq", "cq", "rq", "bare") if v.group(g) is not None)
        value = v.group(group)
        if group == "bare" and value[0] in ":=":
            continue  # `password : 값`은 구분자 규칙(_SECRET_KEY·_SHORT_KEY)이 가린다
        if _credential_like(value) and _ends_cleanly(text, v.end()):
            yield _Hit("secret", *v.span(group))


def _find_codes(text: str) -> Iterator[_Hit]:
    """OTP·백업·복구·인증 코드 이름 뒤의 코드 목록. 코드마다 따로 가려서 쉼표·줄바꿈은 남긴다."""
    for m in _CODE_LABEL.finditer(text):
        pos = _CODE_GAP.match(text, m.end()).end()
        if not _code_at(text, pos):
            nl = _CODE_NEWLINE.match(text, pos)
            if nl is None or not _code_line(text, nl.end()):
                continue
            pos = nl.end()
        for _ in range(MAX_CODES):
            c = _CODE.match(text, pos)
            if c is None or not _code_like(c.group()) or not _ends_cleanly(text, c.end()):
                break
            yield _Hit("secret", c.start(), c.end())
            sep = _CODE_SEP.match(text, c.end())
            if sep is not None and _code_at(text, sep.end()):
                pos = sep.end()
                continue
            nl = _CODE_NEWLINE.match(text, c.end())
            if nl is None or not _code_line(text, nl.end()):
                break
            pos = nl.end()


def _code_at(text: str, pos: int) -> bool:
    return pos < len(text) and text[pos].isascii() and text[pos].isalnum()


def _code_line(text: str, start: int) -> bool:
    """start부터 줄 끝까지가 코드(영문·숫자·-)와 구분자로만 되어 있는지. 목록이 다음 줄로 이어질 때만 쓴다."""
    limit = min(len(text), start + MAX_CODE_LINE_CHARS + 2)
    end = text.find("\n", start, limit)
    if end == -1 and limit < len(text):
        return False  # 너무 긴 줄
    line = text[start:limit if end == -1 else end].rstrip("\r")
    return 0 < len(line) <= MAX_CODE_LINE_CHARS and _CODE_LINE.fullmatch(line) is not None


def _code_like(token: str) -> bool:
    """OTP·백업 코드처럼 보이는지: 숫자가 있고 영문·숫자 4자 이상. 연도·날짜·버전 등은 아니다."""
    alnum = sum(ch.isascii() and ch.isalnum() for ch in token)
    return (alnum >= 4 and any("0" <= ch <= "9" for ch in token) and not _YEAR.fullmatch(token)
            and not _NOT_CREDENTIAL.fullmatch(token))


def _credential_like(value: str) -> bool:
    """비밀번호 같은 값인지(모듈 docstring "비밀번호 같은 값"). 정규식 보강과 gate.pipeline ②가 같이 쓴다."""
    core = value.rstrip(".?!")  # 문장 부호("password correct?")는 기호로 치지 않는다. 가리는 범위는 그대로다
    if len(core) < 4 or core.startswith(_PLACEHOLDER_START) or set(core) <= SEPARATORS | {"*"}:
        return False
    if core.lower() in _NOT_SECRET or _NOT_CREDENTIAL.fullmatch(core):
        return False
    return any("0" <= ch <= "9" for ch in core) or _CREDENTIAL_SYMBOL.search(core) is not None


def _ends_cleanly(text: str, end: int) -> bool:
    """값 바로 뒤가 한글이 아니거나 조사·어미의 첫 글자인지("7741이야"는 되고 "8~16자로", "5회"는 아님)."""
    nxt = text[end:end + 1]
    return not _HANGUL_CHAR.match(nxt) or nxt in _VALUE_ENDINGS


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
