"""gate.cache: 판단 캐시 키와 LRU."""

import threading

import pytest

from gate.cache import CachedJudgment, CacheKey, JudgmentCache, context_digest

VALUE = CachedJudgment("allow", "safe", 0.97)


def key(content="c", version="v", path="/a.txt", process="/bin/x", purpose=None) -> CacheKey:
    return CacheKey.build(content, version, path, process, purpose)


def test_key_changes_with_each_part():
    base = key()
    assert key() == base
    for changed in (key(content="c2"), key(version="v2"), key(path="/b.txt"), key(process="/bin/y"),
                    key(purpose="요약")):
        assert changed != base


def test_purpose_whitespace_is_normalized_like_the_question():
    assert context_digest("/a", "/bin/x", "월간  요약\n정리") == context_digest("/a", "/bin/x", "월간 요약 정리")
    assert context_digest("/a", "/bin/x", None) == context_digest("/a", "/bin/x", "")
    assert context_digest("/a", None, None) == context_digest("/a", "", None)


def test_context_digest_cannot_be_forged_by_joining_fields():
    assert context_digest("/a", "b", None) != context_digest('/a", "b', None, None)


def test_get_put_and_counters():
    cache = JudgmentCache()
    assert cache.get(key()) is None
    cache.put(key(), VALUE)
    assert cache.get(key()) == VALUE
    assert (cache.hits, cache.misses, len(cache)) == (1, 1, 1)
    cache.clear()
    assert len(cache) == 0


def test_lru_evicts_least_recently_used():
    cache = JudgmentCache(max_entries=2)
    cache.put(key("1"), VALUE)
    cache.put(key("2"), VALUE)
    cache.get(key("1"))           # 1을 최근 사용으로
    cache.put(key("3"), VALUE)    # 2가 빠진다
    assert cache.get(key("2")) is None
    assert cache.get(key("1")) == VALUE
    assert cache.get(key("3")) == VALUE
    assert len(cache) == 2


def test_rejects_non_positive_size():
    with pytest.raises(ValueError):
        JudgmentCache(max_entries=0)


def test_thread_safe():
    cache = JudgmentCache(max_entries=50)

    def worker(n):
        for i in range(200):
            cache.put(key(f"{n}-{i}"), VALUE)
            cache.get(key(f"{n}-{i // 2}"))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(cache) == 50
