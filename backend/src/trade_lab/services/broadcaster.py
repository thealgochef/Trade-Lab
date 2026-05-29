"""WebSocket fan-out for runtime deltas.

Raw ticks are intentionally not broadcast by default: clients receive domain
deltas (bars, levels, touches, observations, feed state) so slow browsers cannot
turn market-data bursts into unbounded memory growth.
"""

import asyncio
from itertools import count
from typing import Any

from starlette.websockets import WebSocket

from trade_lab.api.dto import (
    MessageType,
    SnapshotPayload,
    bars_payload,
    feed_status_to_dto,
    levels_payload,
    make_envelope,
    observation_to_dto,
    snapshot_payload_from_runtime,
    touch_to_dto,
    warning_to_dto,
)
from trade_lab.api.serialization import dumps_bytes
from trade_lab.domain.data_quality import DataQualityCode, DataQualityWarning
from trade_lab.services.runtime import ApplicationRuntime, RuntimeUpdate


class WebSocketBroadcaster:
    def __init__(self, runtime: ApplicationRuntime, *, queue_depth: int = 100) -> None:
        if queue_depth <= 0:
            raise ValueError("queue_depth must be positive")
        self.runtime = runtime
        self.queue_depth = queue_depth
        self._sequence = count(1)
        self._clients: set[asyncio.Queue[bytes]] = set()
        self._pending_backpressure_drops: dict[asyncio.Queue[bytes], int] = {}
        self._client_dropped_messages: dict[asyncio.Queue[bytes], int] = {}
        self.dropped_messages = 0

    def snapshot_payload(self) -> SnapshotPayload:
        return snapshot_payload_from_runtime(self.runtime.snapshot())

    def envelope_bytes(self, message_type: MessageType, payload: Any) -> bytes:
        return dumps_bytes(make_envelope(message_type, next(self._sequence), payload))

    async def connect(self, websocket: WebSocket) -> asyncio.Queue[bytes]:
        await websocket.accept()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.queue_depth)
        self._clients.add(queue)
        self._pending_backpressure_drops[queue] = 0
        self._client_dropped_messages[queue] = 0
        await queue.put(self.envelope_bytes("system.snapshot", self.snapshot_payload()))
        await queue.put(self.envelope_bytes("system.heartbeat", {"status": "ok"}))
        return queue

    def disconnect(self, queue: asyncio.Queue[bytes]) -> None:
        self._clients.discard(queue)
        self._pending_backpressure_drops.pop(queue, None)
        self._client_dropped_messages.pop(queue, None)

    async def send_loop(self, websocket: WebSocket, queue: asyncio.Queue[bytes]) -> None:
        while True:
            await websocket.send_bytes(await queue.get())

    async def broadcast_update(self, update: RuntimeUpdate) -> None:
        await self._fanout_messages(self.messages_for_update(update))

    def messages_for_update(self, update: RuntimeUpdate) -> tuple[bytes, ...]:
        messages: list[bytes] = []
        if update.feed_status is not None:
            messages.append(
                self.envelope_bytes("feed.status", feed_status_to_dto(update.feed_status))
            )
        for warning in update.warnings:
            messages.append(self.envelope_bytes("data_quality.warning", warning_to_dto(warning)))
        if update.current_bars:
            messages.append(
                self.envelope_bytes("market.bar.updated", bars_payload(update.current_bars))
            )
        if update.closed_bars:
            messages.append(
                self.envelope_bytes("market.bar.closed", bars_payload(update.closed_bars))
            )
        if update.display_levels:
            messages.append(
                self.envelope_bytes("levels.updated", levels_payload(update.display_levels))
            )
        for touch in update.touches:
            messages.append(self.envelope_bytes("touch.detected", touch_to_dto(touch)))
        for observation in update.observations:
            messages.append(
                self.envelope_bytes("observation.updated", observation_to_dto(observation))
            )
        return tuple(messages)

    async def _fanout(self, message: bytes) -> None:
        await self._fanout_messages((message,))

    async def _fanout_messages(self, messages: tuple[bytes, ...]) -> None:
        for queue in tuple(self._clients):
            for message in messages:
                self._put_domain_message(queue, message)
            self._put_backpressure_warning_if_room(queue)

    def _put_domain_message(self, queue: asyncio.Queue[bytes], message: bytes) -> None:
        if queue.full():
            queue.get_nowait()
            self.dropped_messages += 1
            self._client_dropped_messages[queue] = self._client_dropped_messages.get(queue, 0) + 1
            self._pending_backpressure_drops[queue] = (
                self._pending_backpressure_drops.get(queue, 0) + 1
            )
        queue.put_nowait(message)

    def _put_backpressure_warning_if_room(self, queue: asyncio.Queue[bytes]) -> None:
        pending_drops = self._pending_backpressure_drops.get(queue, 0)
        if pending_drops <= 0 or queue.full():
            return
        queue.put_nowait(self._backpressure_warning(queue, pending_drops))
        self._pending_backpressure_drops[queue] = 0

    def _backpressure_warning(self, queue: asyncio.Queue[bytes], dropped_messages: int) -> bytes:
        return self.envelope_bytes(
            "data_quality.warning",
            warning_to_dto(
                DataQualityWarning(
                    code=DataQualityCode.BACKPRESSURE_DROP,
                    message="client queue overflow; dropped oldest websocket message",
                    source="websocket",
                    metadata={
                        "dropped_messages": dropped_messages,
                        "client_dropped_messages": self._client_dropped_messages.get(queue, 0),
                        "total_dropped_messages": self.dropped_messages,
                    },
                )
            ),
        )
