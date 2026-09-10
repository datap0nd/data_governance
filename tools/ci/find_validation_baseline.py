"""Find the newest successful full-Windows validation on a Git ancestor."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from collections.abc import Callable, Iterable


def has_full_windows(jobs: Iterable[dict]) -> bool:
    successful = {job.get("name") for job in jobs if job.get("conclusion") == "success"}
    if "Python (windows-latest)" in successful:
        return True
    shards = {name for name in successful if isinstance(name, str) and name.startswith("Python windows ")}
    return len(shards) == 6 and "Test inventory reconciliation" in successful


def select_baseline(
    runs: Iterable[dict],
    *,
    current_sha: str,
    is_ancestor: Callable[[str, str], bool],
    jobs_for_run: Callable[[int], Iterable[dict]],
) -> str | None:
    for run in runs:
        sha = run.get("head_sha")
        if (
            run.get("conclusion") != "success"
            or not isinstance(sha, str)
            or sha == current_sha
            or not is_ancestor(sha, current_sha)
        ):
            continue
        if has_full_windows(jobs_for_run(int(run["id"]))):
            return sha
    return None


def select_equivalent(
    runs: Iterable[dict],
    *,
    current_sha: str,
    equivalent: Callable[[str, str], bool],
    jobs_for_run: Callable[[int], Iterable[dict]],
) -> str | None:
    for run in runs:
        sha = run.get("head_sha")
        if (
            run.get("conclusion") != "success"
            or not isinstance(sha, str)
            or sha == current_sha
            or not equivalent(sha, current_sha)
        ):
            continue
        if has_full_windows(jobs_for_run(int(run["id"]))):
            return sha
    return None


def _api(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--mode", choices=("baseline", "equivalent"), default="baseline")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("")
        return 0
    api_root = f"https://api.github.com/repos/{args.repository}"
    query = "branch=main&status=success" if args.mode == "baseline" else "event=pull_request&status=success"
    runs = _api(f"{api_root}/actions/workflows/tests.yml/runs?{query}&per_page=50").get(
        "workflow_runs", []
    )

    def ancestor(candidate: str, head: str) -> bool:
        return subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, head], check=False
        ).returncode == 0

    def jobs(run_id: int) -> Iterable[dict]:
        return _api(f"{api_root}/actions/runs/{run_id}/jobs?per_page=100").get("jobs", [])

    relevant = [
        "app", "api", "tests", "tools", "transforms", "ci", "vendor",
        "requirements.txt", "requirements-ci.txt", "requirements-ci.lock",
        ".github/workflows/tests.yml", "setup.ps1", "update_app.ps1",
    ]

    def equivalent(candidate: str, head: str) -> bool:
        present = subprocess.run(
            ["git", "cat-file", "-e", f"{candidate}^{{commit}}"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
        if not present:
            subprocess.run(["git", "fetch", "origin", candidate], check=False)
        return subprocess.run(["git", "diff", "--quiet", candidate, head, "--", *relevant], check=False).returncode == 0

    if args.mode == "baseline":
        found = select_baseline(runs, current_sha=args.current_sha, is_ancestor=ancestor, jobs_for_run=jobs)
    else:
        found = select_equivalent(runs, current_sha=args.current_sha, equivalent=equivalent, jobs_for_run=jobs)
    print(found or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
