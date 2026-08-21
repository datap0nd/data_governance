# NERP Remote Runner Integration Handoff

## Purpose

This document gives the remote-PC owner enough context about Metronome to
describe the existing NERP script runner in a second Markdown file. Metronome
will use that returned document to implement a NERP module in Flows.

The first version has one job:

1. Start an approved Python script on the remote PC through an API.
2. Show its execution progress, logs, and current state in Metronome.
3. Record whether it finished successfully or failed.
4. Allow the next pipeline step to start only after confirmed success.

The finance target tables already exist in the shared SQL environment. NERP
does not need to upload files, copy table data, or create SQL tables. Any later
SQL freshness or row-count validation belongs to a separate downstream pipeline
step.

## Important boundary

Connection details alone are not enough. Metronome needs a stable execution
contract: how to identify a script, start exactly one run, obtain a durable run
ID, poll state, retrieve logs, and distinguish a successful process exit from a
launch acknowledgement.

The returned handoff must not contain passwords, bearer tokens, private keys,
cookies, or other secret values. It should name the authentication method and
the secure location or environment-variable names from which secrets will be
provided.

## How Metronome Flows works today

Metronome is a FastAPI application with a browser UI, a SQLite control-plane
database, a scheduler, and execution workers.

ASAP and GSCM are portal-specific Flow adapters. A configured Flow is converted
to a versioned job and inserted into `flow_runs` with status `queued`. A worker
claims the job, posts progress and heartbeats while it works, and sends a
terminal result. Metronome persists the current state as well as the event
history, timing information, error details, and any artifacts.

The existing Metronome run lifecycle is:

```text
queued -> claimed -> running -> succeeded
                            -> failed
                            -> cancelled
```

`succeeded`, `failed`, and `cancelled` are terminal states. A worker that stops
heartbeating is treated as lost and its active run is failed. Metronome prevents
two active runs of the same Flow and retains run history for diagnosis.

Current worker-facing API patterns include:

- Worker registration
- Atomic job claiming
- Progress and event reporting
- Periodic heartbeat reporting
- Terminal success, failure, or cancellation reporting
- Run detail retrieval with events, timings, errors, and artifacts

The NERP user experience should reuse this run history and state model, but the
execution adapter is different. Metronome will call a service on the NERP PC,
store the returned remote run ID, poll the service, and translate remote state
and logs into Metronome run events.

## Intended NERP module

NERP must appear as its own module in Flows, beside ASAP and GSCM. It is not a
website, report, download, or transformation-script setting.

A NERP Flow definition only needs:

- Flow name
- Remote connection profile
- Approved remote script ID
- Optional approved parameters, if the runner supports them
- Execution timeout
- Manual or scheduled trigger
- Flow owner

The module must use a stable script ID supplied by the remote runner. Metronome
must not send arbitrary filesystem paths, Python source code, or shell commands.
The remote service owns the allowlist and maps each stable ID to the actual
script path.

For each run, Metronome should store at least:

- Metronome run ID
- Remote run ID
- Script ID and display name
- Trigger type and requester
- Remote host or connection profile name
- Current state and current stage
- Created, started, last-updated, and finished timestamps
- Exit code when available
- Error summary when available
- Incremental stdout and stderr, with truncation clearly indicated

## Preferred interaction

```text
User or schedule
      |
      v
Metronome queues NERP Flow run
      |
      v
Metronome calls remote runner: start approved script
      |
      +---- receives durable remote_run_id
      |
      v
Metronome polls state and incremental logs
      |
      +---- running: update Flow run and continue polling
      |
      +---- succeeded with exit_code 0: release next pipeline step
      |
      +---- failed/cancelled/timed_out/lost: stop pipeline and retain diagnostics
```

An accepted start request is not success. Only a terminal remote result with
`status: succeeded` and `exit_code: 0` is success.

## Minimum remote runner API contract

The endpoint names below are preferred examples. If the service already uses a
different contract, document the actual endpoints and map every required
behavior to them.

### 1. Health and version

`GET /api/v1/health`

Required response information:

```json
{
  "status": "ok",
  "service_version": "<version>",
  "host_id": "<stable non-secret host identifier>",
  "server_time": "<ISO-8601 timestamp>"
}
```

### 2. Discover approved scripts

`GET /api/v1/scripts`

Each script should expose:

```json
{
  "script_id": "<stable allowlisted ID>",
  "display_name": "<human-readable name>",
  "description": "<what it updates>",
  "enabled": true,
  "timeout_seconds": 3600,
  "allowed_parameters": []
}
```

