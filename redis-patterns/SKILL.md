---
name: redis-patterns
description: Redis caching and data structure patterns for high-performance applications. Use when implementing caching, rate limiting, pub/sub, or distributed locks.
---

# Redis Patterns

Production patterns for Redis caching, data structures, and distributed coordination.

## When to Use

Triggers: "Redis", "cache", "缓存", "rate limit", "pub/sub", "distributed lock", "session"

## Core Patterns

### 1. Connection Management

```python
import redis
from redis import asyncio as aioredis

# Sync client with connection pool
redis_pool = redis.ConnectionPool(
    host="localhost",
    port=6379,
    db=0,
    password="secret",
    max_connections=20,
    decode_responses=True,
)
redis_client = redis.Redis(connection_pool=redis_pool)

# Async client
async def get_async_redis():
    return await aioredis.from_url(
        "redis://localhost:6379",
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
    )

# Context manager pattern
class RedisManager:
    def __init__(self, url: str):
        self.url = url
        self.client = None

    async def __aenter__(self):
        self.client = await aioredis.from_url(self.url)
        return self.client

    async def __aexit__(self, *args):
        await self.client.close()
```

### 2. Caching Patterns

```python
import json
from typing import Optional, Callable
from functools import wraps

class CacheService:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.default_ttl = 3600  # 1 hour

    def get(self, key: str) -> Optional[dict]:
        """Get cached value"""
        data = self.redis.get(key)
        return json.loads(data) if data else None

    def set(self, key: str, value: dict, ttl: int = None) -> None:
        """Set cached value with TTL"""
        self.redis.setex(
            key,
            ttl or self.default_ttl,
            json.dumps(value)
        )

    def delete(self, key: str) -> None:
        """Delete cached value"""
        self.redis.delete(key)

    def get_or_set(
        self,
        key: str,
        factory: Callable,
        ttl: int = None
    ) -> dict:
        """Get from cache or compute and cache"""
        cached = self.get(key)
        if cached is not None:
            return cached

        value = factory()
        self.set(key, value, ttl)
        return value


# Decorator pattern
def cached(prefix: str, ttl: int = 3600):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build cache key
            key = f"{prefix}:{func.__name__}:{hash((args, tuple(kwargs.items())))}"

            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value

            result = func(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator

@cached("user", ttl=300)
def get_user_profile(user_id: int) -> dict:
    return db.query_user(user_id)
```

### 3. Cache-Aside Pattern

```python
class CacheAsideService:
    """Cache-Aside (Lazy Loading) pattern"""

    def __init__(self, redis_client, db_client):
        self.cache = redis_client
        self.db = db_client

    async def get_item(self, item_id: str) -> dict:
        # 1. Try cache first
        cache_key = f"item:{item_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return json.loads(cached)

        # 2. Cache miss - fetch from DB
        item = await self.db.get_item(item_id)
        if item is None:
            return None

        # 3. Populate cache
        self.cache.setex(cache_key, 3600, json.dumps(item))
        return item

    async def update_item(self, item_id: str, data: dict) -> dict:
        # 1. Update database
        item = await self.db.update_item(item_id, data)

        # 2. Invalidate cache
        self.cache.delete(f"item:{item_id}")

        return item
```

### 4. Rate Limiting

```python
import time

class RateLimiter:
    """Sliding window rate limiter"""

    def __init__(self, redis_client):
        self.redis = redis_client

    def is_allowed(
        self,
        key: str,
        limit: int,
        window_seconds: int
    ) -> tuple[bool, int]:
        """
        Check if request is allowed.
        Returns (allowed, remaining_requests)
        """
        now = time.time()
        window_start = now - window_seconds

        pipe = self.redis.pipeline()

        # Remove old entries
        pipe.zremrangebyscore(key, 0, window_start)
        # Add current request
        pipe.zadd(key, {str(now): now})
        # Count requests in window
        pipe.zcard(key)
        # Set expiry
        pipe.expire(key, window_seconds)

        results = pipe.execute()
        request_count = results[2]

        allowed = request_count <= limit
        remaining = max(0, limit - request_count)

        return allowed, remaining


# Fixed window counter (simpler)
class SimpleRateLimiter:
    def is_allowed(self, key: str, limit: int, window: int) -> bool:
        current = self.redis.incr(key)
        if current == 1:
            self.redis.expire(key, window)
        return current <= limit


# Usage
rate_limiter = RateLimiter(redis_client)

def api_endpoint(user_id: str):
    allowed, remaining = rate_limiter.is_allowed(
        f"rate_limit:{user_id}",
        limit=100,
        window_seconds=60
    )
    if not allowed:
        raise HTTPException(429, "Rate limit exceeded")
```

### 5. Distributed Lock

