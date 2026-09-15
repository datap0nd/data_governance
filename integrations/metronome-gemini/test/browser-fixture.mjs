// Isolated standard-browser MCP process for synthetic tests only.
import { createBrowserServer } from '../browser.mjs';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
const adapter = await createBrowserServer({ directory: process.env.METRONOME_EVIDENCE_DIR,
  metronomeUrl: process.env.METRONOME_BASE_URL,
  launchOptions: process.platform === 'win32' ? { channel: 'msedge', headless: true } : { headless: true } });
adapter.server.onclose = () => adapter.close().finally(() => process.exit());
await adapter.server.connect(new StdioServerTransport());
