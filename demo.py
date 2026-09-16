"""Four tickets in. Three resolved end to end, one stopped at the approval gate.

    python demo.py

Four mock enterprise systems, a real playbook engine, real state transitions.
No model, no network.
"""

import sys

sys.path.insert(0, "src")

from crew.crew import Crew
from crew.systems.mock import build_default_registry
from crew.tickets.models import Ticket

crew = Crew(systems=build_default_registry())

TICKETS = [
    ("Cannot log in, password reset needed", {}),
    ("I would like to book annual leave", {"days": 3}),
    ("Production down, all users affected", {}),
    ("Refund for invoice 4421 please", {"invoice_id": "4421", "amount": 4200}),
]

print("INPUT")
for subject, facts in TICKETS:
    extra = f"   facts={facts}" if facts else ""
    print(f'   "{subject}"{extra}')
print()

print("OUTPUT")
print(f"   {'ticket':46} {'category':12} {'priority':9} {'status':14} steps")
print("   " + "-" * 96)

worked = []
for subject, facts in TICKETS:
    t = crew.intake(Ticket(subject=subject, requester="e-1001"), **facts)
    t = crew.work(t.id)
    worked.append(t)
    steps = len(t.history)
    print(f"   {subject[:46]:46} {t.category:12} {t.priority.name:9} {t.status.name:14} {steps}")

print()
pending = [t for t in worked if t.id in crew.approvals]
for t in pending:
    ask = crew.approvals[t.id]
    print(f"   HELD FOR APPROVAL   {t.subject}")
    print(f"      blocked on       {ask.operation}")
    print(f"      arguments        {ask.arguments}")

print()
print(f"   calls actually made   {[c[0] for c in crew.systems.calls]}")
print(f"   side effects          {crew.systems.side_effects}")
print()
print("   The lookup before the refund still ran. The gate stops the")
print("   irreversible step, not the whole playbook, and no money moved.")