```python
import uuid
import time

class DistributedLock:
    """Redis-based distributed lock"""

    def __init__(self, redis_client):
        self.redis = redis_client

    def acquire(
        self,
        lock_name: str,
        timeout: int = 10,
        blocking: bool = True,
        retry_delay: float = 0.1
    ) -> Optional[str]:
        """Acquire lock, returns token if successful"""
        token = str(uuid.uuid4())
        end_time = time.time() + timeout

        while True:
            if self.redis.set(
                f"lock:{lock_name}",
                token,
                nx=True,
                ex=timeout
            ):
                return token

            if not blocking:
                return None

            if time.time() > end_time:
                return None

            time.sleep(retry_delay)

    def release(self, lock_name: str, token: str) -> bool:
        """Release lock if we own it"""
        # Lua script for atomic check-and-delete
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        else
            return 0
        end
        """
        result = self.redis.eval(script, 1, f"lock:{lock_name}", token)
        return result == 1


# Context manager
class LockContext:
    def __init__(self, lock: DistributedLock, name: str, timeout: int = 10):
        self.lock = lock
        self.name = name
        self.timeout = timeout
        self.token = None

    def __enter__(self):
        self.token = self.lock.acquire(self.name, self.timeout)
        if not self.token:
            raise RuntimeError(f"Could not acquire lock: {self.name}")
        return self

    def __exit__(self, *args):
        if self.token:
            self.lock.release(self.name, self.token)


# Usage
with LockContext(lock, "process_order") as ctx:
    # Critical section
    process_order(order_id)
```

### 6. Pub/Sub Pattern

```python
import asyncio

class PubSubManager:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.pubsub = redis_client.pubsub()
        self.handlers = {}

    def subscribe(self, channel: str, handler: Callable):
        """Subscribe to channel with handler"""
        self.handlers[channel] = handler
        self.pubsub.subscribe(channel)

    def publish(self, channel: str, message: dict):
        """Publish message to channel"""
        self.redis.publish(channel, json.dumps(message))

    def listen(self):
        """Start listening for messages"""
        for message in self.pubsub.listen():
            if message["type"] == "message":
                channel = message["channel"]
                data = json.loads(message["data"])
                if channel in self.handlers:
                    self.handlers[channel](data)


# Async pub/sub
class AsyncPubSub:
    def __init__(self, redis_url: str):
        self.redis_url = redis_url

    async def subscribe(self, channel: str, handler: Callable):
        redis = await aioredis.from_url(self.redis_url)
        pubsub = redis.pubsub()
        await pubsub.subscribe(channel)

        async for message in pubsub.listen():
            if message["type"] == "message":
                await handler(json.loads(message["data"]))

    async def publish(self, channel: str, message: dict):
        redis = await aioredis.from_url(self.redis_url)
        await redis.publish(channel, json.dumps(message))
        await redis.close()
```

### 7. Session Storage

```python
import secrets
from datetime import datetime, timedelta

class SessionStore:
    def __init__(self, redis_client, prefix: str = "session"):
        self.redis = redis_client
        self.prefix = prefix
        self.ttl = 86400  # 24 hours

    def create(self, user_id: str, data: dict = None) -> str:
        """Create new session"""
        session_id = secrets.token_urlsafe(32)
        session_data = {
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "data": data or {}
        }
        self.redis.setex(
            f"{self.prefix}:{session_id}",
            self.ttl,
            json.dumps(session_data)
        )
        return session_id

    def get(self, session_id: str) -> Optional[dict]:
        """Get session data"""
        data = self.redis.get(f"{self.prefix}:{session_id}")
        return json.loads(data) if data else None

    def refresh(self, session_id: str) -> bool:
        """Extend session TTL"""
        return self.redis.expire(
            f"{self.prefix}:{session_id}",
            self.ttl
        )

    def destroy(self, session_id: str) -> None:
        """Delete session"""
        self.redis.delete(f"{self.prefix}:{session_id}")
```

### 8. Data Structures

```python
class RedisDataStructures:
    def __init__(self, redis_client):
        self.redis = redis_client

    # Sorted Set - Leaderboard
    def add_score(self, board: str, user: str, score: float):
        self.redis.zadd(board, {user: score})

    def get_top(self, board: str, n: int = 10) -> list:
        return self.redis.zrevrange(board, 0, n - 1, withscores=True)

    def get_rank(self, board: str, user: str) -> int:
        return self.redis.zrevrank(board, user)

    # Hash - Object storage
    def save_object(self, key: str, obj: dict):
        self.redis.hset(key, mapping=obj)

    def get_object(self, key: str) -> dict:
        return self.redis.hgetall(key)

    # List - Queue
    def enqueue(self, queue: str, item: str):
        self.redis.rpush(queue, item)

    def dequeue(self, queue: str) -> Optional[str]:
        return self.redis.lpop(queue)

    # Set - Unique items
    def add_to_set(self, key: str, *items):
        self.redis.sadd(key, *items)

    def is_member(self, key: str, item: str) -> bool:
        return self.redis.sismember(key, item)
```

## Key Naming Convention

```
{service}:{entity}:{id}:{attribute}

Examples:
- user:profile:12345
- cache:api:get_user:abc123
- session:abc123xyz
- rate_limit:api:user:12345
- lock:order_processing:99
```

## TTL Guidelines

| Use Case | TTL | Reason |
|----------|-----|--------|
| Session | 24h | User experience |
| API cache | 5-15min | Data freshness |
| Rate limit | 1min-1h | Window size |
| Lock | 10-60s | Prevent deadlock |
| Temp data | 1h-24h | Cleanup |

## Best Practices

1. **Always set TTL** - Prevent memory leaks
2. **Use connection pools** - Efficient resource usage
3. **Prefix keys** - Namespace isolation
4. **Atomic operations** - Use Lua scripts for complex logic
5. **Handle failures** - Cache is not durable storage
6. **Monitor memory** - Set maxmemory policy

## References

- [Redis Documentation](https://redis.io/docs/)
- [redis-py](https://redis-py.readthedocs.io/)
- [Redis Patterns](https://redis.io/docs/manual/patterns/)
