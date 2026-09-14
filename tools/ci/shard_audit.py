"""Fail the merge gate if parallel runs omit, duplicate or fail collected tests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def unique_ids(value: object, label: str) -> set[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a nonempty list of test IDs")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} contains duplicate test IDs")
    return set(value)


def audit(manifests: list[dict], *, platforms: list[str], count: int, revision: str) -> dict[str, int]:
    if count < 1 or not platforms or len(set(platforms)) != len(platforms):
        raise ValueError("expected platforms/count are invalid")
    expected = {(platform, index) for platform in platforms for index in range(count)}
    seen = set()
    collections: dict[str, set[str]] = {}
    selections: dict[str, set[str]] = {platform: set() for platform in platforms}
    for manifest in manifests:
        key = (manifest.get("platform"), manifest.get("index"))
        if key not in expected or key in seen:
            raise ValueError(f"unexpected or duplicate shard: {key}")
        seen.add(key)
        if manifest.get("schema_version") != 1 or manifest.get("count") != count:
            raise ValueError("shard schema/count mismatch")
        if manifest.get("revision") != revision or manifest.get("exit_code") != 0:
            raise ValueError("shard revision mismatch or unsuccessful execution")
        collected = unique_ids(manifest.get("collected"), "collection")
        selected = unique_ids(manifest.get("selected"), "selection")
        finished = unique_ids(manifest.get("finished"), "finished tests")
        platform = key[0]
        if platform in collections and collections[platform] != collected:
            raise ValueError("shards collected different tests on the same platform")
        collections[platform] = collected
        if not selected <= collected or finished != selected:
            raise ValueError("selected tests were not collected or did not finish")
        if selections[platform] & selected:
            raise ValueError("test executed in more than one shard")
        selections[platform].update(selected)
    if seen != expected:
        raise ValueError("required shard artifacts are missing")
    for platform in platforms:
        if selections[platform] != collections[platform]:
            raise ValueError(f"collection coverage is incomplete on {platform}")
    return {platform: len(selections[platform]) for platform in platforms}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--platforms", nargs="+", required=True)
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args(argv)
    try:
        manifests = [json.loads(path.read_text(encoding="utf-8"))
                     for path in args.root.rglob("shard-manifest.json")]
        result = audit(manifests, platforms=args.platforms, count=args.count, revision=args.revision)
    except (ValueError, OSError, TypeError, AttributeError) as exc:
        print(f"Shard audit rejected: {exc}")
        return 1
    print("Shard audit accepted: " + json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
