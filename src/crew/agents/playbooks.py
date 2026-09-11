"""Playbooks: what to attempt for each category.

A playbook is an ordered list of operations, not a prompt. The reason is
auditability: when a customer or an auditor asks what the system did and why,
"it followed playbook access/v1, steps 1 and 2, and stopped at step 3 for approval"
is an answer. "The model decided to" is not.

The language model's job here is triage and explanation. Execution stays on rails.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Step:
    operation: str
    # Maps operation parameters to facts extracted from the ticket. Declarative so a
    # playbook can be read and checked without executing it.
    arguments: dict[str, str] = field(default_factory=dict)
    optional: bool = False


@dataclass
class Playbook:
    name: str
    category: str
    steps: list[Step]


PLAYBOOKS: dict[str, Playbook] = {
    "access": Playbook(
        "access/v1",
        "access",
        [Step("itsm_reset_password", {"account": "requester"})],
    ),
    "hr": Playbook(
        "hr/v1",
        "hr",
        [
            Step("hrms_lookup_employee", {"employee_id": "requester"}),
            Step("hrms_book_leave", {"employee_id": "requester", "days": "days"}),
        ],
    ),
    "billing": Playbook(
        "billing/v1",
        "billing",
        [
            Step("erp_lookup_invoice", {"invoice_id": "invoice_id"}),
            # IRREVERSIBLE. The registry stops here and waits for a human.
            Step("erp_issue_refund", {"invoice_id": "invoice_id", "amount": "amount"}),
        ],
    ),
    "incident": Playbook(
        "incident/v1",
        "incident",
        [Step("itsm_reset_password", {"account": "requester"}, optional=True)],
    ),
}
