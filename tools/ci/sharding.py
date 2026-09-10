"""Create and reconcile deterministic file-level pytest shards."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


SHARD_COUNT = 6


class ShardError(ValueError):
    pass


def inventory(root: Path) -> list[str]:
    files = sorted(path.relative_to(root).as_posix() for path in (root / "tests").glob("test_*.py"))
    if not files:
        raise ShardError("pytest inventory is empty")
    return files


def partition(files: list[str], weights: dict[str, float], default: float, count: int = SHARD_COUNT) -> list[list[str]]:
    if len(files) < count:
        raise ShardError(f"{len(files)} files cannot fill {count} shards")
    groups: list[list[str]] = [[] for _ in range(count)]
    totals = [0.0] * count
    ordered = sorted(files, key=lambda path: (-float(weights.get(path, default)), path))
    for path in ordered:
        target = min(range(count), key=lambda index: (totals[index], index))
        groups[target].append(path)
        totals[target] += float(weights.get(path, default))
    return [sorted(group) for group in groups]


def build_plan(root: Path, os_keys: list[str], files: list[str] | None = None) -> dict:
    files = inventory(root) if files is None else sorted(files)
    assignments: dict[str, dict[str, list[str]]] = {}
    estimates: dict[str, dict[str, float]] = {}
    for os_key in os_keys:
        history = json.loads((root / "ci" / "test-durations" / f"{os_key}.json").read_text(encoding="utf-8"))
        groups = partition(files, history["files"], float(history["default_seconds"]))
        assignments[os_key] = {str(index + 1): group for index, group in enumerate(groups)}
        estimates[os_key] = {
            str(index + 1): round(sum(float(history["files"].get(path, history["default_seconds"])) for path in group), 3)
            for index, group in enumerate(groups)
        }
    plan = {"schema_version": 1, "inventory": files, "assignments": assignments, "estimated_seconds": estimates}
    validate_plan(plan)
    return plan


def validate_plan(plan: dict) -> None:
    expected = plan.get("inventory", [])
    if not expected:
        raise ShardError("plan inventory is empty")
    for os_key, groups in plan.get("assignments", {}).items():
        if sorted(groups) != [str(index) for index in range(1, SHARD_COUNT + 1)]:
            raise ShardError(f"{os_key} does not define exactly {SHARD_COUNT} shards")
        flattened = [path for group in groups.values() for path in group]
        duplicates = sorted(path for path, count in Counter(flattened).items() if count != 1)
        if duplicates or sorted(flattened) != sorted(expected):
            raise ShardError(f"{os_key} assignment is not an exact-once inventory partition: {duplicates}")
        if any(not group for group in groups.values()):
            raise ShardError(f"{os_key} has an unexpected empty shard")


def _load_result_files(results_root: Path, prefix: str) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(results_root.glob(f"{prefix}*/result.json"))]


def reconcile_os(plan: dict, os_key: str, results: list[dict], serial: dict | None = None) -> dict:
    groups = plan["assignments"][os_key]
    if len(results) != SHARD_COUNT:
        raise ShardError(f"{os_key} has {len(results)} result manifests, expected {SHARD_COUNT}")
    by_shard = {str(result.get("shard")): result for result in results}
    if sorted(by_shard) != sorted(groups):
        raise ShardError(f"{os_key} result manifests do not cover every shard exactly once")
    parallel_outcomes: dict[str, str] = {}
    collected_files: list[str] = []
    for shard, assigned in groups.items():
        result = by_shard[shard]
        if result.get("exit_code") != 0:
            raise ShardError(f"{os_key} shard {shard} failed with exit code {result.get('exit_code')}")
        if result.get("assigned") != assigned:
            raise ShardError(f"{os_key} shard {shard} ran a stale or altered assignment")
        manifest = result.get("pytest", {})
        if manifest.get("collection_errors"):
            raise ShardError(f"{os_key} shard {shard} had collection errors")
        collected_files.extend(manifest.get("collected_files", []))
        for nodeid, outcome in manifest.get("outcomes", {}).items():
            if nodeid in parallel_outcomes:
                raise ShardError(f"{os_key} duplicate test result: {nodeid}")
            parallel_outcomes[nodeid] = outcome
    if sorted(collected_files) != sorted(plan["inventory"]):
        raise ShardError(f"{os_key} collected-file union does not match inventory exactly once")
    if serial is not None:
        if serial.get("exit_code") != 0:
            raise ShardError(f"{os_key} serial baseline failed")
        serial_pytest = serial.get("pytest", {})
        if sorted(serial_pytest.get("collected_files", [])) != sorted(plan["inventory"]):
            raise ShardError(f"{os_key} serial collection differs from inventory")
        if serial_pytest.get("outcomes") != parallel_outcomes:
            raise ShardError(f"{os_key} parallel outcomes differ from the serial baseline")
    return {
        "os": os_key,
        "files": len(collected_files),
        "tests": len(parallel_outcomes),
        "skipped": sum(outcome == "skipped" for outcome in parallel_outcomes.values()),
        "serial_compared": serial is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("inventory")
    discover.add_argument("--root", type=Path, default=Path.cwd())
    discover.add_argument("--output", type=Path, required=True)
    create = sub.add_parser("plan")
    create.add_argument("--root", type=Path, default=Path.cwd())
    create.add_argument("--os", action="append", dest="os_keys", required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--inventory", type=Path)
    create.add_argument("--github-output", type=Path)
    select = sub.add_parser("select")
    select.add_argument("--plan", type=Path, required=True)
    select.add_argument("--os", required=True)
    select.add_argument("--shard", required=True)
    select.add_argument("--output", type=Path, required=True)
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--plan", type=Path, required=True)
    reconcile.add_argument("--results-root", type=Path, required=True)
    reconcile.add_argument("--serial-root", type=Path)
    reconcile.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.command == "inventory":
        files = inventory(args.root.resolve())
        args.output.write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")
        print(f"Discovered {len(files)} Python test files independently.")
    elif args.command == "plan":
        files = json.loads(args.inventory.read_text(encoding="utf-8")) if args.inventory else None
        plan = build_plan(args.root.resolve(), args.os_keys, files)
        args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        matrix = {
            "include": [
                {"runner": f"{os_key}-latest", "os_key": os_key, "shard": shard}
                for os_key in args.os_keys
                for shard in range(1, SHARD_COUNT + 1)
            ]
        }
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as output:
                output.write("matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n")
                os_matrix = {"include": [{"runner": f"{os_key}-latest", "os_key": os_key} for os_key in args.os_keys]}
                output.write("os_matrix=" + json.dumps(os_matrix, separators=(",", ":")) + "\n")
        print(json.dumps(matrix))
    elif args.command == "select":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        validate_plan(plan)
        files = plan["inventory"] if args.shard == "serial" else plan["assignments"][args.os][str(args.shard)]
        args.output.write_text(json.dumps(files) + "\n", encoding="utf-8")
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        validate_plan(plan)
        summaries = []
        for os_key in plan["assignments"]:
            results = _load_result_files(args.results_root, f"python-{os_key}-")
            serial = None
            if args.serial_root:
                serial_files = list(args.serial_root.glob(f"serial-{os_key}*/result.json"))
                if serial_files:
                    serial = json.loads(serial_files[0].read_text(encoding="utf-8"))
            summaries.append(reconcile_os(plan, os_key, results, serial))
        args.output.write_text(json.dumps({"schema_version": 1, "summaries": summaries}, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summaries, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
