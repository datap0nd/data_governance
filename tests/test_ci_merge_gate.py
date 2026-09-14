from __future__ import annotations

import pytest

from tools.ci.merge_gate import GateError, validate_needs


def graph(*, application="true", postgres="false", python="success", frontend="success", pg="skipped", windows="false", win="skipped"):
    return {
        "scope": {"result": "success", "outputs": {"application": application, "postgres": postgres, "windows": windows}},
        "python": {"result": python},
        "windows": {"result": win},
        "frontend": {"result": frontend},
        "postgres": {"result": pg},
    }


def test_accepts_selected_application_jobs_and_explicit_postgres_omission():
    assert validate_needs(graph()) == ["scope", "python:success", "windows:skipped", "frontend:success", "postgres:skipped"]


def test_accepts_lightweight_scope_only_run():
    assert validate_needs(graph(application="false", python="skipped", frontend="skipped"))


def test_accepts_successful_postgres_selection():
    assert validate_needs(graph(postgres="true", pg="success"))


def test_requires_successful_windows_contracts_when_selected():
    assert validate_needs(graph(windows="true", win="success"))
    for state in ("failure", "cancelled", "skipped"):
        with pytest.raises(GateError, match="selected job windows"):
            validate_needs(graph(windows="true", win=state))


def test_missing_windows_scope_or_result_fails_closed():
    missing = graph()
    del missing["scope"]["outputs"]["windows"]
    with pytest.raises(GateError, match="Windows contract scope"):
        validate_needs(missing)
    missing = graph(windows="true", win="success")
    del missing["windows"]
    with pytest.raises(GateError, match="windows result is missing"):
        validate_needs(missing)


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
