import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';

export const setting = name => {
  const value = process.env[name];
  return value && value !== '${' + name + '}' ? value : undefined;
};

export function accessSettings(path = join(homedir(), '.gemini', 'metronome', 'access.json')) {
  try {
    const saved = JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));
    return { baseUrl: setting('METRONOME_BASE_URL') || saved.METRONOME_BASE_URL,
      flowIds: setting('METRONOME_FLOW_IDS') || saved.METRONOME_FLOW_IDS || '*',
      siteIds: setting('METRONOME_SITE_IDS') || saved.METRONOME_SITE_IDS || '*' };
  } catch (error) {
    if (error.code !== 'ENOENT') throw new Error('Saved Metronome access settings cannot be read. Rerun setup; existing restrictions were not discarded.');
    return { baseUrl: setting('METRONOME_BASE_URL'), flowIds: setting('METRONOME_FLOW_IDS') || '*', siteIds: setting('METRONOME_SITE_IDS') || '*' };
  }
}

export function sqlSettings() {
  // An explicitly supplied legacy DSN remains supported for existing integrations.
  // Individual fields take precedence once the new setup has been completed.
  const host = setting('METRONOME_SQL_HOST')?.trim();
  return host ? { host, user: setting('METRONOME_SQL_USER')?.trim() || '', password: setting('METRONOME_SQL_PASSWORD') || '',
    database: setting('METRONOME_SQL_DATABASE') || 'postgres' }
    : { dsn: setting('METRONOME_READONLY_DSN') || '' };
}
