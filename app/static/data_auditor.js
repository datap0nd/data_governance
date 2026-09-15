/* Operator controls. The access key stays in this closure for this tab only. */
window.DataAuditor = (() => {
    let accessKey = "", state = {}, flows = [], draft = null, dirty = false;
    let timer = null, disposed = true, saving = false;
    const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
    const element = id => document.getElementById(`auditor-${id}`);
    const count = value => Number(value || 0).toLocaleString("en-US");
    const active = () => ["queued", "running", "cancelling"].includes(state.latest?.status);
    const labels = {queued:"Starting", running:"Running", cancelling:"Stopping", cancelled:"Cancelled", interrupted:"Interrupted by restart", completed:"Completed", completed_with_gaps:"Completed with gaps", unavailable:"Unavailable"};
    const reasons = {
        selected_flow_not_approved:"A selected flow is no longer approved.", no_completed_runs:"No completed run is available.",
        download_unverified:"The complete download could not be verified.", sql_unverified:"SQL inspection is unavailable or its safety checks did not pass.",
        sql_batch_boundary_unverified:"SQL could not be matched safely to this completed run.",
        four_comparable_baseline_days_required:"A stable baseline of four comparable days is not available.",
        time_budget_exhausted:"The time budget ended before all data was checked.", inspection_unavailable:"Inspection was unavailable. Restore reader access and run again."
    };
    async function request(path, method = "GET", body) {
        const response = await fetch(`/api/auditor${path}`, {method, cache:"no-store", credentials:"same-origin",
            headers:{...(accessKey ? {Authorization:`Bearer ${accessKey}`} : {}), ...(body ? {"Content-Type":"application/json"} : {})},
            ...(body ? {body:JSON.stringify(body)} : {})});
        const result = await response.json();
        if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : "Check your selections and try again.");
        return result;
    }
    function feedback(message) { if (element("feedback")) element("feedback").textContent = message; }
    function selected() {
        return {enabled:element("enabled").checked, flow_ids:[...document.querySelectorAll(".auditor-flow:checked")].map(el => Number(el.value)),
            overnight:element("overnight").checked, time:element("time").value || "02:00"};
    }
    function changed() { draft = selected(); dirty = true; paint(); }
    function paint() {
        if (!element("status")) return;
        const enabled = state.settings?.enabled;
        element("status").textContent = dirty ? "Your changes are not saved." : enabled ? `Auditor enabled for ${state.settings.flow_ids.length} flows.` : "Auditor is off. Choose your flows, then enable it.";
        element("run").disabled = !state.authorized || !enabled || dirty || saving || active();
        element("save").disabled = !state.authorized || saving;
        document.querySelectorAll("#auditor-enabled, #auditor-overnight, .auditor-flow").forEach(input => input.disabled = !state.authorized || saving);
        element("stop").disabled = !active() || state.latest?.status === "cancelling";
        element("time").disabled = !state.authorized || saving || !element("overnight").checked;
        element("next").textContent = state.settings?.next_due ? `Next automatic run: ${new Intl.DateTimeFormat("en-GB", {timeZone:"Asia/Dubai", dateStyle:"medium", timeStyle:"short"}).format(new Date(state.settings.next_due))} Dubai time.` : "Automatic runs are off. Run now works independently.";
        element("run-state").textContent = labels[state.latest?.status] || "No audit yet";
        const coverage = state.latest?.coverage || {}, gaps = coverage.unverified || [];
        element("coverage").textContent = state.latest ? `${count(coverage.checked)} datasets checked · ${count(coverage.findings)} possible inconsistencies${gaps.length ? ` · ${count(gaps.length)} checks unverified` : ""}` : "Coverage appears here after an audit starts.";
        const gapText = [...new Set(gaps.map(gap => `${gap.dataset_id ? `${gap.dataset_id}: ` : ""}${reasons[gap.reason] || "This check remains unverified."}`))];
        if (state.latest?.reason && reasons[state.latest.reason]) gapText.push(reasons[state.latest.reason]);
        if (coverage.model && !["completed", "pending"].includes(coverage.model)) gapText.push("Qwen's review did not complete. Findings shown here are supported by computed checks.");
        if (["cancelled", "interrupted"].includes(state.latest?.status)) gapText.push("Unchecked data remains unverified. Run now starts a new audit.");
        element("gaps").innerHTML = gapText.length ? `<ul>${gapText.map(text => `<li>${esc(text)}</li>`).join("")}</ul>` : "";
        element("findings").innerHTML = (state.findings || []).length ? `<div class="auditor-table-wrap"><table><thead><tr><th>Finding</th><th>Dataset</th><th></th></tr></thead><tbody>${state.findings.map(item => `<tr><td>${esc(item.message)}</td><td>${esc(item.dataset_id)}</td><td><button class="btn-outline auditor-evidence" data-id="${Number(item.id)}">View evidence</button></td></tr>`).join("")}</tbody></table></div><p class="auditor-muted">Findings also appear in Alerts. Unchanged findings stay in one alert.</p>` : "";
        element("findings").querySelectorAll(".auditor-evidence").forEach(button => button.onclick = () => showEvidence(Number(button.dataset.id), button));
    }
    function fillChoices() {
        if (!element("flows")) return;
        const value = draft || state.settings || {enabled:false, flow_ids:[], overnight:false, time:"02:00"};
        const available = new Set(flows.map(flow => flow.id));
        const missing = value.flow_ids.filter(id => !available.has(id)).map(id => ({id, name:`Unavailable flow #${id}`, group_name:"Remove this selection before enabling"}));
        element("flows").innerHTML = [...flows, ...missing].map(flow => `<label><input class="auditor-flow" type="checkbox" value="${Number(flow.id)}"${value.flow_ids.includes(flow.id) ? " checked" : ""}>${esc(flow.name)}${flow.group_name ? ` <span class="auditor-muted">· ${esc(flow.group_name)}</span>` : ""}</label>`).join("") || '<p class="auditor-muted">No approved flows are available. Ask an administrator to approve the datasets to audit.</p>';
        element("enabled").checked = value.enabled;
        element("overnight").checked = value.overnight;
        element("time").value = value.time;
        element("flows").querySelectorAll("input").forEach(input => input.onchange = changed);
    }
    async function loadFlows() {
        const result = await request("/catalog");
        if (disposed) return;
        flows = result.flows;
        fillChoices();
        element("access-status").textContent = "Restricted reader is available. SQL permissions and data completeness are checked during each audit.";
        paint();
    }
    async function refresh() {
        try {
            const result = await request("/status");
            if (disposed) return;
            state = result;
            if (!dirty) fillChoices();
            if (!state.authorized) {
                accessKey = "";
                element("access").hidden = false;
                element("controls").disabled = true;
                feedback("Operator access is required to continue.");
            }
            paint();
        } catch (error) { feedback(`Could not refresh audit status. ${error.message}`); }
    }
    async function save() {
        if (saving) return;
        const value = selected();
        if (value.enabled && !value.flow_ids.length) { feedback("Select at least one flow. Your other settings are kept."); return; }
        if (value.overnight && !element("time").value) { feedback("Choose a Dubai start time."); return; }
        saving = true; paint(); feedback("Saving settings…");
        try {
            state.settings = await request("/settings", "PUT", value);
            dirty = false; draft = null; feedback("Settings saved.");
        } catch (error) { feedback(`Settings were not saved. Your selections are kept. ${error.message}`); }
        finally { saving = false; paint(); }
    }
    async function disable() {
        const value = selected(); draft = value; dirty = true;
        saving = true; paint(); feedback("Disabling auditing and cancelling outstanding work…");
        try {
            state.settings = await request("/settings", "PUT", {...state.settings, enabled:false, next_due:undefined});
            if (state.latest && active()) state.latest.status = "cancelling";
            // Preserve any other unsaved selections while stopping immediately.
            dirty = JSON.stringify({...state.settings, next_due:undefined}) !== JSON.stringify(value);
            if (!dirty) draft = null;
            feedback("Auditor disabled. Outstanding work is being cancelled.");
        } catch (error) { feedback(`Auditor could not be disabled and may still be running. Retry Save settings or Stop audit. ${error.message}`); }
        finally { saving = false; paint(); }
    }
    async function showEvidence(id, button) {
        button.disabled = true;
        try {
            const result = await request(`/findings/${id}`);
            if (disposed) return;
            const observations = [result.evidence.current, ...(result.evidence.references || [])];
            element("evidence-body").innerHTML = `<h2>Finding evidence</h2><p>${esc(result.message)}</p><p class="auditor-muted">Computed observations from approved data. Cause unconfirmed.</p><div class="auditor-table-wrap"><table><thead><tr><th>Run</th><th>Source</th><th>Rows</th></tr></thead><tbody>${observations.map(item => `<tr><td>#${Number(item.run_id)}</td><td>${esc(item.source)}</td><td>${count(item.profile?.rows)}</td></tr>`).join("")}</tbody></table></div>${observations.map(item => `<h3>Run #${Number(item.run_id)} · ${esc(item.source)}</h3><div class="auditor-table-wrap"><table><thead><tr><th>Column</th><th>Empty</th><th>Distinct</th><th>Total</th><th>Date / value range</th></tr></thead><tbody>${Object.entries(item.profile?.columns || {}).map(([name, metric]) => `<tr><td>${esc(name)}</td><td>${count(metric.nulls)}</td><td>${metric.distinct == null ? "Unverified" : count(metric.distinct)}</td><td>${esc(metric.sum ?? "—")}</td><td>${esc(metric.min ?? "—")} – ${esc(metric.max ?? "—")}</td></tr>`).join("")}</tbody></table></div>`).join("")}`;
            element("evidence").showModal();
        } catch (error) { feedback(`Could not load evidence. ${error.message}`); }
        finally { button.disabled = false; }
    }
    async function render() {
        state = await request("/status");
        return `<div id="data-auditor"><a href="#dataquality">Data Quality</a><header class="auditor-row auditor-between"><div><h1>AI Auditor</h1><p class="auditor-muted">Find possible inconsistencies in completed downloads and SQL data.</p></div><span class="badge">Read-only inspection</span></header>
        <section id="auditor-access" class="auditor-section"${state.authorized ? " hidden" : ""}><h2>Operator access</h2><p>${esc(state.detail)}</p><form id="auditor-unlock-form" class="auditor-row"><label>Operator access key <input id="auditor-key" type="password" autocomplete="off" required></label><button class="btn-outline" type="submit"${state.configured ? "" : " disabled"}>Unlock controls</button></form><p id="auditor-unlock-feedback" role="status"></p></section>
        <fieldset id="auditor-controls"${state.authorized ? "" : " disabled"}><section class="auditor-section"><div class="auditor-row auditor-between"><div><h2>Auditing</h2><p class="auditor-muted">Qwen inspects approved data. Metronome records findings in Alerts.</p></div><label><input id="auditor-enabled" type="checkbox">Enable auditor</label></div><p id="auditor-status" class="auditor-feedback" role="status"></p><div class="auditor-fields"><div><h2>Flows to audit</h2><div id="auditor-flows" class="auditor-choices"></div><button id="auditor-retry" class="btn-outline">Reload approved flows</button></div><div><label><input id="auditor-overnight" type="checkbox">Run overnight automatically</label><label class="auditor-time">Start time · Dubai <input id="auditor-time" type="time" value="02:00"></label><p id="auditor-next" class="auditor-muted"></p></div></div><div class="auditor-row auditor-actions"><button id="auditor-save" class="btn-outline">Save settings</button><button id="auditor-run" class="btn-new-task">Run now</button><button id="auditor-stop" class="btn-outline">Stop audit</button></div></section></fieldset>
        <p id="auditor-feedback" class="auditor-feedback" role="status" aria-live="polite"></p><section class="auditor-section"><div class="auditor-row auditor-between"><h2>Latest audit</h2><span id="auditor-run-state" class="badge">No audit yet</span></div><p id="auditor-coverage" class="auditor-feedback"></p><div id="auditor-gaps" class="auditor-gaps"></div><div id="auditor-findings"></div></section><section class="auditor-section"><h2>Read-only access</h2><p class="auditor-muted">Approved datasets · Restricted query service · Qwen cannot modify files, databases, flows or settings.</p><p id="auditor-access-status" class="auditor-feedback">${esc(state.detail)}</p></section>
        <dialog id="auditor-evidence"><div id="auditor-evidence-body"></div><button id="auditor-close" class="btn-outline">Back to auditor</button></dialog></div>`;
    }
    async function bind() {
        disposed = false; clearInterval(timer); fillChoices(); paint();
        element("unlock-form").onsubmit = async event => {
            event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true;
            accessKey = element("key").value; element("key").value = "";
            try {
                state = await request("/status");
                if (!state.authorized) { accessKey = ""; throw new Error("The key was not accepted. Remote access requires HTTPS."); }
                element("access").hidden = true; element("controls").disabled = false; await loadFlows();
                feedback("Controls unlocked for this tab.");
            } catch (error) { element("unlock-feedback").textContent = error.message; feedback(error.message); }
            finally { button.disabled = false; paint(); }
        };
        element("enabled").onchange = () => element("enabled").checked ? changed() : disable();
        element("overnight").onchange = changed; element("time").oninput = changed; element("time").onchange = changed;
        element("save").onclick = save;
        element("retry").onclick = async () => { try { await loadFlows(); feedback("Approved flows loaded."); } catch (error) { feedback(error.message); } };
        element("run").onclick = async () => {
            saving = true; paint(); feedback("Starting audit…");
            try { await request("/run", "POST"); feedback("Audit started. You can stop it at any time."); await refresh(); }
            catch (error) { feedback(`Audit could not start. ${error.message}`); }
            finally { saving = false; paint(); }
        };
        element("stop").onclick = async () => {
            element("stop").disabled = true;
            try { await request("/stop", "POST"); if (state.latest) state.latest.status = "cancelling"; feedback("Stopping audit. Outstanding reads are being cancelled."); }
            catch (error) { feedback(`Stop did not complete. Try again. ${error.message}`); }
            finally { paint(); }
        };
        element("close").onclick = () => element("evidence").close();
        if (state.authorized) { try { await loadFlows(); } catch (error) { feedback(error.message); } }
        timer = setInterval(refresh, 5000);
    }
    function dispose() { disposed = true; clearInterval(timer); timer = null; }
    return {render, bind, dispose};
})();
