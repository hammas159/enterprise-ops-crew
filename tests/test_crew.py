"""Enterprise ops crew tests.

The systems are mocks with declared risk, so every authority boundary — what an agent
may do alone, what stops for a human, what happens when a backend is down — is
assertable without an ERP licence.
"""

from __future__ import annotations

import datetime as dt

import pytest

from crew.agents.triage import triage
from crew.crew import Crew
from crew.systems.mock import Operation, Risk, build_default_registry
from crew.tickets.models import Priority, Status, Ticket, TransitionError
from crew.tickets.sla import BusinessHours, SLAPolicy


def crew(**kw) -> Crew:
    return Crew(systems=build_default_registry(**kw))


def ticket(subject, **kw) -> Ticket:
    kw.setdefault("requester", "e-1001")
    return Ticket(subject=subject, **kw)


class TestTransitions:
    def test_legal_path(self):
        t = ticket("x")
        t.transition(Status.TRIAGED, actor="a")
        t.transition(Status.IN_PROGRESS, actor="a")
        t.transition(Status.RESOLVED, actor="a")
        assert t.status is Status.RESOLVED

    def test_illegal_transition_raises(self):
        """A status field that accepts anything turns agent bugs into silent
        data corruption."""
        with pytest.raises(TransitionError):
            ticket("x").transition(Status.RESOLVED, actor="a")

    def test_closed_is_terminal(self):
        t = ticket("x")
        t.transition(Status.CLOSED, actor="a")
        with pytest.raises(TransitionError):
            t.transition(Status.IN_PROGRESS, actor="a")

    def test_reopening_clears_the_resolution_time(self):
        """Otherwise the SLA is measured against a resolution that no longer stands."""
        t = ticket("x")
        t.transition(Status.TRIAGED, actor="a")
        t.transition(Status.IN_PROGRESS, actor="a")
        t.transition(Status.RESOLVED, actor="a")
        assert t.resolved_at is not None
        t.transition(Status.IN_PROGRESS, actor="a")
        assert t.resolved_at is None

    def test_every_transition_is_recorded(self):
        t = ticket("x")
        t.transition(Status.TRIAGED, actor="triage", reason="because")
        event = t.history[-1]
        assert event.detail["to"] == "triaged" and event.detail["reason"] == "because"


class TestTriage:
    @pytest.mark.parametrize(
        ("subject", "category"),
        [
            ("Cannot log in, password reset needed", "access"),
            ("I would like to book annual leave", "hr"),
            ("Refund for invoice 4421 please", "billing"),
            ("Production down, all users affected", "incident"),
        ],
    )
    def test_categories(self, subject, category):
        assert triage(subject).category == category

    def test_urgency_signals_raise_priority(self):
        assert triage("Production down, all users affected").priority is Priority.URGENT

    def test_a_word_inside_a_word_does_not_match(self):
        """'access' must not fire on 'accessory'."""
        assert "access" not in triage("my accessory arrived").matched

    def test_unrecognised_tickets_go_to_a_human(self):
        """A triage system with no abstention route routes everything, including
        what it does not understand."""
        result = triage("hello there")
        assert result.needs_human and result.category == "unknown"


