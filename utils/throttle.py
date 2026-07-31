import time
from collections import defaultdict, deque


class RateLimiter:
    """Антифлуд на основе скользящего окна (в памяти, по user_id)."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._events: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, key: int) -> bool:
        now = time.monotonic()
        events = self._events[key]
        while events and now - events[0] > self.window:
            events.popleft()
        if len(events) >= self.limit:
            return False
        events.append(now)
        return True

    def cooldown(self, key: int) -> float:
        events = self._events.get(key)
        if not events:
            return 0.0
        return max(0.0, events[0] + self.window - time.monotonic())

    def forget(self, key: int) -> None:
        events = self._events.get(key)
        if events:
            try:
                events.pop()
            except IndexError:
                pass
