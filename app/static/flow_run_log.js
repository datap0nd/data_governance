const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[char]));
const duration = ms => {
    if (!Number.isFinite(Number(ms))) return "Not available";
    const seconds = Math.max(0, Math.round(Number(ms) / 1000));
    return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
};
const stamp = value => value ? new Date(value).toLocaleString() : "Not recorded";
const label = value => String(value || "unknown").replaceAll("_", " ");
const runId = Number(location.pathname.match(/\/flow-runs\/(\d+)/)?.[1]);

function render(run) {
    const sql = run.job?.sql_handoff || {};
    const timingRows = (run.timings || []).map(item => `<tr><td>${esc(label(item.phase))}</td><td>${esc(duration(item.duration_ms))}</td><td>${esc(item.status)}</td><td>${esc(item.item_count ?? "")}</td></tr>`).join("");
    const fileRows = (run.files?.length ? run.files : run.artifacts || []).map(item => `<tr><td>${esc(item.filename)}</td><td>${esc(item.period_key || "Full range")}</td><td>${esc(item.row_count ?? "Unknown")}</td><td>${esc(item.file_size ?? "Unknown")}</td><td class="flow-log-path">${esc(item.file_path)}</td></tr>`).join("");
    const events = (run.events || []).map(item => `<article class="flow-log-event ${item.error ? "is-error" : ""}"><header><time>${esc(stamp(item.created_at))}</time><strong>${esc(label(item.stage || item.status))}</strong><span>${esc(item.status)}</span></header><p>${esc(item.message || item.error || "No message")}</p>${item.error ? `<div class="flow-log-error"><strong>Error</strong><pre>${esc(item.error)}</pre></div>` : ""}${item.traceback ? `<details><summary>Full traceback</summary><pre>${esc(item.traceback)}</pre></details>` : ""}<details><summary>Event details</summary><pre>${esc(JSON.stringify(item.details || {}, null, 2))}</pre></details></article>`).join("");
    document.title = `Run #${run.id} logs - Metronome`;
    document.getElementById("flow-run-log").innerHTML = `
        <header class="flow-log-header"><div><a href="/#flows">Back to Flows</a><h1>Run #${run.id}: ${esc(run.flow_name)}</h1><p>Complete diagnostic record captured by Metronome.</p></div><span class="flow-log-status ${esc(run.status)}">${esc(run.status)}</span></header>
        ${run.error ? `<section class="flow-log-failure"><h2>Final error</h2><p>${esc(run.error)}</p></section>` : ""}
        <section class="flow-log-summary"><dl><div><dt>Requested</dt><dd>${esc(stamp(run.created_at))}</dd></div><div><dt>Started</dt><dd>${esc(stamp(run.started_at))}</dd></div><div><dt>Finished</dt><dd>${esc(stamp(run.finished_at))}</dd></div><div><dt>Worker</dt><dd>${esc(run.worker_id || "Not claimed")}</dd></div><div><dt>Browser</dt><dd>${esc(label(run.job?.execution?.browser_mode || "unknown"))}</dd></div><div><dt>SQL handoff</dt><dd>${sql.enabled ? `${esc(sql.mode)} to ${esc(sql.database)}.${esc(sql.schema)}.${esc(sql.table)}` : "Disabled"}</dd></div></dl></section>
        <section class="flow-log-section"><h2>Event timeline</h2>${events || '<p class="flow-inline-empty">This run predates expanded event capture. Its summary, timings, files, and final error are shown below.</p>'}</section>
        <section class="flow-log-section"><h2>Phase timings</h2><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>Phase</th><th>Duration</th><th>Status</th><th>Items</th></tr></thead><tbody>${timingRows || '<tr><td colspan="4">No timings recorded.</td></tr>'}</tbody></table></div></section>
        <section class="flow-log-section"><h2>Downloaded files</h2><div class="flow-table-wrap"><table class="flow-table"><thead><tr><th>File</th><th>Period</th><th>Rows</th><th>Bytes</th><th>Path</th></tr></thead><tbody>${fileRows || '<tr><td colspan="5">No files recorded.</td></tr>'}</tbody></table></div></section>
        <section class="flow-log-section"><h2>Run configuration</h2><details><summary>View saved job configuration</summary><pre>${esc(JSON.stringify(run.job || {}, null, 2))}</pre></details></section>`;
}

if (!Number.isInteger(runId)) {
    document.getElementById("flow-run-log").innerHTML = '<div class="flow-log-failure"><h1>Invalid run</h1><p>The run ID in this URL is not valid.</p></div>';
} else {
    fetch(`/api/flows/runs/${runId}`).then(async response => {
        if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
        return response.json();
    }).then(render).catch(error => {
        document.getElementById("flow-run-log").innerHTML = `<div class="flow-log-failure"><h1>Logs unavailable</h1><p>${esc(error.message)}</p></div>`;
    });
}
