"""Numerical evidence decides alerts. Model prose never becomes an assertion."""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.database import get_db
from . import store


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def weekday(evidence):
    return local_day(evidence).weekday()


def local_day(evidence):
    timestamp = datetime.fromisoformat(evidence["finished_at"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(ZoneInfo("Asia/Dubai")).date()


def baseline_key(evidence):
    return fingerprint([evidence["dataset_id"], evidence.get("dataset_revision", evidence.get("policy_revision")),
                        evidence["scope"], evidence["profile"]["schema"], weekday(evidence)])


def baseline(audit_id, current):
    if current.get("baseline_eligible") is False:
        return None
    key = baseline_key(current)
    with get_db() as db:
        db.execute("BEGIN IMMEDIATE")
        stored = db.execute("SELECT payload FROM auditor_baselines WHERE key=?", (key,)).fetchone()
        if stored:
            return json.loads(stored[0])
        candidates = []
        dates = set()
        for row in db.execute("SELECT payload FROM auditor_profiles WHERE dataset_id=? AND source='download' AND run_id<? ORDER BY run_id DESC LIMIT 200", (current["dataset_id"], current["run_id"])):
            item = json.loads(row[0])
            day = local_day(item)
            if baseline_key(item) == key and day not in dates and item["profile"]["complete"] and item.get("baseline_eligible") is not False:
                candidates.append(item)
                dates.add(day)
            if len(candidates) == 4:
                break
        if len(candidates) != 4:
            return None
        counts = [item["profile"]["rows"] for item in candidates]
        median = statistics.median(counts)
        # A volatile or already inconsistent history is not accepted as normal.
        if median <= 0 or any(abs(n - median) / median > 0.15 for n in counts):
            return None
        value = {"key": key, "observations": candidates, "rows": median}
        if store.active(db, audit_id):
            db.execute("INSERT OR IGNORE INTO auditor_baselines VALUES(?,?,?)", (key, json.dumps(value), store.now().isoformat()))
        return value


def finding(current, kind, detail, references, dimension=""):
    # Stable across reruns. Repeated unhealthy data cannot establish normality
    # or close an alert. Acknowledgement/resolution remains a human operation.
    revision = current.get("dataset_revision", current.get("policy_revision"))
    return {"fingerprint": fingerprint([current["dataset_id"], revision, current["scope"], kind, dimension]),
        "flow_id": current["flow_id"], "dataset_id": current["dataset_id"], "kind": kind,
        "message": f"Data auditor · {current['dataset_id']} · {detail} Cause unconfirmed.",
        "evidence": {"current": current, "references": references}}


def detect(current, reference, sql_evidence=None):
    results = []
    profile = current["profile"]
    if not profile["complete"]:
        return results
    if profile.get("missing_periods"):
        results.append(finding(current, "missing_periods", f"The completed artifact manifest is missing {profile['missing_periods']} planned reporting periods.", []))
    if sql_evidence and sql_evidence["sql_comparable"] and sql_evidence["scope"] == current["scope"] and sql_evidence["run_id"] == current["run_id"]:
        observed = sql_evidence["profile"]["rows"]
        if observed != profile["rows"]:
            results.append(finding(current, "insertion_count_mismatch", f"SQL contains {observed:,} rows; the complete final CSV bundle contains {profile['rows']:,}.", [sql_evidence]))
        for name, metric in profile["columns"].items():
            sql_metric = sql_evidence["profile"]["columns"].get(name, {})
            if metric.get("sum") is not None and sql_metric.get("sum") is not None and Decimal(metric["sum"]) != Decimal(sql_metric["sum"]):
                results.append(finding(current, "insertion_total_mismatch", f"SQL column {name} totals {sql_metric['sum']}; the final CSV bundle totals {metric['sum']}.", [sql_evidence], name))
    for name, metric in profile["columns"].items():
        if metric["invalid"]:
            results.append(finding(current, "invalid_values", f"Column {name} contains {metric['invalid']:,} values outside its approved format.", [], name))
    if reference is None:
        return results
    before = reference["rows"]
    change = (profile["rows"] - before) / before
    if change <= -0.30 or change >= 1:
        results.append(finding(current, "row_count_change", f"{profile['rows']:,} rows versus a stable baseline of {before:,.0f} ({change:+.1%}).", reference["observations"]))
    for name, metric in profile["columns"].items():
        historical = [item["profile"]["columns"].get(name) for item in reference["observations"]]
        if any(item is None for item in historical):
            continue
        old_null_rate = statistics.median(item["nulls"] / max(1, ref["profile"]["rows"]) for item, ref in zip(historical, reference["observations"]))
        null_rate = metric["nulls"] / max(1, profile["rows"])
        if null_rate - old_null_rate >= 0.05 and metric["nulls"] >= 10:
            results.append(finding(current, "null_rate_change", f"Column {name} has {null_rate:.1%} empty values; baseline {old_null_rate:.1%}.", reference["observations"], name))
        if metric["sum"] is not None and all(item["sum"] is not None for item in historical):
            old_sum = statistics.median(Decimal(item["sum"]) for item in historical)
            current_sum = Decimal(metric["sum"])
            if old_sum > 0 and current_sum <= old_sum * Decimal("0.70"):
                results.append(finding(current, "total_drop", f"Column {name} totals {current_sum} versus baseline {old_sum}.", reference["observations"], name))
        if metric["groups_complete"] and all(item["groups_complete"] for item in historical):
            common = set.intersection(*(set(item["groups"]) for item in historical))
            missing = common - set(metric["groups"])
            # Ignore tiny historical groups; labels stay pseudonymous.
            missing = {key for key in missing if statistics.median(item["groups"][key] for item in historical) >= 10}
            if missing:
                results.append(finding(current, "missing_groups", f"Column {name} is missing {len(missing)} categories present in all four baseline observations.", reference["observations"], name))
        if metric["distinct"] is not None and all(item["distinct"] == ref["profile"]["rows"] for item, ref in zip(historical, reference["observations"])):
            duplicates = profile["rows"] - metric["nulls"] - metric["distinct"]
            if duplicates >= max(10, profile["rows"] * 0.01):
                results.append(finding(current, "possible_duplicate_keys", f"Column {name} has {duplicates:,} repeated nonempty values; it was unique in all baseline observations.", reference["observations"], name))
        if metric.get("kind") == "date" and metric.get("max") and all(item.get("max") for item in historical):
            earliest = min(item["max"] for item in historical)
            if metric["max"] < earliest:
                results.append(finding(current, "date_regression", f"Latest date in {name} is {metric['max']}; it precedes all four baseline observations.", reference["observations"], name))
    return results
