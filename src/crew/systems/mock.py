"""Mock enterprise systems, exposed as tools with declared risk.

Four systems because that is what a real back office has, and because the interesting
behaviour only appears when an agent must cross between them - read from HRMS, write to
ITSM, and stop before touching payroll.

Risk is declared per operation, at registration, by the person who wrote it. An agent
does not get to judge whether its own action is reversible.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class Risk(enum.IntEnum):
    READ = 0
    WRITE = 1  # reversible
    EXTERNAL = 2  # leaves the company: email, SMS, supplier API
    IRREVERSIBLE = 3  # payment, deletion, contract, anything sent to a person


class ApprovalRequired(RuntimeError):
    def __init__(self, operation: str, risk: Risk, arguments: dict) -> None:
        # Not `self.args`: BaseException owns that name and super().__init__ would
        # overwrite it with the message tuple, losing what a human needs to approve.
        self.operation, self.risk, self.arguments = operation, risk, arguments
        super().__init__(f"{operation} is {risk.name} and requires approval")


class SystemError_(RuntimeError):
    """A backend failed. Ordinary, and recoverable."""


@dataclass
class Operation:
    name: str
    system: str
    risk: Risk
    fn: Callable[..., Any]
    description: str = ""


@dataclass
class SystemsRegistry:
    operations: dict[str, Operation] = field(default_factory=dict)
    autonomous_ceiling: Risk = Risk.WRITE
    calls: list[tuple[str, dict]] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)

    def register(self, op: Operation) -> None:
        self.operations[op.name] = op

    def describe(self) -> list[str]:
        return [
            f"{op.system}.{op.name} [{op.risk.name}]"
            + ("" if op.risk <= self.autonomous_ceiling else " (approval required)")
            for op in sorted(self.operations.values(), key=lambda o: (o.system, o.name))
        ]

    def call(self, name: str, arguments: dict, *, approved: bool = False) -> Any:
        op = self.operations.get(name)
        if op is None:
            raise SystemError_(f"no such operation {name!r}")
        if op.risk > self.autonomous_ceiling and not approved:
            raise ApprovalRequired(name, op.risk, arguments)
        self.calls.append((name, arguments))
        try:
            return op.fn(**arguments)
        except ApprovalRequired:
            raise
        except Exception as exc:
            raise SystemError_(f"{name} failed: {exc}") from exc


def build_default_registry(**kw) -> SystemsRegistry:
    """A small back office: HRMS, ITSM, CRM, ERP."""
    reg = SystemsRegistry(**kw)
    effects = reg.side_effects

    reg.register(
        Operation(
            "hrms_lookup_employee",
            "hrms",
            Risk.READ,
            lambda employee_id: {
                "id": employee_id,
                "name": "A. Employee",
                "manager": "M. Manager",
                "leave_days": 12,
            },
            "Fetch an employee record",
        )
    )
    reg.register(
        Operation(
            "hrms_book_leave",
            "hrms",
            Risk.WRITE,
            lambda employee_id, days: {"booked": days, "remaining": 12 - days},
            "Book annual leave",
        )
    )
    reg.register(
        Operation(
            "itsm_reset_password",
            "itsm",
            Risk.WRITE,
            lambda account: {"account": account, "reset": True},
            "Reset an account password",
        )
    )
    reg.register(
        Operation(
            "itsm_grant_access",
            "itsm",
            Risk.IRREVERSIBLE,
            lambda account, system: (
                effects.append(f"access:{account}:{system}") or {"granted": True}
            ),
            "Grant access to a system",
        )
    )
    reg.register(
        Operation(
            "crm_lookup_customer",
            "crm",
            Risk.READ,
            lambda customer_id: {"id": customer_id, "tier": "gold"},
            "Fetch a customer record",
        )
    )
    reg.register(
        Operation(
            "crm_send_email",
            "crm",
            Risk.EXTERNAL,
            lambda to, body: effects.append(f"email:{to}") or {"sent": True},
            "Email a customer. Cannot be recalled.",
        )
    )
    reg.register(
        Operation(
            "erp_lookup_invoice",
            "erp",
            Risk.READ,
            lambda invoice_id: {"id": invoice_id, "amount": 4200, "status": "unpaid"},
            "Fetch an invoice",
        )
    )
    reg.register(
        Operation(
            "erp_issue_refund",
            "erp",
            Risk.IRREVERSIBLE,
            lambda invoice_id, amount: (
                effects.append(f"refund:{invoice_id}") or {"refunded": amount}
            ),
            "Issue a refund. Moves money.",
        )
    )
    return reg
