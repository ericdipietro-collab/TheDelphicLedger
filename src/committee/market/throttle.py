"""Rate limiter and exponential backoff for external HTTP calls."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, TypeVar

import requests


@dataclass
class RateLimiter:
    """Enforces a minimum interval between consecutive calls."""

    min_interval_s: float
    _last: float = field(default=0.0, init=False, repr=False)

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval_s:
            time.sleep(self.min_interval_s - elapsed)
        self._last = time.monotonic()


_F = TypeVar("_F", bound=Callable[..., Any])


def with_backoff(
    max_retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> Callable[[_F], _F]:
    """Retry on transient HTTP/network errors. Never retries 4xx responses."""

    def decorator(fn: _F) -> _F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except (requests.Timeout, requests.ConnectionError):
                    if attempt == max_retries:
                        raise
                    time.sleep(min(base_delay * (2**attempt), max_delay))
                except requests.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code < 500:
                        raise  # Never retry 4xx
                    if attempt == max_retries:
                        raise
                    time.sleep(min(base_delay * (2**attempt), max_delay))

        return wrapper  # type: ignore[return-value]

    return decorator
