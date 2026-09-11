"""Validate the GitHub Actions dependency graph behind the Merge ready check."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping


class GateError(ValueError):
    """The selected CI graph is incomplete or unsuccessful."""


def _enabled(value: object) -> bool:
    return str(value).lower() == "true"


def validate_needs(needs: Mapping[str, object]) -> list[str]:
    scope = needs.get("scope")
    if not isinstance(scope, Mapping) or scope.get("result") != "success":
        raise GateError("change scope did not complete successfully")
    outputs = scope.get("outputs")
    if not isinstance(outputs, Mapping):
        raise GateError("change scope outputs are missing")

    regression = _enabled(outputs.get("regression"))
    selected = {
        "inventory": regression,
        "plan": regression,
        "browsers": regression,
        "python": regression,
        "serial": _enabled(outputs.get("equivalence")),
        "reconcile": regression,
        "frontend": _enabled(outputs.get("frontend_required")),
        "postgres": _enabled(outputs.get("postgres_required")),
    }
    accepted: list[str] = ["scope"]
    for job, required in selected.items():
        state = needs.get(job)
        if not isinstance(state, Mapping) or "result" not in state:
            raise GateError(f"{job} result is missing")
        result = state["result"]
        if required and result != "success":
            raise GateError(f"selected job {job} finished as {result}")
        if not required and result != "skipped":
            raise GateError(f"excluded job {job} unexpectedly finished as {result}")
        accepted.append(f"{job}:{result}")
    return accepted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--needs-json", required=True)
    args = parser.parse_args(argv)
    try:
        needs = json.loads(args.needs_json)
        accepted = validate_needs(needs)
    except (json.JSONDecodeError, GateError) as exc:
        print(f"Merge gate rejected: {exc}", file=sys.stderr)
        return 1
    print("Merge gate accepted: " + ", ".join(accepted))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
