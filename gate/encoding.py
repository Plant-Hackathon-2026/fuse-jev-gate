"""파일 내용의 글자 인코딩 알아보기 (2026-11-01 사용자 결정, plans/next-tasks-plan.md 5번 A).

게이트는 정규식·규칙·NER·판단 모델을 글자(str)에 돌린다. 예전에는 모든 파일을 UTF-8로 보고 풀어서, UTF-16 파일은
글자 사이에 0 바이트가 끼어 아무것도 찾지 못했고 판단 모델이 깨진 글을 안전으로 보면 원문이 그대로 나갔다.
이제 내용을 보고 인코딩을 고른 뒤 글자로 바꿔 검사하고, 가린 결과는 원래 인코딩으로 돌려준다(encode).

고르는 순서
  1. 빈 내용: UTF-8.
  2. UTF-16 BOM(FF FE, FE FF): BOM 뒤를 그 바이트 순서로 엄격하게 풀어 "그럴듯한 UTF-16 글"(아래)이면 UTF-16.
     아니면 아래 순서를 계속 본다(BOM처럼 보이는 바이트로 시작하는 다른 내용일 수 있다).
     UTF-8 BOM(EF BB BF)은 따로 보지 않는다. 4번에서 U+FEFF 글자 하나로 풀리고 되돌리면 그대로 나온다.
  3. 0 바이트가 하나라도 있으면: BOM 없는 UTF-16(LE·BE)을 둘 다 시도해 그럴듯한 쪽(둘 다면 ASCII가 많은 쪽)을 고른다.
     둘 다 아니면 바이너리(binary)다. 0 바이트가 있는 UTF-8 글은 보통 글이 아니라서 4번으로 가지 않는다.
  4. 엄격한 UTF-8.
  5. 엄격한 CP949(EUC-KR을 포함한다) + 한국어 글인지 확인(아래). 한글 윈도우 문서를 읽기 위한 것이다.
  6. 그 밖: 알아보지 못함(unknown). 게이트는 차단한다(안전한 쪽). 섞인 인코딩, UTF-8 중간의 잘못된 바이트,
     Latin-1·Shift-JIS 같은 다른 인코딩이 여기에 온다.

그럴듯한 UTF-16 글
  - 글자 단위(코드 유닛) 중 ASCII(U+0000~U+007F)가 5% 이상이다. 진짜 글에는 띄어쓰기·줄바꿈·숫자·문장 부호가 있다.
    UTF-8 글을 UTF-16으로 잘못 풀면 0 바이트가 없어 ASCII 글자가 하나도 나오지 않는다(한글·한자처럼 보이는 글자만 나온다).
    LE 글을 BE로 잘못 풀어도 ASCII가 거의 나오지 않는다.
  - 이상한 글자(제어 문자 중 탭·줄바꿈·폼 피드 말고, 쓰지 않는 코드 포인트, 사용자 정의 영역)가 1% 이하다.
    0 바이트가 0 글자(U+0000)로 풀리면 이상한 글자다(UTF-32, 바이너리).
  - 그래서 ASCII가 하나도 없는 짧은 UTF-16 글(예: BOM + "홍길동")은 알아보지 못함 → 차단이다.

한국어 CP949 글
  - ASCII가 아닌 글자 중 자주 쓰는 한글(EUC-KR에 있는 완성형 2,350자와 호환 자모)이 절반 이상이고, 이상한 글자가 1% 이하다.
    한자가 대부분인 글이나 다른 인코딩을 잘못 푼 글을 거른다. 예를 들어 Shift-JIS 일본어를 CP949로 풀면
    `궞귪궸`처럼 CP949에만 있는 드문 한글이 나온다. ASCII 부분은 어느 쪽이든 그대로 풀린다.

한계
  - UTF-32, ASCII가 없는 UTF-16, 한자가 대부분인 EUC-KR 문서는 알아보지 못한다(차단하거나 바이너리로 본다).
  - 진짜 바이너리여도 0 바이트가 없고 UTF-8이나 한국어 CP949로 풀리면 글로 본다(예전과 같은 흐름).
"""

import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

Kind = Literal["text", "binary", "unknown"]

UTF16_MIN_ASCII_RATIO = 0.05
MAX_ODD_RATIO = 0.01
CP949_MIN_HANGUL_RATIO = 0.5