Do not expose the full remote filesystem path unless it is genuinely required.
Metronome should select from this inventory rather than accept free-text code or
paths.

### 3. Start a run

`POST /api/v1/runs`

Preferred request shape:

```json
{
  "script_id": "<approved script ID>",
  "idempotency_key": "metronome-run-<unique ID>",
  "requested_by": "<Metronome actor or service>",
  "parameters": {}
}
```

Preferred response shape:

```json
{
  "remote_run_id": "<durable unique ID>",
  "script_id": "<approved script ID>",
  "status": "queued",
  "created_at": "<ISO-8601 timestamp>"
}
```

The `idempotency_key` is mandatory. Retrying the same start request after a
network timeout must return the original run instead of launching the script a
second time.

### 4. Read run state

`GET /api/v1/runs/{remote_run_id}`

Required response information:

```json
{
  "remote_run_id": "<durable unique ID>",
  "script_id": "<approved script ID>",
  "status": "running",
  "stage": "<short current activity or null>",
  "created_at": "<ISO-8601 timestamp>",
  "started_at": "<ISO-8601 timestamp or null>",
  "updated_at": "<ISO-8601 timestamp>",
  "finished_at": null,
  "exit_code": null,
  "error": null
}
```

The service must retain completed run records long enough for Metronome to
recover after a restart or temporary network outage. State must not exist only
in process memory.

### 5. Read incremental logs

Preferred endpoint:

`GET /api/v1/runs/{remote_run_id}/logs?after=<cursor>`

Preferred response shape:

```json
{
  "remote_run_id": "<durable unique ID>",
  "entries": [
    {
      "cursor": 1,
      "timestamp": "<ISO-8601 timestamp>",
      "stream": "stdout",
      "message": "<one log entry>"
    }
  ],
  "next_cursor": 1,
  "truncated": false
}
```

If the runner exposes one combined log field instead, document the maximum
size, retention, ordering, encoding, and how Metronome can avoid downloading
the full log on every poll. Logs must redact secrets and sensitive connection
strings.

### 6. Cancellation

Cancellation is recommended but not required for the first release.

Preferred endpoint:

`POST /api/v1/runs/{remote_run_id}/cancel`

The response must say whether cancellation was requested, confirmed, rejected,
or impossible because the run was already terminal. Metronome must not display
`cancelled` until the remote runner confirms the process is no longer running.

## Remote state mapping

The returned handoff must list every real status emitted by the runner. The
preferred mapping is:

| Remote runner state | Metronome state | Terminal |
|---|---|---|
| accepted or queued | queued | No |
| starting | claimed | No |
| running | running | No |
| succeeded with exit code 0 | succeeded | Yes |
| failed or nonzero exit code | failed | Yes |
| timed out | failed | Yes |
| lost or orphaned | failed | Yes |
| cancellation confirmed | cancelled | Yes |

If the remote service reports `succeeded` with a nonzero exit code, Metronome
must treat the run as failed. If the service cannot prove a terminal outcome
after losing the process, it must report an explicit lost or unknown condition,
not success.

## Execution rules the remote service must document

The response file must explain:

- Which Windows account or service identity runs scripts
- Python executable or environment used for each script
- Working directory and environment-variable handling
- Whether only one script may run at a time
- Per-script and global timeout behavior
- What happens when the service or PC restarts during a run
- How stdout and stderr are captured, encoded, limited, and retained
- How exit codes and unhandled Python exceptions are surfaced
- How duplicate start requests are deduplicated
- Completed-run and log retention periods
- Time zone used by timestamps, preferably UTC with ISO 8601 offsets
- Whether parameters are supported and how they are validated
- Whether scripts can overlap and, if so, the concurrency limit

## Connectivity and security requirements

The remote runner should be reachable only over an approved private network,
VPN, or mutually trusted tunnel. Do not expose an unauthenticated script runner
to the public internet.

The response file must document:

- Actual base URL or DNS name and port
- HTTP or HTTPS, including certificate trust requirements
- Network route from the Metronome host to the remote PC
- Required firewall rules and source-IP restrictions
- Authentication method, required header names, and token lifetime
- Secret delivery mechanism, using secret-store or environment-variable names
  rather than secret values
- Authorization scope needed to list scripts, start runs, read runs, read logs,
  and cancel runs
