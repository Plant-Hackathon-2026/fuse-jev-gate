"""IP·내부 주소 규칙 (3e): NER 대신 글자 규칙으로 IP 주소와 내부 호스트 이름을 찾는다. 표준 라이브러리만 쓴다.

새 NER(팀원 재학습판 6종)에는 IP·내부 주소 종류가 없어서 이 규칙이 찾는다(plans/ner-v2-plan.md 3.3, 2026-11-01).
찾은 것은 정책 묶음 host("IP·내부 주소", 기본 가림)로 가고 `[IP]`·`[내부 주소]`로 바뀐다(칸·표시는 gate.ner_client).
NER을 꺼도 늘 적용된다(gate.pipeline). 데몬 쪽 다른 모듈을 import하지 않는다.

규칙
  - IPv4(IP_ADDRESS): 점으로 이은 네 칸, 칸마다 0~255(앞자리 0 없이). 앞에 숫자·영문·`_`·`.`가 붙거나
    뒤에 숫자나 `.숫자`가 이어지면 아니다(`1.2.3.4.5`, 더 긴 숫자열의 일부, `v1.2.3.4` 같은 버전 표기는 잡지 않음).
    문장 끝의 마침표(`10.0.0.12.`)는 괜찮다.
  - IPv6(IP_ADDRESS): 16진수와 `:`로 된 글자 묶음이 ipaddress로 올바른 IPv6이고, `::`가 있거나 16진수 칸이 6개 이상
    (`::ffff:10.0.0.1`처럼 끝의 IPv4는 두 칸으로 센다). 시각(`10:11:12`)과 MAC 주소는 IPv6가 아니라 잡지 않는다.
    `::`가 있어도 값이 든 칸이 두 개 미만(`::`, `::1`)이면 잡지 않는다. 코드의 자르기(`a[::2]`, `x[::-1]`)와 구별할 수
    없어서다(`::1`은 이 컴퓨터 주소라 가릴 정보도 아니다).
  - 내부 주소(INTERNAL_HOST): 점으로 이은 호스트 이름(칸 둘 이상) 중 칸 하나가 internal·intra·intranet·corp·local·lan·
    localdomain이거나 끝이 home.arpa인 것. 그 칸이 마지막 칸이거나(`build-07.internal`, `printer.local`),
    마지막 칸이 흔한 최상위 도메인(_TLDS)일 때만 잡는다(`db.internal.example.local`, `wiki.intra.example.co.kr`).
    그래서 `config.local.yaml`·`settings.local.json` 같은 파일 이름은 잡지 않는다. 바로 뒤에 `()`나 `(영문`이 오면
    (`threading.local()`) 코드로 보고 뺀다(`printer.local(10.0.0.5)`는 잡는다). 일반 도메인(`example.com`)은 잡지 않는다.
  - 이메일·URL의 사용자 정보(`user@db.internal`, `postgres://u:pw@…`의 비밀번호)는 정규식(gate.detectors)이 먼저 가린다.
    받는 쪽(gate.pipeline)이 정규식 범위를 뺀다(gate.ner_client.outside).

겹치면 긴 것을 고른다(`::ffff:10.0.0.1` 안의 IPv4는 IPv6 하나로). 결과는 start 순이고 서로 겹치지 않는다.
"""

import ipaddress
import re
from dataclasses import dataclass

