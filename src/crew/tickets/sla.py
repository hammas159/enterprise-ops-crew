"""SLA clocks that run on business hours.

The detail everyone gets wrong. A ticket raised at 5pm Friday with a "4 hour" SLA is
not breached at 9pm Friday - the support desk was closed. Measuring in wall-clock time
produces a dashboard full of breaches that nobody caused and nobody can prevent, and a
dashboard nobody believes is a dashboard nobody reads.

Time spent waiting on the *customer* is also excluded, for the same reason: an agent
cannot be held to a clock it has no way to stop.

Implemented with the standard library only. Business-hours arithmetic is fiddly but it
is not deep, and it is worth owning rather than importing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .models import Priority


@dataclass(frozen=True)
class BusinessHours:
    start_hour: int = 9
    end_hour: int = 17
    # Monday is 0. Pakistan's working week is Monday-Friday in most of the sector,
    # but this is configuration, not an assumption baked into the arithmetic.
    working_days: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    holidays: frozenset[dt.date] = frozenset()

    def is_open(self, moment: dt.datetime) -> bool:
        return (
            moment.weekday() in self.working_days
            and moment.date() not in self.holidays
            and self.start_hour <= moment.hour < self.end_hour
        )

    def elapsed_seconds(self, start: dt.datetime, end: dt.datetime) -> float:
        """Business seconds between two moments."""
        if end <= start:
            return 0.0

        total = 0.0
        cursor = start
        while cursor.date() <= end.date():
            day_open = cursor.replace(hour=self.start_hour, minute=0, second=0, microsecond=0)
            day_close = cursor.replace(hour=self.end_hour, minute=0, second=0, microsecond=0)

            if cursor.weekday() in self.working_days and cursor.date() not in self.holidays:
                window_start = max(cursor, day_open)
                window_end = min(end, day_close)
                if window_end > window_start:
                    total += (window_end - window_start).total_seconds()

            # Advance to the next day at opening time.
            cursor = (cursor + dt.timedelta(days=1)).replace(
                hour=self.start_hour, minute=0, second=0, microsecond=0
            )
            if cursor > end:
                break

        return total


# Response and resolution targets, in business hours.
DEFAULT_TARGETS: dict[Priority, tuple[float, float]] = {
    Priority.URGENT: (0.5, 4.0),
    Priority.HIGH: (2.0, 8.0),
    Priority.NORMAL: (8.0, 24.0),
    Priority.LOW: (24.0, 72.0),
}


@dataclass
class SLAPolicy:
    hours: BusinessHours = field(default_factory=BusinessHours)
    targets: dict[Priority, tuple[float, float]] = field(
        default_factory=lambda: dict(DEFAULT_TARGETS)
    )

    def target_seconds(self, priority: Priority) -> tuple[float, float]:
        response, resolution = self.targets[priority]
        return response * 3600, resolution * 3600

    def status(
        self, *, created_at: float, priority: Priority, first_response_at: float | None,
        resolved_at: float | None, paused_seconds: float = 0.0, now: float | None = None,
    ) -> dict:
        """Where this ticket stands against its targets.

        `paused_seconds` is time the ticket spent waiting on the customer. Excluded,
        because an agent cannot be held to a clock it cannot stop.
        """
        import time as _time

        now = now if now is not None else _time.time()
        created = dt.datetime.fromtimestamp(created_at)
        response_target, resolution_target = self.target_seconds(priority)

        response_end = dt.datetime.fromtimestamp(first_response_at or now)
        response_elapsed = max(
            0.0, self.hours.elapsed_seconds(created, response_end) - paused_seconds
        )

        resolution_end = dt.datetime.fromtimestamp(resolved_at or now)
        resolution_elapsed = max(
            0.0, self.hours.elapsed_seconds(created, resolution_end) - paused_seconds
        )

        return {
            "response_elapsed_h": round(response_elapsed / 3600, 3),
            "response_target_h": round(response_target / 3600, 3),
            "response_breached": response_elapsed > response_target,
            "resolution_elapsed_h": round(resolution_elapsed / 3600, 3),
            "resolution_target_h": round(resolution_target / 3600, 3),
            "resolution_breached": resolution_elapsed > resolution_target,
            # Fraction of the resolution budget consumed. Above 1.0 is a breach; the
            # number matters more than the boolean, because 0.9 is when to act.
            "burn": round(resolution_elapsed / resolution_target, 3)
            if resolution_target
            else 0.0,
        }
