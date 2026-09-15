import { lstat, mkdir, realpath } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

// A fixed destination, not a model-supplied filesystem operation. Evidence and
// idempotency receipts deliberately stay in their existing durable stores.
export class Workspace {
  constructor(home = homedir()) { this.home = resolve(home); }
  paths() {
    const root = join(this.home, 'Metronome Gemini Work');
    return { root, working_directory: join(root, 'scratch'), reports_directory: join(root, 'reports'),
      cleanup: 'Close Gemini, then delete unwanted contents of scratch. Keep reports you need. Originals and MCP evidence/proposal receipts are separate. No automatic deletion.',
      file_rule: 'Put every generated script, temporary file, package environment and cache in working_directory; put finished reports, CSVs, reusable report code and source maps in reports_directory. Use absolute output paths and set the working directory for every shell invocation. Never write generated work in the Metronome checkout or source folders.' };
  }
  async prepare() {
    const paths = this.paths();
    // Refuse redirected workspace directories before making any children.
    for (const path of [paths.root, paths.working_directory, paths.reports_directory]) {
      try {
        const info = await lstat(path);
        if (info.isSymbolicLink() || !info.isDirectory()) throw new Error('Not a plain directory');
      } catch (error) {
        if (error.code !== 'ENOENT') throw new Error('Gemini work folder is blocked by a file or link. Preserve it and restore a normal folder before retrying.');
      }
    }
    const home = await realpath(this.home);
    for (const path of [paths.root, paths.working_directory, paths.reports_directory]) {
      await mkdir(path, { recursive: true });
      const expected = join(home, path.slice(this.home.length + 1));
      if (await realpath(path) !== expected) throw new Error('Gemini work folder was redirected. Stop and inspect the folder before retrying.');
    }
    return paths;
  }
}
