"""Publish the latest sanitized Flow failure to one GitHub issue."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

from app.scanner.pbi_auth import resolve_proxy

DEFAULT_REPOSITORY = "datap0nd/data_governance"
ISSUE_TITLE = "Metronome: latest Flow failure"
MAX_EVENTS = 20
MAX_FIELD_CHARS = 4_000
MAX_TRACEBACK_CHARS = 2_500
MAX_ISSUE_BODY_CHARS = 24_000
REQUEST_TIMEOUT_SECONDS = 20
_RUN_MARKER_RE = re.compile(r"<!--\s*metronome-flow-run-id:(\d+)\s*-->")
_PUBLISH_LOCK = threading.Lock()


def _safe_text(value: Any, limit: int = MAX_FIELD_CHARS, *, tail: bool = False) -> str:
    """Bound diagnostic text and remove credentials, email, and local paths."""
    text = str(value or "")
    token = str(os.environ.get("DG_GITHUB_TOKEN") or "")
    if token:
        text = text.replace(token, "[redacted]")
    text = re.sub(
        r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+",
        "Bearer [redacted]",
        text,
    )
    text = re.sub(
        r"(?i)\bauthorization\s*[=:]\s*(?:bearer|basic)?\s*[^\s,;}]+",
        "authorization=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)([\"'](?:password|passwd|token|secret|authorization|api[_ -]?key)[\"']\s*:\s*)"
        r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
        lambda match: (
            f"{match.group(1)}{match.group('quote')}[redacted]"
            f"{match.group('quote')}"
        ),
        text,
    )
    text = re.sub(
        r"(?i)(password|passwd|token|secret|authorization|api[_ -]?key)"
        r"\s*[=:]\s*([\"']).*?\2",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)(password|passwd|token|secret|authorization|api[_ -]?key)"
        r"\s*[=:]\s*[^\s;,]+",
        r"\1=[redacted]",
        text,
    )
    text = re.sub(
        r"(?i)postgresql(?:\+\w+)?://[^\s]+",
        "postgresql://[redacted]",
        text,
    )
    text = re.sub(
        r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\)[^\r\n<>\"']+",
        "[local path]",
        text,
    )
    text = re.sub(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "[email redacted]",
        text,
    )
    text = text.strip()
    if len(text) <= limit:
        return text
    return ("…" + text[-limit:]) if tail else (text[:limit] + "…")


def _pre(value: Any, limit: int = MAX_FIELD_CHARS, *, tail: bool = False) -> str:
    return f"<pre>{html.escape(_safe_text(value, limit, tail=tail))}</pre>"


def _version() -> str:
    try:
        return _safe_text(
            (Path(__file__).resolve().parent.parent / "VERSION").read_text(
                encoding="utf-8",
            ),
            120,
        ) or "dev"
    except OSError:
        return "dev"


def build_issue_body(snapshot: dict) -> str:
    """Render one bounded Markdown report from an immutable failure snapshot."""
    run_id = int(snapshot["run_id"])
    files_saved = int(snapshot.get("files_saved") or 0)
    rows = (
        ("Flow", snapshot.get("flow_name")),
        ("Run", f"#{run_id}"),
        ("Status", snapshot.get("status") or "failed"),
        ("Stage", snapshot.get("failure_stage") or "unknown"),
        ("Site", snapshot.get("site_name") or "unknown"),
        ("Report", snapshot.get("report_name") or "unknown"),
        ("Trigger", snapshot.get("trigger_type") or "unknown"),
        ("Started", snapshot.get("started_at") or "not recorded"),
        ("Finished", snapshot.get("finished_at") or "not recorded"),
        ("Files saved", str(files_saved)),
        ("Metronome version", snapshot.get("version") or _version()),
    )
    lines = [
        f"<!-- metronome-flow-run-id:{run_id} -->",
        "# Latest Metronome Flow failure",
        "",
        "> This issue is maintained automatically. Its body is replaced when a newer Flow run fails.",
        "",
        "| Field | Value |",
        "|---|---|",
        *(
            f"| {label} | {html.escape(_safe_text(value, 500)).replace('|', '&#124;')} |"
            for label, value in rows
        ),
        "",
        "## Terminal error",
        "",
        _pre(
            snapshot.get("error") or "The run failed without an error message.",
            8_000,
            tail=True,
        ),
        "",
        "## Recent run events (newest first)",
        "",
    ]
    events = list(snapshot.get("events") or [])[-MAX_EVENTS:]
    if not events:
        lines.append("No run events were recorded.")
    prefix = "\n".join(lines)
    footer = (
        "\n---\nNo credentials, email addresses, local filesystem paths, "
        "downloaded data, or screenshots are published."
    )
    blocks = []
    used = len(prefix) + len(footer)
    for event in reversed(events):
        heading = " · ".join(filter(None, (
            _safe_text(event.get("created_at"), 80),
            _safe_text(event.get("status"), 40),
            _safe_text(event.get("stage"), 120),
        )))
        event_lines = [f"### {heading or 'Event'}", ""]
        if event.get("message"):
            event_lines.extend((_pre(event["message"], 1_200, tail=True), ""))
        if event.get("error") and event.get("error") != event.get("message"):
            event_lines.extend((
                "**Error**", "", _pre(event["error"], 1_800, tail=True), "",
            ))
        if event.get("traceback"):
            event_lines.extend((
                "<details><summary>Sanitized traceback tail</summary>",
                "",
                _pre(event["traceback"], MAX_TRACEBACK_CHARS, tail=True),
                "</details>",
                "",
            ))
        block = "\n".join(event_lines)
        if used + len(block) > MAX_ISSUE_BODY_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return prefix + "\n" + "\n".join(blocks) + footer


def _headers() -> dict[str, str]:
    token = str(os.environ.get("DG_GITHUB_TOKEN") or "").strip()
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Metronome",
        "Authorization": f"Bearer {token}",
    }


def _request_via_powershell(method: str, url: str, payload: dict | None) -> Any:
    child_env = os.environ.copy()
    child_env["METRONOME_GITHUB_METHOD"] = method
    child_env["METRONOME_GITHUB_REQUEST_URL"] = url
    child_env["METRONOME_GITHUB_REQUEST_BODY"] = json.dumps(
        payload, ensure_ascii=False,
    ) if payload is not None else ""
    script = r"""
