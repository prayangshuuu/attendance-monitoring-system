from datetime import UTC, datetime

from app.utils.clock import Clock, SystemClock, get_clock


class FrozenClock:
    def __init__(self, frozen_time: datetime) -> None:
        self._time = frozen_time

    def now(self) -> datetime:
        return self._time


def test_system_clock_returns_utc() -> None:
    clock = SystemClock()
    now1 = clock.now()
    assert now1.tzinfo is not None
    assert now1.tzinfo == UTC


def test_get_clock_dependency() -> None:
    clock = get_clock()
    assert isinstance(clock, SystemClock)
    assert isinstance(clock, Clock)
    now = clock.now()
    assert isinstance(now, datetime)


def test_frozen_clock_implements_protocol() -> None:
    fixed_time = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)
    clock: Clock = FrozenClock(fixed_time)
    assert clock.now() == fixed_time
