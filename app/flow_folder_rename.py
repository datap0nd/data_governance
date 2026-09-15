"""Rename owned folders with database rollback and interrupted-save recovery."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from app import flow_layout, flow_paths
from app.flow_execution_lock import ExecutionLocks


def relocated(value, old: Path, new: Path):
    """Replace complete path prefixes only; never alter names, URLs or log prose."""
    if isinstance(value, dict):
        return {key: relocated(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [relocated(item, old, new) for item in value]
    if isinstance(value, str):
        normalized = value.replace('\\', '/')
        prefix = str(old).replace('\\', '/').rstrip('/')
        compare = str.casefold if os.name == 'nt' else str
        if compare(normalized) == compare(prefix):
            return str(new)
        if compare(normalized).startswith(compare(prefix) + '/'):
            return str(new.joinpath(*normalized[len(prefix) + 1:].split('/')))
    return value


def _move(old: Path, new: Path):
    """Never replace an occupied directory, including a concurrent allocation."""
    flow_layout._regular(old)
    flow_layout._regular(new)
    if os.name == 'nt':
        old.rename(new)  # Windows rename fails if the destination exists.
    else:
        # Linux rename() can replace an empty user directory. NOREPLACE cannot.
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise OSError('This host cannot safely rename folders without replacing a destination.')
        if rename(-100, os.fsencode(old), -100, os.fsencode(new), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(new))


def recover(folder: Path, flow_id: int, root: str):
    """Reconcile a move interrupted before its SQLite commit on the next save."""
    flow_paths.assert_inside(str(folder), root, label='Flow folder')
    flow_layout._regular(folder.parent)
    flow_layout._regular(folder)
    if folder.exists():
        # pathlib equality is case-insensitive on Windows. Read the actual entry
        # spelling so an interrupted case-only rename can be rolled back too.
        candidates = [next((p for p in folder.parent.iterdir() if p == folder), folder)]
    else:
        candidates = list(folder.parent.iterdir())
    for candidate in candidates:
        try:
            manifest = flow_layout.read_manifest(candidate, flow_id)
        except (OSError, ValueError):
            continue
        pending = manifest.get('folder_rename') or {}
        if pending.get('from') != str(folder) and pending.get('to') != str(folder):
            continue
        if str(candidate) != str(folder):
            if pending.get('from') != str(folder) or pending.get('to') != str(candidate):
                continue
            flow_paths.assert_inside(str(candidate), root, label='Renamed flow folder')
            _move(candidate, folder)
        manifest.pop('folder_rename', None)
        flow_layout.write_manifest(folder, manifest)
        return


def _update_paths(db, flow_id: int, old: Path, new: Path):
    # Keep frozen run settings/evidence, changing only paths into the moved tree.
    tables = [
        ('flows', 'id=?', ('flow_folder', 'target_folder', 'transform_script_path', 'local_file_path')),
        ('flow_runs', 'flow_id=?', ('job_json', 'artifact_json', 'run_folder', 'folder_key')),
        ('flow_run_files', 'run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)',
         ('file_path', 'published_file_path')),
        ('flow_download_tasks', 'run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)',
         ('output_folder', 'artifact_json')),
        ('flow_retention_ops', 'source_run_id IN (SELECT id FROM flow_runs WHERE flow_id=?)',
         ('original_path', 'tombstone_path')),
    ]
    for table, where, columns in tables:
        for row in db.execute(f"SELECT id,{','.join(columns)} FROM {table} WHERE {where}", (flow_id,)).fetchall():
            changes = {}
            for column in columns:
                value = row[column]
                if value is None:
                    continue
                parsed = json.loads(value) if column.endswith('_json') else value
                changed = relocated(parsed, old, new)
                if changed != parsed:
                    changes[column] = json.dumps(changed) if column.endswith('_json') else changed
            if changes:
                if table == 'flow_runs' and 'run_folder' in changes:
                    from app.routers.flows import _folder_key
                    changes['folder_key'] = _folder_key(changes['run_folder'])
                db.execute(f"UPDATE {table} SET " + ','.join(f'{key}=?' for key in changes) + ' WHERE id=?',
                           (*changes.values(), row['id']))
    db.execute('UPDATE flows SET folder_slug=? WHERE id=?', (new.name, flow_id))


class FolderSave:
    def __init__(self, flow_id):
        self.flow_id = flow_id
        self.old = self.new = self.manifest = None
        self.moved = False

    def rename(self, db, name):
        row = db.execute('SELECT flow_folder FROM flows WHERE id=?', (self.flow_id,)).fetchone()
        if not row or not row['flow_folder']:
            return
        old = Path(row['flow_folder'])
        new = old.with_name(flow_layout.flow_folder_slug(name, self.flow_id))
        if str(old) == str(new):
            return
        root = flow_paths.get_flows_root(db)
        flow_paths.assert_inside(str(old), root, label='Flow folder')
        flow_paths.assert_inside(str(new), root, label='Renamed flow folder')
        manifest = flow_layout.read_manifest(old, self.flow_id)
        # BEGIN IMMEDIATE in save_transaction prevents a new run being queued
        # between this check and the committed path update.
        if db.execute("SELECT 1 FROM flow_runs WHERE flow_id=? AND status IN ('queued','claimed','running')",
                      (self.flow_id,)).fetchone():
            raise ValueError('Wait for the active Flow run to finish before renaming its folder.')
        for other in db.execute('SELECT id,local_file_path,transform_script_path,target_folder FROM flows WHERE id<>?', (self.flow_id,)):
            if any(relocated(other[key], old, new) != other[key] for key in ('local_file_path', 'transform_script_path', 'target_folder')):
                raise ValueError('Another Flow uses files in this folder. Update that Flow before renaming this folder.')
        for child in old.parent.iterdir():
            if child.name.casefold() == new.name.casefold() and child.name != old.name:
                raise FileExistsError('A folder with this name already exists. Choose a different flow name and save again.')
        downloads = old / 'Downloads'
        flow_layout._regular(downloads)
        if any(downloads.glob('.metronome-publish-*')):
            raise ValueError('Recover the interrupted output publication by running the Flow again before renaming its folder.')
        # Save the intent in the owned directory before moving it. A process
        # interruption leaves enough evidence to restore the database's path.
        flow_layout.write_manifest(old, {**manifest, 'folder_rename': {'from': str(old), 'to': str(new)}})
        self.old, self.new, self.manifest = old, new, manifest
        _move(old, new)
        self.moved = True
        _update_paths(db, self.flow_id, old, new)

    def rollback(self):
        if self.old is None:
            return
        # Case-only renames on Windows must restore the actual directory casing.
        if self.moved:
            flow_layout.read_manifest(self.new, self.flow_id)
            _move(self.new, self.old)
        flow_layout.write_manifest(self.old, self.manifest)

    def finish(self):
        if self.new is None:
            return
        try:
            manifest = flow_layout.read_manifest(self.new, self.flow_id)
            manifest.pop('folder_rename', None)
            flow_layout.write_manifest(self.new, manifest)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning('Flow folder rename marker cleanup deferred until next save.')


@contextmanager
def save_transaction(get_db, flow_id):
    from app.flow_handover import _LOCK
    save = FolderSave(flow_id)
    try:
        # Serialize generation with moving files; hold the process lock through
        # committed script refresh so an offline run cannot enter halfway through.
        with _LOCK, ExecutionLocks([f'flow:{flow_id}']):
            try:
                with get_db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT flow_folder FROM flows WHERE id=?', (flow_id,)).fetchone()
                    if row and row['flow_folder']:
                        recover(Path(row['flow_folder']), flow_id, flow_paths.get_flows_root(db))
                    yield db, save
            except Exception:
                save.rollback()
                raise
            save.finish()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, 'A flow with that name already exists.') from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(409, f'Could not save the flow folder: {exc}') from exc
