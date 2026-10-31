"""판단 파이프라인: 게이트가 파일 내용을 돌려주기 전에 거치는 단계.

순서 (명세 2절, 계획 문서 4절)
  1. 정책: PolicySource.current(). 정책 파일이 잘못되어 있으면 차단한다.
  2. 경로 규칙: 요청 경로와 실제 경로(심볼릭 링크를 따라간 것) 둘 다로 본다.
     deny면 내용을 읽지 않고 끝낸다. allow면 검사 없이 원본을 돌려준다. ask_human이면 아래 검사를 마친 뒤 묻는다.
  3. 파일 전체를 읽어 정규식으로 가린다(mask_bytes, 종류별 공개 범위는 정책). 찾은 종류별 동작이 deny면 Jev를 부르지 않는다.
  3e. 규칙(gate.host_rules): IP 주소·내부 주소를 찾는다. NER을 꺼도 늘 돈다. 정규식 범위와 겹치는 부분은 뺀다.
  3c. NER(ner_backend가 none이 아닐 때, 기본 켜짐): 원문을 로컬 NER 서버에 보내 이름·연락처·소속 등의 위치를 받는다.
     정규식·규칙 범위와 겹치는 부분은 뺀다(우선순위: 정규식 > 규칙 > NER). 규칙과 NER 개체를 합쳐(겹치지 않는다)
     칸(entities 묶음 또는 numbers 종류)별 동작이 allow인 개체는 그대로 두고, deny면 Jev를 부르지 않고 차단한다.
     NER 실패(서버 없음, 타임아웃, 형식 오류)는 차단이다.
  4. 캐시 → Jev. 가린 내용과 요청 맥락(요청 프로세스, 실제 경로, 목적, 사용자 맥락 규칙)을 넘긴다.
     max_inspect_bytes보다 크면 나눠 묻고 가장 엄격한 답을 쓴다. Jev 오류·예외·형식 오류는 차단이다.
     - NER이 꺼져 있거나 가린 것이 없으면: 정규식(과 규칙)으로 가린 내용(3b와 같은 흐름). 답은 임계값 두 개로 허용·승인·차단.
       NER이 꺼져 있으면 규칙으로 가렸어도 이 흐름이다.
     - NER이 켜져 있고 가린 것(정규식·NER)이 있으면(3c ②, 2026-10-31 사용자 결정): 정규식과 NER로 가린 결과, 즉 에이전트에게
       보여 줄 내용을 묻는다. 안전(safe)이면 가린 채 보여 주고, 아니면 가려지지 않은 정보가 남았다고 보고 사람에게 묻는다.
       NER이 이름을 놓쳐도 Jev가 한 번 더 걸러 내게 하려는 것이다(팀원 v3의 has_pii가 "가려지지 않은 채 남았는가"를 묻는다).
  5. 가장 엄격한 동작으로 합친다. allow인데 가린 것이 있으면 가린 내용(mask)을 돌려준다.
  6. ask_human이면 사람에게 묻는다(gate.approval). 승인하면 가린 내용(찾은 게 없으면 원본과 같다)을 돌려주고,
     거부·무응답이면 차단한다. 이미 "이 폴더 허용"을 받았으면 묻지 않는다. 결정한 단계(stage)는 human이다.
     번호 deny·Jev deny로 어차피 막힐 파일은 묻지 않는다(그래서 경로 ask_human도 검사를 먼저 한다).

- 승인 창구(approvals)가 없거나 ask=False(search)면 묻지 않고 ask_human 그대로 돌려준다(Blocked, EACCES).
  ask=False여도 이미 받은 폴더 승인은 쓴다.
- 빈 파일은 Jev에 묻지 않고 허용한다(보여 줄 내용이 없다). 경로 규칙이 ask_human이면 빈 파일도 묻는다.
- Jev에는 가린 내용을 UTF-8로 풀어 넘긴다. UTF-8이 아닌 바이트는 U+FFFD로 바꾼다. 판단용 사본일 뿐이고,
  돌려주는 내용은 바이트 그대로다. 정규식이 가린 부분은 같은 길이 `*`, 규칙·NER이 가린 부분은 `[이름]`·`[IP]`처럼
  종류 이름이라 무언가를 가리면 길이가 원본과 달라진다.
- NER 결과(위치만, 원문 없음)는 내용 해시 + NER 모델 식별값으로 캐시한다. 정책과 무관한 값이라 정책이 바뀌어도 그대로 쓴다.
- 나눠 물을 때 경계에 걸친 맥락(앞 조각의 이름 + 뒤 조각의 병명 등)은 Jev가 한 번에 보지 못한다.
"""

