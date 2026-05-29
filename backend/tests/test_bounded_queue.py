from trade_lab.domain.data_quality import DataQualityCode
from trade_lab.services.bounded_queue import BoundedQueue, DropPolicy


def test_bounded_queue_drops_oldest_and_records_warning() -> None:
    queue = BoundedQueue[int](2)

    assert queue.push(1) is None
    assert queue.push(2) is None
    drop = queue.push(3)

    assert drop is not None
    assert drop.warning.code == DataQualityCode.BACKPRESSURE_DROP
    assert queue.dropped_count == 1
    assert queue.drain() == (2, 3)


def test_bounded_queue_can_drop_newest() -> None:
    queue = BoundedQueue[int](1, drop_policy=DropPolicy.DROP_NEWEST)
    queue.push(1)
    queue.push(2)

    assert queue.drain() == (1,)


def test_bounded_queue_rejects_non_positive_depth() -> None:
    for max_depth in (0, -1):
        try:
            BoundedQueue[int](max_depth)
        except ValueError as exc:
            assert "max_depth must be positive" in str(exc)
        else:  # pragma: no cover - defensive assertion branch
            raise AssertionError("BoundedQueue accepted non-positive max_depth")


def test_bounded_queue_never_grows_past_max_depth_when_dropping_oldest() -> None:
    queue = BoundedQueue[int](3, max_warnings=3)

    drops = [queue.push(i) for i in range(10)]

    assert len(queue) == 3
    assert queue.drain() == (7, 8, 9)
    assert queue.dropped_count == 7
    assert queue.overflow_count == 7
    assert len(queue.warnings) == 3
    assert [warning.metadata["dropped_count"] for warning in queue.warnings] == [5, 6, 7]
    assert [drop.dropped_count for drop in drops if drop is not None] == list(range(1, 8))


def test_bounded_queue_drop_newest_policy_is_deterministic_and_records_warning() -> None:
    queue = BoundedQueue[str](2, drop_policy=DropPolicy.DROP_NEWEST)

    queue.push("first")
    queue.push("second")
    drop = queue.push("third")

    assert drop is not None
    assert queue.drain() == ("first", "second")
    assert drop.warning.code == DataQualityCode.BACKPRESSURE_DROP
    assert drop.warning.source == "bounded_queue"
    assert drop.warning.message == "bounded queue full; dropped newest item"
    assert drop.warning.metadata == {
        "dropped_count": 1,
        "overflow_count": 1,
        "current_depth": 2,
        "max_depth": 2,
        "drop_policy": "drop_newest",
        "item_type": "str",
    }


def test_bounded_queue_pop_and_drain_clear_items_without_clearing_warnings() -> None:
    queue = BoundedQueue[int](1)
    queue.push(1)
    queue.push(2)

    assert queue.pop() == 2
    assert len(queue) == 0
    assert queue.drain() == ()
    assert len(queue.warnings) == 1


def test_bounded_queue_rejects_negative_warning_limit() -> None:
    try:
        BoundedQueue[int](1, max_warnings=-1)
    except ValueError as exc:
        assert "max_warnings must be non-negative" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("BoundedQueue accepted negative max_warnings")
