import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const flowLog = fs.readFileSync(new URL("../app/static/flow_run_log.js", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");
const start = source.indexOf("function initAIChatPanel()");
const end = source.indexOf("\n// ── AI Report Risk", start);

assert.notEqual(start, -1, "Operations Investigator UI must exist");
assert.notEqual(end, -1, "Investigator code must have a bounded source section");
const investigator = source.slice(start, end);

assert.match(investigator, /\/api\/ai\/operations\/runs/,
    "Investigator must use the durable read-only operations API");
assert.doesNotMatch(investigator, /\/api\/ai\/chat/,
    "Investigator must not fall back to generic ecosystem chat");
for (const mutatingRoute of ["/resume", "/retry-sql", "/resend-summary", "/materialized-views"]) {
    assert.equal(investigator.includes(mutatingRoute), false,
        `Investigator must not contain an operational route: ${mutatingRoute}`);
}
assert.doesNotMatch(investigator, /renderMd\(/,
    "Model-authored investigator text must be escaped structured fields, not rendered Markdown");
assert.doesNotMatch(investigator, /className\s*=\s*["']ai-fab["']/,
    "Run analysis must not recreate a generic floating AI launcher");
assert.doesNotMatch(style, /\.ai-fab\b/,
    "The retired floating AI launcher must not remain in the stylesheet");
assert.match(source, /data-alert-analysis/,
    "Every dashboard Alert detail must own an inline Analysis surface");
assert.match(source, /Automatic overall review/,
    "The normal Alert path must present automatic overall analysis, not require a manual run");
assert.match(investigator, /current_analysis_run_id/,
    "Alert details must load the server-created current automatic assessment");
assert.match(investigator, /run\.focus_type === "alert"/,
    "Automatic assessment polling must verify that the run belongs to the exact Alert");
assert.match(investigator, /deterministic alert remains active/i,
    "A pending or unavailable model must never imply that the deterministic Alert disappeared");
assert.match(source, /pipeline_failed:\s*"Pipeline Failed"/,
    "Pipeline failure Alerts need a first-class issue label");
assert.match(source, /pbi_reconnect:\s*"Power BI Reconnect"/,
    "Power BI reconnect Alerts need a first-class issue label");
assert.match(source, /return "Power BI connection"/,
    "System-level reconnect Alerts must not fall through to an unknown SQL asset");
assert.match(source, /\/api\/actions\/\$\{actionId\}\/occurrences/,
    "Alert detail must load exact linked occurrences lazily");
assert.match(investigator, /action_id:\s*actionId/,
    "Alert-originated analysis must retain its alert linkage");
assert.match(investigator, /linkedBody\.occurrence_id\s*=\s*occurrenceId/,
    "Alert-originated analysis must retain its occurrence linkage when available");
assert.doesNotMatch(investigator, /error\.status\s*!==\s*422/,
    "Alert analysis must fail closed rather than retrying as detached run analysis");
assert.match(investigator, /_runMatchesAIFocus\(run, expectedFocus\)/,
    "Every poll must verify the durable run against the pinned focus");
assert.match(investigator, /focusGeneration !== _aiFocusGeneration/,
    "Stale create and poll responses must be ignored after focus changes");
assert.match(investigator, /err\.status < 500/,
    "Permanent polling errors must terminate instead of retrying forever");
assert.match(investigator, /\[2000, 5000, 10000\]/,
    "Transient polling failures must use bounded backoff");
assert.match(investigator, /ALERT_ANALYSIS_FRESHNESS_MS\s*=\s*15000/,
    "Open Alert analysis must periodically revalidate current recommendations");
assert.match(investigator, /setTimeout\([\s\S]*?_pollAlertInvestigation[\s\S]*?ALERT_ANALYSIS_FRESHNESS_MS/,
    "Completed Alert analysis must keep checking durable freshness state");
assert.match(investigator, /_revalidateCompletedAIInvestigation[\s\S]*?ALERT_ANALYSIS_FRESHNESS_MS/,
    "Contextual run analysis must also re-check alert-bound recommendations");
assert.match(investigator, /operational action is recommended|cannot execute the recommendation/i,
    "Recommendations must remain text-only");

const emailPreviewStart = source.indexOf("function _emailNextAction");
const emailPreviewEnd = source.indexOf("async function renderEmail", emailPreviewStart);
const emailPreview = source.slice(emailPreviewStart, emailPreviewEnd);
assert.match(emailPreview, /alert\.ai_assessment/,
    "The Email-page preview must use the same current Alert AI assessment as Outlook");
assert.match(emailPreview, /assessment\.recommendation_title/,
    "The Email-page preview must select the Qwen recommendation before deterministic fallback");
assert.match(emailPreview, /Pending or unavailable/,
    "The preview must disclose when AI is not current instead of implying it was included");
assert.match(emailPreview, /alert\.ai_analysis_enabled !== false/,
    "The Email preview must ignore AI-derived next actions when email analysis is disabled");
assert.match(emailPreview, /AI analysis is disabled in System > AI/,
    "The Email preview must explain the centralized feature switch");
assert.match(emailPreview, /const assessmentHtml = alert\.ai_analysis_enabled === false[\s\S]*?\? ""/,
    "Disabled AI copy must be omitted from each Alert row rather than shown as pending");

const context = {};
vm.createContext(context);
vm.runInContext(`
    function esc(value) {
        return String(value ?? "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }
    function formatDate(value) { return String(value ?? ""); }
    ${investigator}
    this.validFocus = _validAIFocus;
    this.sameFocus = _sameAIFocus;
    this.runMatches = _runMatchesAIFocus;
    this.evidenceHref = _aiEvidenceHref;
    this.resultHtml = _aiResultHtml;
    this.analysisStaleness = _aiAnalysisStaleness;
    this.normalizeOccurrences = _normalizeAlertOccurrences;
    this.occurrencesHtml = _alertOccurrencesHtml;
`, context);

assert.equal(context.validFocus({ type: "flow_run", id: 7 }), true);
assert.equal(context.validFocus({ type: "source", id: 7 }), false);
assert.equal(context.sameFocus(
    { type: "pipeline_run", id: 8 }, { type: "pipeline_run", id: "8" }
), true);
assert.equal(context.runMatches(
    { focus_type: "flow_run", focus_id: 7 }, { type: "flow_run", id: 7 }
), true);
assert.equal(context.runMatches(
    { focus_type: "flow_run", focus_id: 8 }, { type: "flow_run", id: 7 }
), false);

const occurrences = context.normalizeOccurrences({ occurrences: [{
    id: 44,
    subject_type: "flow_run",
    subject_id: 7,
    status: "failed",
    summary: '<img src=x onerror="alert(1)">',
    evidence_revision: 3,
    latest_analysis_run_id: 91,
}]}, {});
assert.equal(occurrences.length, 1);
assert.equal(occurrences[0].focus.type, "flow_run");
assert.equal(occurrences[0].focus.id, 7);
assert.equal(occurrences[0].analysis_run_id, 91);
const occurrencesHtml = context.occurrencesHtml(12, occurrences);
assert.match(occurrencesHtml, /data-occurrence-id="44"/);
assert.match(occurrencesHtml, /data-analysis-run-id="91"/);
assert.doesNotMatch(occurrencesHtml, /<img/,
    "Occurrence summaries must be escaped before entering Alert details");

const pbiOccurrences = context.normalizeOccurrences({ occurrences: [{
    id: 45,
    focus_type: "pbi_sync",
    focus_id: 13,
    summary: "Power BI sign-in expired",
    label: "Power BI sign-in expired",
    evidence_json: '{"status":"failed"}',
}]}, {});
assert.equal(pbiOccurrences[0].status, "failed");
const pbiOccurrenceHtml = context.occurrencesHtml(13, pbiOccurrences);
assert.match(pbiOccurrenceHtml, /Power BI sync #13/);
assert.match(pbiOccurrenceHtml, /Recorded evidence/);
assert.doesNotMatch(pbiOccurrenceHtml, /data-alert-analyze-occurrence/,
    "Unsupported system occurrences must remain visible without an unsafe analysis trigger");

const oldOccurrence = context.normalizeOccurrences({ evidence_revision: 4, occurrences: [{
    occurrence_id: 46,
    focus_type: "flow_run",
    focus_id: 14,
    evidence_revision: 3,
    status: "failed",
}]}, {});
const oldOccurrenceHtml = context.occurrencesHtml(14, oldOccurrence);
assert.match(oldOccurrenceHtml, /No saved analysis/);
assert.doesNotMatch(oldOccurrenceHtml, /data-alert-analyze-occurrence/,
    "A superseded occurrence without saved analysis must not offer an analysis the server rejects");
const savedOldOccurrence = context.normalizeOccurrences({ evidence_revision: 4, occurrences: [{
    occurrence_id: 47,
    focus_type: "flow_run",
    focus_id: 15,
    evidence_revision: 3,
    status: "failed",
    latest_analysis_run_id: 92,
}]}, {});
const savedOldOccurrenceHtml = context.occurrencesHtml(14, savedOldOccurrence);
assert.match(savedOldOccurrenceHtml, /View historical analysis/);
assert.match(savedOldOccurrenceHtml, /data-analysis-run-id="92"/,
    "A superseded occurrence may still open its saved historical analysis");

const unboundFallback = context.normalizeOccurrences({ occurrences: [{
    focus_type: "flow_run",
    focus_id: 16,
    status: "failed",
    is_current: true,
}]}, {});
const unboundFallbackHtml = context.occurrencesHtml(15, unboundFallback);
assert.match(unboundFallbackHtml, /Exact occurrence unavailable/);
assert.doesNotMatch(unboundFallbackHtml, /data-alert-analyze-occurrence/,
    "A run link without an immutable occurrence id must never start detached Alert analysis");

assert.equal(context.evidenceHref({ deep_link: "/flow-runs/12" }), "/flow-runs/12");
assert.equal(context.evidenceHref({ deep_link: "https://evil.example.test" }), "");
assert.equal(context.evidenceHref({ deep_link: "/#lineage" }), "",
    "Non-exact Pipeline evidence must not become a misleading link");

const html = context.resultHtml({
    provider_mode: "qwen",
    model: "Qwen/Qwen3.8-27B",
    result: {
        conclusion: '<img src=x onerror="alert(1)">',
        conclusion_evidence_refs: ["flow_run:7"],
        confidence: "high",
        observed_facts: [{
            statement: "<script>alert(1)</script>", evidence_refs: ["flow_run:7"],
        }],
        inferences: [],
        recommendations: [{
            action_type: "inspect", title: "Inspect only", rationale: "No mutation",
            evidence_refs: ["flow_run:7"],
        }],
        unknowns: [],
    },
    evidence: [{
        reference: "flow_run:7", entity_type: "flow_run", entity_id: "7",
        label: '<b>unsafe label</b>', deep_link: "/flow-runs/7",
        observed_at: "2026-08-27T10:00:00+00:00",
    }],
});
assert.doesNotMatch(html, /<img|<script|<b>unsafe/,
    "All model and evidence text must be HTML-escaped");
assert.match(html, /&lt;img/);
assert.match(html, /href="\/flow-runs\/7"/);
assert.doesNotMatch(html, /<button[^>]*(resume|retry|refresh|send)/i,
    "Structured results must not generate operational buttons");

const staleHtml = context.resultHtml({
    provider_mode: "qwen",
    model: "Qwen/Qwen3.8-27B",
    superseded_at: "2026-08-27T10:10:00+00:00",
    superseded_reason: "A newer failure occurrence was recorded.",
    is_current: false,
    result: {
        conclusion: "Historical conclusion",
        conclusion_evidence_refs: [],
        confidence: "high",
        observed_facts: [],
        inferences: [],
        recommendations: [{
            action_type: "retry_sql",
            title: "DO NOT SHOW THIS STALE STEP",
            rationale: "Old evidence",
            evidence_refs: [],
        }],
        unknowns: [],
    },
    evidence: [],
});
assert.match(staleHtml, /Stale analysis/);
assert.match(staleHtml, /Recommendations from this snapshot are hidden/);
assert.doesNotMatch(staleHtml, /DO NOT SHOW THIS STALE STEP/,
    "Superseded analysis must never present its recommendation as current");
const revisionStaleness = context.analysisStaleness({
    action_evidence_revision: 2, current_alert_evidence_revision: 3,
});
assert.equal(revisionStaleness.stale, true);
assert.equal(revisionStaleness.reason, "Alert evidence advanced from revision 2 to 3.");
assert.equal(context.analysisStaleness({
    superseded_at: "2026-08-27", superseded_reason: "alert_resolved",
}).reason, "The alert was resolved after this analysis.");
assert.equal(context.analysisStaleness({ recommendations_current: false }).stale, true,
    "Backend recommendation-current metadata must fail closed");

assert.match(source, /selectedReportId !== Number\(expectedReportId\)/,
    "Pipeline polling must stop when a different report is selected");
assert.match(source, /Number\(run\.report_id\) !== Number\(expectedReportId\)/,
    "Pipeline status must verify the returned report before rendering");
assert.match(source, /pipelineStatus\.hidden = true/,
    "Changing report must immediately hide stale Pipeline status");
assert.match(source, /Manual inspection required/,
    "Pipeline uncertainty must be visible to the user");
const automaticPoll = source.slice(
    source.indexOf("async function _pollAutomaticAlertAnalysis"),
    source.indexOf("\nfunction _setAlertAnalysisBusy"),
);
assert.match(automaticPoll,
    /holder\.dataset\.analysisGeneration !== String\(generation\)\) return;/,
    "An old automatic Alert poll must yield to a newer occurrence analysis");
assert.doesNotMatch(
    automaticPoll.slice(0, automaticPoll.indexOf("try {")),
    /holder\.dataset\.analysisGeneration\s*=/,
    "An automatic poll must not reclaim generation ownership before checking it");
assert.match(flowLog, /investigate=flow_run&subject_id=/,
    "Expanded Flow logs must deep-link to the exact investigation focus");
assert.match(style, /\.ai-investigation-failed\s*\{[^}]*var\(--red\)/,
    "Failed investigations must have a clear compatible error style");

console.log("AI Operations Investigator display tests passed");
