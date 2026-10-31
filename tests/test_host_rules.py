"""gate.host_rules (3e): IP·내부 주소 규칙. 주소는 모두 지어낸 예시다(문서용 대역·example 도메인)."""

from pathlib import Path

import pytest

from gate.host_rules import INTERNAL_HOST, IP_ADDRESS, find_hosts


def found(text):
    return [(h.kind, text[h.start:h.end]) for h in find_hosts(text)]


# --- IPv4 ---

@pytest.mark.parametrize("text, value", [
    ("서버 주소는 10.0.0.12입니다", "10.0.0.12"),
    ("접속: 192.168.0.1:5432", "192.168.0.1"),
    ("대역 10.0.0.0/8", "10.0.0.0"),
    ("마지막은 203.0.113.255.", "203.0.113.255"),       # 문장 끝 마침표
    ("host=198.51.100.7,port=80", "198.51.100.7"),
    ("(0.0.0.0)", "0.0.0.0"),
])
def test_ipv4_is_found(text, value):
    assert found(text) == [(IP_ADDRESS, value)]


@pytest.mark.parametrize("text", [
    "1.2.3.4.5",            # 칸이 다섯
    "버전 2.10.4",           # 버전 번호(칸 셋)
    "v1.2.3.4",             # 버전 표기
    "release-1.2.3",
    "2026.11.01",           # 날짜
    "26.11.01",
    "256.1.1.1",            # 칸이 255를 넘음
    "01.02.03.04",          # 앞자리 0
    "123.45.67.890",
    "12345.6.7.8",          # 더 긴 숫자열의 일부
    "3.14",
])
def test_ipv4_like_things_are_not_found(text):
    assert found(text) == []


# --- IPv6 ---

@pytest.mark.parametrize("text, value", [
    ("링크 로컬 fe80::1 로 접속", "fe80::1"),
    ("http://[2001:db8::42]:8080/", "2001:db8::42"),
    ("전체 2001:db8:85a3:0:0:8a2e:370:7334 끝", "2001:db8:85a3:0:0:8a2e:370:7334"),
    ("매핑 ::ffff:10.0.0.1", "::ffff:10.0.0.1"),           # 안의 IPv4는 IPv6 하나로
    ("주소 fe80::1: 연결됨", "fe80::1"),                   # 뒤의 쌍점은 문장 부호
    ("FE80::ABCD:1", "FE80::ABCD:1"),
])
def test_ipv6_is_found(text, value):
    assert found(text) == [(IP_ADDRESS, value)]


@pytest.mark.parametrize("text", [
    "회의는 10:11:12에",       # 시각
    "2026-11-01T10:11:12",
    "00:1a:2b:3c:4d:5e",       # MAC 주소
    "std::vector<int>",        # 코드
    "a[::2], x[::-1]",         # 파이썬 자르기
    "::1",                     # 값이 든 칸이 하나뿐(이 컴퓨터 주소)
    "비율 1:2:3",
])
def test_ipv6_like_things_are_not_found(text):
    assert found(text) == []


# --- 내부 주소 ---

@pytest.mark.parametrize("text, value", [
    ("DB는 db.internal.example.local", "db.internal.example.local"),
    ("위키 wiki.intra.example.co.kr에 있음", "wiki.intra.example.co.kr"),
    ("빌드 서버 build-07.internal", "build-07.internal"),
    ("프린터 printer.local로 출력", "printer.local"),
    ("프린터 printer.local(사무실)", "printer.local"),
    ("nas.home.arpa", "nas.home.arpa"),
    ("NAS.LAN", "NAS.LAN"),
    ("localhost.localdomain", "localhost.localdomain"),
    ("api.intranet.example.com/v1", "api.intranet.example.com"),
    ("https://corp.example.com/login", "corp.example.com"),
    ("postgres://app:****@db.internal:5432/app", "db.internal"),
])
def test_internal_host_is_found(text, value):
    assert found(text) == [(INTERNAL_HOST, value)]


@pytest.mark.parametrize("text", [
    "example.com",                 # 일반 도메인
    "www.example.co.kr",
    "home.arpa",                   # 도메인 자체
    "config.local.yaml",           # 파일 이름(마지막 칸이 확장자)
    "settings.local.json",
    ".env.local",
    "threading.local()",           # 코드
    "obj.local(x)",
    "internal",                    # 칸 하나
    "internal-api.example.com",    # 칸 전체가 같아야 한다
    "e.g. local",
])
def test_internal_host_like_things_are_not_found(text):
    assert found(text) == []


def test_email_is_left_to_regex():
    """이메일은 정규식이 먼저 가린다. 규칙은 도메인 부분을 내부 주소로 잡을 수 있지만 받는 쪽이 정규식 범위를 뺀다."""
    assert found("hong@example.com") == []
    text = "admin@db.internal.example.local"
    assert found(text) == [(INTERNAL_HOST, "db.internal.example.local")]


def test_server_list_without_names_is_found():
    """이름 없이 나열한 서버 목록(NER이 놓친 예, eval/README "기술 문서")도 찾는다."""
    text = "servers: build-07.internal, 10.0.0.12, fe80::1"
    assert found(text) == [(INTERNAL_HOST, "build-07.internal"), (IP_ADDRESS, "10.0.0.12"), (IP_ADDRESS, "fe80::1")]


def test_results_are_sorted_and_disjoint():
    text = "10.0.0.1 a.internal ::ffff:192.0.2.1 b.lan 2001:db8::1"
    hits = find_hosts(text)
    assert [h.start for h in hits] == sorted(h.start for h in hits)
    assert all(a.end <= b.start for a, b in zip(hits, hits[1:]))
    assert [h.kind for h in hits] == [IP_ADDRESS, INTERNAL_HOST, IP_ADDRESS, INTERNAL_HOST, IP_ADDRESS]


def test_module_imports_no_other_gate_module():
    source = Path(__file__).resolve().parent.parent.joinpath("gate", "host_rules.py").read_text(encoding="utf-8")
    assert "from gate" not in source and "import gate" not in source
