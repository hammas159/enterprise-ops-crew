"""The crew: intake, triage, execution, escalation, reporting.

Multi-agent in the sense that matters operationally — distinct roles with distinct
authority — rather than in the sense of several models talking to each other. Triage
decides, a playbook executes, and a human is the only thing that can authorise an
irreversible action. Each boundary is a place the system can stop safely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .agents.playbooks import PLAYBOOKS, Playbook
from .agents.triage import triage
from .systems.mock import ApprovalRequired, SystemError_, SystemsRegistry
from .tickets.models import Status, Ticket, TransitionError
from .tickets.sla import SLAPolicy


@dataclass
class PendingApproval:
    ticket_id: str
    operation: str
    risk: str
    arguments: dict
    requested_at: float = field(default_factory=time.time)


@dataclass
class Crew:
    systems: SystemsRegistry
    sla: SLAPolicy = field(default_factory=SLAPolicy)
    tickets: dict[str, Ticket] = field(default_factory=dict)
    approvals: dict[str, PendingApproval] = field(default_factory=dict)
    # Facts a playbook step needs. In production this is an extraction step; keeping
    # it explicit here lets the routing and authority logic be tested on its own.
    context: dict[str, dict] = field(default_factory=dict)

    # ---- intake ---------------------------------------------------------------

    def intake(self, ticket: Ticket, **facts) -> Ticket:
        self.tickets[ticket.id] = ticket
        self.context[ticket.id] = {"requester": ticket.requester, **facts}
        ticket.record("intake", "received", subject=ticket.subject)

        result = triage(ticket.subject, ticket.body)
        ticket.category = result.category
        ticket.priority = result.priority
        ticket.record(
            "triage",
            "classified",
            category=result.category,
            priority=result.priority.name,
            confidence=result.confidence,
            matched=result.matched,
        )

        if result.needs_human:
            # Abstention, not a guess. A triage system with no abstention route
            # routes everything, including what it does not understand.
            ticket.transition(Status.ESCALATED, actor="triage", reason=result.reason)
            return ticket

        ticket.transition(Status.TRIAGED, actor="triage")
        return ticket

    # ---- execution ------------------------------------------------------------

    def _playbook(self, ticket: Ticket) -> Playbook | None:
        return PLAYBOOKS.get(ticket.category)

    def work(self, ticket_id: str, *, approvals: set[str] | None = None) -> Ticket:
        """Run the playbook until it completes, fails, or needs a human."""
        approvals = approvals or set()
        ticket = self.tickets[ticket_id]

        if ticket.status is Status.TRIAGED:
            ticket.transition(Status.IN_PROGRESS, actor="resolver")
        elif ticket.status is Status.AWAITING_APPROVAL:
            ticket.transition(Status.IN_PROGRESS, actor="resolver", reason="approved")
        elif ticket.status is not Status.IN_PROGRESS:
            raise TransitionError(f"{ticket_id} is {ticket.status.value}, cannot be worked")

        playbook = self._playbook(ticket)
        if playbook is None:
            ticket.transition(
                Status.ESCALATED,
                actor="resolver",
                reason=f"no playbook for category {ticket.category!r}",
            )
            return ticket

        facts = self.context.get(ticket.id, {})

        for step in playbook.steps:
            try:
                arguments = {p: facts[source] for p, source in step.arguments.items()}
            except KeyError as missing:
                if step.optional:
                    continue
                # Missing information is an escalation, not a failure. A human can
                # supply it, and guessing an account number is how the wrong account
                # gets reset.
                ticket.transition(
                    Status.ESCALATED,
                    actor="resolver",
                    reason=f"missing information: {missing.args[0]}",
                )
                return ticket

            try:
                result = self.systems.call(
                    step.operation, arguments, approved=step.operation in approvals
                )
                ticket.record(
                    "resolver", "executed", operation=step.operation, result=str(result)[:200]
                )
            except ApprovalRequired as gate:
                self.approvals[ticket.id] = PendingApproval(
                    ticket_id=ticket.id,
                    operation=gate.operation,
                    risk=gate.risk.name,
                    arguments=gate.arguments,
                )
                ticket.record(
                    "resolver",
                    "approval_requested",
                    operation=gate.operation,
                    risk=gate.risk.name,
                )
                ticket.transition(
                    Status.AWAITING_APPROVAL,
                    actor="resolver",
                    reason=f"{gate.operation} is {gate.risk.name}",
                )
                return ticket
            except SystemError_ as exc:
                if step.optional:
                    ticket.record(
                        "resolver", "step_skipped", operation=step.operation, error=str(exc)
                    )
                    continue
                ticket.transition(Status.ESCALATED, actor="resolver", reason=str(exc))
                return ticket

        ticket.transition(
            Status.RESOLVED, actor="resolver", reason=f"playbook {playbook.name} completed"
        )
        return ticket

    # ---- human in the loop ----------------------------------------------------

    def approve(self, ticket_id: str, *, approver: str) -> Ticket:
        pending = self.approvals.pop(ticket_id, None)
        if pending is None:
            raise KeyError(f"{ticket_id} has no pending approval")
        ticket = self.tickets[ticket_id]
        ticket.record(
            approver, "approved", operation=pending.operation, arguments=pending.arguments
        )
        return self.work(ticket_id, approvals={pending.operation})

    def reject(self, ticket_id: str, *, approver: str, reason: str = "") -> Ticket:
        pending = self.approvals.pop(ticket_id, None)
        if pending is None:
            raise KeyError(f"{ticket_id} has no pending approval")
        ticket = self.tickets[ticket_id]
        ticket.record(approver, "rejected", operation=pending.operation, reason=reason)
        ticket.transition(
            Status.ESCALATED, actor=approver, reason=reason or "approval rejected"
        )
        return ticket

    # ---- reporting ------------------------------------------------------------

    def sla_status(self, ticket_id: str, *, now: float | None = None) -> dict:
        ticket = self.tickets[ticket_id]
        first_response = next(
            (e.at for e in ticket.history if e.action in {"executed", "transition"}), None
        )
        return self.sla.status(
            created_at=ticket.created_at,
            priority=ticket.priority,
            first_response_at=first_response,
            resolved_at=ticket.resolved_at,
            now=now,
        )

    def at_risk(self, *, burn_threshold: float = 0.8, now: float | None = None) -> list[str]:
        """Tickets about to breach.

        Reported before the breach, not after. A list of tickets that already failed
        is a report; a list about to fail is something someone can act on.
        """
        return [
            t.id
            for t in self.tickets.values()
            if t.status not in {Status.RESOLVED, Status.CLOSED}
            and self.sla_status(t.id, now=now)["burn"] >= burn_threshold
        ]

    def daily_report(self, *, now: float | None = None) -> dict:
        tickets = list(self.tickets.values())
        resolved = [t for t in tickets if t.status in {Status.RESOLVED, Status.CLOSED}]
        escalated = [t for t in tickets if t.status is Status.ESCALATED]
        awaiting = [t for t in tickets if t.status is Status.AWAITING_APPROVAL]

        by_category: dict[str, int] = {}
        for t in tickets:
            key = t.category or "unknown"
            by_category[key] = by_category.get(key, 0) + 1

        return {
            "tickets": len(tickets),
            "resolved": len(resolved),
            # Measured over tickets the crew actually finished with. Counting one
            # still in progress as a failure would make the number meaningless.
            "autonomous_resolution_rate": (
                round(len(resolved) / (len(resolved) + len(escalated)), 4)
                if (resolved or escalated)
                else 0.0
            ),
            "escalated": len(escalated),
            "awaiting_approval": len(awaiting),
            "at_risk": self.at_risk(now=now),
            "by_category": dict(sorted(by_category.items())),
            "irreversible_actions_taken": len(self.systems.side_effects),
            "escalation_reasons": [
                next(
                    (
                        e.detail.get("reason", "")
                        for e in reversed(t.history)
                        if e.action == "transition"
                    ),
                    "",
                )
                for t in escalated
            ],
        }