_BOMS = ((b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))
NAMES = {"utf-8": "UTF-8", "utf-16-le": "UTF-16", "utf-16-be": "UTF-16", "cp949": "EUC-KR"}
_ALLOWED_CONTROLS = frozenset("\t\n\r\f\v")


@dataclass(frozen=True)
class Decoded:
    """알아본 결과. text 종류면 text에 글자, codec·bom에 되돌리는 방법이 있다."""
    kind: Kind
    text: str = ""
    codec: str = ""   # Python 코덱 이름: utf-8, utf-16-le, utf-16-be, cp949. text가 아니면 빈 문자열
    bom: bytes = b""  # UTF-16 BOM. encode가 앞에 다시 붙인다

    @property
    def name(self) -> str:
        """사람이 읽는 이름(UTF-8, UTF-16, EUC-KR). text가 아니면 빈 문자열."""
        return NAMES.get(self.codec, self.codec)

    def encode(self, text: str) -> bytes:
        """text(가린 결과)를 원래 인코딩으로 되돌린다. 바꿀 수 없는 글자가 있으면 UnicodeEncodeError."""
        if self.kind != "text":
            raise ValueError("글로 알아본 내용만 되돌릴 수 있습니다")
        return self.bom + text.encode(self.codec)


def decode_content(data: bytes) -> Decoded:
    """data의 인코딩을 알아보고 글자로 푼다. 순서는 모듈 설명 참고."""
    if not data:
        return Decoded("text", "", "utf-8")
    for bom, codec in _BOMS:
        if data.startswith(bom):
            text = _strict(data[len(bom):], codec)
            if text is not None and _plausible_utf16(text):
                return Decoded("text", text, codec, bom)
    if b"\x00" in data:
        candidates = []
        for codec in ("utf-16-le", "utf-16-be"):
            text = _strict(data, codec)
            if text is not None and _plausible_utf16(text):
                candidates.append((_ascii_ratio(text), codec, text))
        if candidates:
            _, codec, text = max(candidates)
            return Decoded("text", text, codec)
        return Decoded("binary")
    text = _strict(data, "utf-8")
    if text is not None:
        return Decoded("text", text, "utf-8")
    text = _strict(data, "cp949")
    if text is not None and _plausible_korean(text):
        return Decoded("text", text, "cp949")
    return Decoded("unknown")


def _strict(data: bytes, codec: str) -> str | None:
    try:
        return data.decode(codec)
    except UnicodeDecodeError:
        return None


def _odd(ch: str) -> bool:
    """글에 나오면 이상한 글자: 탭·줄바꿈 말고 제어 문자, 쓰지 않는 코드 포인트, 사용자 정의 영역, 대리 문자."""
    if ch in _ALLOWED_CONTROLS:
        return False
    return unicodedata.category(ch) in ("Cc", "Cn", "Co", "Cs")


def _odd_ratio(text: str) -> float:
    return sum(map(_odd, text)) / len(text) if text else 0.0


def _ascii_ratio(text: str) -> float:
    return sum(ch < "\x80" for ch in text) / len(text) if text else 0.0


def _plausible_utf16(text: str) -> bool:
    return bool(text) and _ascii_ratio(text) >= UTF16_MIN_ASCII_RATIO and _odd_ratio(text) <= MAX_ODD_RATIO


@lru_cache(maxsize=4096)
def _common_hangul(ch: str) -> bool:
    """EUC-KR(KS X 1001)에 있는 한글. CP949가 더한 드문 음절(똠·햏 등)은 아니다."""
    if not ("가" <= ch <= "힣" or "ㄱ" <= ch <= "ㆎ"):
        return False
    # Python의 euc_kr 코덱은 드문 음절도 8바이트 조합형으로 바꿔 주므로 쓸 수 없다. CP949에서 두 바이트가 모두
    # 0xA1 이상이면 EUC-KR(KS X 1001) 영역이다. CP949가 더한 음절은 둘째 바이트가 0xA1보다 작거나 첫 바이트가 0xA1보다 작다.
    raw = ch.encode("cp949")
    return len(raw) == 2 and raw[0] >= 0xA1 and raw[1] >= 0xA1


def _plausible_korean(text: str) -> bool:
    wide = [ch for ch in text if ch >= "\x80"]
    if not wide:
        return False  # ASCII뿐이면 UTF-8에서 이미 풀렸다
    return sum(map(_common_hangul, wide)) / len(wide) >= CP949_MIN_HANGUL_RATIO and _odd_ratio(text) <= MAX_ODD_RATIO
