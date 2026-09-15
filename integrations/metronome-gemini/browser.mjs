// Microsoft Playwright MCP still supplies every browser operation. This adapter
// limits its tool surface and records real completed downloads for reconciliation.
import { createConnection } from '@playwright/mcp';
import { chromium } from 'playwright';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { mkdir, readFile, stat } from 'node:fs/promises';
import { join, extname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { Evidence, evidenceDirectory } from './evidence.mjs';
import { fileHash } from './verification.mjs';
import { accessSettings } from './settings.mjs';

export const BROWSER_TOOLS = ['browser_close', 'browser_resize', 'browser_handle_dialog', 'browser_find', 'browser_fill_form', 'browser_press_key', 'browser_type', 'browser_navigate', 'browser_navigate_back', 'browser_take_screenshot', 'browser_snapshot', 'browser_click', 'browser_drag', 'browser_hover', 'browser_select_option', 'browser_tabs', 'browser_wait_for'];
export async function createBrowserServer({ directory = evidenceDirectory(), launchOptions = { channel: 'msedge', headless: false }, metronomeUrl = accessSettings().baseUrl || 'http://127.0.0.1:8000' } = {}) {
  const evidence = new Evidence(null, null, directory), pending = new Set(), receipts = [];
  let browser, context;
  const contextGetter = async () => {
    if (context) return context;
    await mkdir(directory, { recursive: true });
    browser = await chromium.launch(launchOptions);
    context = await browser.newContext({ acceptDownloads: true, serviceWorkers: 'block' });
    context.on('close', () => { context = null; });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      // Source browsing cannot operate Metronome's web UI or API.
      const app = new URL(metronomeUrl);
      const loopback = host => ['localhost', '127.0.0.1', '[::1]'].includes(host);
      return url.origin === app.origin || loopback(url.hostname) && loopback(app.hostname) && url.port === app.port ? route.abort() : route.continue();
    });
    context.on('page', page => page.on('download', download => {
      const started_at = new Date().toISOString(), page_origin = new URL(page.url()).origin;
      const task = (async () => {
        const suffix = extname(download.suggestedFilename()).toLowerCase();
        if (!['.csv', '.xlsx'].includes(suffix)) { await download.cancel(); throw new Error('Only XLSX/CSV downloads can be verified.'); }
        const path = join(directory, randomUUID() + suffix);
        await download.saveAs(path);
        if ((await stat(path)).size > 20 * 1024 * 1024) throw new Error('Download exceeds verification bounds.');
        const bytes = await readFile(path);
        const id = await evidence.store({ kind: 'browser_download', status: 'completed', path, sha256: fileHash(bytes), started_at, completed_at: new Date().toISOString(), page_origin });
        receipts.push({ download_id: id, status: 'completed', path });
      })().catch(() => { receipts.push({ status: 'failed', next: 'Download failed or unsupported. Retry the source download; no comparison receipt was created.' }); });
      pending.add(task); task.finally(() => pending.delete(task));
    }));
    return context;
  };
  // contextGetter already creates a fresh, nonpersistent context. The public
  // MCP API's isolated=true would ask SimpleBrowser to create a second context.
  const official = await createConnection({ browser: { isolated: false }, saveSession: false, outputDir: join(directory, 'browser-output') }, contextGetter);
  const inner = new Client({ name: 'metronome-browser-receipts', version: '1' });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await official.connect(a); await inner.connect(b);
  const server = new Server({ name: 'report-browser', version: '0.2.0' }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: (await inner.listTools()).tools.filter(t => BROWSER_TOOLS.includes(t.name)) }));
  server.setRequestHandler(CallToolRequestSchema, async request => {
    if (!BROWSER_TOOLS.includes(request.params.name)) throw new Error('Browser tool is not exposed.');
    if (request.params.name === 'browser_navigate') {
      const url = new URL(request.params.arguments?.url);
      if (!['https:', 'http:'].includes(url.protocol) || url.origin === new URL(metronomeUrl).origin || url.username || url.password) throw new Error('Navigate to an independent source website. Metronome operations use its MCP API only.');
    }
    if (request.params.arguments?.filename) throw new Error('Custom browser output paths are not exposed. Use the generated screenshot artifact.');
    const result = await inner.callTool(request.params);
    // Do not block indefinitely on an interrupted download. A later snapshot
    // returns its receipt once saveAs completes; a pending transfer is no proof.
    await Promise.race([Promise.all([...pending]), new Promise(resolve => { const timer = setTimeout(resolve, 100); timer.unref(); })]);
    const completed = receipts.splice(0);
    return { ...result, content: [...result.content, ...(completed.length ? [{ type: 'text', text: JSON.stringify({ independent_downloads: completed }) }] : [])] };
  });
  let closing;
  const close = () => closing ||= (async () => { await inner.close(); await official.close(); await browser?.close(); await server.close(); })();
  return { server, close };
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const { server, close } = await createBrowserServer();
  server.onclose = () => { close().finally(() => process.exit()); };
  await server.connect(new StdioServerTransport());
}