$ErrorActionPreference = 'Stop'
$headers = @{
    'Accept' = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent' = 'Metronome'
    'Authorization' = 'Bearer ' + $env:DG_GITHUB_TOKEN
}
$parameters = @{
    Uri = $env:METRONOME_GITHUB_REQUEST_URL
    Method = $env:METRONOME_GITHUB_METHOD
    Headers = $headers
    TimeoutSec = 20
}
if ($env:METRONOME_GITHUB_REQUEST_BODY) {
    $parameters['ContentType'] = 'application/json; charset=utf-8'
    $parameters['Body'] = $env:METRONOME_GITHUB_REQUEST_BODY
}
$result = Invoke-RestMethod @parameters
$result | ConvertTo-Json -Depth 20 -Compress
"""
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        env=child_env,
    )
    if completed.returncode != 0:
        detail = completed.stderr or completed.stdout or "PowerShell request failed"
        raise RuntimeError(_safe_text(detail, 1_000, tail=True))
    return json.loads((completed.stdout or "null").lstrip("\ufeff"))


def _request(method: str, url: str, payload: dict | None = None) -> Any:
    import httpx

    try:
        response = httpx.request(
            method,
            url,
            headers=_headers(),
            json=payload,
            proxy=resolve_proxy(url),
            follow_redirects=True,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except Exception as primary_error:
        if os.name != "nt":
            raise RuntimeError(_safe_text(primary_error, 1_000, tail=True)) from primary_error
        try:
            return _request_via_powershell(method, url, payload)
        except Exception as fallback_error:
            raise RuntimeError(
                "GitHub request failed through both network stacks: "
                f"{_safe_text(primary_error, 500, tail=True)}; "
                f"{_safe_text(fallback_error, 500, tail=True)}"
            ) from primary_error


def publish_failure_issue(snapshot: dict) -> dict:
    """Create or replace the repository's single latest-failure issue."""
    token = str(os.environ.get("DG_GITHUB_TOKEN") or "").strip()
    if not token:
        return {"status": "disabled", "reason": "DG_GITHUB_TOKEN is not configured."}
    repository = str(
        os.environ.get("DG_GITHUB_REPOSITORY") or DEFAULT_REPOSITORY
    ).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return {"status": "failed", "reason": "DG_GITHUB_REPOSITORY is invalid."}
    api_root = f"https://api.github.com/repos/{repository}"
    issues = _request(
        "GET",
        f"{api_root}/issues?state=all&sort=updated&direction=desc&per_page=100",
    )
    existing = next((
        issue for issue in issues if isinstance(issue, dict)
        and issue.get("title") == ISSUE_TITLE and "pull_request" not in issue
    ), None) if isinstance(issues, list) else None
    run_id = int(snapshot["run_id"])
    if existing:
        marker = _RUN_MARKER_RE.search(str(existing.get("body") or ""))
        if marker and int(marker.group(1)) > run_id:
            return {
                "status": "superseded",
                "run_id": run_id,
                "issue_url": existing.get("html_url"),
            }
        result = _request(
            "PATCH",
            f"{api_root}/issues/{int(existing['number'])}",
            {"title": ISSUE_TITLE, "body": build_issue_body(snapshot), "state": "open"},
        )
        status = "updated"
    else:
        result = _request(
            "POST",
            f"{api_root}/issues",
            {"title": ISSUE_TITLE, "body": build_issue_body(snapshot)},
        )
        status = "created"
    return {
        "status": status,
        "run_id": run_id,
        "issue_url": result.get("html_url") if isinstance(result, dict) else None,
    }


def schedule_failure_issue(snapshot: dict) -> dict:
    """Publish without delaying the worker response or changing run status."""
    if not str(os.environ.get("DG_GITHUB_TOKEN") or "").strip():
        return {"status": "disabled", "reason": "DG_GITHUB_TOKEN is not configured."}
    try:
        frozen = json.loads(json.dumps(snapshot, ensure_ascii=False))
        run_id = int(snapshot["run_id"])
    except Exception as exc:
        logging.getLogger(__name__).error(
            "Could not prepare latest Flow failure for GitHub: %s",
            _safe_text(exc, 1_000, tail=True),
        )
        return {"status": "failed", "reason": "Could not prepare the failure snapshot."}

    def publish() -> None:
        try:
            with _PUBLISH_LOCK:
                result = publish_failure_issue(frozen)
            logging.getLogger(__name__).info(
                "GitHub latest Flow failure issue result: %s",
                {key: value for key, value in result.items() if key != "body"},
            )
        except Exception as exc:
            logging.getLogger(__name__).error(
                "Could not publish latest Flow failure to GitHub: %s",
                _safe_text(exc, 1_000, tail=True),
            )

    try:
        threading.Thread(
            target=publish,
            name=f"flow-failure-github-{run_id}",
            daemon=True,
        ).start()
    except Exception as exc:
        logging.getLogger(__name__).error(
            "Could not queue latest Flow failure for GitHub: %s",
            _safe_text(exc, 1_000, tail=True),
        )
        return {"status": "failed", "run_id": run_id, "reason": "Could not start publisher."}
    return {"status": "queued", "run_id": run_id}