IP_ADDRESS = "IP_ADDRESS"
INTERNAL_HOST = "INTERNAL_HOST"
KINDS: tuple[str, ...] = (IP_ADDRESS, INTERNAL_HOST)

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = re.compile(r"(?<![A-Za-z0-9_.])" + _OCTET + r"(?:\." + _OCTET + r"){3}(?!\d)(?!\.\d)")
# 16진수·`:`·`.`이 이어진 묶음 중 `:`가 두 번 이상인 것. 올바른지는 ipaddress로 본다.
_IPV6_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_:.])[0-9A-Fa-f:.]*:[0-9A-Fa-f.]*:[0-9A-Fa-f:.]*")
_IPV6_MIN_GROUPS = 6
_IPV6_MIN_GROUPS_WITH_GAP = 2
_WORD_AFTER = re.compile(r"[A-Za-z0-9_]")

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOSTNAME = re.compile(r"(?<![A-Za-z0-9_.-])" + _LABEL + r"(?:\." + _LABEL + r")+(?![A-Za-z0-9_-])")
_CALL_AFTER = re.compile(r"\((?:\)|[A-Za-z_])")  # `threading.local()`, `obj.local(x)` 같은 함수 호출
INTERNAL_LABELS = frozenset({"internal", "intra", "intranet", "corp", "local", "lan", "localdomain"})
# 내부 칸이 가운데에 있을 때 마지막 칸으로 받는 최상위 도메인(흔한 것만). 파일 확장자(yaml, json 등)와 구별하려는 것이다.
_TLDS = frozenset({
    "com", "net", "org", "io", "co", "kr", "jp", "cn", "us", "uk", "de", "fr", "eu", "dev", "app", "ai", "cloud",
    "biz", "info", "me", "xyz", "tech", "site", "online", "edu", "gov", "mil",
})


@dataclass(frozen=True)
class HostHit:
    kind: str   # KINDS 중 하나
    start: int  # 문자(str 인덱스), end 미포함
    end: int


def find_hosts(text: str) -> list[HostHit]:
    """text에서 IP 주소와 내부 주소를 찾는다. start 순, 겹치지 않음."""
    hits = [*_find_ipv4(text), *_find_ipv6(text), *_find_internal(text)]
    chosen: list[HostHit] = []
    for hit in sorted(hits, key=lambda h: (h.start - h.end, h.start)):  # 긴 것 → 앞에 있는 것
        if all(hit.end <= c.start or c.end <= hit.start for c in chosen):
            chosen.append(hit)
    return sorted(chosen, key=lambda h: h.start)


def _find_ipv4(text: str):
    for m in _IPV4.finditer(text):
        yield HostHit(IP_ADDRESS, m.start(), m.end())


def _find_ipv6(text: str):
    for m in _IPV6_CANDIDATE.finditer(text):
        if _WORD_AFTER.match(text, m.end()):
            continue  # 영문·숫자로 더 이어진다(16진수가 아닌 글자가 붙은 낱말)
        value = m.group().rstrip(".")
        if value.endswith(":") and not value.endswith("::"):
            value = value[:-1]  # 문장 속 쌍점(`fe80::1:`)
        if not _valid_ipv6(value):
            continue  # 시각(10:11:12), MAC 주소 등
        end = m.start() + len(value)
        groups = [g for g in value.split(":") if g]
        count = len(groups) + (1 if groups and "." in groups[-1] else 0)  # 끝의 IPv4는 두 칸
        if ("::" in value and count >= _IPV6_MIN_GROUPS_WITH_GAP) or count >= _IPV6_MIN_GROUPS:
            yield HostHit(IP_ADDRESS, m.start(), end)


def _valid_ipv6(value: str) -> bool:
    try:
        ipaddress.IPv6Address(value)
    except ValueError:
        return False
    return True


def _find_internal(text: str):
    for m in _HOSTNAME.finditer(text):
        labels = m.group().lower().split(".")
        if _CALL_AFTER.match(text, m.end()) or labels[-1].isdecimal():
            continue  # 함수 호출 같은 코드, 숫자로 끝나면 IP·버전 표기다
        if _is_internal(labels):
            yield HostHit(INTERNAL_HOST, m.start(), m.end())


def _is_internal(labels: list[str]) -> bool:
    if len(labels) >= 3 and labels[-2:] == ["home", "arpa"]:
        return True
    if labels[-1] in INTERNAL_LABELS:
        return True
    return labels[-1] in _TLDS and any(label in INTERNAL_LABELS for label in labels[:-1])