class TestBusinessHours:
    def test_after_hours_time_does_not_count(self):
        """A ticket raised at 5pm Friday is not breached at 9pm Friday."""
        bh = BusinessHours()
        elapsed = bh.elapsed_seconds(
            dt.datetime(2026, 9, 11, 17, 0), dt.datetime(2026, 9, 11, 21, 0)
        )
        assert elapsed == 0.0

    def test_the_weekend_does_not_count(self):
        bh = BusinessHours()
        elapsed = bh.elapsed_seconds(
            dt.datetime(2026, 9, 11, 16, 0), dt.datetime(2026, 9, 14, 10, 0)
        )
        assert elapsed / 3600 == 2.0  # 1h Friday + 1h Monday

    def test_a_full_working_day(self):
        bh = BusinessHours()
        elapsed = bh.elapsed_seconds(
            dt.datetime(2026, 9, 14, 9, 0), dt.datetime(2026, 9, 14, 17, 0)
        )
        assert elapsed / 3600 == 8.0

    def test_multiple_days_accumulate(self):
        bh = BusinessHours()
        elapsed = bh.elapsed_seconds(
            dt.datetime(2026, 9, 14, 9, 0), dt.datetime(2026, 9, 16, 12, 0)
        )
        assert elapsed / 3600 == 19.0  # 8 + 8 + 3

    def test_holidays_are_excluded(self):
        bh = BusinessHours(holidays=frozenset({dt.date(2026, 9, 15)}))
        elapsed = bh.elapsed_seconds(
            dt.datetime(2026, 9, 15, 9, 0), dt.datetime(2026, 9, 15, 17, 0)
        )
        assert elapsed == 0.0

    def test_burn_is_reported_not_just_the_breach(self):
        """0.9 is when to act. A boolean only tells you once it is too late."""
        policy = SLAPolicy()
        created = dt.datetime(2026, 9, 14, 9, 0).timestamp()
        now = dt.datetime(2026, 9, 14, 12, 0).timestamp()
        status = policy.status(
            created_at=created,
            priority=Priority.URGENT,
            first_response_at=None,
            resolved_at=None,
            now=now,
        )
        assert status["burn"] == pytest.approx(0.75, abs=0.01)
        assert not status["resolution_breached"]


class TestAutonomousWork:
    def test_a_reversible_action_is_taken_alone(self):
        c = crew()
        t = c.intake(ticket("Cannot log in, password reset needed"))
        assert c.work(t.id).status is Status.RESOLVED
        assert ("itsm_reset_password", {"account": "e-1001"}) in c.systems.calls

    def test_multi_step_playbook_runs_to_completion(self):
        c = crew()
        t = c.intake(ticket("I would like to book annual leave"), days=3)
        assert c.work(t.id).status is Status.RESOLVED
        assert len(c.systems.calls) == 2

    def test_missing_information_escalates_rather_than_guessing(self):
        """Guessing an account number is how the wrong account gets reset."""
        c = crew()
        t = c.intake(ticket("I would like to book annual leave"))  # no `days`
        result = c.work(t.id)
        assert result.status is Status.ESCALATED
        assert "missing information" in result.history[-1].detail["reason"]

    def test_a_backend_failure_escalates(self):
        c = crew()
        c.systems.register(
            Operation(
                "itsm_reset_password",
                "itsm",
                Risk.WRITE,
                lambda account: (_ for _ in ()).throw(ConnectionError("itsm down")),
            )
        )
        t = c.intake(ticket("Cannot log in, password reset needed"))
        assert c.work(t.id).status is Status.ESCALATED

    def test_an_optional_step_failing_does_not_escalate(self):
        c = crew()
        c.systems.register(
            Operation(
                "itsm_reset_password",
                "itsm",
                Risk.WRITE,
                lambda account: (_ for _ in ()).throw(ConnectionError("itsm down")),
            )
        )
        t = c.intake(ticket("Production down, all users affected"))
        assert c.work(t.id).status is Status.RESOLVED

    def test_an_unknown_category_escalates(self):
        c = crew()
        t = c.intake(ticket("hello there"))
        assert t.status is Status.ESCALATED


