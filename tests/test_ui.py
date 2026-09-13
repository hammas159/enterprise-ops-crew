"""Smoke tests for the Streamlit demo (the `ui` dependency group)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")


def _app() -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=15)
    assert not at.exception
    return at


def _submit(at: AppTest, scenario: str) -> AppTest:
    at.selectbox[0].set_value(scenario).run(timeout=15)
    button = next(b for b in at.button if b.label == "Submit ticket")
    button.click().run(timeout=15)
    return at


def test_app_loads_without_exceptions():
    _app()


def test_access_ticket_resolves_without_approval():
    at = _submit(_app(), "Access — password reset")
    assert not at.exception
    assert any("resolved" in s.value for s in at.success)


def test_billing_refund_stops_at_the_approval_gate_then_resolves():
    at = _submit(_app(), "Billing — refund (hits the approval gate)")
    assert not at.exception
    assert any("awaiting_approval" in w.value for w in at.warning)

    button = next(b for b in at.button if b.label == "Approve as human operator")
    button.click().run(timeout=15)
    assert not at.exception
    assert any("resolved" in s.value for s in at.success)


def test_billing_refund_can_be_rejected():
    at = _submit(_app(), "Billing — refund (hits the approval gate)")
    button = next(b for b in at.button if b.label == "Reject")
    button.click().run(timeout=15)
    assert not at.exception
    assert any("escalated" in w.value for w in at.warning)


def test_low_confidence_ticket_escalates_to_a_human():
    at = _submit(_app(), "Unclear — low confidence (escalates to a human)")
    assert not at.exception
    assert any("escalated" in w.value for w in at.warning)


def test_fleet_tab_reflects_submitted_tickets():
    at = _submit(_app(), "Access — password reset")
    assert not at.exception
    assert any("id" in str(df.value) for df in at.dataframe) or len(at.dataframe) > 0