import errno
import hashlib
import posixpath
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal

from gate.approval import AnsweredBy, Approval, ApprovalRequest, Approvals
from gate.cache import CachedJudgment, CacheKey, JudgmentCache
from gate.detectors import mask_bytes
from gate.host_rules import find_hosts
from gate.jev_client import JevClient, JevDecision, JevError, RequestContext, cache_tag, make_client
from gate.ner_client import MASK_TEXT, Entity, NerClient, NerError, NerResult, make_ner_client, outside, policy_slot
from gate.peer import Peer
from gate.policy import (
    ACTIONS,
    KIND_NAMES,
    Action,
    Policy,
    PolicyError,
    PolicySource,
    jev_action,
    number_action,
    path_action,
    slot_action,
    strictest,
)
from gate.protocol import GateError
from gate.store import Store

Stage = Literal["policy", "path", "number", "entity", "jev", "empty", "human"]
JevFactory = Callable[[Policy], JevClient]
NerFactory = Callable[[Policy], "NerClient | None"]
MAX_NER_CACHE = 1024

MAX_SEARCH_FILES = 1000
MAX_SEARCH_RESULTS = 1000
MAX_SEARCH_DEPTH = 32
MAX_PATTERN_CHARS = 1000
MAX_LINE_CHARS = 300


@dataclass(frozen=True)
class Judgment:
    """판단 요약. 감사 로그(Phase 7)에 그대로 쓸 수 있도록 원문과 탐지 값은 담지 않는다."""
    action: Action
    reason: str                     # 사람이 읽는 이유. 차단이면 요청자에게 그대로 간다
    stage: Stage                    # 결정이 난 단계
    policy_version: str | None = None
    content_sha256: str | None = None
    findings: Mapping[str, int] = field(default_factory=dict)  # 종류별 탐지 건수
    jev_label: str | None = None
    jev_confidence: float | None = None
    jev_calls: int = 0
    cached: bool = False
    approval: Approval | None = None          # 사람 승인 결과. once·session·remembered면 승인, deny·timeout이면 차단
    approved_by: AnsweredBy | None = None     # 누가 답했나. remembered·timeout이면 None
    # findings 중 규칙·NER이 `[이름]`처럼 종류 이름으로 가린 것의 종류별 건수. 나머지는 정규식이 같은 길이 `*`로 가렸다.
    # NER도 휴대폰·일반 전화·이메일 칸을 쓰므로(정규식이 놓친 번호) 종류 이름만으로는 가린 방식을 알 수 없어 따로 센다.
    labeled: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "findings", MappingProxyType(dict(self.findings)))
        object.__setattr__(self, "labeled", MappingProxyType(dict(self.labeled)))


@dataclass(frozen=True)
class Verdict:
    judgment: Judgment
    data: bytes | None = field(default=None, repr=False)  # allow면 원본, mask면 가린 내용, 그 밖은 None

    @property
    def readable(self) -> bool:
        return self.judgment.action in ("allow", "mask")


class Blocked(GateError):
    """판단 결과가 deny나 ask_human(묻지 않았음)이다. EACCES로 돌려준다."""

    def __init__(self, judgment: Judgment) -> None:
        if judgment.action == "ask_human":
            message = f"사람 승인이 필요합니다 ({judgment.reason})"
        else:
            message = judgment.reason
        super().__init__(errno.EACCES, message)
        self.judgment = judgment


@dataclass(frozen=True)
class _JevResult:
    action: Literal["allow", "ask_human", "deny"]
    label: str | None
    confidence: float | None
    calls: int
    cached: bool
    error: str | None = None


@dataclass(frozen=True)
class _NerOutcome:
    enabled: bool
    spans: tuple[tuple[Entity, str], ...] = ()  # (개체, 정책 칸). 정규식·규칙 범위를 뺀 것
    error: str | None = None
    model: str = ""  # NER 모델 식별값. 가린 결과를 Jev에 물을 때 판단 캐시 키에 넣는다


def default_jev_factory(policy: Policy) -> JevClient:
    return make_client(policy.jev_backend, timeout=policy.jev_timeout_seconds)


def default_ner_factory(policy: Policy) -> "NerClient | None":
    return make_ner_client(policy.ner_backend, timeout=policy.ner_timeout_seconds)


