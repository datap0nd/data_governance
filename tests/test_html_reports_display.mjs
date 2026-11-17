// HTML report refresh Flows: the run log's stage table and the Flows builder pieces.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'}[char]));

// --- Run log -------------------------------------------------------------------
const logSource = fs.readFileSync(new URL('../app/static/flow_run_log.js', import.meta.url), 'utf8');
const logContext = {location: {pathname: '/flow-runs/7'}, document: {getElementById: () => null}};
vm.createContext(logContext);
vm.runInContext(logSource.slice(0, logSource.indexOf('function render(run)')), logContext);
const section = (report, run, now) => logContext.htmlReportSection(report, run, now);

assert.equal(section(null, {status: 'succeeded'}), '', 'A run that is not an HTML report refresh has no section');

const now = Date.parse('2026-11-17T08:05:00Z');
const running = section({
    report_key: 'smp_trade_in_value_html', publishes: true, notices: [],
    site_url: 'https://pages.example.com/smp/', repository_url: 'https://github.example.com/team/smp',
    stages: [
        {name: 'Extract <b>meto</b>', status: 'succeeded', started_at: '2026-11-17T08:00:00.000', finished_at: '2026-11-17T08:00:42.000', duration_seconds: 42.4},
        {name: 'Validate data', status: 'running', started_at: '2026-11-17T08:01:00.000', finished_at: null, duration_seconds: null},
    ],
    total_seconds: null,
}, {status: 'running', started_at: '2026-11-17T08:00:00'}, now);
assert.match(running, /Live · publishes to GitHub/);
assert.match(running, /Extract &lt;b&gt;meto&lt;\/b&gt;/, 'Stage names are untrusted and escaped');
assert.match(running, /42s/);
assert.match(running, /4m 0s so far/, 'A running stage counts from its start');
assert.match(running, /Total duration: <strong class="flow-log-number">5m 0s<\/strong> so far/);
assert.match(running, /Result: <strong>running<\/strong>, from the controller's exit code/);
assert.match(running, /href="https:\/\/pages\.example\.com\/smp\/"[^>]*>Published site/);

const stopped = section({
    report_key: 'r', publishes: false, total_seconds: 90,
    notices: [{level: 'failure', text: 'Every reported stage succeeded, but the controller was stopped. The run is not a success.'}],
    stages: [{name: 'Publish', status: 'incomplete', started_at: '2026-11-17T08:00:30.000', finished_at: '2026-11-17T08:01:30', duration_seconds: null, start_inferred: true}],
}, {status: 'cancelled'}, now);
assert.match(stopped, /Dry run · nothing is published/);
assert.match(stopped, /Did not finish/);
assert.match(stopped, /1m 0s</, 'An incomplete stage runs until the run ended');
assert.match(stopped, /from its duration/);
assert.match(stopped, /flow-log-notice failure/);
assert.doesNotMatch(stopped, /so far/);

const silent = section({report_key: 'r', publishes: true, stages: [], notices: [], total_seconds: 3}, {status: 'failed'}, now);
assert.match(silent, /No stages were reported\./);

// --- Flows builder and list ------------------------------------------------------
const appSource = fs.readFileSync(new URL('../app/static/app.js', import.meta.url), 'utf8');
const start = appSource.indexOf('// --- HTML report refresh Flows');
const end = appSource.indexOf('function _flowBuilderHtml(', start);
assert.ok(start > 0 && end > start, 'The HTML report block precedes the builder router');
const groupStart = appSource.indexOf('// HTML report refresh Flows in the list');
const groupEnd = appSource.indexOf('function _flowRowModel(', groupStart);
const appContext = {
    window: {_flowsState: {people: []}, confirm: message => { appContext.asked = message; return false; }},
    esc,
    _isHostPc: () => true,
    _FLOW_WEEKDAYS: ['monday', 'tuesday'],
    _flowTriggersHtml: () => '',
    _flowOwnerFieldHtml: () => '<label><select id="flow-owner"></select></label>',
};
vm.createContext(appContext);
vm.runInContext(`${appSource.slice(start, end)}\n${appSource.slice(groupStart, groupEnd)}`, appContext);

const settings = {
    script: 'D:\\Tools\\dashboard-refresh-controller\\refresh.py', report_root: 'D:\\Reports\\html sites',
    arguments: '--report {report} --report-root {report_root}', dry_run_arguments: '--dry-run', live_arguments: '--publish',
    allowed_reports: ['smp_trade_in_value_html', 'smp_stock_cover_html'], problems: [], ready: true,
    repository_url: 'https://github.example.com/team/dashboard-refresh-controller',
};
assert.equal(appContext._flowHtmlCommandPreview(settings, 'smp_stock_cover_html', 'dry_run'),
    'python refresh.py --report smp_stock_cover_html --report-root "D:\\Reports\\html sites" --dry-run');
assert.equal(appContext._flowHtmlCommandPreview(settings, 'smp_stock_cover_html', 'live'),
    'python refresh.py --report smp_stock_cover_html --report-root "D:\\Reports\\html sites" --publish');

assert.equal(appContext._flowHtmlCommandPreview({...settings, previews: {smp_stock_cover_html: {dry_run: '--report smp_stock_cover_html --root "D:\\Reports\\html sites" --dry-run', live: 'x'}}}, 'smp_stock_cover_html', 'dry_run'),
    'python refresh.py --report smp_stock_cover_html --root "D:\\Reports\\html sites" --dry-run', 'The server\'s exact command wins');
assert.equal(appContext._flowRunLabel({html_report: {mode: 'live'}}), 'Run · publishes');
assert.equal(appContext._flowRunLabel({html_report: {mode: 'dry_run'}}), 'Dry run');
assert.equal(appContext._flowRunLabel({source_type: 'python'}), 'Run');
assert.equal(appContext._flowSourceGroup({source_type: 'python', html_report: {mode: 'live'}}), 'HTML');
assert.equal(appContext._flowSourceGroup({source_type: 'python'}), 'Python');

assert.equal(appContext._flowConfirmHtmlRun({name: 'A', html_report: {mode: 'dry_run', report_key: 'r'}}, false), true);
assert.equal(appContext._flowConfirmHtmlRun({name: 'A', html_report: {mode: 'live', report_key: 'r'}}, true), true,
    'A dry run of a live Flow needs no confirmation');
assert.equal(appContext._flowConfirmHtmlRun({name: 'A', html_report: {mode: 'live', report_key: 'r'}}, false), false);
assert.match(appContext.asked, /publishes it to its existing GitHub repository/);
assert.match(appContext.asked, /does not mean GitHub Pages has finished deploying/);

appContext.window._flowsState.htmlReports = settings;
const builder = appContext._flowHtmlReportBuilderHtml({
    id: 4, name: 'Old <report>', schedule_type: 'manual', python_timeout_minutes: 30,
    html_report: {report_key: 'retired_html', mode: 'live', repository_url: 'https://github.example.com/team/x"onmouseover="y', site_url: ''},
});
assert.match(builder, /data-source-type="html"/);
assert.doesNotMatch(builder, /flow-python-script-path|flow-python-interpreter/, 'The form never offers a script or Python');
assert.match(builder, /<option value="retired_html" selected>retired_html \(no longer allowed\)<\/option>/);
assert.match(builder, /value="live" checked/);
assert.match(builder, /Old &lt;report&gt;/);
assert.match(builder, /x&quot;onmouseover=&quot;y/);
assert.match(builder, /never clones, pulls or runs code from GitHub, and holds no GitHub credentials/);

appContext.window._flowsState.htmlReports = {...settings, script: '', allowed_reports: [], ready: false,
    problems: ['Set the controller script on the worker in Settings > Flows > HTML reports.'], settings_place: 'Settings > Flows > HTML reports'};
const notReady = appContext._flowHtmlReportBuilderHtml({_source_type: 'html'});
assert.match(notReady, /Not ready to run/);
assert.match(notReady, /Set the controller script/);
assert.match(notReady, /id="flow-html-inspect" disabled/);
assert.match(notReady, /No report is allowed yet/);
assert.match(notReady, /value="dry_run" checked/, 'A new Flow starts as a dry run');

console.log('HTML report display tests passed');
