"""Private artifact storage and crash-recoverable direct Flow publication.

Direct-output Flows keep their immutable run artifacts under the browser
profile, then publish only the requested deliverables into the configured
target folder.  Publication uses same-directory temporary files and backups,
plus an ownership journal, so a normal failure restores the prior bundle and
an interrupted restore can be reconciled by the next serialized publisher.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import socket
from pathlib import Path
from typing import Iterable


TARGET_MARKER = ".metronome_target.json"
PUBLISH_PREFIX = ".metronome-publish-"
JOURNAL_SUFFIX = ".json"


def normalize_target_path(value: str | Path) -> str:
    """Canonical, filesystem-independent identity for a Windows/UNC target."""
    raw = str(value).strip().strip('"').replace("/", "\\")
    if not raw:
        return ""
    if raw.startswith("\\\\?\\UNC\\"):
        raw = "\\\\" + raw[8:]
    elif raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return ntpath.normcase(ntpath.normpath(raw)).rstrip("\\")


def artifact_store_id(profile_dir: Path) -> str:
    """Opaque identity shared by workers using one machine/profile store."""
    profile = os.path.normcase(os.path.abspath(str(profile_dir)))
    identity = f"{socket.gethostname().casefold()}|{profile}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def private_target_root(profile_dir: Path, target: Path) -> Path:
    """Return and ownership-check the private retention parent for a target."""
    target_key = normalize_target_path(target)
    target_hash = hashlib.sha256(target_key.encode("utf-8")).hexdigest()[:24]
    root = Path(profile_dir) / "run_artifacts" / target_hash
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or _is_junction(root) or not root.is_dir():
        raise RuntimeError(f"Private Flow artifact storage is not a regular folder: {root}")
    marker = root / TARGET_MARKER
    expected = {
        "schema_version": 1,
        "target_key": target_key,
        "target_hash": target_hash,
    }
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or _is_junction(marker) or not marker.is_file():
            raise RuntimeError(f"Private Flow artifact target marker is not a regular file: {marker}")
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Private Flow artifact target marker is unreadable: {marker}") from exc
        if current != expected:
            raise RuntimeError(
                "Private Flow artifact storage belongs to a different target; "
                f"refusing to reuse {root}."
            )
    else:
        existing = [item for item in root.iterdir() if item.name != TARGET_MARKER]
        if existing:
            raise RuntimeError(
                f"Private Flow artifact storage has no ownership marker and is not empty: {root}"
            )
        with marker.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(expected, sort_keys=True))
    return root


def read_size_checksum(path: Path) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"file_size": size, "checksum": digest.hexdigest()}


def artifact_file_valid(artifact: dict, *, require_deliverable: bool = False) -> bool:
    """Verify a private artifact descriptor before Resume or SQL reuse."""
    fields = [("file_path", "file_size", "checksum")]
    if require_deliverable:
        fields.append(
            ("deliverable_file_path", "deliverable_file_size", "deliverable_checksum")
        )
    for path_key, size_key, checksum_key in fields:
        raw_path = artifact.get(path_key)
        if not raw_path:
            return False
        path = Path(str(raw_path))
        try:
            if not path.is_file() or path.is_symlink() or _is_junction(path):
                return False
            observed = read_size_checksum(path)
        except OSError:
            return False
        expected_size = artifact.get(size_key)
        expected_checksum = artifact.get(checksum_key)
        if expected_size is not None and observed["file_size"] != expected_size:
            return False
        if expected_checksum and observed["checksum"] != expected_checksum:
            return False
    return True


def _is_junction(path: Path) -> bool:
    probe = getattr(path, "is_junction", None)
    return bool(probe()) if callable(probe) else False


def _path_present(path: Path) -> bool:
    """Existence check that does not overlook a broken symlink or junction."""
    return path.exists() or path.is_symlink() or _is_junction(path)


def _write_journal(path: Path, journal: dict) -> None:
    def verify_owned(candidate: Path) -> None:
        if candidate.is_symlink() or _is_junction(candidate) or not candidate.is_file():
            raise RuntimeError(f"Refusing to overwrite a non-regular publish journal: {candidate}")
        try:
            existing = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Publish journal staging file is unreadable: {candidate}") from exc
        identity = ("owner", "run_id", "target_key")
        if any(existing.get(key) != journal.get(key) for key in identity):
            raise RuntimeError(f"Publish journal staging file has different ownership: {candidate}")

    scratch = path.parent / f"{path.name}.tmp"
    if _path_present(scratch):
        verify_owned(scratch)
        scratch.unlink()
    if _path_present(path):
        verify_owned(path)
    with scratch.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(journal, sort_keys=True))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(scratch, path)


def _owned_child(target: Path, raw: str, expected_name: str) -> Path:
    candidate = Path(raw)
    if candidate.parent != target or candidate.name != expected_name:
        raise RuntimeError("A publish journal names a path outside its target folder.")
    return candidate


def _load_journal(
    target: Path, journal_path: Path, *, expected_journal_name: str | None = None,
) -> dict:
    if journal_path.is_symlink() or _is_junction(journal_path) or not journal_path.is_file():
        raise RuntimeError(f"Publish journal is not a regular file: {journal_path}")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Metronome publish journal is unreadable: {journal_path}") from exc
    if not isinstance(journal, dict) or journal.get("owner") != "metronome-flow-publish-v1":
        raise RuntimeError(f"Publish journal does not contain a Metronome ownership marker: {journal_path}")
    if journal.get("schema_version") != 1:
        raise RuntimeError(f"Publish journal has an unsupported schema version: {journal_path}")
    if journal.get("state") not in {"preparing", "prepared", "committed"}:
        raise RuntimeError(f"Publish journal has an invalid transaction state: {journal_path}")
    if journal.get("target_key") != normalize_target_path(target):
        raise RuntimeError(f"Publish journal belongs to a different target: {journal_path}")
    run_id = journal.get("run_id")
    if not isinstance(run_id, int) or run_id <= 0:
        raise RuntimeError(f"Publish journal has an invalid run id: {journal_path}")
    expected_name = expected_journal_name or journal_path.name
    if expected_name != f"{PUBLISH_PREFIX}{run_id}{JOURNAL_SUFFIX}":
        raise RuntimeError(f"Publish journal filename does not match its run id: {journal_path}")
    entries = journal.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError(f"Publish journal has no entries: {journal_path}")
    filenames = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise RuntimeError(f"Publish journal entry {index} is invalid: {journal_path}")
        filename = str(entry.get("filename") or "")
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            raise RuntimeError(f"Publish journal entry {index} has an unsafe filename: {journal_path}")
        filename_key = ntpath.normcase(filename)
        if filename_key in filenames:
            raise RuntimeError(f"Publish journal repeats destination {filename}: {journal_path}")
        filenames.add(filename_key)
        checksum = entry.get("new_checksum")
        size = entry.get("new_file_size")
        if (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(checksum, str)
            or len(checksum) != 64
            or any(character not in "0123456789abcdef" for character in checksum.casefold())
        ):
            raise RuntimeError(f"Publish journal entry {index} lacks verified file metadata: {journal_path}")
        for field in ("had_destination", "backed_up", "installed"):
            if not isinstance(entry.get(field), bool):
                raise RuntimeError(f"Publish journal entry {index} has an invalid {field}: {journal_path}")
        _owned_child(target, str(entry.get("destination") or ""), filename)
        _owned_child(
            target,
            str(entry.get("temporary") or ""),
            f"{PUBLISH_PREFIX}{run_id}-{index}.tmp",
        )
        _owned_child(
            target,
            str(entry.get("backup") or ""),
            f"{PUBLISH_PREFIX}{run_id}-{index}.bak",
        )
    return journal


def _safe_unlink_owned(path: Path) -> None:
    if path.is_symlink() or _is_junction(path) or (path.exists() and not path.is_file()):
        raise RuntimeError(f"Refusing to remove a non-regular Metronome publish file: {path}")
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _matches(path: Path, size: int | None, checksum: str | None) -> bool:
    try:
        if not path.is_file() or path.is_symlink() or _is_junction(path):
            return False
        observed = read_size_checksum(path)
    except OSError:
        return False
    return (
        (size is None or observed["file_size"] == size)
        and (not checksum or observed["checksum"] == checksum)
    )


def reconcile_journal(target: Path, journal_path: Path) -> dict:
    """Finish cleanup for a committed journal or roll an incomplete one back."""
    journal = _load_journal(target, journal_path)
    entries = journal["entries"]
    if journal.get("state") == "committed":
        for entry in entries:
            _safe_unlink_owned(Path(entry["temporary"]))
            _safe_unlink_owned(Path(entry["backup"]))
        _safe_unlink_owned(journal_path)
        return {"run_id": journal["run_id"], "outcome": "committed_cleanup"}

    failures = []
    for entry in reversed(entries):
        destination = Path(entry["destination"])
        backup = Path(entry["backup"])
        temporary = Path(entry["temporary"])
        try:
            if _path_present(backup):
                if backup.is_symlink() or _is_junction(backup) or not backup.is_file():
                    raise RuntimeError(f"publish backup is not a regular file: {backup}")
                if _path_present(destination):
                    if not _matches(
                        destination,
                        entry.get("new_file_size"),
                        entry.get("new_checksum"),
                    ):
                        raise RuntimeError(
                            f"published destination changed after interruption: {destination}"
                        )
                    _safe_unlink_owned(destination)
                os.replace(backup, destination)
            elif not entry.get("had_destination") and _path_present(destination):
                if not _matches(
                    destination,
                    entry.get("new_file_size"),
                    entry.get("new_checksum"),
                ):
                    raise RuntimeError(
                        f"new destination changed after interruption: {destination}"
                    )
                _safe_unlink_owned(destination)
            _safe_unlink_owned(temporary)
        except OSError as exc:
            failures.append(f"{destination.name}: {exc}")
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError(
            "The previous direct-file publish could not be restored. Its backups and "
            f"journal were retained for the next reconciliation: {'; '.join(failures)}"
        )
    _safe_unlink_owned(journal_path)
    return {"run_id": journal["run_id"], "outcome": "rolled_back"}


def reconcile_target(target: Path) -> list[dict]:
    """Reconcile every verified journal while the server holds the folder lock."""
    results = []
    # A crash can happen after a fully fsynced journal update is written but
    # before its final atomic rename. That scratch is the newest transaction
    # state, so verify it structurally and promote it before reconciliation.
    for scratch in sorted(target.glob(f"{PUBLISH_PREFIX}*{JOURNAL_SUFFIX}.tmp")):
        journal_path = target / scratch.name.removesuffix(".tmp")
        scratch_journal = _load_journal(
            target, scratch, expected_journal_name=journal_path.name,
        )
        if _path_present(journal_path):
            current = _load_journal(target, journal_path)
            if (
                current.get("run_id") != scratch_journal.get("run_id")
                or current.get("target_key") != scratch_journal.get("target_key")
            ):
                raise RuntimeError(f"Publish journal scratch ownership is ambiguous: {scratch}")
        os.replace(scratch, journal_path)
    for journal_path in sorted(target.glob(f"{PUBLISH_PREFIX}*{JOURNAL_SUFFIX}")):
        results.append(reconcile_journal(target, journal_path))
    return results


def _copy_verified(source: Path, destination: Path, expected: dict) -> None:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_handle, destination.open("xb") as output_handle:
        for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
            output_handle.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    checksum = digest.hexdigest()
    if size != expected["file_size"] or checksum != expected["checksum"]:
        raise RuntimeError(f"Private deliverable changed while staging {destination.name}.")
    observed = read_size_checksum(destination)
    if observed != expected:
        raise RuntimeError(f"Published temporary file failed read-back verification: {destination}")


def publish_bundle(target: Path, run_id: int, artifacts: Iterable[dict]) -> list[dict]:
    """Atomically replace one validated direct-output bundle with rollback."""
    target = Path(target)
    if not target.is_dir():
        raise RuntimeError(f"Target folder does not exist: {target}")
    reconcile_target(target)
    candidates = list(artifacts)
    if not candidates:
        raise RuntimeError("Direct publication has no validated deliverables.")
    entries = []
    seen = set()
    for index, artifact in enumerate(candidates, start=1):
        source = Path(str(artifact.get("deliverable_file_path") or ""))
        filename = str(artifact.get("deliverable_filename") or source.name)
        if not filename or Path(filename).name != filename or filename in {".", ".."}:
            raise RuntimeError(f"Direct publication has an unsafe filename: {filename!r}")
        filename_key = ntpath.normcase(filename)
        if filename_key in seen:
            raise RuntimeError(f"Direct publication resolves more than one deliverable to {filename}.")
        seen.add(filename_key)
        expected = {
            "file_size": artifact.get("deliverable_file_size"),
            "checksum": artifact.get("deliverable_checksum"),
        }
        if expected["file_size"] is None or not expected["checksum"]:
            raise RuntimeError(f"Direct publication lacks verified metadata for {filename}.")
        if not artifact_file_valid(artifact, require_deliverable=True):
            raise RuntimeError(f"Private deliverable is missing or changed: {source}")
        destination = target / filename
        destination_present = _path_present(destination)
        if destination_present and (
            destination.is_symlink() or _is_junction(destination) or not destination.is_file()
        ):
            raise RuntimeError(f"Direct output destination is not a regular file: {destination}")
        entries.append({
            "filename": filename,
            "source": str(source),
            "destination": str(destination),
            "temporary": str(target / f"{PUBLISH_PREFIX}{run_id}-{index}.tmp"),
            "backup": str(target / f"{PUBLISH_PREFIX}{run_id}-{index}.bak"),
            "had_destination": destination_present,
            "backed_up": False,
            "installed": False,
            "new_file_size": expected["file_size"],
            "new_checksum": expected["checksum"],
        })
    journal_path = target / f"{PUBLISH_PREFIX}{run_id}{JOURNAL_SUFFIX}"
    if _path_present(journal_path):
        reconcile_journal(target, journal_path)
    journal = {
        "owner": "metronome-flow-publish-v1",
        "schema_version": 1,
        "run_id": run_id,
        "target_key": normalize_target_path(target),
        "state": "preparing",
        "entries": entries,
    }
    for entry in entries:
        temporary = Path(entry["temporary"])
        backup = Path(entry["backup"])
        if _path_present(temporary) or _path_present(backup):
            raise RuntimeError(
                "A Metronome-named publish staging path already exists without a valid "
                f"ownership journal; nothing was removed: {temporary}"
            )
    _write_journal(journal_path, journal)
    try:
        for entry in entries:
            temporary = Path(entry["temporary"])
            backup = Path(entry["backup"])
            if _path_present(temporary) or _path_present(backup):
                raise RuntimeError(
                    f"Owned publish staging path already exists without a recoverable journal: {temporary}"
                )
            _copy_verified(
                Path(entry["source"]),
                temporary,
                {"file_size": entry["new_file_size"], "checksum": entry["new_checksum"]},
            )
        journal["state"] = "prepared"
        _write_journal(journal_path, journal)
        for entry in entries:
            destination = Path(entry["destination"])
            backup = Path(entry["backup"])
            temporary = Path(entry["temporary"])
            if _path_present(destination):
                if destination.is_symlink() or _is_junction(destination) or not destination.is_file():
                    raise RuntimeError(f"Direct output destination changed type: {destination}")
                os.replace(destination, backup)
                entry["backed_up"] = True
                _write_journal(journal_path, journal)
            os.replace(temporary, destination)
            entry["installed"] = True
            _write_journal(journal_path, journal)
        journal["state"] = "committed"
        _write_journal(journal_path, journal)
        results = [
            {
                "published_file_path": entry["destination"],
                "published_filename": entry["filename"],
                "published_file_size": entry["new_file_size"],
                "published_checksum": entry["new_checksum"],
                "publish_status": "published",
            }
            for entry in entries
        ]
        reconcile_journal(target, journal_path)
        return results
    except Exception as exc:
        try:
            reconcile_journal(target, journal_path)
        except Exception as rollback_exc:
            raise RuntimeError(f"Direct publication failed: {exc} Rollback also failed: {rollback_exc}") from exc
        raise RuntimeError(f"Direct publication failed and the previous bundle was restored: {exc}") from exc
