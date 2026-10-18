"""Testler için bellek içi, küçük bir Redis taklidi (decode_responses=True davranışı).

Yalnızca uygulamanın kullandığı komutlar: liste (lpush/rpush/brpop/rpop/lrange/llen/lrem/ltrim), hash
(hget/hmget/hset), sıralı küme (zadd/zrangebyscore/zrem/zremrangebyscore/zcard), string (get/set nx ex/setex),
delete/expire ve pipeline. Süre aşımları (ex/expire) dikkate alınmaz.
"""

from collections import deque


class FakeRedis:
    def __init__(self):
        self.lists: dict[str, deque] = {}
        self.hashes: dict[str, dict] = {}
        self.zsets: dict[str, dict] = {}
        self.strings: dict[str, str] = {}

    # ---------------- listeler
    def lpush(self, key, *values):
        q = self.lists.setdefault(key, deque())
        for v in values:
            q.appendleft(str(v))
        return len(q)

    def rpush(self, key, *values):
        q = self.lists.setdefault(key, deque())
        for v in values:
            q.append(str(v))
        return len(q)

    def brpop(self, keys, timeout=0):
        for key in keys if isinstance(keys, list | tuple) else [keys]:
            q = self.lists.get(key)
            if q:
                return key, q.pop()
        return None

    def rpop(self, key):
        q = self.lists.get(key)
        return q.pop() if q else None

    def llen(self, key):
        return len(self.lists.get(key, ()))

    def lrange(self, key, start, end):
        items = list(self.lists.get(key, ()))
        end = len(items) if end == -1 else end + 1
        return items[start:end]

    def lrem(self, key, count, value):
        q = self.lists.get(key)
        if not q:
            return 0
        items = list(q)
        removed = 0
        out = []
        for it in items:
            if it == value and (count == 0 or removed < abs(count)):
                removed += 1
                continue
            out.append(it)
        self.lists[key] = deque(out)
        return removed

    # ---------------- hash
    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hmget(self, key, *fields):
        if len(fields) == 1 and isinstance(fields[0], list | tuple):
            fields = fields[0]
        h = self.hashes.get(key, {})
        return [h.get(f) for f in fields]

    def hset(self, key, field=None, value=None, mapping=None):
        h = self.hashes.setdefault(key, {})
        if field is not None:
            h[field] = str(value)
        for k, v in (mapping or {}).items():
            h[k] = str(v)
        return 1

    # ---------------- sıralı küme
    def zadd(self, key, mapping):
        z = self.zsets.setdefault(key, {})
        for member, score in mapping.items():
            z[str(member)] = float(score)
        return len(mapping)

    @staticmethod
    def _score(v):
        if v in ("-inf",):
            return float("-inf")
        if v in ("+inf", "inf"):
            return float("inf")
        return float(v)

    def zrangebyscore(self, key, mn, mx, start=None, num=None):
        z = self.zsets.get(key, {})
        lo, hi = self._score(mn), self._score(mx)
        items = sorted((s, m) for m, s in z.items() if lo <= s <= hi)
        members = [m for _, m in items]
        if start is not None and num is not None:
            members = members[start:start + num]
        return members

    def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        n = 0
        for m in members:
            if z.pop(str(m), None) is not None:
                n += 1
        return n

    def zremrangebyscore(self, key, mn, mx):
        z = self.zsets.get(key, {})
        lo, hi = self._score(mn), self._score(mx)
        drop = [m for m, s in z.items() if lo <= s <= hi]
        for m in drop:
            del z[m]
        return len(drop)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    # ---------------- string / genel
    def get(self, key):
        return self.strings.get(key)

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        return True

    def setex(self, key, ttl, value):
        self.strings[key] = str(value)
        return True

    def delete(self, *keys):
        n = 0
        for k in keys:
            for store in (self.lists, self.hashes, self.zsets, self.strings):
                if store.pop(k, None) is not None:
                    n += 1
        return n

    def expire(self, key, seconds):
        return 1

    def scan_iter(self, pattern="*"):
        prefix = pattern.rstrip("*")
        keys = set(self.lists) | set(self.hashes) | set(self.zsets) | set(self.strings)
        return [k for k in keys if k.startswith(prefix)]

    def ping(self):
        return True

    def pipeline(self, transaction=True):
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, r: FakeRedis):
        self.r = r
        self.ops: list = []

    def __getattr__(self, name):
        fn = getattr(self.r, name)

        def queue(*args, **kwargs):
            self.ops.append((fn, args, kwargs))
            return self

        return queue

    def execute(self):
        out = [fn(*a, **kw) for fn, a, kw in self.ops]
        self.ops = []
        return out

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class BrokenRedis(FakeRedis):
    """Her komutta bağlantı hatası veren Redis (yedek yolları sınamak için)."""

    def __getattribute__(self, name):
        if name.startswith("_") or name in ("lists", "hashes", "zsets", "strings"):
            return object.__getattribute__(self, name)

        def fail(*a, **kw):
            raise ConnectionError("redis yok")

        return fail
