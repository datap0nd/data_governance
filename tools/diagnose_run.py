"""Write a redacted diagnosis bundle for one Flow run.

The bundle is the handoff between the field agent on the work PC (a human or
an AI command-line assistant with access to the live browsers, shares and
portals) and the engineering side that has the code, tests and CI but no live
access. Everything in it comes from projections Metronome already exposes to
its read-only operations investigator, scrubbed again with the recording
diagnostics allowlist. Local paths, URLs, cookies, tokens, entered values,
e-mail addresses and raw browser messages are not written. Report files, run
folders, browser profiles, replay recipes and stored browser state are never
read.

    python tools/diagnose_run.py --run-id 184 --output diagnosis

Run it from the deployed checkout so the default ``governance.db`` is found,
or set ``DG_DB_PATH``. The result is ``<output>/run-<id>/`` containing
``bundle.json``, ``steps.json`` (recorded Flows only), ``README.md`` and a
``findings.md`` template for the investigator to complete.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# The embedded Windows runtime can ignore PYTHONPATH, so the package root is
# bootstrapped here exactly as the other repository tools do.
_CODE_DIR = Path(__file__).resolve().parent.parent
if str(_CODE_DIR) not in sys.path:
    sys.path.insert(0, str(_CODE_DIR))

BUNDLE_VERSION = 1
MAX_EVENTS = 30
MAX_ARTIFACTS = 50
MAX_STRING = 1200
# Keys whose values are never copied, whatever projection they come from.
FORBIDDEN_KEYS = frozenset({
    "raw_message", "raw_html", "cookie", "cookies", "set-cookie", "storage_state",
    "authorization", "password", "passwd", "token", "secret", "api_key", "apikey",
    "file_path", "published_file_path", "target_folder", "profile_dir", "local_file_path",
    "connection_info", "connection", "query", "recipient", "recipients",
})
# Server-issued relative links are kept verbatim; nothing else may look like a path.
SAFE_LINK = re.compile(r"^/(?:flow-runs|flows|alerts|pipeline-runs)/\d+$")
SCREENSHOT = re.compile(r"failure-\d{2}\.png")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(value, definition=None, *, key=None):
    """Recursively drop forbidden keys and redact every string."""
    from app import flow_recording_diagnostics as diagnostics

    if isinstance(value, dict):
        result = {}
        for name, item in value.items():
            if not isinstance(name, str) or name.casefold() in FORBIDDEN_KEYS:
                continue
            result[name] = scrub(item, definition, key=name)
        return result
    if isinstance(value, (list, tuple)):
        return [scrub(item, definition, key=key) for item in value]
    if isinstance(value, str):
        if key == "deep_link" and SAFE_LINK.match(value):
            return value
        # Playwright call logs quote entered values and page fragments; keep
        # only the one-line summary, as the recording diagnostics do.
        if "Call log:" in value:
            value = value.split("Call log:", 1)[0].rstrip() + " [call log excluded]"
        return diagnostics.safe_text(value, definition, limit=MAX_STRING)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return diagnostics.safe_text(str(value), definition, limit=MAX_STRING)


def _envelope(handler, args):
    """Run one read projection; a projection that cannot be computed is
    recorded as unavailable instead of failing the whole bundle (for example
    the recovery preflight of a Flow whose recording is still a draft)."""
    from app import flow_recording_diagnostics as diagnostics

    try:
        return handler(args).to_dict(), None
    except Exception as exc:  # noqa: BLE001 - every projection failure is evidence
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        return None, {"unavailable": diagnostics.safe_text(detail, limit=300) or type(exc).__name__}


def _recording_context(run_id: int):
    """Active recording definition, failing step and step list for a recorded run."""
    from app.database import get_db
    from app import flow_recording_diagnostics as diagnostics

    with get_db() as db:
        run = db.execute("SELECT job_json FROM flow_runs WHERE id=?", (run_id,)).fetchone()
        if not run:
            raise LookupError("Flow run not found.")
        try:
            job = json.loads(run["job_json"] or "{}")
        except ValueError:
            job = {}
        recording = job.get("recording") or {}
        definition = recording.get("definition") if isinstance(recording, dict) else None
        if not isinstance(definition, dict):
            return None, None, []
        events = db.execute(
            """SELECT stage, status, details_json FROM flow_run_events
               WHERE run_id=? ORDER BY id""",
            (run_id,),
        ).fetchall()
    outcomes: dict[str, dict] = {}
    failing_step_id = None
    screenshots: list[str] = []
    for event in events:
        try:
            details = json.loads(event["details_json"] or "{}")
        except ValueError:
            details = {}
        if not isinstance(details, dict):
            continue
        if isinstance(details.get("step_outcomes"), dict):
            for step_id, outcome in details["step_outcomes"].items():
                if isinstance(outcome, dict):
                    outcomes[str(step_id)] = outcome
        if details.get("step_id") and (details.get("diagnostic") or {}).get("phase") == "action_failed":
            failing_step_id = str(details["step_id"])
        screenshots.extend(SCREENSHOT.findall(json.dumps(details)))
    steps = []
    failing = None
    for step in diagnostics._steps(definition):
        contract = diagnostics.execution_contract(step)
        entry = {
            "step_id": step.get("id"),
            "label": diagnostics.action_label(step),
            "action": step.get("action"),
            "page": step.get("page"),
            "locator": diagnostics._locator(step.get("locator", []), definition),
            "outcome": scrub(outcomes.get(str(step.get("id")), {}), definition),
        }
        if step.get("output"):
            entry["output_contract"] = scrub({
                key: step["output"].get(key)
                for key in ("format", "completion", "min_rows", "headers", "period_checks")
                if key in step["output"]
            }, definition)
        if step.get("range"):
            entry["range_contract"] = scrub({
                key: step["range"].get(key)
                for key in ("unit", "start", "end", "selected_state", "navigation")
                if key in step["range"]
            }, definition)
        steps.append(entry)
        if failing_step_id and str(step.get("id")) == failing_step_id:
            failing = {**entry, "execution_contract": scrub(
                {key: contract[key] for key in ("action", "page", "timeout_ms", "delay_before_seconds",
                                                 "arguments", "post_click_verification",
                                                 "explicit_text_check", "explicit_assertion")},
                definition,
            )}
    context = {
        "revision": recording.get("revision"),
        "definition_version": definition.get("version"),
        "adapter": definition.get("adapter") or (job.get("site") or {}).get("adapter"),
        "step_count": len(steps),
        "failing_step_id": failing_step_id,
        "screenshot_files": sorted(set(screenshots)),
    }
    return definition, {"context": context, "failing_step": failing}, steps


def build_bundle(run_id: int) -> tuple[dict, list[dict]]:
    """Return the redacted bundle document and step list for *run_id*."""
    from app.ai import operations_tools as tools

    definition, recording, steps = _recording_context(run_id)
    run, run_error = _envelope(tools._get_flow_run, tools.FlowRunArgs(run_id=run_id))
    events, events_error = _envelope(
        tools._get_flow_run_events, tools.FlowRunEventsArgs(run_id=run_id, limit=MAX_EVENTS)
    )
    artifacts, artifacts_error = _envelope(
        tools._get_flow_run_artifacts, tools.FlowRunArtifactsArgs(run_id=run_id, limit=MAX_ARTIFACTS)
    )
    comparison, comparison_error = _envelope(
        tools._compare_flow_runs, tools.CompareFlowRunsArgs(run_id=run_id)
    )
    bundle = {
        "bundle_version": BUNDLE_VERSION,
        "generated_at": _utc_now(),
        "run_id": run_id,
        "redaction": (
            "Local paths, URLs, cookies, tokens, entered values, e-mail addresses and raw "
            "browser messages are excluded. Screenshots are listed by file name only and "
            "stay on the worker profile. Report data and run folders were not read."
        ),
        "run": run or run_error,
        "events": events or events_error,
        "artifacts": artifacts or artifacts_error,
        "comparison": comparison or comparison_error,
        "recording": recording,
    }
    return scrub(bundle, definition), scrub(steps, definition)


README = """# Diagnosis bundle for Flow run #{run_id}

