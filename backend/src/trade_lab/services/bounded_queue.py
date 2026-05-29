"""Bounded queue/backpressure primitive for future broadcasters.

WebSocket fan-out must never grow without limit under slow clients. This utility
keeps a configured maximum depth and records explicit data-quality warnings when
items are dropped due to pressure.
"""

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from trade_lab.domain.data_quality import DataQualityCode, DataQualityWarning

T = TypeVar("T")


class DropPolicy(StrEnum):
    DROP_OLDEST = "drop_oldest"
    DROP_NEWEST = "drop_newest"


@dataclass(frozen=True, slots=True)
class QueueDrop:
    dropped_count: int
    warning: DataQualityWarning


class BoundedQueue[T]:
    def __init__(
        self,
        max_depth: int,
        *,
        drop_policy: DropPolicy = DropPolicy.DROP_OLDEST,
        max_warnings: int = 100,
    ) -> None:
        if max_depth <= 0:
            raise ValueError("max_depth must be positive")
        if max_warnings < 0:
            raise ValueError("max_warnings must be non-negative")
        self.max_depth = max_depth
        self.drop_policy = drop_policy
        self.max_warnings = max_warnings
        self._items: deque[T] = deque()
        self.warnings: deque[DataQualityWarning] = deque(maxlen=max_warnings)
        self.dropped_count = 0
        self.overflow_count = 0

    def push(self, item: T) -> QueueDrop | None:
        if len(self._items) < self.max_depth:
            self._items.append(item)
            return None
        self.dropped_count += 1
        if self.drop_policy == DropPolicy.DROP_OLDEST:
            self._items.popleft()
            self._items.append(item)
            message = "bounded queue full; dropped oldest item"
        else:
            message = "bounded queue full; dropped newest item"
        current_depth = len(self._items)
        warning = DataQualityWarning(
            code=DataQualityCode.BACKPRESSURE_DROP,
            message=message,
            source="bounded_queue",
            metadata={
                "dropped_count": self.dropped_count,
                "overflow_count": self.overflow_count + 1,
                "current_depth": current_depth,
                "max_depth": self.max_depth,
                "drop_policy": self.drop_policy.value,
                "item_type": type(item).__name__,
            },
        )
        self.overflow_count += 1
        self.warnings.append(warning)
        return QueueDrop(self.dropped_count, warning)

    def pop(self) -> T:
        return self._items.popleft()

    def drain(self) -> tuple[T, ...]:
        items = tuple(self._items)
        self._items.clear()
        return items

    def __len__(self) -> int:
        return len(self._items)
