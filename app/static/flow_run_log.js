const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
const duration = ms => {
    if (!Number.isFinite(Number(ms))) return "Not available";
    const seconds = Math.max(0, Math.round(Number(ms) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
};
const stamp = value => value ? new Date(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(String(value)) ? String(value).replace(' ', 'T') + 'Z' : value).toLocaleString('en-GB', {timeZone:'Asia/Dubai'}) : "Not recorded";
const label = value => String(value || "unknown").replaceAll("_", " ");
const runId = Number(location.pathname.match(/\/flow-runs\/(\d+)/)?.[1]);
let loadRun = null;
const consoleState = {lines: [], after: 0, omitted: 0, follow: true, loading: false};

function renderConsole() {
    const target = document.getElementById("python-console-lines");
    if (!target) return;
    let previous = 0;
    target.innerHTML = consoleState.lines.map(line => {
        const gap = line.line_no > previous + 1 ? `<div class="python-console-omitted">… ${line.line_no - previous - 1} lines omitted …</div>` : "";
        previous = line.line_no;
        return gap + `<div class="python-console-line ${esc(line.stream)} ${String(line.text).startsWith("::stage::") || String(line.text).startsWith("::progress::") ? "marker" : ""}"><span>${esc(line.stream)}</span><code>${esc(line.text)}</code></div>`;
    }).join("") || '<div class="flow-inline-empty">Console output will appear here while the script runs.</div>';
    if (consoleState.follow) target.scrollTop = target.scrollHeight;
    const count = document.getElementById("python-console-count");
    if (count) count.textContent = `${consoleState.lines.length} retained line(s)${consoleState.omitted ? ` · ${consoleState.omitted} omitted` : ""}`;
}

async function loadOutput() {
    if (consoleState.loading || !document.getElementById("python-console-lines")) return;
    consoleState.loading = true;
    try {
        for (let page = 0; page < 6; page++) {
            const response = await fetch(`/api/flows/runs/${runId}/output?after_line=${consoleState.after}&limit=1000`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            for (const line of data.lines || []) {
                consoleState.lines.push(line);
                consoleState.after = Math.max(consoleState.after, line.line_no);
            }
            consoleState.omitted = data.omitted || 0;
            if ((data.lines || []).length < 1000) break;
        }
        // The server keeps the first 500 and newest 4500 lines. Bound this
        // browser copy too, preserving the same head and tail.
        if (consoleState.lines.length > 5000) consoleState.lines = [
            ...consoleState.lines.slice(0, 500), ...consoleState.lines.slice(-4500),
        ];
        renderConsole();
    } catch (error) {
        const status = document.getElementById("python-console-count");
        if (status) status.textContent = `Console unavailable: ${error.message}`;
    } finally { consoleState.loading = false; }
}

function render(run) {
    const python = run.job?.flow?.source_type === "python";
    const live = run.live || {};
    const processRows = (live.processes || []).map(item => `<tr><td>${esc(item.label || "process")}</td><td>${esc(item.pid)}</td><td>${esc(item.parent_pid || "—")}</td><td>${esc(item.state || "running")}${item.exit_code !== null && item.exit_code !== undefined ? ` · exit ${esc(item.exit_code)}` : ""}</td><td>${esc(stamp(item.started_at))}</td><td>${esc(item.duration_seconds ?? 0)}s</td><td>${esc(item.cpu_percent ?? 0)}%</td><td>${esc(Math.round((item.memory_bytes || 0) / 1048576))} MB</td></tr>`).join("");
    const scriptProgress = /^\d+%$/.test(live.progress || "") ? Number.parseInt(live.progress) : /^\d+\/\d+$/.test(live.progress || "") ? (() => {const [a,b] = live.progress.split("/").map(Number); return b ? Math.min(100, Math.round(a / b * 100)) : 0;})() : null;
    const silentMinutes = live.last_output_at ? Math.floor((Date.now() - Date.parse(live.last_output_at)) / 60000) : 0;
    const excelFailure = run.status === 'failed' ? [...(run.events || [])].reverse().find(item => item.details?.excel)?.details.excel : null;
    const sql = run.job?.sql_handoff || {};
    const transformation = run.job?.transformation || {};
    const terminal = ["succeeded", "failed", "cancelled"].includes(run.status);
    const artifacts = Array.isArray(run.artifacts) ? run.artifacts : [];
    const savedArtifacts = artifacts.filter(item => item?.file_path && item.filename);
    // A confirmed commit rules out an SQL-only retry: appending again would duplicate rows. Recovery then means refreshing views.
    const reconciliationBlocksRetry = run.sql_reconciliation_required && sql.mode === "append";
    const canRetrySql = terminal && !excelFailure && sql.enabled && savedArtifacts.length > 0 && !reconciliationBlocksRetry && !run.sql_outcome?.committed && (!run.downloads || run.downloads.completed === run.downloads.total);
    const sqlRetrySource = run.job?.sql_retry?.source_run_id;
    const refresh = run.view_refresh;
    const canRetryViews = refresh?.retry?.status === "eligible";
    const email = run.email;
    const emailStatusText = !email ? "" : email.status === "pending" ? "Handed to Outlook, waiting for its receipt"
        : email.status === "submitted" ? "Submitted by Outlook"
        : email.status === "failed" ? "Not sent"
        : email.status === "unknown" ? "No Outlook receipt within 24 hours; delivery is unknown"
        : email.status === "skipped" ? "Skipped"
        : "Not attempted yet";
    const canResendEmail = Boolean(email) && run.status === "succeeded" && !run.progress?.no_op;
    const viewRows = (refresh?.views || []).map(item => `<tr><td>${item.sequence_no}</td><td><code>${esc(item.database)}.${esc(item.schema)}.${esc(item.name)}</code></td><td><span class="flow-log-view-status ${esc(item.status)}">${esc(label(item.status))}</span></td><td>${item.duration_ms === null || item.duration_ms === undefined ? "" : esc(duration(item.duration_ms))}</td><td>${esc(item.finished_at ? stamp(item.finished_at) : "")}</td><td>${item.error ? `<span class="flow-log-view-error">${esc(item.error)}</span>` : ""}</td></tr>`).join("");
    const sqlOutcome = run.sql_outcome;
    const sqlOutcomeText = !sql.enabled && !sqlRetrySource && !refresh?.source_run_id ? "SQL insertion disabled"
        : sqlOutcome?.committed ? `SQL insertion committed${sqlOutcome.rows_written !== undefined && sqlOutcome.rows_written !== null ? ` · ${sqlOutcome.rows_written} row(s)` : ""}${sqlOutcome.owner_username ? ` · table owner: ${sqlOutcome.owner_username}` : ""}${sqlOutcome.inherited_from_run_id ? ` in run #${sqlOutcome.inherited_from_run_id}` : ""}`
        : sqlOutcome?.committed === false ? "SQL insertion rolled back; nothing was committed"
        : sqlOutcome ? (sql.mode === "replace" ? "SQL completion was interrupted; rerun the replace operation" : "SQL commit outcome uncertain; reconcile the target before rerunning")
        : "SQL insertion has not started";
    const timingRows = (run.timings || []).map(item => `<tr><td>${esc(label(item.phase))}</td><td>${esc(duration(item.duration_ms))}</td><td>${esc(item.status)}</td><td>${esc(item.item_count ?? "")}</td></tr>`).join("");
    const fileRows = (run.files?.length ? run.files : artifacts).map(item => `<tr><td>${esc(item.filename)}</td><td>${esc(item.period_key || "Full range")}</td><td>${esc(item.row_count ?? "Unknown")}</td><td>${esc(item.file_size ?? "Unknown")}</td><td class="flow-log-path">${esc(item.file_path)}</td></tr>`).join("");
    const events = (run.events || []).map(item => `<article class="flow-log-event ${item.error ? "is-error" : ""}"><header><time>${esc(stamp(item.created_at))}</time><strong>${esc(label(item.stage || item.status))}</strong><span>${esc(item.status)}</span></header><p>${esc(item.message || item.error || "No message")}</p>${item.error ? `<div class="flow-log-error"><strong>Error</strong><pre>${esc(item.error)}</pre></div>` : ""}${item.traceback ? `<details><summary>Full traceback</summary><pre>${esc(item.traceback)}</pre></details>` : ""}<details><summary>Event details</summary><pre>${esc(JSON.stringify(item.details || {}, null, 2))}</pre></details></article>`).join("");
    document.title = `Run #${run.id} logs - Metronome`;
    document.getElementById("flow-run-log").innerHTML = `
        <header class="flow-log-header"><div><a href="/#flows">Back to Flows</a><h1>Run #${run.id}: ${esc(run.flow_name)}</h1><p>Complete diagnostic record captured by Metronome.${sqlRetrySource ? ` SQL-only retry of run #${esc(sqlRetrySource)}; the source was not opened.` : ""}</p></div><div class="flow-log-header-actions"><span class="flow-log-status ${esc(run.status)}">${esc(run.status)}</span><a class="btn-outline" href="/?investigate=flow_run&subject_id=${run.id}#flows">Investigate safely</a>${canRetrySql ? '<button class="btn-primary" id="flow-retry-sql">Retry SQL only</button>' : ""}${canRetryViews ? '<button class="btn-primary" id="flow-retry-views">Retry view refresh</button>' : ""}</div></header>
        ${run.error ? `<section class="flow-log-failure"><h2>${excelFailure ? 'Excel processing failed' : 'Final error'}</h2><p>${esc(run.error)}</p>${excelFailure ? `<p><strong>Workbook:</strong> ${esc(excelFailure.workbook)}</p><p><strong>Worksheets found:</strong> ${(excelFailure.available_sheets || []).map(name => `<code>${esc(name)}</code>`).join(', ') || 'None'}</p><p>SQL was not started. The original workbook is preserved.</p><a class="btn-primary" href="/?edit_flow=${encodeURIComponent(run.flow_id)}&worksheet_run=${encodeURIComponent(run.id)}#flows">Choose worksheets</a>` : ''}</section>` : ""}
        ${python ? `<section class="flow-log-section python-script-activity"><h2>Script activity</h2><p><strong>${esc(live.script || run.progress?.script || "Preparing script")}</strong>${run.progress?.step && run.progress?.steps ? ` · step ${esc(run.progress.step)} of ${esc(run.progress.steps)}` : ""}${live.stage ? ` · ${esc(live.stage)}` : ""}</p>${scriptProgress !== null ? `<div class="python-script-progress" role="progressbar" aria-valuenow="${scriptProgress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${scriptProgress}%"></span></div><p>${esc(live.progress)}</p>` : ""}${live.waiting ? `<p>Waiting for ${esc(live.waiting)} process(es) started by the script.</p>` : ""}${!terminal && silentMinutes >= 10 ? `<p class="python-script-silent">No console output for ${silentMinutes} min.</p>` : ""}<div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Process</th><th>PID</th><th>Parent</th><th>State</th><th>Started (Dubai)</th><th>Duration</th><th>CPU</th><th>Memory</th></tr></thead><tbody>${processRows || '<tr><td colspan="8">Process details will appear when the script starts.</td></tr>'}</tbody></table></div><div class="python-console-head"><h3>Live console</h3><label><input type="checkbox" id="python-console-follow" ${consoleState.follow ? "checked" : ""}> Follow</label><button type="button" class="btn-secondary" id="python-console-copy">Copy</button><button type="button" class="btn-secondary" id="python-console-download">Download .log</button></div><p id="python-console-count" class="flow-log-muted"></p><div id="python-console-lines" class="python-console-lines" aria-live="off"></div></section>` : ""}
        ${reconciliationBlocksRetry ? '<section class="flow-log-failure"><h2>SQL reconciliation required</h2><p>The prior append may have completed. Check and reconcile the target, then acknowledge reconciliation from the Flow’s More menu before running again.</p></section>' : ''}
        ${refresh ? `<section class="flow-log-section flow-log-views"><h2>SQL insertion → Refresh materialized views</h2><p><strong>${esc(sqlOutcomeText)}.</strong> ${refresh.deferred_to_pipeline ? "This run belongs to a full-pipeline run, which refreshes the configured views once after every upstream Flow finished." : refresh.blocked ? `Refresh blocked: ${esc(refresh.blocked)}` : `${refresh.completed} of ${refresh.total} materialized view(s) refreshed, each in its own transaction, upstream first.${refresh.source_run_id ? ` Refresh-only retry of run #${esc(refresh.source_run_id)}; nothing was downloaded, transformed or inserted again.` : ""}${refresh.discovered_at ? ` List frozen when the run was queued (${esc(stamp(refresh.discovered_at))}${refresh.metadata_at ? `, metadata verified ${esc(stamp(refresh.metadata_at))}` : ""}).` : ""}`}${refresh.retry && refresh.retry.status !== "eligible" ? ` ${esc(refresh.retry.message)}` : ""}</p>${viewRows ? `<div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>#</th><th>Materialized view</th><th>Status</th><th>Duration</th><th>Finished</th><th>Error</th></tr></thead><tbody>${viewRows}</tbody></table></div>` : ""}</section>` : ""}
        ${email ? `<section class="flow-log-section flow-log-email"><h2>Email the final file</h2><p><strong>Status:</strong> <span class="flow-log-email-status ${esc(email.status || "none")}">${esc(emailStatusText)}</span>${email.detail ? ` <span class="${email.status === "failed" || email.status === "unknown" ? "flow-log-view-error" : ""}">${esc(email.detail)}</span>` : ""}</p><dl><div><dt>Recipients</dt><dd>${esc((email.recipients || []).join("; ") || "None")}</dd></div><div><dt>Subject</dt><dd>${esc(email.subject || `Metronome flow file: ${run.flow_name} (run #${run.id})`)}</dd></div><div><dt>File(s)</dt><dd>${esc((email.files || []).join(", ") || "None recorded")}</dd></div></dl>${canResendEmail ? '<p><button class="btn-secondary" id="flow-resend-email">Send again</button> <span id="flow-resend-email-status" role="status"></span></p>' : ""}<p class="flow-log-muted">The email is handed to Outlook on the BI desktop after the run succeeds; Outlook's receipt updates this status. Files above 20 MB are described instead of attached.</p></section>` : ""}
        ${run.downloads ? `<section class="flow-log-section"><h2>Download tasks</h2><p>${run.downloads.completed} of ${run.downloads.total} completed · ${run.downloads.active} active slots · ${esc(label(run.downloads.state))}</p><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Export</th><th>Status</th><th>Worker</th><th>Attempt</th><th>Error</th></tr></thead><tbody>${run.downloads.tasks.map(task => `<tr><td>${task.ordinal}</td><td>${esc(task.state)}</td><td>${esc(task.worker_id || 'Waiting')}</td><td>${task.attempt}</td><td>${esc(task.error || '')}</td></tr>`).join('')}</tbody></table></div></section>` : ''}
        <section class="flow-log-summary"><dl><div><dt>Requested</dt><dd>${esc(stamp(run.created_at))}</dd></div><div><dt>Started</dt><dd>${esc(stamp(run.started_at))}</dd></div><div><dt>Finished</dt><dd>${esc(stamp(run.finished_at))}</dd></div><div><dt>Worker</dt><dd>${esc(run.worker_id || "Not claimed")}</dd></div><div><dt>Browser</dt><dd>${esc(label(run.job?.execution?.browser_mode || "unknown"))}</dd></div><div><dt>Transformation</dt><dd>${transformation.enabled ? `${esc(transformation.script_path)} → script_results` : "Disabled"}</dd></div><div><dt>SQL handoff</dt><dd>${sql.enabled ? `${esc(sql.mode)} to ${esc(sql.database)}.${esc(sql.schema)}.${esc(sql.table)}` : "Disabled"}</dd></div><div><dt>Email</dt><dd>${email ? `${(email.recipients || []).length} recipient(s)` : "Disabled"}</dd></div></dl></section>
        ${sql.owner_username || sqlOutcome?.owner_username ? `<section class="flow-log-section"><h2>SQL table ownership</h2><p>${sqlOutcome?.committed && sqlOutcome.owner_username ? `Committed table owner: <code>${esc(sqlOutcome.owner_username)}</code>. Refresh table Properties in pgAdmin to see this owner.` : `Requested owner: <code>${esc(sql.owner_username)}</code>. Ownership is confirmed only after the SQL load commits.`}</p></section>` : ''}
        <section class="flow-log-section"><h2>Event timeline</h2>${events || '<p class="flow-inline-empty">This run predates expanded event capture. Its summary, timings, files, and final error are shown below.</p>'}</section>
        <section class="flow-log-section"><h2>Phase timings</h2><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Phase</th><th>Duration</th><th>Status</th><th>Items</th></tr></thead><tbody>${timingRows || '<tr><td colspan="4">No timings recorded.</td></tr>'}</tbody></table></div></section>
        <section class="flow-log-section"><h2>Run files</h2><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>File</th><th>Period</th><th>Rows</th><th>Bytes</th><th>Path</th></tr></thead><tbody>${fileRows || '<tr><td colspan="5">No files recorded.</td></tr>'}</tbody></table></div></section>
        <section class="flow-log-section"><h2>Run configuration</h2><details><summary>View saved job configuration</summary><pre>${esc(JSON.stringify(run.job || {}, null, 2))}</pre></details></section>`;

    if (python) {
        renderConsole();
        document.getElementById("python-console-follow").onchange = event => { consoleState.follow = event.target.checked; if (consoleState.follow) renderConsole(); };
        document.getElementById("python-console-copy").onclick = () => navigator.clipboard.writeText(consoleState.lines.map(line => `[${line.stream}] ${line.text}`).join("\n"));
        document.getElementById("python-console-download").onclick = () => {
            const blob = new Blob([consoleState.lines.map(line => `[${line.at}] [${line.stream}] ${line.text}`).join("\n")], {type: "text/plain"});
            const link = document.createElement("a"); link.href = URL.createObjectURL(blob); link.download = `flow-run-${run.id}.log`; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        };
    }

    const retryViewsButton = document.getElementById("flow-retry-views");
    if (retryViewsButton) retryViewsButton.onclick = async () => {
        const remaining = (refresh.views || []).filter(item => item.status !== "succeeded").map(item => `${item.database}.${item.schema}.${item.name}`);
        if (!window.confirm(`This refreshes the ${remaining.length} materialized view(s) that did not finish:\n${remaining.join("\n")}\n\nViews already refreshed are kept. Nothing is downloaded, transformed or inserted into SQL again. Continue?`)) return;
        retryViewsButton.disabled = true;
        retryViewsButton.textContent = "Queuing refresh...";
        try {
            const response = await fetch(`/api/flows/runs/${run.id}/retry-views`, { method: "POST" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
            location.href = `/flow-runs/${payload.id}`;
        } catch (error) {
            window.alert(`View refresh was not queued: ${error.message}`);
            retryViewsButton.disabled = false;
            retryViewsButton.textContent = "Retry view refresh";
        }
    };
    const resendButton = document.getElementById("flow-resend-email");
    if (resendButton) resendButton.onclick = async () => {
        const recipients = (email.recipients || []).join("; ");
        const files = (email.files || []).join(", ") || "the recorded final file";
        const duplicate = ["submitted", "unknown", "pending"].includes(email.status)
            ? "\n\nOutlook may already have sent this email; sending again can deliver a duplicate."
            : "";
        if (!window.confirm(`Send ${files} again through Outlook to ${recipients}?${duplicate}\n\nNothing is downloaded, transformed or inserted into SQL again.`)) return;
        resendButton.disabled = true;
        resendButton.textContent = "Handing to Outlook...";
        const status = document.getElementById("flow-resend-email-status");
        try {
            const response = await fetch(`/api/flows/runs/${run.id}/resend-email`, { method: "POST" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
            if (status) status.textContent = "Handed to Outlook; waiting for its receipt.";
            if (loadRun) loadRun();
        } catch (error) {
            if (status) status.textContent = `Email not sent: ${error.message}`;
            resendButton.disabled = false;
            resendButton.textContent = "Send again";
        }
    };
    const retryButton = document.getElementById("flow-retry-sql");
    if (retryButton) retryButton.onclick = async () => {
        const target = `${sql.database}.${sql.schema}.${sql.table}`;
        const consequence = sql.mode === "replace"
            ? `This will replace all rows in ${target} from the saved CSV using run #${run.id}. A missing target will be created. An existing target will keep its grants, indexes, constraints, triggers, and older columns while all rows are replaced in one transaction.`
            : `This will append to ${target} using the saved CSV from run #${run.id}. If a previous append committed, this can duplicate rows.`;
        if (!window.confirm(`${consequence}\n\nThe source will not open and no file will be downloaded. Continue?`)) return;
        retryButton.disabled = true;
        retryButton.textContent = "Queuing SQL...";
        try {
            const response = await fetch(`/api/flows/runs/${run.id}/retry-sql`, { method: "POST" });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || `HTTP ${response.status}`);
            location.href = `/flow-runs/${payload.id}`;
        } catch (error) {
            window.alert(`SQL retry was not queued: ${error.message}`);
            retryButton.disabled = false;
            retryButton.textContent = "Retry SQL only";
        }
    };
}

if (!Number.isInteger(runId)) {
    document.getElementById("flow-run-log").innerHTML = '<div class="flow-log-failure"><h1>Invalid run</h1><p>The run ID in this URL is not valid.</p></div>';
} else {
    let refreshTimer;
    let emailPolls = 0;
    loadRun = () => fetch(`/api/flows/runs/${runId}`).then(async response => {
        if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
        return response.json();
    }).then(run => {
        render(run);
        if (run.job?.flow?.source_type === "python") loadOutput();
        clearTimeout(refreshTimer);
        if (!["succeeded", "failed", "cancelled"].includes(run.status)) {
            refreshTimer = setTimeout(loadRun, 2000);
        } else if (run.email?.status === "pending" && emailPolls++ < 24) {
            // Outlook's receipt is reconciled by the server within about a minute.
            refreshTimer = setTimeout(loadRun, 5000);
        }
    }).catch(error => {
        document.getElementById("flow-run-log").innerHTML = `<div class="flow-log-failure"><h1>Logs unavailable</h1><p>${esc(error.message)}</p></div>`;
    });
    loadRun();
}
