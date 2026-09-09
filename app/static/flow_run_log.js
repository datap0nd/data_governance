const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
const duration = ms => {
    if (!Number.isFinite(Number(ms))) return "Not available";
    const seconds = Math.max(0, Math.round(Number(ms) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
};
const stamp = value => value ? new Date(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(String(value)) ? String(value).replace(' ', 'T') + 'Z' : value).toLocaleString('en-GB', {timeZone:'Asia/Dubai'}) : "Not recorded";
const label = value => String(value || "unknown").replaceAll("_", " ");
const runId = Number(location.pathname.match(/\/flow-runs\/(\d+)/)?.[1]);

function render(run) {
    const sql = run.job?.sql_handoff || {};
    const transformation = run.job?.transformation || {};
    const terminal = ["succeeded", "failed", "cancelled"].includes(run.status);
    const savedArtifacts = (run.artifacts || []).filter(item => item.file_path && item.filename);
    // A confirmed commit rules out an SQL-only retry: appending again would duplicate rows. Recovery then means refreshing views.
    const reconciliationBlocksRetry = run.sql_reconciliation_required && sql.mode === "append";
    const canRetrySql = terminal && sql.enabled && savedArtifacts.length > 0 && !reconciliationBlocksRetry && !run.sql_outcome?.committed && (!run.downloads || run.downloads.completed === run.downloads.total);
    const sqlRetrySource = run.job?.sql_retry?.source_run_id;
    const refresh = run.view_refresh;
    const canRetryViews = refresh?.retry?.status === "eligible";
    const viewRows = (refresh?.views || []).map(item => `<tr><td>${item.sequence_no}</td><td><code>${esc(item.database)}.${esc(item.schema)}.${esc(item.name)}</code></td><td><span class="flow-log-view-status ${esc(item.status)}">${esc(label(item.status))}</span></td><td>${item.duration_ms === null || item.duration_ms === undefined ? "" : esc(duration(item.duration_ms))}</td><td>${esc(item.finished_at ? stamp(item.finished_at) : "")}</td><td>${item.error ? `<span class="flow-log-view-error">${esc(item.error)}</span>` : ""}</td></tr>`).join("");
    const sqlOutcome = run.sql_outcome;
    const sqlOutcomeText = !sql.enabled && !sqlRetrySource && !refresh?.source_run_id ? "SQL insertion disabled"
        : sqlOutcome?.committed ? `SQL insertion committed${sqlOutcome.rows_written !== undefined && sqlOutcome.rows_written !== null ? ` · ${sqlOutcome.rows_written} row(s)` : ""}${sqlOutcome.owner_username ? ` · table owner: ${sqlOutcome.owner_username}` : ""}${sqlOutcome.inherited_from_run_id ? ` in run #${sqlOutcome.inherited_from_run_id}` : ""}`
        : sqlOutcome?.committed === false ? "SQL insertion rolled back; nothing was committed"
        : sqlOutcome ? (sql.mode === "replace" ? "SQL completion was interrupted; rerun the replace operation" : "SQL commit outcome uncertain; reconcile the target before rerunning")
        : "SQL insertion has not started";
    const timingRows = (run.timings || []).map(item => `<tr><td>${esc(label(item.phase))}</td><td>${esc(duration(item.duration_ms))}</td><td>${esc(item.status)}</td><td>${esc(item.item_count ?? "")}</td></tr>`).join("");
    const fileRows = (run.files?.length ? run.files : run.artifacts || []).map(item => `<tr><td>${esc(item.filename)}</td><td>${esc(item.period_key || "Full range")}</td><td>${esc(item.row_count ?? "Unknown")}</td><td>${esc(item.file_size ?? "Unknown")}</td><td class="flow-log-path">${esc(item.file_path)}</td></tr>`).join("");
    const events = (run.events || []).map(item => `<article class="flow-log-event ${item.error ? "is-error" : ""}"><header><time>${esc(stamp(item.created_at))}</time><strong>${esc(label(item.stage || item.status))}</strong><span>${esc(item.status)}</span></header><p>${esc(item.message || item.error || "No message")}</p>${item.error ? `<div class="flow-log-error"><strong>Error</strong><pre>${esc(item.error)}</pre></div>` : ""}${item.traceback ? `<details><summary>Full traceback</summary><pre>${esc(item.traceback)}</pre></details>` : ""}<details><summary>Event details</summary><pre>${esc(JSON.stringify(item.details || {}, null, 2))}</pre></details></article>`).join("");
    document.title = `Run #${run.id} logs - Metronome`;
    document.getElementById("flow-run-log").innerHTML = `
        <header class="flow-log-header"><div><a href="/#flows">Back to Flows</a><h1>Run #${run.id}: ${esc(run.flow_name)}</h1><p>Complete diagnostic record captured by Metronome.${sqlRetrySource ? ` SQL-only retry of run #${esc(sqlRetrySource)}; the source was not opened.` : ""}</p></div><div class="flow-log-header-actions"><span class="flow-log-status ${esc(run.status)}">${esc(run.status)}</span><a class="btn-outline" href="/?investigate=flow_run&subject_id=${run.id}#flows">Investigate safely</a>${canRetrySql ? '<button class="btn-primary" id="flow-retry-sql">Retry SQL only</button>' : ""}${canRetryViews ? '<button class="btn-primary" id="flow-retry-views">Retry view refresh</button>' : ""}</div></header>
        ${run.error ? `<section class="flow-log-failure"><h2>Final error</h2><p>${esc(run.error)}</p></section>` : ""}
        ${reconciliationBlocksRetry ? '<section class="flow-log-failure"><h2>SQL reconciliation required</h2><p>The prior append may have completed. Check and reconcile the target, then acknowledge reconciliation from the Flow’s More menu before running again.</p></section>' : ''}
        ${refresh ? `<section class="flow-log-section flow-log-views"><h2>SQL insertion → Refresh materialized views</h2><p><strong>${esc(sqlOutcomeText)}.</strong> ${refresh.deferred_to_pipeline ? "This run belongs to a full-pipeline run, which refreshes the configured views once after every upstream Flow finished." : refresh.blocked ? `Refresh blocked: ${esc(refresh.blocked)}` : `${refresh.completed} of ${refresh.total} materialized view(s) refreshed, each in its own transaction, upstream first.${refresh.source_run_id ? ` Refresh-only retry of run #${esc(refresh.source_run_id)}; nothing was downloaded, transformed or inserted again.` : ""}${refresh.discovered_at ? ` List frozen when the run was queued (${esc(stamp(refresh.discovered_at))}${refresh.metadata_at ? `, metadata verified ${esc(stamp(refresh.metadata_at))}` : ""}).` : ""}`}${refresh.retry && refresh.retry.status !== "eligible" ? ` ${esc(refresh.retry.message)}` : ""}</p>${viewRows ? `<div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>#</th><th>Materialized view</th><th>Status</th><th>Duration</th><th>Finished</th><th>Error</th></tr></thead><tbody>${viewRows}</tbody></table></div>` : ""}</section>` : ""}
        ${run.downloads ? `<section class="flow-log-section"><h2>Download tasks</h2><p>${run.downloads.completed} of ${run.downloads.total} completed · ${run.downloads.active} active slots · ${esc(label(run.downloads.state))}</p><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Export</th><th>Status</th><th>Worker</th><th>Attempt</th><th>Error</th></tr></thead><tbody>${run.downloads.tasks.map(task => `<tr><td>${task.ordinal}</td><td>${esc(task.state)}</td><td>${esc(task.worker_id || 'Waiting')}</td><td>${task.attempt}</td><td>${esc(task.error || '')}</td></tr>`).join('')}</tbody></table></div></section>` : ''}
        <section class="flow-log-summary"><dl><div><dt>Requested</dt><dd>${esc(stamp(run.created_at))}</dd></div><div><dt>Started</dt><dd>${esc(stamp(run.started_at))}</dd></div><div><dt>Finished</dt><dd>${esc(stamp(run.finished_at))}</dd></div><div><dt>Worker</dt><dd>${esc(run.worker_id || "Not claimed")}</dd></div><div><dt>Browser</dt><dd>${esc(label(run.job?.execution?.browser_mode || "unknown"))}</dd></div><div><dt>Transformation</dt><dd>${transformation.enabled ? `${esc(transformation.script_path)} → script_results` : "Disabled"}</dd></div><div><dt>SQL handoff</dt><dd>${sql.enabled ? `${esc(sql.mode)} to ${esc(sql.database)}.${esc(sql.schema)}.${esc(sql.table)}` : "Disabled"}</dd></div></dl></section>
        ${sql.owner_username || sqlOutcome?.owner_username ? `<section class="flow-log-section"><h2>SQL table ownership</h2><p>${sqlOutcome?.committed && sqlOutcome.owner_username ? `Committed table owner: <code>${esc(sqlOutcome.owner_username)}</code>. Refresh table Properties in pgAdmin to see this owner.` : `Requested owner: <code>${esc(sql.owner_username)}</code>. Ownership is confirmed only after the SQL load commits.`}</p></section>` : ''}
        <section class="flow-log-section"><h2>Event timeline</h2>${events || '<p class="flow-inline-empty">This run predates expanded event capture. Its summary, timings, files, and final error are shown below.</p>'}</section>
        <section class="flow-log-section"><h2>Phase timings</h2><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Phase</th><th>Duration</th><th>Status</th><th>Items</th></tr></thead><tbody>${timingRows || '<tr><td colspan="4">No timings recorded.</td></tr>'}</tbody></table></div></section>
        <section class="flow-log-section"><h2>Run files</h2><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>File</th><th>Period</th><th>Rows</th><th>Bytes</th><th>Path</th></tr></thead><tbody>${fileRows || '<tr><td colspan="5">No files recorded.</td></tr>'}</tbody></table></div></section>
        <section class="flow-log-section"><h2>Run configuration</h2><details><summary>View saved job configuration</summary><pre>${esc(JSON.stringify(run.job || {}, null, 2))}</pre></details></section>`;

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
    const loadRun = () => fetch(`/api/flows/runs/${runId}`).then(async response => {
        if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
        return response.json();
    }).then(run => {
        render(run);
        clearTimeout(refreshTimer);
        if (!["succeeded", "failed", "cancelled"].includes(run.status)) {
            refreshTimer = setTimeout(loadRun, 2000);
        }
    }).catch(error => {
        document.getElementById("flow-run-log").innerHTML = `<div class="flow-log-failure"><h1>Logs unavailable</h1><p>${esc(error.message)}</p></div>`;
    });
    loadRun();
}