Generated {generated_at} by `tools/diagnose_run.py` (bundle version {version}).

- `bundle.json` - redacted run summary, recovery preflight, the last {events}
  events with exception signals and traceback tails, artifact metadata, and the
  comparison with the latest earlier successful run of the same Flow.
- `steps.json` - the recorded step list with each step's outcome (recorded
  Flows only). The failing step, when known, is repeated under
  `recording.failing_step` in `bundle.json`.
- `findings.md` - the investigator's conclusion. Fill it in; keep every claim
  tied to a field in these files.

{redaction}

Screenshots named in `bundle.json` live under the worker profile's
`diagnostics` folder on the work PC. Look at them there; do not copy them into
the bundle or a pull request.
"""

FINDINGS = """# Findings for Flow run #{run_id}

Complete this after reading `bundle.json` and `steps.json`. One paragraph per
section, no more than 100 words each. Cite fields like `events[3].error` or
`recording.failing_step.step_id`. If the evidence does not support a
conclusion, say "insufficient evidence" and name the single missing fact.

## What happened

First abnormal stage and the supported explanation (operational failure,
expected timing, configuration issue, external-service issue, insufficient
evidence).

## Impact

Which files, stages or downstream targets did not complete.

## Suggested action

One action or one discriminating check. Never a production SQL retry and never
an edit to `app/` on the work PC.

## Live observations (work PC only)

Date, deployed revision, browser, what the screen showed when the step failed.
No portal URLs, no report rows, no share paths.
"""


def write_bundle(run_id: int, output: Path) -> Path:
    bundle, steps = build_bundle(run_id)
    folder = output / f"run-{run_id}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "bundle.json").write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if steps:
        (folder / "steps.json").write_text(
            json.dumps(steps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (folder / "README.md").write_text(
        README.format(
            run_id=run_id, generated_at=bundle["generated_at"], version=BUNDLE_VERSION,
            events=MAX_EVENTS, redaction=bundle["redaction"],
        ),
        encoding="utf-8",
    )
    findings = folder / "findings.md"
    if not findings.exists():
        findings.write_text(FINDINGS.format(run_id=run_id), encoding="utf-8")
    return folder


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--run-id", type=int, required=True, help="flow_runs.id to describe")
    parser.add_argument(
        "--output", type=Path, default=Path("diagnosis"),
        help="folder that receives run-<id>/ (default: ./diagnosis)",
    )
    arguments = parser.parse_args(argv)
    if arguments.run_id < 1:
        parser.error("--run-id must be a positive integer")
    try:
        folder = write_bundle(arguments.run_id, arguments.output)
    except LookupError as exc:
        print(f"Flow run {arguments.run_id}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": arguments.run_id, "bundle": str(folder)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
