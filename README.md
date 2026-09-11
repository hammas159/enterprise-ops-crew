# enterprise-ops-crew

[![ci](https://github.com/hammas159/enterprise-ops-crew/actions/workflows/ci.yml/badge.svg)](https://github.com/hammas159/enterprise-ops-crew/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.12-blue)
![license](https://img.shields.io/badge/license-MIT-green)

**A back-office crew that resolves tickets across four systems, and stops before it
does anything it cannot undo.**

Intake → triage → playbook execution → approval gate → escalation → daily report.
Multi-agent in the sense that matters operationally: **distinct roles with distinct
authority**, not several models talking to each other.

---

## The three places it can stop safely

| Boundary | What it prevents |
|---|---|
| **Triage abstention** | Routing a ticket nobody understood. Below a confidence floor it goes to a human instead of a confident guess. |
| **Missing information** | Guessing an account number — which is how the *wrong* account gets reset. Escalates instead. |
| **Approval gate** | Anything `IRREVERSIBLE`. The refund stops; the invoice lookup before it still happened. |

The assertion that matters is not that the system reported a stop:

```python
def test_an_irreversible_action_stops_for_a_human():
    assert result.status is Status.AWAITING_APPROVAL
    assert c.systems.side_effects == []       # no money moved
```

And the gate has to be a gate, not a wall — `approve()` lets the same ticket continue,
`reject()` escalates it and takes no action. Both are tests.

## Execution runs on playbooks, not prompts

```python
Playbook("billing/v1", "billing", [
    Step("erp_lookup_invoice", {"invoice_id": "invoice_id"}),
    Step("erp_issue_refund",   {"invoice_id": "invoice_id", "amount": "amount"}),
])
```

When a customer or an auditor asks what happened, *"it followed playbook billing/v1,
step 1 succeeded, step 2 stopped for approval by m.manager"* is an answer. *"The model
decided to"* is not.

**Risk is declared per operation by whoever wrote it** — an agent does not get to judge
whether its own action is reversible. The autonomous ceiling is configuration, and
raising it visibly removes the gate (also a test).

## SLAs run on business hours

The detail everyone gets wrong. A ticket raised at **5pm Friday** with a 4-hour SLA is
not breached at 9pm Friday — the desk was closed. Wall-clock measurement produces a
dashboard full of breaches nobody caused and nobody could have prevented, and a
dashboard nobody believes is a dashboard nobody reads.

```python
Friday 16:00 → Monday 10:00  =  2 business hours
Monday 09:00 → Wednesday 12:00 = 19 business hours
```

Weekends, configurable working days, and holidays are excluded. So is time spent
waiting on the customer — an agent cannot be held to a clock it has no way to stop.

**Burn is reported, not just the breach.** `0.9` is when to act; a boolean only tells
you once it is too late:

```python
at_risk(burn_threshold=0.8)   # about to fail — actionable
```

## Status transitions are validated

A crew of agents will attempt every illegal transition eventually — resolving a ticket
it never picked up, closing one that is waiting on a human. A status field that accepts
any assignment turns those bugs into **silent data corruption** instead of errors.

Reopening a resolved ticket clears its resolution time, or the SLA would be measured
against a resolution that no longer stands.

## The report answers the question a manager actually asks

```python
{
  "autonomous_resolution_rate": 0.5,     # of tickets the crew *finished with*
  "escalated": 1,
  "awaiting_approval": 1,
  "at_risk": ["a3f9c1"],
  "irreversible_actions_taken": 1,
  "escalation_reasons": ["ambiguous: several categories matched equally"],
}
```

Resolution rate is measured over tickets the crew finished with — counting one still in
progress as a failure would make the number meaningless. `escalation_reasons` is there
because the point of a pilot is to learn *why* the machine could not finish, and a bare
count teaches nothing.

## Systems

Four mock backends, because the interesting behaviour only appears when an agent must
cross between them — read from HRMS, write to ITSM, and stop before touching payroll.

| System | Operations |
|---|---|
| HRMS | lookup employee `READ`, book leave `WRITE` |
| ITSM | reset password `WRITE`, grant access `IRREVERSIBLE` |
| CRM | lookup customer `READ`, send email `EXTERNAL` |
| ERP | lookup invoice `READ`, issue refund `IRREVERSIBLE` |

## Tests

**39 tests. No ERP licence, no model, no network.**

```bash
make test
```

| Covered | |
|---|---|
| Transitions | legal paths, illegal raises, terminal state, reopen clears resolution |
| Triage | four categories, urgency, word boundaries (`accessory` ≠ `access`), abstention |
| Business hours | after-hours, weekends, holidays, multi-day, burn before breach |
| Autonomy | reversible actions taken alone, multi-step playbooks, missing info, backend failure, optional steps |
| Approval | stops with no side effect, partial progress kept, approve, reject, arguments recorded, ceiling raised |
| Reporting | resolution rate, irreversible count, escalation reasons, at-risk, empty report |
| Audit | full trail on the ticket, approver named |

## Limits

- Triage is rule-based, and the rules are visible on purpose — a misrouted ticket is
  expensive and someone will need to know why it went where it went. An LLM classifier
  fits behind the same interface where the vocabulary is genuinely open.
- The systems are mocks. Real MCP servers implement the same `Operation` shape.
- One SLA policy per crew. Per-tenant policies are a dictionary lookup away.
- Facts a playbook needs are supplied explicitly rather than extracted from the ticket
  text — deliberate, so the routing and authority logic can be tested on its own.

## License

MIT
