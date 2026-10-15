from time import time
from typing import Generic, TypeVar


T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time():
            self._items.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T) -> None:
        self._items[key] = (time() + self.ttl_seconds, value)

    def clear(self) -> None:
        self._items.clear()
