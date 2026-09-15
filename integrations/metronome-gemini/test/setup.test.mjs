import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, mkdir, writeFile, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { accessSettings, sqlSettings } from '../settings.mjs';

test('setup asks three separate fields and preserves registration/scopes through recovery', () => {
  const output = execFileSync('pwsh', ['-NoProfile', '-File', fileURLToPath(new URL('setup-fixture.ps1', import.meta.url)),
    '-ExtensionRoot', fileURLToPath(new URL('../', import.meta.url))], { encoding: 'utf8' });
  assert.match(output, /PASS: 16 isolated setup journeys/);
});

test('setup field definitions mask only the password and remove manual URI/table configuration', async () => {
  const manifest = JSON.parse(await readFile(new URL('../gemini-extension.json', import.meta.url), 'utf8'));
  assert.deepEqual(manifest.settings.map(s => s.envVar), ['METRONOME_SQL_HOST', 'METRONOME_SQL_USER', 'METRONOME_SQL_PASSWORD']);
  assert.equal(manifest.settings[2].sensitive, true);
  assert.ok(!manifest.settings[0].sensitive && !manifest.settings[1].sensitive);
});

test('saved access restrictions survive Windows BOM; malformed settings fail closed', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'gemini-access-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const path = join(dir, 'access.json');
  await writeFile(path, '\ufeff' + JSON.stringify({ METRONOME_FLOW_IDS: '42', METRONOME_SITE_IDS: '1' }));
  assert.deepEqual(accessSettings(path), { baseUrl: undefined, flowIds: '42', siteIds: '1' });
  await writeFile(path, '{bad');
  assert.throws(() => accessSettings(path), /restrictions were not discarded/);
});

test('individual credentials override legacy DSN without trimming the password', () => {
  const values = { METRONOME_SQL_HOST: ' fixture-server ', METRONOME_SQL_USER: ' reader ', METRONOME_SQL_PASSWORD: '${literal} password ',
    METRONOME_READONLY_DSN: 'postgresql://legacy@localhost/old' };
  const before = Object.fromEntries(Object.keys(values).map(k => [k, process.env[k]]));
  try {
    Object.assign(process.env, values);
    assert.deepEqual(sqlSettings(), { host: 'fixture-server', user: 'reader', password: '${literal} password ', database: 'postgres' });
  } finally { for (const [k, v] of Object.entries(before)) if (v === undefined) delete process.env[k]; else process.env[k] = v; }
});

test('custom Gemini home reads the same saved access restrictions as the installer', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'gemini-home-'));
  const keys = ['GEMINI_CLI_HOME', 'METRONOME_BASE_URL', 'METRONOME_FLOW_IDS', 'METRONOME_SITE_IDS'];
  const previous = Object.fromEntries(keys.map(k => [k, process.env[k]]));
  t.after(async () => {
    for (const [k, v] of Object.entries(previous)) if (v === undefined) delete process.env[k]; else process.env[k] = v;
    await rm(dir, { recursive: true, force: true });
  });
  for (const key of keys) delete process.env[key];
  process.env.GEMINI_CLI_HOME = dir;
  const saved = join(dir, '.gemini', 'metronome');
  await mkdir(saved, { recursive: true });
  await writeFile(join(saved, 'access.json'), JSON.stringify({ METRONOME_FLOW_IDS: '42', METRONOME_SITE_IDS: '1' }));
  assert.deepEqual(accessSettings(), { baseUrl: undefined, flowIds: '42', siteIds: '1' });
});
