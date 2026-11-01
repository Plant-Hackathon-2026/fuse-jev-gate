"""gate.encoding: 파일 내용의 인코딩 알아보기(2026-11-01 사용자 결정). 모든 값은 가짜 예시다."""

import random

import pytest

from gate.encoding import Decoded, decode_content

DOC = "상담 기록\n이름: 홍길동\n휴대폰: 010-1234-5678\npassword: hunter2\n"


@pytest.mark.parametrize("data, codec, bom", [
    (DOC.encode("utf-8"), "utf-8", b""),
    (DOC.encode("utf-8-sig"), "utf-8", b""),                     # UTF-8 BOM은 U+FEFF 글자로 남는다
    (b"\xff\xfe" + DOC.encode("utf-16-le"), "utf-16-le", b"\xff\xfe"),
    (b"\xfe\xff" + DOC.encode("utf-16-be"), "utf-16-be", b"\xfe\xff"),
    (DOC.encode("utf-16-le"), "utf-16-le", b""),                 # BOM 없음: 0 바이트 위치로 고른다
    (DOC.encode("utf-16-be"), "utf-16-be", b""),
    (DOC.encode("cp949"), "cp949", b""),                         # 한글 윈도우 문서(EUC-KR)
    ("주소: 서울시 마포구, 똠양꿍 맛집\n".encode("cp949"), "cp949", b""),  # CP949에만 있는 음절이 조금 섞여도
    (b"", "utf-8", b""),
    (b"plain ascii\n", "utf-8", b""),
])
def test_text_is_decoded_and_encodes_back_to_the_same_bytes(data, codec, bom):
    decoded = decode_content(data)
    assert (decoded.kind, decoded.codec, decoded.bom) == ("text", codec, bom)
    assert decoded.text.lstrip("﻿") == data[len(bom):].decode(codec).lstrip("﻿")
    assert decoded.encode(decoded.text) == data


def test_masked_text_goes_back_to_the_original_encoding():
    for data in (DOC.encode("utf-16"), DOC.encode("utf-16-be"), DOC.encode("cp949")):
        decoded = decode_content(data)
        masked = decoded.text.replace("홍길동", "[이름]").replace("hunter2", "*******")
        back = decoded.encode(masked)
        assert back.startswith(decoded.bom)
        assert back[len(decoded.bom):].decode(decoded.codec) == masked


@pytest.mark.parametrize("data", [
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)),   # 이미지
    bytes(range(256)),
    DOC.encode("utf-32"),                                          # UTF-32는 지원하지 않는다(0 글자가 많다)
    "hello".encode() + b"\x00" * 20,                               # 0 바이트가 낀 UTF-8
    random.Random(7).randbytes(4096),
])
def test_content_with_zero_bytes_that_is_not_utf16_is_binary(data):
    assert decode_content(data) == Decoded("binary")


@pytest.mark.parametrize("data", [
    "홍길동".encode("utf-16"),                                     # ASCII가 하나도 없는 UTF-16(0 바이트 없음)
    b"\xff\xfe" + "연락처 010-1234-5678".encode() + b"\x80",      # BOM처럼 시작하는 UTF-8 + 잘못된 바이트
    DOC.encode() + b"\xb0\x80\n",                                  # UTF-8 중간의 잘못된 바이트(섞인 인코딩)
    "café résumé\n".encode("latin-1"),
    "これは日本語のテキストです。電話番号は03-1234-5678です。\n".encode("shift_jis"),
    "这是一个中文文档，包含一些内容。\n".encode("gbk"),
    bytes(b or 1 for b in random.Random(8).randbytes(1000)),        # 0 바이트 없는 무작위 바이트
])
def test_content_no_encoding_explains_is_unknown(data):
    assert decode_content(data) == Decoded("unknown")


def test_le_and_be_are_told_apart_by_ascii():
    text = "가대글저 abc 123\n" * 3  # 낮은 바이트가 0인 한글(U+AC00 등)이 섞여 있어도
    assert decode_content(text.encode("utf-16-le")).codec == "utf-16-le"
    assert decode_content(text.encode("utf-16-be")).codec == "utf-16-be"


def test_names_and_encode_guard():
    assert decode_content(DOC.encode("utf-16")).name == "UTF-16"
    assert decode_content(DOC.encode("cp949")).name == "EUC-KR"
    assert decode_content(DOC.encode()).name == "UTF-8"
    with pytest.raises(ValueError):
        Decoded("binary").encode("x")
