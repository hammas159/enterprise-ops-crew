"""Triage: category and priority, with a confidence the system is willing to act on.

Rule-based first, and the rules are visible. Two reasons, both practical rather than
ideological: a misrouted ticket is expensive and someone will need to know *why* it
went where it went, and the closed-class vocabulary of support tickets - "reset",
"refund", "invoice", "access" - is genuinely well served by keywords.

The part that matters is **the confidence floor**. Below it, the ticket goes to a human
rather than to a confident guess. A triage system with no abstention route routes
everything, including the things it does not understand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..tickets.models import Priority

CATEGORY_RULES: dict[str, list[str]] = {
    "access": ["password", "reset", "locked out", "cannot log in", "can't log in",
               "access", "permission", "mfa", "2fa"],
    "hr": ["leave", "holiday", "annual leave", "payslip", "salary", "onboarding",
           "resign", "sick"],
    "billing": ["invoice", "refund", "charge", "payment", "billing", "overcharged",
                "receipt"],
    "incident": ["outage", "down", "not working", "broken", "error", "failed",
                 "timeout", "500"],
    "request": ["please add", "request", "new account", "provision", "install"],
}

# Words that raise priority regardless of category.
URGENCY_SIGNALS: dict[str, Priority] = {
    "production down": Priority.URGENT,
    "outage": Priority.URGENT,
    "all users": Priority.URGENT,
    "data loss": Priority.URGENT,
    "security": Priority.URGENT,
    "breach": Priority.URGENT,
    "urgent": Priority.HIGH,
    "asap": Priority.HIGH,
    "blocked": Priority.HIGH,
    "cannot work": Priority.HIGH,
    "deadline": Priority.HIGH,
}

# A word inside a longer word should not count: "access" must not fire on "accessory".
_WORD_BOUNDARY = r"\b{}\b"


@dataclass
class TriageResult:
    category: str
    priority: Priority
    confidence: float
    matched: list[str]
    needs_human: bool = False
    reason: str = ""


def _hits(text: str, phrases: list[str]) -> list[str]:
    return [
        p for p in phrases
        if re.search(_WORD_BOUNDARY.format(re.escape(p)), text, re.I)
    ]


def triage(subject: str, body: str = "", *, confidence_floor: float = 0.34) -> TriageResult:
    text = f"{subject}\n{body}"

    scores: dict[str, list[str]] = {}
    for category, phrases in CATEGORY_RULES.items():
        found = _hits(text, phrases)
        if found:
            scores[category] = found

    urgency = [p for p in URGENCY_SIGNALS if re.search(re.escape(p), text, re.I)]
    priority = min((URGENCY_SIGNALS[p] for p in urgency), default=Priority.NORMAL)

    if not scores:
        return TriageResult("unknown", priority, 0.0, [], needs_human=True,
                            reason="no category matched")

    best = max(scores, key=lambda c: len(scores[c]))
    total = sum(len(v) for v in scores.values())
    confidence = round(len(scores[best]) / total, 3)

    # An incident outranks a category that merely shares vocabulary with it.
    if "incident" in scores and best != "incident" and priority <= Priority.HIGH:
        best = "incident"
        confidence = round(len(scores["incident"]) / total, 3)

    needs_human = confidence < confidence_floor
    return TriageResult(
        category=best, priority=priority, confidence=confidence,
        matched=scores[best], needs_human=needs_human,
        reason="ambiguous: several categories matched equally" if needs_human else "",
    )
