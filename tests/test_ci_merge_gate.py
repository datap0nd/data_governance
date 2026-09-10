from __future__ import annotations

import pytest

from tools.ci.merge_gate import GateError, validate_needs


def graph(*, application="true", postgres="false", python="success", frontend="success", pg="skipped"):
    return {
        "scope": {"result": "success", "outputs": {"application": application, "postgres": postgres}},
        "python": {"result": python},
        "frontend": {"result": frontend},
        "postgres": {"result": pg},
    }


def test_accepts_selected_application_jobs_and_explicit_postgres_omission():
    assert validate_needs(graph()) == ["scope", "python:success", "frontend:success", "postgres:skipped"]


def test_accepts_lightweight_scope_only_run():
    assert validate_needs(graph(application="false", python="skipped", frontend="skipped"))


def test_accepts_successful_postgres_selection():
    assert validate_needs(graph(postgres="true", pg="success"))


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
