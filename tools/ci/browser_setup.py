"""Probe real Playwright channels and install only missing required browsers."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path


def required_browsers(root: Path) -> list[str]:
    source = "\n".join(path.read_text(encoding="utf-8") for path in sorted((root / "tests").glob("test_*.py")))
    required = []
    if ".chromium.launch()" in source or ".chromium.launch(headless" in source:
        required.append("chromium")
    if re.search(r"launch\([^)]*channel\s*=\s*['\"]chrome['\"]", source):
        required.append("chrome")
    if re.search(r"launch\([^)]*channel\s*=\s*['\"]msedge['\"]", source):
        required.append("msedge")
    return required


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    browsers = required_browsers(args.root)
    results: dict[str, dict[str, str | bool]] = {}
    for browser in browsers:
        ready, detail = probe(browser)
        if not ready:
            command = [sys.executable, "-m", "playwright", "install"]
            if browser == "chromium" and sys.platform.startswith("linux"):
                command.append("--with-deps")
            command.append(browser)
            subprocess.run(command, check=True)
            ready, detail = probe(browser)
        if not ready:
            raise SystemExit(f"{browser} remained unavailable after its dedicated installation: {detail}")
        results[browser] = {"ready": ready, "detail": detail}
    payload = {
        "schema_version": 1,
        "playwright": importlib.metadata.version("playwright"),
        "platform": sys.platform,
        "browsers": results,
    }
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