- Token renewal or reauthentication behavior
- Rate limits and recommended polling interval
- Whether a proxy is required or bypassed
- A non-production test script that can safely succeed
- A non-production test script or mode that safely exits with a known failure

If inbound connectivity to the remote PC is not allowed, state that clearly.
The fallback design is a small NERP agent on the remote PC that polls Metronome
for jobs and reports progress using the existing Metronome worker pattern. Do
not open a new inbound firewall path merely to preserve the preferred call
direction.

## File the remote-PC owner must return

Create a file named `nerp_remote_runner_connection.md` using the structure
below. Replace placeholders with actual non-secret details. Remove example
payloads that do not match the real service and include real sanitized request
and response examples from its documentation or a test environment.

```md
# NERP Remote Runner Connection

## Owner and environment
- Technical owner:
- Environment name:
- Host ID:
- Operating system:
- Service name and version:
- Time zone:

## Network path
- Base URL:
- Port:
- HTTP or HTTPS:
- Metronome-to-runner route:
- Firewall or source-IP requirement:
- Proxy requirement:
- Certificate authority or trust requirement:

## Authentication and authorization
- Authentication method:
- Required header names:
- Secret environment-variable or secret-store references:
- Token lifetime:
- Renewal or reauthentication procedure:
- Permissions granted to Metronome:

Do not put secret values in this file.

## Approved scripts
| Script ID | Display name | Purpose | Timeout | Allowed parameters | Expected SQL targets |
|---|---|---|---:|---|---|
| | | | | | |

## API endpoints

### Health
- Method and path:
- Success status code:
- Sanitized response:

### List scripts
- Method and path:
- Success status code:
- Pagination:
- Sanitized response:

### Start run
- Method and path:
- Required headers:
- Request fields:
- Success status code:
- Idempotency behavior:
- Sanitized request and response:

### Get run
- Method and path:
- Success status code:
- Polling interval:
- Sanitized running response:
- Sanitized success response:
- Sanitized failure response:

### Get logs
- Method and path:
- Cursor or pagination behavior:
- Retention and maximum size:
- Encoding:
- Redaction behavior:
- Sanitized response:

### Cancel run
- Supported: yes/no
- Method and path:
- Cancellation semantics:
- Sanitized response:

## Status model
| Actual runner status | Meaning | Terminal | Exit code behavior | Metronome mapping |
|---|---|---|---|---|
| | | | | |

## Execution behavior
- Windows service identity:
- Python executable or environment:
- Working directory:
- Environment-variable handling:
- Concurrency policy:
- Timeout policy:
- PC or service restart behavior:
- Duplicate request behavior:
- Completed-run retention:
- Log retention:

## Error behavior
| Situation | HTTP status | Run status | Response or error shape | Safe to retry |
|---|---:|---|---|---|
| Unknown script ID | | | | |
| Invalid parameters | | | | |
| Duplicate idempotency key | | | | |
| Runner busy | | | | |
| Authentication expired | | | | |
| Script exits nonzero | | | | |
| Script exceeds timeout | | | | |
| PC or service restarts mid-run | | | | |
| Run ID not found | | | | |

## Safe integration tests
- Success test script ID:
- Expected success behavior and maximum duration:
- Failure test script ID or safe failure mode:
- Expected failure exit code and message:
- Whether tests write to SQL or other systems:
- Cleanup required:

## Known limitations
-

## Contact and escalation
- Normal support contact:
- Incident contact:
- Support hours or expected response time:
```

## Acceptance evidence required before enabling NERP Flows

Documentation alone will not prove the integration works. Before NERP is
enabled for production pipelines, the complete path must be tested from the
actual Metronome host:

1. Authenticate and read runner health.
2. Discover the approved script inventory.
3. Start the safe success script and receive a durable remote run ID.
4. Observe queued or running state, incremental logs, terminal success, and
   exit code 0 in Metronome.
5. Start the safe failure script and observe terminal failure, its nonzero exit
   code, and useful stderr or error details in Metronome.
6. Repeat a start request with the same idempotency key and confirm that only
   one remote process exists.
7. Verify timeout or lost-process behavior without reporting false success.
8. Verify authentication expiry and renewal or reauthentication.
9. Confirm that a successful NERP step releases the next pipeline step and a
   failed NERP step blocks it.
10. Confirm the SQL targets remain outside the runner transport and are
    available to the downstream pipeline as intended.

NERP must remain disabled for production scheduling until every mandatory item
above has concrete passing evidence.
