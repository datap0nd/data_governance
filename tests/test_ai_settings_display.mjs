import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";


const source = fs.readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const index = fs.readFileSync(new URL("../app/static/index.html", import.meta.url), "utf8");
const style = fs.readFileSync(new URL("../app/static/style.css", import.meta.url), "utf8");
const start = source.indexOf("// ── System > AI settings ──");
const end = source.indexOf("\nasync function renderRefreshSchedule", start);

assert.notEqual(start, -1, "System AI settings implementation must exist");
assert.notEqual(end, -1, "AI settings implementation must have a bounded source section");
const settingsSource = source.slice(start, end);

assert.match(index, /data-pages="[^"]*\bai\b/,
    "AI must be registered as a System page");
assert.match(index, /href="#ai" data-page="ai"[^>]*>AI<\/a>/,
    "System must expose an AI navigation item");
assert.match(source, /ai:\s*renderAISettings/,
    "the application router must render the AI page");
assert.match(source, /page === "ai"\) bindAISettingsPage\(\)/,
    "the application router must bind AI controls");
assert.match(settingsSource, /\/api\/ai\/settings/,
    "the UI must use the centralized runtime settings API");
assert.match(settingsSource, /\/api\/ai\/settings\/test/,
    "the UI must expose the no-business-context connection test");
assert.match(settingsSource, /\/api\/ai\/settings\/models/,
    "the UI must load the provider model catalog through the server");
assert.match(settingsSource, /_loadAISettingsModels\(\{ quiet: true \}\)/,
    "opening System > AI must automatically load the provider model catalog");
