"""Bounded CSV inspection. Reads existing handles; never executes workbook code."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import os
import stat
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from .policy import InspectionError, digest


def category(value: str, salt: bytes):
    # Approved aggregate output never contains raw category values.
    return hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()[:24]


def _safe_path(path, roots):
    candidate = Path(os.path.abspath(path))
    if not any(candidate.is_relative_to(Path(os.path.abspath(root))) for root in roots):
        raise InspectionError("artifact_outside_approved_roots")
    for part in (candidate, *candidate.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InspectionError("artifact_link_rejected")
    if not candidate.is_file() or candidate.suffix.lower() != ".csv":
        raise InspectionError("normalized_csv_required")
    return candidate


def _handle_path(handle):
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        func = ctypes.WinDLL("kernel32", use_last_error=True).GetFinalPathNameByHandleW
        func.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        func.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        size = func(msvcrt.get_osfhandle(handle.fileno()), buffer, len(buffer), 0)
        if not size or size >= len(buffer):
            raise InspectionError("artifact_handle_unverified")
        value = buffer.value
        if value.startswith("\\\\?\\UNC\\"):
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\"):
            value = value[4:]
        return Path(value)
    link = Path(f"/proc/self/fd/{handle.fileno()}")
    if link.exists():
        return Path(os.readlink(link))
    raise InspectionError("artifact_handle_unverified")


class HashingReader(io.RawIOBase):
    """Hash exactly the bytes consumed by CSV, including embedded newlines."""
    def __init__(self, raw, cancelled):
        self.raw, self.cancelled = raw, cancelled
        self.hasher = hashlib.sha256()

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.cancelled():
            raise InspectionError("cancelled")
        size = self.raw.readinto(buffer)
        if size:
            self.hasher.update(memoryview(buffer)[:size])
        return size


def profile_csv(artifacts, dataset, policy, salt, cancelled):
    if not artifacts or len(artifacts) > 64:
        raise InspectionError("artifact_bundle_unverified")
    stats = {c.name: {"nulls": 0, "invalid": 0, "sum": Decimal(0), "min": None,
                      "max": None, "distinct": set(), "groups": Counter(), "groups_complete": True}
             for c in dataset.columns}
    rows = total_bytes = 0
    headers = None
    identities = []
    seen_files = set()
    for artifact in artifacts:
        if cancelled():
            raise InspectionError("cancelled")
        checksum = artifact.get("checksum", "")
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise InspectionError("artifact_checksum_missing")
        path = _safe_path(artifact.get("file_path", ""), policy.artifact_roots)
        with path.open("rb") as raw:
            actual = _handle_path(raw)
            _safe_path(actual, policy.artifact_roots)
            if str(actual) in seen_files:
                raise InspectionError("duplicate_artifact_manifest_entry")
            seen_files.add(str(actual))
            before = os.fstat(raw.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise InspectionError("artifact_link_rejected")
            total_bytes += before.st_size
            if total_bytes > policy.max_file_bytes:
                raise InspectionError("artifact_byte_budget")
            hashed = HashingReader(raw, cancelled)
            with io.TextIOWrapper(io.BufferedReader(hashed), encoding="utf-8-sig", newline="") as stream:
                reader = csv.reader(stream, strict=True)
                header = next(reader, None)
                if not header or len(header) != len(set(header)) or len(header) > 256:
                    raise InspectionError("artifact_header_unverified")
                if headers is not None and header != headers:
                    raise InspectionError("artifact_schema_mismatch")
                headers = header
                try:
                    indices = {c.name: header.index(c.csv_header) for c in dataset.columns}
                except ValueError:
                    raise InspectionError("approved_column_missing") from None
                for row in reader:
                    rows += 1
                    if rows > policy.max_rows:
                        raise InspectionError("artifact_row_budget")
                    if len(row) != len(header):
                        raise InspectionError("artifact_ragged_rows")
                    if rows % 1024 == 0 and cancelled():
                        raise InspectionError("cancelled")
                    for column in dataset.columns:
                        value = row[indices[column.name]]
                        metric = stats[column.name]
                        if value == "":
                            metric["nulls"] += 1
                            continue
                        token = category(value, salt)
                        if metric["distinct"] is not None:
                            metric["distinct"].add(token)
                            if len(metric["distinct"]) > 100000:
                                metric["distinct"] = None
                        if token in metric["groups"] or len(metric["groups"]) < 256:
                            metric["groups"][token] += 1
                        else:
                            metric["groups_complete"] = False
                        if column.kind == "text":
                            continue
                        try:
                            if len(value) > 64:
                                raise ValueError()
                            parsed = Decimal(value) if column.kind == "number" else date.fromisoformat(value)
                            if column.kind == "number":
                                if not parsed.is_finite() or abs(parsed.adjusted()) > 30:
                                    raise ValueError()
                                with localcontext() as decimal_context:
                                    decimal_context.prec = 100
                                    metric["sum"] += parsed
                            metric["min"] = min(metric["min"], parsed) if metric["min"] is not None else parsed
                            metric["max"] = max(metric["max"], parsed) if metric["max"] is not None else parsed
                        except (ValueError, InvalidOperation, OverflowError):
                            metric["invalid"] += 1
                after = os.fstat(raw.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise InspectionError("artifact_changed_during_read")
                if not hmac.compare_digest(hashed.hasher.hexdigest(), checksum):
                    raise InspectionError("artifact_checksum_changed")
        identities.append(checksum)
    output = {}
    for column in dataset.columns:
        item = stats[column.name]
        output[column.name] = {
            "kind": column.kind,
            "nulls": item["nulls"], "invalid": item["invalid"],
            "distinct": len(item["distinct"]) if item["distinct"] is not None else None,
            "sum": str(item["sum"]) if column.kind == "number" and not item["invalid"] else None,
            "min": str(item["min"]) if item["min"] is not None else None,
            "max": str(item["max"]) if item["max"] is not None else None,
            "groups": dict(item["groups"]) if item["groups_complete"] else {},
            "groups_complete": item["groups_complete"],
        }
    return {"rows": rows, "columns": output, "schema": digest(headers),
            "identity": digest(identities), "complete": True}
