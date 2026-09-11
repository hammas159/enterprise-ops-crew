"""The ticket, and the states it can legitimately be in.

Transitions are validated rather than assumed. A workforce of agents will attempt
every illegal transition eventually - resolving a ticket it never picked up, closing
one that is waiting on a human - and a status field that accepts any assignment turns
those bugs into silent data corruption instead of errors.
"""

from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


class Priority(enum.IntEnum):
    LOW = 3
    NORMAL = 2
    HIGH = 1
    URGENT = 0  # lowest value sorts first


class Status(enum.StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"


# What may follow what. Everything else is a bug, and is raised as one.
ALLOWED: dict[Status, set[Status]] = {
    Status.NEW: {Status.TRIAGED, Status.ESCALATED, Status.CLOSED},
    Status.TRIAGED: {Status.IN_PROGRESS, Status.ESCALATED, Status.CLOSED},
    Status.IN_PROGRESS: {
        Status.AWAITING_APPROVAL,
        Status.RESOLVED,
        Status.ESCALATED,
        Status.TRIAGED,
    },
    Status.AWAITING_APPROVAL: {Status.IN_PROGRESS, Status.RESOLVED, Status.ESCALATED},
    Status.ESCALATED: {Status.IN_PROGRESS, Status.RESOLVED, Status.CLOSED},
    Status.RESOLVED: {Status.CLOSED, Status.IN_PROGRESS},  # reopening is legitimate
    Status.CLOSED: set(),  # terminal
}


class TransitionError(RuntimeError):
    pass


@dataclass
class Event:
    at: float
    actor: str
    action: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class Ticket:
    subject: str
    body: str = ""
    tenant: str = "default"
    requester: str = ""
    category: str = ""
    priority: Priority = Priority.NORMAL
    status: Status = Status.NEW
    assignee: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: float = field(default_factory=time.time)
    resolved_at: float | None = None
    history: list[Event] = field(default_factory=list)

    def record(self, actor: str, action: str, **detail: Any) -> None:
        self.history.append(Event(at=time.time(), actor=actor, action=action, detail=detail))

    def transition(self, to: Status, *, actor: str, reason: str = "") -> None:
        if to not in ALLOWED[self.status]:
            raise TransitionError(f"{self.id}: cannot go {self.status.value} -> {to.value}")
        self.record(
            actor, "transition", **{"from": self.status.value, "to": to.value, "reason": reason}
        )
        self.status = to
        if to is Status.RESOLVED:
            self.resolved_at = time.time()
        elif to is Status.IN_PROGRESS:
            # Reopening clears the resolution time, or the SLA would be measured
            # against a resolution that no longer stands.
            self.resolved_at = None