def _rule_spans(text: str, regex_ranges: list[tuple[int, int]]) -> list[tuple[Entity, str]]:
    """규칙(IP·내부 주소, 3e)이 찾은 것. 정규식 범위를 뺀다(정규식 우선). 칸은 host다."""
    found = [Entity(hit.kind, hit.start, hit.end, 1.0) for hit in find_hosts(text)]
    return [(e, policy_slot(e.label, text[e.start:e.end])) for e in outside(found, regex_ranges, text)]


def split_utf8(data: bytes, max_bytes: int) -> list[bytes]:
    """올바른 UTF-8 바이트를 max_bytes 이하 조각으로 나눈다. 글자 중간에서 자르지 않고, 뒤쪽 절반에 줄바꿈이 있으면 거기서 자른다."""
    if max_bytes < 4:
        raise ValueError("max_bytes는 4 이상이어야 합니다")
    chunks = []
    start = 0
    while len(data) - start > max_bytes:
        end = start + max_bytes
        while (data[end] & 0xC0) == 0x80:  # 이어지는 바이트면 글자 시작까지 물러난다
            end -= 1
        newline = data.rfind(b"\n", start + max_bytes // 2, end)
        if newline != -1:
            end = newline + 1
        chunks.append(data[start:end])
        start = end
    chunks.append(data[start:])
    return chunks


def _kind_names(findings: Mapping[str, int]) -> str:
    return ", ".join(KIND_NAMES.get(kind, kind) for kind in findings)


class Inspector:
    """파일 하나를 판단한다. 데몬의 여러 스레드가 같이 쓴다."""

    def __init__(
        self,
        store: Store,
        policy_source: PolicySource | None = None,
        *,
        jev_factory: JevFactory = default_jev_factory,
        ner_factory: NerFactory = default_ner_factory,
        cache: JudgmentCache | None = None,
        approvals: Approvals | None = None,
    ) -> None:
        self.store = store
        self.policy_source = policy_source if policy_source is not None else PolicySource(None)
        self.jev_factory = jev_factory
        self.ner_factory = ner_factory
        self.cache = cache if cache is not None else JudgmentCache()
        self.approvals = approvals
        self._clients: dict[tuple[str, float], JevClient] = {}
        self._ner_clients: dict[tuple[str, float], NerClient | None] = {}
        self._ner_cache: OrderedDict[tuple[str, str], tuple[Entity, ...]] = OrderedDict()
        self._lock = threading.Lock()

    def inspect(self, peer: Peer | None, vpath: str, purpose: str | None = None, *, ask: bool = True) -> Verdict:
        """판단 결과를 돌려준다. 경로가 없거나 원본 밖이면 GateError(ENOENT, EACCES 등)를 그대로 던진다.
        ask면 ask_human일 때 사람에게 묻고 답을 기다린다(approval.timeout_seconds까지)."""
        try:
            policy = self.policy_source.current()
        except PolicyError as exc:
            return Verdict(Judgment("deny", f"정책 파일에 문제가 있어 차단했습니다: {exc}", "policy"))
        real = self.store.canonical(vpath)
        by_path = path_action(policy, [vpath, real])
        if by_path == "deny":
            return Verdict(Judgment("deny", "경로 규칙에 따라 차단했습니다", "path", policy.version))

        data = self.store.read(real)
        digest = hashlib.sha256(data).hexdigest()
        if by_path == "allow":
            return Verdict(Judgment("allow", "경로 규칙에 따라 검사 없이 허용했습니다", "path", policy.version, digest), data)

        masked, found = mask_bytes(data, visible=policy.visible)
        counts = Counter(f.kind for f in found)
        common = {"policy_version": policy.version, "content_sha256": digest, "findings": dict(counts)}
        by_number = number_action(policy, found)
        if by_number == "deny":
            denied = {k: n for k, n in counts.items() if policy.number_rule(k) == "deny"}
            return Verdict(Judgment("deny", f"정책에 따라 {_kind_names(denied)}이(가) 있는 파일은 차단합니다",
                                    "number", **common))
        if not data and by_path is None:
            return Verdict(Judgment("allow", "빈 파일입니다", "empty", **common), data)

        text = data.decode("utf-8", "surrogateescape")
        regex_ranges = [(f.start, f.end) for f in found]
        rules = _rule_spans(text, regex_ranges)
        ner = self._entities(policy, text, digest, regex_ranges + [(e.start, e.end) for e, _ in rules]) \
            if data else _NerOutcome(False)
        if ner.error is not None:
            return Verdict(Judgment("deny", ner.error, "entity", **common))
        spans = sorted([*rules, *ner.spans], key=lambda span: span[0].start)
        hidden = [(e, slot) for e, slot in spans if policy.slot_rule(slot) != "allow"]
        entity_counts = Counter(slot for _, slot in hidden)
        counts += entity_counts
        common["findings"] = dict(counts)
        common["labeled"] = dict(entity_counts)
        by_entity = slot_action(policy, [slot for _, slot in hidden])
        if by_entity == "deny":
            denied = {k: n for k, n in entity_counts.items() if policy.slot_rule(k) == "deny"}
            return Verdict(Judgment("deny", f"정책에 따라 {_kind_names(denied)}이(가) 있는 파일은 차단합니다",
                                    "entity", **common))
        shown = masked
        if hidden:
            replacements = [(e.start, e.end, MASK_TEXT[e.label]) for e, _ in hidden]
            shown, _ = mask_bytes(data, visible=policy.visible, replacements=replacements)

        # 3c ②(2026-10-31 사용자 결정): NER이 켜져 있고 가린 것이 있으면 Jev는 가린 결과(보여 줄 내용)를 판단한다.
        # 안전이면 가린 채로 보여 주고, 아니면(가려지지 않은 정보가 남았다고 보면) 사람에게 묻는다. Jev 오류는 차단 그대로.
        # NER이 꺼져 있으면 규칙으로 가렸어도 3b 흐름이다. Jev는 어느 쪽이든 보여 줄 내용(shown)을 본다.
        recheck = ner.enabled and bool(counts)
        jev = self._judge(policy, peer, real, purpose, shown, digest, ner.model if recheck else "") if data else None
        jev_info = {} if jev is None else {"jev_label": jev.label, "jev_confidence": jev.confidence,
                                           "jev_calls": jev.calls, "cached": jev.cached}
        jev_effective = jev.action if jev else None
        if recheck and jev is not None and jev.error is None:
            jev_effective = "allow" if jev.label == "safe" else "ask_human"
        final = strictest(by_path, by_number, by_entity, jev_effective)
        if final == "deny":
            reason = jev.error or f"Jev 판단({jev.label}, 확신도 {jev.confidence:.2f})에 따라 차단했습니다"
            return Verdict(Judgment("deny", reason, "jev", **common, **jev_info))
        if final == "ask_human":
            if by_path == "ask_human":
                stage, reason = "path", "경로 규칙에 따라 사람에게 물어봅니다"
            elif jev_effective == "ask_human" and recheck:
                stage, reason = "jev", (f"가린 뒤에도 Jev가 가려지지 않은 정보가 남았다고 판단했습니다"
                                        f"({jev.label}, 확신도 {jev.confidence:.2f})")
            elif jev_effective == "ask_human":
                stage, reason = "jev", f"Jev 판단({jev.label}, 확신도 {jev.confidence:.2f})이 확실하지 않습니다"
            else:
                asked = {k: n for k, n in counts.items() if policy.slot_rule(k) == "ask_human"}
                stage = "number" if by_number == "ask_human" else "entity"
                reason = f"정책에 따라 {_kind_names(asked)}이(가) 있는 파일은 사람에게 물어봅니다"
            judgment = Judgment("ask_human", reason, stage, **common, **jev_info)
            return self._ask(policy, judgment, peer, vpath, real, purpose, shown if counts else data, ask)
        if counts:
            reason = f"{_kind_names(counts)}을(를) 가렸습니다"
            if recheck and jev is not None:
                reason += f" (가린 뒤 Jev 판단 {jev.label}, 확신도 {jev.confidence:.2f})"
            return Verdict(Judgment("mask", reason, "jev", **common, **jev_info), shown)
        return Verdict(Judgment("allow", "허용했습니다", "jev", **common, **jev_info), data)

    def _entities(self, policy: Policy, text: str, digest: str, taken: list[tuple[int, int]]) -> _NerOutcome:
        """NER로 개체를 찾는다. 꺼져 있으면 enabled=False. 실패하면 error(차단 이유)를 채운다.
        taken(정규식·규칙이 찾은 범위)과 겹치는 부분은 뺀다."""
        try:
            client = self._ner_client(policy)
            if client is None:
                return _NerOutcome(False)
            tag = client.cache_tag()
            with self._lock:
                entities = self._ner_cache.get((digest, tag))
                if entities is not None:
                    self._ner_cache.move_to_end((digest, tag))
            model = tag
            if entities is None:
                result = client.find(text)
                if not isinstance(result, NerResult):
                    raise NerError(f"NER 응답 형식이 올바르지 않습니다: {type(result).__name__}")
                entities, model = result.entities, result.model or tag
                with self._lock:
                    self._ner_cache[(digest, model)] = entities
                    while len(self._ner_cache) > MAX_NER_CACHE:
                        self._ner_cache.popitem(last=False)
            spans = [(e, policy_slot(e.label, text[e.start:e.end])) for e in outside(entities, taken, text)]
            return _NerOutcome(True, tuple(spans), model=model)
        except Exception as exc:
            detail = str(exc) if isinstance(exc, NerError) else type(exc).__name__
            return _NerOutcome(True, (), f"NER 판단에 실패해 차단했습니다: {detail}")

    def _ner_client(self, policy: Policy) -> "NerClient | None":
        key = (policy.ner_backend, policy.ner_timeout_seconds)
        with self._lock:
            if key not in self._ner_clients:
                self._ner_clients[key] = self.ner_factory(policy)
            return self._ner_clients[key]

    def _ask(self, policy: Policy, judgment: Judgment, peer: Peer | None, vpath: str, real: str,
             purpose: str | None, shown: bytes, ask: bool) -> Verdict:
        """ask_human을 사람에게 묻는다. 승인하면 shown(가린 내용)을 돌려준다."""
        if self.approvals is None:
            return Verdict(judgment)
        exe = peer.exe if peer else None
        folder = posixpath.dirname(real)
        session_key = (folder, exe, policy.version)
        approved_action = "mask" if judgment.findings else "allow"
        if self.approvals.sessions.approved(session_key):
            return Verdict(replace(judgment, action=approved_action, stage="human", approval="remembered",
                                   reason=f"이 폴더는 이미 승인을 받았습니다 ({judgment.reason})"), shown)
        if not ask:
            return Verdict(judgment)
        request = ApprovalRequest(
            vpath, folder, judgment.reason, exe, peer.pid if peer else None, purpose, judgment.findings,
            policy.approval_timeout_seconds, policy.approval_session_minutes,
        )
        result = self.approvals.request(request, key=(real, exe, policy.version, judgment.content_sha256))
        if result.approved:
            if result.approval == "session":
                self.approvals.sessions.remember(session_key, policy.approval_session_minutes)
            return Verdict(replace(judgment, action=approved_action, stage="human", approval=result.approval,
                                   approved_by=result.by, reason=f"사용자가 승인했습니다 ({judgment.reason})"), shown)
        if result.approval == "deny":
            return Verdict(replace(judgment, action="deny", stage="human", approval="deny", approved_by=result.by,
                                   reason="사용자가 거부했습니다"))
        return Verdict(replace(judgment, action="deny", stage="human", approval="timeout",
                               reason=f"{policy.approval_timeout_seconds:g}초 안에 승인이 없어 차단했습니다 "
                                      f"({judgment.reason})"))

    def _client(self, policy: Policy) -> JevClient:
        key = (policy.jev_backend, policy.jev_timeout_seconds)
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = self._clients[key] = self.jev_factory(policy)
            return client

    def _judge(self, policy: Policy, peer: Peer | None, path: str, purpose: str | None,
               masked: bytes, digest: str, ner_model: str = "") -> _JevResult:
        """masked(Jev에 보낼 내용)를 판단한다. ner_model은 NER로 가린 내용을 보낼 때 그 NER 모델 식별값이다.
        캐시 키는 원본 해시라서, 보낸 내용을 바꾸는 NER 모델도 키에 넣는다(정책 버전은 이미 들어 있다)."""
        try:
            client = self._client(policy)
            model_tag = cache_tag(client) + (f"|{ner_model}" if ner_model else "")
        except Exception as exc:
            return _JevResult("deny", None, None, 0, False, f"Jev를 준비하지 못해 차단했습니다: {_safe_error(exc)}")
        key = CacheKey.build(digest, policy.version, path, peer.exe if peer else None, purpose, model_tag)
        hit = self.cache.get(key)
        if hit is not None:
            return _JevResult(hit.action, hit.label, hit.confidence, 0, True)

        context = RequestContext.from_peer(peer, path, purpose, policy.context_rules)
        text = masked.decode("utf-8", "replace").encode("utf-8")
        worst: tuple[str, JevDecision] | None = None
        calls = 0
        for chunk in split_utf8(text, policy.max_inspect_bytes):
            calls += 1
            try:
                decision = client.judge(chunk.decode("utf-8"), context)
                if not isinstance(decision, JevDecision):
                    raise JevError(f"Jev 응답 형식이 올바르지 않습니다: {type(decision).__name__}")
            except Exception as exc:
                return _JevResult("deny", None, None, calls, False, f"Jev 판단에 실패해 차단했습니다: {_safe_error(exc)}")
            action = jev_action(policy, decision)
            # 동작이 같으면 safe가 아닌 답을 남긴다. 그래야 조각 하나라도 민감하면 label이 safe가 아니다(3c ②가 label을 본다)
            rank = (ACTIONS.index(action), decision.label != "safe")
            if worst is None or rank > (ACTIONS.index(worst[0]), worst[1].label != "safe"):
                worst = (action, decision)
            if action == "deny":
                break
        action, decision = worst
        self.cache.put(key, CachedJudgment(action, decision.label, decision.confidence))
        return _JevResult(action, decision.label, decision.confidence, calls, False)


def _safe_error(exc: BaseException) -> str:
    """JevError 메시지는 값이 들어가지 않게 만들어져 있다. 그 밖의 예외는 종류만 보여 준다."""
    return str(exc) if isinstance(exc, JevError) else type(exc).__name__


# --- search ---

def _walk(store: Store, vpath: str, seen: set[str], depth: int) -> Iterator[str]:
    real = store.canonical(vpath)
    kind = store.stat(real).type
    if kind == "file":
        yield vpath
        return
    if kind != "dir" or real in seen or depth > MAX_SEARCH_DEPTH:
        return
    seen.add(real)
    for entry in store.list_dir(real):
        if entry.type not in ("file", "dir"):
            continue
        child = vpath.rstrip("/") + "/" + entry.name
        try:
            yield from _walk(store, child, seen, depth + 1)
        except GateError:
            yield child  # 그사이 바뀐 항목. inspect가 오류를 skipped에 남긴다


def search(
    inspector: Inspector,
    peer: Peer | None,
    vpath: str,
    pattern: str,
    *,
    ignore_case: bool = False,
    purpose: str | None = None,
    max_results: int = 100,
) -> dict:
    """검사를 통과한 내용(허용·마스킹)에서만 찾는다. 차단된 파일은 내용을 보지 않고 skipped에 경로와 이유만 넣는다.
    사람 승인이 필요한 파일은 묻지 않고 건너뛴다(파일마다 대화상자가 뜨지 않게). 이미 받은 폴더 승인은 쓴다.

    패턴은 정규식이 아니라 글자 그대로 찾는다(요청자가 보낸 정규식으로 데몬이 멈추지 않게).
    truncated는 결과 수나 파일 수 제한에 걸려 더 있을 수 있다는 뜻이다.
    """
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS or "\n" in pattern:
        raise GateError(errno.EINVAL, f"pattern은 줄바꿈 없는 1~{MAX_PATTERN_CHARS}자 문자열이어야 합니다")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= MAX_SEARCH_RESULTS:
        raise GateError(errno.EINVAL, f"max_results는 1~{MAX_SEARCH_RESULTS} 사이 정수여야 합니다")
    needle = pattern.casefold() if ignore_case else pattern
    matches: list[dict] = []
    skipped: list[dict] = []
    searched = 0
    truncated = False

    for path in _walk(inspector.store, vpath, set(), 0):
        if searched >= MAX_SEARCH_FILES:
            truncated = True
            break
        searched += 1
        try:
            verdict = inspector.inspect(peer, path, purpose, ask=False)
        except GateError as exc:
            skipped.append({"path": path, "errno": exc.name, "message": exc.message})
            continue
        if not verdict.readable:
            blocked = Blocked(verdict.judgment)
            message = blocked.message
            if verdict.judgment.action == "ask_human" and inspector.approvals is not None:
                message += ". search는 묻지 않습니다. gate cat으로 열면 사용자에게 묻습니다"
            skipped.append({"path": path, "errno": blocked.name, "message": message})
            continue
        for number, line in enumerate(verdict.data.decode("utf-8", "replace").splitlines(), 1):
            haystack = line.casefold() if ignore_case else line
            if needle in haystack:
                text = line if len(line) <= MAX_LINE_CHARS else line[:MAX_LINE_CHARS] + "…"
                matches.append({"path": path, "line": number, "text": text})
                if len(matches) >= max_results:
                    truncated = True  # 더 있을 수 있다
                    break
        if truncated:
            break
    return {"matches": matches, "skipped": skipped, "files_searched": searched, "truncated": truncated}
