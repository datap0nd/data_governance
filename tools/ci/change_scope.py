"""Classify changed repository paths for the CI execution policy."""

from __future__ import annotations

import argparse
import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path


DOC_PATTERNS = (
    "docs/**",
    "*.md",
    "AGENTS.md",
    "CLAUDE.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)
ORCHESTRATION_PATTERNS = (
    ".github/workflows/tests.yml",
    "tools/ci/**",
    "ci/**",
    "tests/test_ci_*.py",
    "tests/test_check_command.py",
)
POSTGRES_PATTERNS = (
    "app/flow_sql.py",
    "tests/test_sql_ownership_postgres.py",
    "**/*.sql",
    "requirements-ci.*",
)
FRONTEND_PATTERNS = ("app/static/**", "tests/test_*.mjs")


def matches(path: str, patterns: tuple[str, ...] | list[str]) -> bool:
    path = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def read_patterns(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


@dataclass(frozen=True)
class Scope:
    application: bool
    windows: bool
    postgres: bool
    frontend: bool
    orchestration: bool
    equivalence: bool
    docs_only: bool

    def github_outputs(self) -> dict[str, str]:
        return {key: str(value).lower() for key, value in asdict(self).items()}


def execution_policy(scope: Scope, event: str, equivalent_windows_sha: str = "") -> dict[str, object]:
    if not scope.application:
        os_keys: list[str] = []
    elif event == "pull_request":
        os_keys = ["ubuntu", *( ["windows"] if scope.windows else [])]
    elif event == "push":
        os_keys = [] if equivalent_windows_sha else ["windows"]
    else:
        os_keys = ["ubuntu", "windows"]
    regression = bool(os_keys)
    return {
        "regression": regression,
        "os_csv": ",".join(os_keys),
        "frontend_required": regression and event != "push",
        "postgres_required": scope.postgres and regression,
        "equivalent_windows_sha": equivalent_windows_sha,
    }


def classify(
    paths: list[str],
    windows_patterns: list[str],
    *,
    force_full: bool = False,
    require_equivalence: bool = False,
) -> Scope:
    normalized = sorted({path.strip().replace("\\", "/") for path in paths if path.strip()})
    if force_full:
        return Scope(True, True, True, True, False, False, False)
    docs_only = bool(normalized) and all(matches(path, DOC_PATTERNS) for path in normalized)
    orchestration = any(matches(path, ORCHESTRATION_PATTERNS) for path in normalized)
    application = bool(normalized) and (not docs_only or orchestration)
    frontend = application and any(matches(path, FRONTEND_PATTERNS) for path in normalized)
    postgres = application and any(matches(path, POSTGRES_PATTERNS) for path in normalized)
    explicit_windows = any(matches(path, windows_patterns) for path in normalized)
    unknown_backend = any(
        path.startswith(("app/", "api/", "tests/", "tools/", "transforms/"))
        and not matches(path, FRONTEND_PATTERNS)
        and not matches(path, windows_patterns)
        for path in normalized
    )
    windows = application and (explicit_windows or unknown_backend or orchestration)
    return Scope(
        application,
        windows,
        postgres,
        frontend,
        orchestration,
        orchestration and require_equivalence,
        docs_only,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths-file", type=Path, required=True)
    parser.add_argument("--windows-manifest", type=Path, required=True)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--force-full", action="store_true")
    parser.add_argument("--require-equivalence", action="store_true")
    parser.add_argument("--event", choices=("pull_request", "push", "schedule", "workflow_dispatch"), default="pull_request")
    parser.add_argument("--equivalent-windows-sha", default="")
    args = parser.parse_args()
    scope = classify(
        args.paths_file.read_text(encoding="utf-8").splitlines(),
        read_patterns(args.windows_manifest),
        force_full=args.force_full,
        require_equivalence=args.require_equivalence,
    )
    policy = execution_policy(scope, args.event, args.equivalent_windows_sha)
    print(json.dumps({**asdict(scope), **policy}, sort_keys=True))
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            for key, value in scope.github_outputs().items():
                output.write(f"{key}={value}\n")
            for key, value in policy.items():
                output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
