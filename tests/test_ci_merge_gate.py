from __future__ import annotations

import pytest

from tools.ci.merge_gate import GateError, validate_needs


def graph(*, regression="true", postgres_required="false", equivalence="false", python="success", frontend="success", pg="skipped"):
    selected = "success" if regression == "true" else "skipped"
    return {
        "scope": {"result": "success", "outputs": {"regression": regression, "postgres_required": postgres_required, "frontend_required": regression, "equivalence": equivalence}},
        "inventory": {"result": selected},
        "plan": {"result": selected},
        "browsers": {"result": selected},
        "python": {"result": python},
        "serial": {"result": "success" if equivalence == "true" else "skipped"},
        "reconcile": {"result": selected},
        "frontend": {"result": frontend},
        "postgres": {"result": pg},
    }


def test_accepts_selected_application_jobs_and_explicit_postgres_omission():
    assert validate_needs(graph()) == [
        "scope", "inventory:success", "plan:success", "browsers:success", "python:success", "serial:skipped",
        "reconcile:success", "frontend:success", "postgres:skipped",
    ]


def test_accepts_lightweight_scope_only_run():
    assert validate_needs(graph(regression="false", python="skipped", frontend="skipped"))


def test_accepts_successful_postgres_selection():
    assert validate_needs(graph(postgres_required="true", pg="success"))


def test_accepts_required_serial_equivalence_for_orchestration():
    assert "serial:success" in validate_needs(graph(equivalence="true"))


@pytest.mark.parametrize("state", ["failure", "cancelled", "skipped"])
def test_rejects_unsuccessful_selected_job(state):
    with pytest.raises(GateError, match="selected job python"):
        validate_needs(graph(python=state))


def test_rejects_missing_result_and_unexpected_execution():
    missing = graph()
    del missing["frontend"]
    with pytest.raises(GateError, match="frontend result is missing"):
        validate_needs(missing)
    with pytest.raises(GateError, match="excluded job postgres unexpectedly"):
        validate_needs(graph(pg="success"))
