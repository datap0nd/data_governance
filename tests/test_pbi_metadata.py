import base64
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scanner import pbi_auth, pbi_metadata
from app.scanner.walker import DiscoveredReport


WORKSPACE = {"id": "workspace-1", "name": "Governed Workspace"}


def _report(report_id: str, dataset_id: str) -> dict:
    return {
        "id": report_id,
        "name": f"Report {report_id}",
        "dataset_id": dataset_id,
        "web_url": f"https://app.powerbi.test/{report_id}",
    }


def _template(report: dict, *, provider: str) -> DiscoveredReport:
    return DiscoveredReport(
        name=report["name"],
        tmdl_path=f"{provider}://{report['dataset_id']}",
        tables=[],
        measures=[],
        metadata_provider=provider,
    )


def test_live_reader_uses_tom_first_and_reads_shared_model_once(monkeypatch):
    reports = [_report("one", "shared"), _report("two", "shared")]
    monkeypatch.setattr(
        pbi_metadata,
        "fetch_workspace_reports",
        lambda _workspace: {"workspace": WORKSPACE, "reports": reports},
    )
    calls = []
    monkeypatch.setattr(
        pbi_metadata,
        "_read_with_tom",
        lambda report, _workspace: calls.append(report["dataset_id"])
        or _template(report, provider="xmla_tom"),
    )
    monkeypatch.setattr(
        pbi_metadata,
        "_read_with_fabric",
        lambda *_args: (_ for _ in ()).throw(AssertionError("Fabric must not run")),
    )

    result = pbi_metadata.read_live_reports("Governed Workspace")

    assert calls == ["shared"]
    assert [item.name for item in result] == ["Report one", "Report two"]
    assert {item.metadata_provider for item in result} == {"xmla_tom"}
    assert [item.pbi_report_id for item in result] == ["one", "two"]


def test_tom_model_preserves_named_parameters_for_source_resolution():
    report = _report("one", "model-1")
    result = pbi_metadata._report_from_tom_model(
        report,
        WORKSPACE,
        {
            "expressions": {
                "ServerParameter": "db.internal",
                "DatabaseParameter": "warehouse",
            },
            "tables": [
                {
                    "name": "Orders",
                    "columns": ["id"],
                    "measures": [],
                    "partitions": [
                        {
                            "name": "Orders",
                            "mode": "Import",
                            "expression": (
                                "let Source = PostgreSQL.Database(ServerParameter, "
                                "DatabaseParameter), Rows = Source{[Schema=\"sales\", "
                                "Item=\"orders\"]}[Data] in Rows"
                            ),
                        }
                    ],
                }
            ],
        },
    )

    assert result.expressions == {
        "ServerParameter": "db.internal",
        "DatabaseParameter": "warehouse",
    }
    assert result.tables[0].source.postgres_identity_is_exact is False


def test_fabric_definition_is_decoded_and_parsed_without_staging_files(monkeypatch):
    tmdl = """table Orders
\tcolumn id
\tmeasure 'Order Count' = COUNTROWS(Orders)
\tpartition Orders = m
\t\tmode: import
\t\tsource =
\t\t\tlet
\t\t\t\tSource = PostgreSQL.Database(\"db.internal\", \"warehouse\"),
\t\t\t\tRows = Source{[Schema=\"sales\", Item=\"orders\"]}[Data]
\t\t\tin
\t\t\t\tRows
"""
    expressions = 'expression ServerParameter = "db.internal" meta [IsParameterQuery=true]'

    def part(path: str, value: str) -> dict:
        return {
            "path": path,
            "payload": base64.b64encode(value.encode("utf-8")).decode("ascii"),
            "payloadType": "InlineBase64",
        }

    monkeypatch.setattr(
        pbi_metadata,
        "_fabric_definition",
        lambda *_args: {
            "definition": {
                "parts": [
                    part("definition/expressions.tmdl", expressions),
                    part("definition/tables/Orders.tmdl", tmdl),
                ]
            }
        },
    )

    result = pbi_metadata._read_with_fabric(_report("one", "model-1"), WORKSPACE)

    assert result.metadata_provider == "fabric_get_definition"
    assert result.expressions == {"ServerParameter": "db.internal"}
    assert [table.table_name for table in result.tables] == ["Orders"]
    assert result.tables[0].source.sql_table == "sales.orders"
    assert result.tables[0].columns == ["id"]
    assert result.measures[0].measure_name == "Order Count"


