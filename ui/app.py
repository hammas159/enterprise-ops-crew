"""The Streamlit demo the `ui` dependency group declared but never shipped.

Two tabs: submit a ticket and watch it move through intake -> triage -> a playbook
(stopping for human approval before anything irreversible), and a fleet view of every
ticket created in this session.

Run: streamlit run ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from crew.crew import Crew  # noqa: E402
from crew.systems.mock import build_default_registry  # noqa: E402
from crew.tickets.models import Ticket  # noqa: E402

st.set_page_config(page_title="enterprise-ops-crew demo", layout="wide")
st.title("enterprise-ops-crew")
st.caption(
    "Distinct roles with distinct authority. Triage decides, a playbook executes, "
    "and a human is the only thing that can authorise an irreversible action."
)

if "crew" not in st.session_state:
    st.session_state.crew = Crew(systems=build_default_registry())

crew: Crew = st.session_state.crew

SAMPLES = {
    "Access — password reset": {
        "subject": "Cannot log in, password reset needed",
        "body": "I'm locked out of my account and need a password reset.",
        "facts": {},
    },
    "HR — leave request": {
        "subject": "Requesting annual leave next week",
        "body": "I'd like to take 3 days of annual leave.",
        "facts": {"employee_id": "emp-42", "days": "3"},
    },
    "Billing — refund (hits the approval gate)": {
        "subject": "Refund request for overcharged invoice",
        "body": "I was overcharged on invoice INV-2001, please refund.",
        "facts": {"invoice_id": "INV-2001", "amount": "150.00"},
    },
    "Incident — production outage": {
        "subject": "Production down, all users affected",
        "body": "The service is completely down, urgent.",
        "facts": {},
    },
    "Unclear — low confidence (escalates to a human)": {
        "subject": "hey can someone help me with the thing",
        "body": "not sure who to ask",
        "facts": {},
    },
}

tab_ticket, tab_fleet = st.tabs(["Submit & work a ticket", "Fleet"])

with tab_ticket:
    sample_key = st.selectbox("Sample ticket", list(SAMPLES.keys()))
    sample = SAMPLES[sample_key]
    subject = st.text_input("Subject", value=sample["subject"])
    body = st.text_area("Body", value=sample["body"])

    if st.button("Submit ticket", type="primary"):
        ticket = Ticket(subject=subject, body=body, requester="demo-user")
        crew.intake(ticket, **sample["facts"])
        st.session_state.active_ticket_id = ticket.id
        if ticket.status.value == "triaged":
            # intake() escalates low-confidence tickets directly, without ever
            # reaching "triaged" - those must not be handed to work().
            crew.work(ticket.id)

    active_id = st.session_state.get("active_ticket_id")
    if active_id and active_id in crew.tickets:
        ticket = crew.tickets[active_id]
        st.divider()
        status_color = {
            "resolved": "success",
            "escalated": "warning",
            "awaiting_approval": "warning",
            "closed": "info",
        }.get(ticket.status.value, "info")
        getattr(st, status_color)(
            f"**{ticket.id}** — status: **{ticket.status.value}** — "
            f"category: {ticket.category or 'unclassified'} — priority: {ticket.priority.name}"
        )

        pending = crew.approvals.get(ticket.id)
        if pending:
            st.warning(
                f"Waiting for human approval: **{pending.operation}** "
                f"(risk: {pending.risk}) with args {pending.arguments}"
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Approve as human operator"):
                    crew.approve(ticket.id, approver="demo-operator")
                    st.rerun()
            with col2:
                if st.button("Reject"):
                    crew.reject(ticket.id, approver="demo-operator", reason="declined in demo")
                    st.rerun()

        st.subheader("Event history")
        for event in ticket.history:
            st.write(f"`{event.actor}` **{event.action}** — {event.detail}")

with tab_fleet:
    st.markdown("Every ticket created in this session.")
    if not crew.tickets:
        st.info("No tickets yet — submit one in the first tab.")
    else:
        rows = [
            {
                "id": t.id,
                "subject": t.subject[:40],
                "category": t.category or "—",
                "priority": t.priority.name,
                "status": t.status.value,
                "events": len(t.history),
            }
            for t in crew.tickets.values()
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)

        st.subheader("By status")
        from collections import Counter

        st.bar_chart(Counter(t.status.value for t in crew.tickets.values()))
