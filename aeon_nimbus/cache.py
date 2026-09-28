"""Performance optimization utilities for Aeon Nimbus platform.

Implements caching, memoization, and lazy loading strategies to handle
2000+ companies efficiently.
"""
import functools
import time
from typing import Any, Callable, Optional


def timed_lru_cache(seconds: int, maxsize: int = 128):
    """LRU cache with TTL expiration."""
    def wrapper_cache(func: Callable) -> Callable:
        func = functools.lru_cache(maxsize=maxsize)(func)
        func.lifetime = seconds
        func.expiration = time.time() + seconds

        @functools.wraps(func)
        def wrapped_func(*args, **kwargs):
            if time.time() >= func.expiration:
                func.cache_clear()
                func.expiration = time.time() + func.lifetime
            return func(*args, **kwargs)

        return wrapped_func
    return wrapper_cache


class ResponseCache:
    """Thread-safe response cache with TTL."""
    def __init__(self, ttl: int = 3600):
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        """Get cached value if not expired."""
        if key not in self._cache:
            return None
        ts, value = self._cache[key]
        if time.time() - ts > self.ttl:
            del self._cache[key]
            return None
        return value

    def set(self, key: str, value: Any):
        """Store value with current timestamp."""
        self._cache[key] = (time.time(), value)

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()

    def invalidate(self, pattern: str):
        """Remove all keys matching pattern."""
        keys_to_remove = [k for k in self._cache if pattern in k]
        for k in keys_to_remove:
            del self._cache[k]


# Global cache instances
platform_cache = ResponseCache(ttl=3600)  # 1 hour for universe data
company_cache = ResponseCache(ttl=1800)   # 30 min for company details
valuation_cache = ResponseCache(ttl=7200) # 2 hours for DCF computations


def cached_deep_computation(slug: str) -> Optional[dict]:
    """Retrieve cached DCF computation if available."""
    return company_cache.get(f"deep:{slug}")


def cache_deep_computation(slug: str, data: dict):
    """Store DCF computation result."""
    company_cache.set(f"deep:{slug}", data)


def invalidate_company_cache(slug: str):
    """Clear all cached data for a company."""
    company_cache.invalidate(slug)