class TestApprovalGate:
    def test_an_irreversible_action_stops_for_a_human(self):
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        result = c.work(t.id)
        assert result.status is Status.AWAITING_APPROVAL
        assert c.approvals[t.id].operation == "erp_issue_refund"
        # The assertion that matters: no money moved.
        assert c.systems.side_effects == []

    def test_the_lookup_before_it_still_happened(self):
        """The gate stops the dangerous step, not the whole playbook."""
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        assert c.systems.calls[0][0] == "erp_lookup_invoice"

    def test_approval_lets_the_work_through(self):
        """The gate must be a gate, not a wall."""
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        assert c.approve(t.id, approver="manager").status is Status.RESOLVED
        assert c.systems.side_effects == ["refund:4421"]

    def test_rejection_escalates_and_takes_no_action(self):
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        result = c.reject(t.id, approver="manager", reason="customer not eligible")
        assert result.status is Status.ESCALATED
        assert c.systems.side_effects == []

    def test_the_approval_record_keeps_the_arguments(self):
        """A human approving 'issue a refund' without the amount is not approving."""
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        assert c.approvals[t.id].arguments == {"invoice_id": "4421", "amount": 4200}

    def test_approving_nothing_raises(self):
        c = crew()
        with pytest.raises(KeyError):
            c.approve("no-such-ticket", approver="manager")

    def test_raising_the_ceiling_removes_the_gate(self):
        """Policy is configuration, and the effect of changing it must be visible."""
        c = Crew(systems=build_default_registry(autonomous_ceiling=Risk.IRREVERSIBLE))
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        assert c.work(t.id).status is Status.RESOLVED
        assert c.systems.side_effects == ["refund:4421"]


class TestReporting:
    def test_resolution_rate_excludes_work_in_progress(self):
        """Counting an unfinished ticket as a failure makes the number meaningless."""
        c = crew()
        c.work(c.intake(ticket("Cannot log in, password reset needed")).id)
        c.intake(ticket("hello there"))  # escalates at triage
        c.intake(ticket("Refund please", body="invoice"), invoice_id="1", amount=1)
        report = c.daily_report()
        assert report["autonomous_resolution_rate"] == 0.5

    def test_irreversible_actions_are_counted(self):
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        c.approve(t.id, approver="manager")
        assert c.daily_report()["irreversible_actions_taken"] == 1

    def test_escalation_reasons_are_reported(self):
        c = crew()
        c.intake(ticket("hello there"))
        assert any(r for r in c.daily_report()["escalation_reasons"])

    def test_at_risk_is_reported_before_the_breach(self):
        """NORMAL priority allows 24 business hours. Opened 09:00 Monday, checked at
        13:00 Wednesday: 8 + 8 + 4 = 20 business hours, so 83% of the budget is gone
        and the ticket has *not* yet breached. That gap is the whole point — a list
        of tickets that already failed is a report, not something anyone can act on.
        """
        c = crew()
        t = ticket("Refund for invoice 4421 please")
        t.created_at = dt.datetime(2026, 9, 14, 9, 0).timestamp()
        c.intake(t, invoice_id="4421", amount=4200)
        c.work(t.id)  # parks in AWAITING_APPROVAL

        approaching = dt.datetime(2026, 9, 16, 13, 0).timestamp()
        status = c.sla_status(t.id, now=approaching)
        assert not status["resolution_breached"]
        assert status["burn"] >= 0.8
        assert t.id in c.at_risk(now=approaching)

    def test_a_resolved_ticket_is_not_at_risk(self):
        c = crew()
        t = ticket("Cannot log in, password reset needed")
        t.created_at = dt.datetime(2026, 9, 14, 9, 0).timestamp()
        c.intake(t)
        c.work(t.id)
        far = dt.datetime(2026, 9, 30, 14, 0).timestamp()
        assert t.id not in c.at_risk(now=far)

    def test_empty_report_does_not_divide_by_zero(self):
        assert crew().daily_report()["autonomous_resolution_rate"] == 0.0


class TestAudit:
    def test_the_full_trail_is_on_the_ticket(self):
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        c.approve(t.id, approver="manager")
        actions = [e.action for e in t.history]
        assert "received" in actions
        assert "classified" in actions
        assert "approval_requested" in actions
        assert "approved" in actions
        assert "executed" in actions

    def test_the_approver_is_named(self):
        c = crew()
        t = c.intake(ticket("Refund for invoice 4421 please"), invoice_id="4421", amount=4200)
        c.work(t.id)
        c.approve(t.id, approver="m.manager")
        assert any(e.actor == "m.manager" for e in t.history)