assert.match(settingsSource, /endpoint\?\.addEventListener\("change",/,
    "editing the endpoint must refresh model discovery");
assert.match(style, /\.ai-settings-shell\s*\{/,
    "AI settings must have a dedicated responsive layout");

const elements = {
    "ai-endpoint": { value: "http://qwen.office/v1/chat/completions" },
    "ai-model": { value: "Qwen/Qwen3.8-27B", innerHTML: "", isConnected: true },
    "ai-model-custom": { value: "", hidden: true, disabled: true, focus: () => {} },
    "btn-refresh-ai-models": { disabled: false, textContent: "Refresh models", isConnected: true },
    "ai-models-status": { dataset: {}, textContent: "" },
    "ai-provider-profile": { value: "qwen_vllm" },
    "ai-reasoning-effort": { value: "medium" },
    "ai-max-tool-calls": { value: "8" },
    "ai-max-model-turns": { value: "6" },
    "ai-max-seconds": { value: "180" },
    "ai-http-timeout-seconds": { value: "90" },
    "ai-max-output-tokens": { value: "4096" },
    "ai-temperature": { value: "1" },
    "ai-top-p": { value: "0.95" },
    "ai-feature-operations": { checked: true },
    "ai-feature-alert-review": { checked: true },
    "ai-feature-alert-email": { checked: true },
    "ai-feature-documentation": { checked: false },
    "ai-clear-api-key": { checked: false },
    "ai-api-key": { value: "" },
};
const modeInput = { value: "qwen" };
const settings = {
    mode: "qwen",
    endpoint: "http://qwen.office/v1/chat/completions",
    model: "Qwen/Qwen3.8-27B",
    provider_profile: "qwen_vllm",
    reasoning_effort: "medium",
    max_tool_calls: 8,
    max_model_turns: 6,
    max_seconds: 180,
    http_timeout_seconds: 90,
    max_output_tokens: 4096,
    temperature: 1,
    top_p: 0.95,
    operations_investigator_enabled: true,
    automatic_alert_review_enabled: true,
    alert_email_analysis_enabled: true,
    documentation_suggestions_enabled: false,
    api_key_configured: true,
    api_key_source: "saved",
    configuration_source: "saved",
    effective_state: "ready",
    api_key: "THIS_MUST_NEVER_RENDER",
};
const context = {
    Number,
    String,
    Boolean,
    window: {},
    document: {
        getElementById: id => elements[id] || null,
        querySelector: selector => selector.includes('input[name="ai-mode"]') ? modeInput : null,
        querySelectorAll: () => [],
    },
    api: async () => settings,
    apiPostJson: async () => ({ models: [] }),
    toast: () => {},
};
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
    ${settingsSource}
    this.readPayload = _aiSettingsPayload;
    this.readModelsPayload = _aiSettingsModelsPayload;
    this.renderSettings = renderAISettings;
    this.readModelIds = _aiSettingsModelIds;
    this.modelOptions = _aiSettingsModelOptions;
    this.loadModels = _loadAISettingsModels;
    this.invalidateModels = _invalidateAISettingsModels;
`, context);

const payload = context.readPayload();
assert.equal(payload.mode, "qwen");
assert.equal(payload.endpoint, "http://qwen.office/v1/chat/completions");
assert.equal(payload.alert_email_analysis_enabled, true);
assert.equal(payload.documentation_suggestions_enabled, false);
assert.equal("api_key" in payload, false,
    "a blank secret field must preserve the saved key rather than submit one");

elements["ai-api-key"].value = "new-secret";
assert.equal(context.readPayload().api_key, "new-secret");
elements["ai-clear-api-key"].checked = true;
const clearPayload = context.readPayload();
assert.equal(clearPayload.clear_api_key, true);
assert.equal("api_key" in clearPayload, false,
    "explicit clear and a replacement secret must never be submitted together");

const html = await context.renderSettings();
assert.match(html, /id="ai-settings-form"/);
assert.match(html, /Operations Investigator/);
assert.match(html, /Automatic Alert review/);
assert.match(html, /Alert email analysis/);
assert.match(html, /Documentation suggestions/);
assert.match(html, /<strong>Local AI<\/strong>/,
    "the provider-backed operating mode must not be named after one model family");
assert.doesNotMatch(html, /<strong>Qwen<\/strong>/,
    "Qwen must not be presented as an operating mode");
assert.match(html, /id="ai-model"[^>]*>/,
    "the model must be chosen from a select control");
assert.match(html, /id="btn-refresh-ai-models"/,
    "the model catalog must be refreshable");
assert.match(html, /Custom model ID…/,
    "a provider without model discovery must still allow an exact model ID");
assert.match(html, /id="ai-api-key"[^>]*value=""/,
    "the secret field must always load blank");
assert.doesNotMatch(html, /THIS_MUST_NEVER_RENDER/,
    "even an incorrectly returned backend secret must never enter the page");
assert.match(html, /cannot edit data, retry runs, refresh reports, or send email/i,
    "the page must state the model's read-only boundary");

assert.deepEqual(
    [...context.readModelIds({ data: [
        { id: "local/model-a" },
        { id: "local/model-a" },
        { model: "local/model-b" },
        "local/model-c",
        { id: "" },
    ] })],
    ["local/model-a", "local/model-b", "local/model-c"],
    "model discovery must accept OpenAI-style data and return unique model IDs",
);
assert.doesNotMatch(
    context.modelOptions(["<img src=x onerror=alert(1)>"], ""),
    /<img/,
    "provider-supplied model IDs must be escaped before entering the dropdown",
);

let discoveryRequest = null;
context.apiPostJson = async (url, body) => {
    discoveryRequest = { url, body };
    return { data: [{ id: "local/model-a" }, { id: "local/model-b" }] };
};
await context.loadModels();
assert.equal(discoveryRequest.url, "/api/ai/settings/models");
assert.equal(discoveryRequest.body.endpoint, "http://qwen.office/v1/chat/completions");
assert.equal("mode" in discoveryRequest.body, false,
    "model discovery must send only connection fields, not all AI settings");
assert.equal("model" in discoveryRequest.body, false);
assert.match(elements["ai-model"].innerHTML, /local\/model-a/);
assert.match(elements["ai-model"].innerHTML, /Qwen\/Qwen3\.8-27B \(current; not advertised\)/,
    "loading a new catalog must preserve a saved model not advertised by the endpoint");
assert.equal(elements["ai-models-status"].dataset.status, "warning");
assert.match(elements["ai-models-status"].textContent, /current selection is not advertised/);

elements["ai-model"].value = "__metronome_custom_model__";
elements["ai-model-custom"].value = "office/custom-model";
assert.equal(context.readPayload().model, "office/custom-model",
    "the custom fallback must submit the exact manually entered model ID");
elements["ai-model"].value = "Qwen/Qwen3.8-27B";

let finishCredentialLookup;
context.apiPostJson = () => new Promise(resolve => { finishCredentialLookup = resolve; });
elements["ai-endpoint"].value = "http://qwen.office/v1/chat/completions";
const credentialLookup = context.loadModels();
elements["ai-api-key"].value = "replacement-key";
context.invalidateModels();
finishCredentialLookup({ models: ["stale/credential-model"] });
await credentialLookup;
assert.doesNotMatch(elements["ai-model"].innerHTML, /stale\/credential-model/,
    "editing candidate credentials must invalidate an in-flight lookup");
assert.match(elements["ai-models-status"].textContent, /Connection details changed/);

let finishStaleLookup;
context.apiPostJson = () => new Promise(resolve => { finishStaleLookup = resolve; });
elements["ai-endpoint"].value = "http://first-provider.office/v1/chat/completions";
const staleLookup = context.loadModels();
elements["ai-endpoint"].value = "";
await context.loadModels();
finishStaleLookup({ models: ["stale/provider-model"] });
await staleLookup;
assert.doesNotMatch(elements["ai-model"].innerHTML, /stale\/provider-model/,
    "clearing the endpoint must invalidate an older in-flight model lookup");
assert.match(elements["ai-models-status"].textContent, /Enter the provider endpoint/);
assert.equal(elements["btn-refresh-ai-models"].disabled, false);

console.log("AI settings display tests passed");
