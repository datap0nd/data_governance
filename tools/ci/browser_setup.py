"""Probe real Playwright channels and install only missing required browsers."""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path


def parameterized_browsers(tree: ast.AST) -> set[str]:
    """Return literal browser channels supplied through pytest parametrization."""
    required: set[str] = set()
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not (
            isinstance(call.func, ast.Attribute)
            and call.func.attr == "parametrize"
            and len(call.args) >= 2
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ):
            continue
        names = [name.strip() for name in call.args[0].value.split(",")]
        if "channel" not in names or not isinstance(call.args[1], (ast.List, ast.Tuple)):
            continue
        index = names.index("channel")
        for value in call.args[1].elts:
            candidate = value if len(names) == 1 else (
                value.elts[index]
                if isinstance(value, (ast.List, ast.Tuple)) and len(value.elts) > index
                else None
            )
            if isinstance(candidate, ast.Constant) and candidate.value in {"chrome", "msedge"}:
                required.add(candidate.value)
    return required


def required_browsers(root: Path, sources: list[Path] | None = None) -> list[str]:
    paths = sorted((root / "tests").glob("test_*.py")) if sources is None else sources
    required: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        required.update(parameterized_browsers(tree))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            function = call.func
            if not (
                isinstance(function, ast.Attribute)
                and function.attr == "launch"
                and isinstance(function.value, ast.Attribute)
                and function.value.attr == "chromium"
            ):
                continue
            channel = next((keyword.value for keyword in call.keywords if keyword.arg == "channel"), None)
            if isinstance(channel, ast.Constant) and channel.value in {"chrome", "msedge"}:
                required.add(channel.value)
            elif channel is None:
                required.add("chromium")
    return [browser for browser in ("chromium", "chrome", "msedge") if browser in required]


def probe(browser: str) -> tuple[bool, str]:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            kwargs = {} if browser == "chromium" else {"channel": browser}
            instance = playwright.chromium.launch(headless=True, **kwargs)
            instance.close()
        return True, "runnable"
    except Exception as exc:  # Playwright supplies the actionable install diagnostic.
        return False, str(exc).splitlines()[0]


def prepare_browsers(browsers: list[str], probe_only: bool) -> tuple[dict[str, dict[str, str | bool]], list[str]]:
    results: dict[str, dict[str, str | bool]] = {}
    missing: list[str] = []
    for browser in browsers:
        ready, detail = probe(browser)
        if not ready and probe_only:
            missing.append(browser)
        elif not ready:
            command = [sys.executable, "-m", "playwright", "install"]
            if browser == "chromium" and sys.platform.startswith("linux"):
                command.append("--with-deps")
            command.append(browser)
            subprocess.run(command, check=True)
            ready, detail = probe(browser)
        if not ready and not probe_only:
            raise SystemExit(f"{browser} remained unavailable after its dedicated installation: {detail}")
        results[browser] = {"ready": ready, "detail": detail}
    return results, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", action="append", type=Path, dest="sources")
    parser.add_argument(
        "--browser", action="append", choices=("chromium", "chrome", "msedge"), dest="browsers"
    )
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    sources = [path if path.is_absolute() else args.root / path for path in args.sources] if args.sources else None
    browsers = required_browsers(args.root, sources) if sources or not args.browsers else []
    browsers = [
        browser
        for browser in ("chromium", "chrome", "msedge")
        if browser in set(browsers) | set(args.browsers or [])
    ]
    results, missing = prepare_browsers(browsers, args.probe_only)
    payload = {
        "schema_version": 1,
        "playwright": importlib.metadata.version("playwright"),
        "platform": sys.platform,
        "browsers": results,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    if missing:
        print("Missing or unrunnable browser channels: " + ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
