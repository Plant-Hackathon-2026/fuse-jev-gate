"""Jev 판단 캐시 (명세 3.5).

키는 sha256(파일 내용) + 정책 버전 + 요청 맥락 요약이다. 내용·정책·맥락이 같으면 Jev를 다시 부르지 않는다.
- 요청 맥락 요약에는 실제 경로, 요청 프로세스 실행 파일, 목적(purpose)을 넣는다. Jev 답이 이 셋에 따라 달라질 수 있어서다.
  PID는 넣지 않는다. `gate cat`은 부를 때마다 새 프로세스라서 PID를 넣으면 캐시가 맞지 않는다.
- 정책 버전(gate.policy.Policy.version)이 키에 들어가므로 정책이 바뀌면 이전 판단은 자연히 쓰이지 않는다.
- Jev 오류는 캐시하지 않는다. 일시적인 실패가 굳어지지 않게 하려는 것이다(오류면 그 요청만 차단).
- 원문이나 탐지 값은 담지 않는다. 판단 결과(동작, 라벨, 확신도)만 담는다.
"""

import hashlib
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass

from gate.jev_client import Label

DEFAULT_MAX_ENTRIES = 4096
_MAX_PURPOSE_CHARS = 500


@dataclass(frozen=True)
class CacheKey:
    content_sha256: str
    policy_version: str
    context: str  # context_digest()

    @classmethod
    def build(cls, content_sha256: str, policy_version: str, path: str, process: str | None,
              purpose: str | None) -> "CacheKey":
        return cls(content_sha256, policy_version, context_digest(path, process, purpose))


@dataclass(frozen=True)
class CachedJudgment:
    action: str             # gate.policy.jev_action 결과 (allow | ask_human | deny)
    label: Label            # 결정을 이끈 답 (나눠 물었으면 가장 엄격한 쪽)
    confidence: float


def context_digest(path: str, process: str | None, purpose: str | None) -> str:
    """요청 맥락 요약. 목적은 공백을 하나로 줄이고 앞부분만 쓴다(Jev 질문에 들어가는 모양과 같다)."""
    flat = " ".join((purpose or "").split())[:_MAX_PURPOSE_CHARS]
    payload = json.dumps([path, process or "", flat], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class JudgmentCache:
    """크기 제한이 있는 LRU 캐시. 여러 스레드에서 불러도 된다."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries는 1 이상이어야 합니다")
        self.max_entries = max_entries
        self._items: OrderedDict[CacheKey, CachedJudgment] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: CacheKey) -> CachedJudgment | None:
        with self._lock:
            value = self._items.get(key)
            if value is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: CacheKey, value: CachedJudgment) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