def test_live_reader_rejects_partial_workspace_snapshot(monkeypatch):
    reports = [_report("one", "model-1"), _report("two", "model-2")]
    monkeypatch.setattr(
        pbi_metadata,
        "fetch_workspace_reports",
        lambda _workspace: {"workspace": WORKSPACE, "reports": reports},
    )
    monkeypatch.setattr(
        pbi_metadata,
        "_read_with_tom",
        lambda report, _workspace: (
            _template(report, provider="xmla_tom")
            if report["dataset_id"] == "model-1"
            else (_ for _ in ()).throw(RuntimeError("XMLA unavailable"))
        ),
    )
    monkeypatch.setattr(
        pbi_metadata,
        "_read_with_fabric",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Fabric unavailable")),
    )

    with pytest.raises(pbi_metadata.PowerBiMetadataError) as error:
        pbi_metadata.read_live_reports("Governed Workspace")

    assert "existing catalog was retained" in str(error.value)
    assert "Report two" in str(error.value)


def test_fabric_long_running_operation_fetches_result_after_success(monkeypatch):
    definition = {"definition": {"parts": []}}

    class Response:
        def __init__(self, status_code, payload=None, headers=None):
            self.status_code = status_code
            self._payload = payload or {}
            self.headers = headers or {}
            self.content = json.dumps(self._payload).encode("utf-8") if payload else b""
            self.text = self.content.decode("utf-8")

        def json(self):
            return self._payload

    state_url = "https://api.fabric.microsoft.com/v1/operations/op-1"
    result_url = f"{state_url}/result"
    responses = {
        state_url: Response(200, {"status": "Succeeded"}, {"Location": result_url}),
        result_url: Response(200, definition),
    }
    calls = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, _url, *, headers):
            calls.append(("POST", headers))
            return Response(
                202,
                headers={
                    "Location": state_url,
                    "x-ms-operation-id": "op-1",
                    "Retry-After": "0",
                },
            )

        def get(self, url, *, headers):
            calls.append((url, headers))
            return responses[url]

    observed_scope = []
    monkeypatch.setattr(
        pbi_metadata,
        "get_access_token",
        lambda *, scope: observed_scope.append(scope) or {"access_token": "fabric-token"},
    )
    monkeypatch.setattr(pbi_metadata.httpx, "Client", Client)
    monkeypatch.setattr(pbi_metadata.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pbi_metadata, "resolve_proxy", lambda _url: None)

    result = pbi_metadata._fabric_definition("workspace-1", "model-1")

    assert result == definition
    assert observed_scope == [pbi_metadata.FABRIC_SCOPE]
    assert [call[0] for call in calls] == ["POST", state_url, result_url]
    assert all(call[1]["Authorization"] == "Bearer fabric-token" for call in calls)


def test_access_token_cache_is_never_reused_for_a_different_resource(monkeypatch):
    record = {
        "refresh_token": "refresh-token",
        "access_token": "power-bi-token",
        "access_token_scope": pbi_auth.PBI_SCOPE,
        "access_token_expires_at": pbi_auth.time.time() + 3600,
        "account": "analyst@example.test",
    }
    observed_scopes = []
    monkeypatch.setattr(pbi_auth, "_load_record", lambda: dict(record))
    monkeypatch.setattr(
        pbi_auth,
        "_refresh_tokens",
        lambda cached, *, scope: observed_scopes.append(scope)
        or {
            **cached,
            "access_token": "fabric-token",
            "access_token_scope": scope,
        },
    )

    result = pbi_auth.get_access_token(scope=pbi_auth.FABRIC_SCOPE)

    assert result["access_token"] == "fabric-token"
    assert observed_scopes == [pbi_auth.FABRIC_SCOPE]


def test_tom_helper_error_redacts_access_token(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="metronome-pbi-metadata-") as folder:
        helper = Path(folder) / "metadata.exe"
        helper.write_bytes(b"placeholder")
        token = "secret-access-token"
        monkeypatch.setattr(pbi_metadata, "PBI_TOM_HELPER", str(helper))
        monkeypatch.setattr(
            pbi_metadata, "get_access_token", lambda: {"access_token": token}
        )
        monkeypatch.setattr(
            pbi_metadata.subprocess,
            "run",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr=f"Connection failed with Password={token}",
            ),
        )

        with pytest.raises(pbi_metadata.PowerBiMetadataError) as error:
            pbi_metadata._read_with_tom(_report("one", "model-1"), WORKSPACE)

        assert token not in str(error.value)
        assert "[redacted]" in str(error.value)
