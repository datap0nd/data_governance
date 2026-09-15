"""Administrator-owned policy; no HTTP endpoint can change it."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReadRequest(Strict):
    dataset_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    run_id: int = Field(gt=0)
    source: Literal["download", "sql"]


class Column(Strict):
    name: str = Field(min_length=1, max_length=63)
    csv_header: str = Field(min_length=1, max_length=128)
    kind: Literal["text", "number", "date"] = "text"


class Dataset(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    flow_id: int = Field(gt=0)
    columns: tuple[Column, ...] = Field(min_length=1, max_length=12)
    schema_name: str = Field(default="", max_length=63)
    table_name: str = Field(default="", max_length=63)
    source_server: str = Field(default="", max_length=256)
    # Pin a reviewed catalog definition. An empty fingerprint disables SQL.
    fingerprint: str = Field(default="", pattern=r"^([a-f0-9]{64})?$")
    exclusive_replace_target: bool = False
    period_comparison: Literal["exact", "completed_windows"] = "exact"
    transformation_revision: str = Field(default="", max_length=128)

    @model_validator(mode="after")
    def unique_columns(self):
        if len({c.name for c in self.columns}) != len(self.columns):
            raise ValueError("Duplicate approved column")
        if len({c.csv_header for c in self.columns}) != len(self.columns):
            raise ValueError("Duplicate CSV header")
        return self


class Policy(Strict):
    manifest_db: str
    artifact_roots: tuple[str, ...] = Field(min_length=1, max_length=16)
    datasets: tuple[Dataset, ...] = Field(min_length=1, max_length=100)
    max_file_bytes: int = Field(default=536870912, ge=1024, le=2147483648)
    max_rows: int = Field(default=5000000, ge=1, le=20000000)

    @model_validator(mode="after")
    def unique_datasets(self):
        if len({d.id for d in self.datasets}) != len(self.datasets):
            raise ValueError("Duplicate dataset")
        return self

    @property
    def revision(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def dataset(self, dataset_id):
        for dataset in self.datasets:
            if dataset.id == dataset_id:
                return dataset
        raise InspectionError("dataset_not_approved")


class InspectionError(Exception):
    """Only static reason codes may cross the service boundary."""


def load_policy():
    path = os.environ.get("METRONOME_AUDIT_READER_POLICY", "")
    if not path:
        raise InspectionError("reader_not_configured")
    # JSON mode accepts JSON arrays as immutable tuples, unlike Python mode.
    return Policy.model_validate_json(Path(path).read_bytes())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True).encode()).hexdigest()
