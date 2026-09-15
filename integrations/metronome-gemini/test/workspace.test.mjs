import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm, readdir, mkdir, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { createServer } from '../server.mjs';
import { Workspace } from '../workspace.mjs';

async function fixture(t) {
  const home = await mkdtemp(join(tmpdir(), 'gemini-workspace-'));
  t.after(() => rm(home, { recursive: true, force: true }));
  return { home, workspace: new Workspace(home) };
}

test('MCP prepares fixed work paths; cleaning scratch preserves reports and source files', async t => {
  const { home, workspace } = await fixture(t);
  const source = join(home, 'fictional-source.csv');
  await writeFile(source, '법인,금액\n한국,7\n');
  const server = createServer({ workspace });
  const client = new Client({ name: 'workspace-fixture', version: '1' });
  const [a, b] = InMemoryTransport.createLinkedPair();
  await server.connect(a); await client.connect(b);
  t.after(async () => { await client.close(); await server.close(); });
  const unpack = result => { assert.ok(!result.isError, result.content[0].text); return JSON.parse(result.content[0].text); };
  const caps = unpack(await client.callTool({ name: 'capabilities', arguments: {} }));
  assert.deepEqual(await readdir(home), ['fictional-source.csv'], 'capabilities must be read-only');
  const paths = unpack(await client.callTool({ name: 'prepare_workspace', arguments: {} }));
  assert.deepEqual(paths, caps.workspace);
  assert.equal(paths.working_directory, join(home, 'Metronome Gemini Work', 'scratch'));
  await writeFile(join(paths.working_directory, 'analysis.py'), '# fictional script');
  await writeFile(join(paths.reports_directory, '출장.html'), '<p>한국</p>');
  const again = unpack(await client.callTool({ name: 'prepare_workspace', arguments: {} }));
  assert.deepEqual(again, paths);
  assert.equal(await readFile(join(paths.working_directory, 'analysis.py'), 'utf8'), '# fictional script');
  await rm(paths.working_directory, { recursive: true });
  unpack(await client.callTool({ name: 'prepare_workspace', arguments: {} }));
  assert.deepEqual(await readdir(paths.working_directory), []);
  assert.equal(await readFile(join(paths.reports_directory, '출장.html'), 'utf8'), '<p>한국</p>');
  assert.equal(await readFile(source, 'utf8'), '법인,금액\n한국,7\n');
});

test('blocked work folder fails without fallback or data loss, then recovers', async t => {
  const { workspace } = await fixture(t);
  const paths = workspace.paths();
  await mkdir(paths.root);
  await writeFile(paths.working_directory, 'preserve this fictional obstruction');
  await assert.rejects(workspace.prepare(), /blocked by a file or link/);
  assert.deepEqual(await readdir(paths.root), ['scratch']);
  assert.equal(await readFile(paths.working_directory, 'utf8'), 'preserve this fictional obstruction');
  await rm(paths.working_directory);
  await workspace.prepare();
  assert.deepEqual((await readdir(paths.root)).sort(), ['reports', 'scratch']);
});

test('redirected work root is rejected without creating anything in the target', async t => {
  const { home, workspace } = await fixture(t);
  const target = join(home, 'fictional-app');
  await mkdir(target);
  await symlink(target, workspace.paths().root, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(workspace.prepare(), /blocked by a file or link/);
  assert.deepEqual(await readdir(target), []);
});
