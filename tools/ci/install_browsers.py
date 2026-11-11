"""Install the browsers the test suite drives, and prove that they start.

    python tools/ci/install_browsers.py chromium chrome

``playwright install --with-deps`` also installs system packages with apt.
When a runner cannot reach the Ubuntu package mirrors, that part fails after
several minutes of retries and the job dies before any test has run, although
the runner image already has the libraries the browsers need. The packages
apt still wanted on such a day were fonts.

So: install with system packages when the mirrors answer, without them when
they do not or when that attempt fails, and then start every browser once.
Several browser tests skip themselves where no browser starts, so a job that
got this far without a working browser has to fail here, loudly, rather than
pass with those tests skipped.
"""

from __future__ import annotations

import socket
import subprocess
import sys

MIRRORS = ("azure.archive.ubuntu.com", "archive.ubuntu.com")


def mirrors_answer(resolve=socket.getaddrinfo) -> bool:
    """Whether any Ubuntu package mirror name resolves from this machine."""
    for name in MIRRORS:
        try:
            resolve(name, 80)
            return True
        except OSError:
            continue
    return False


def install_command(browsers: list[str], *, with_packages: bool) -> list[str]:
    command = [sys.executable, "-m", "playwright", "install"]
    if with_packages:
        command.append("--with-deps")
    return [*command, *browsers]


def install(browsers: list[str], *, packages_reachable: bool, run=subprocess.call) -> bool:
    """Install ``browsers``; True when an install command succeeded."""
    if packages_reachable:
        if run(install_command(browsers, with_packages=True)) == 0:
            return True
        print("::warning::Installing with system packages failed; installing the browsers alone.")
    else:
        print("::warning::The Ubuntu package mirrors do not resolve; installing the browsers alone.")
    return run(install_command(browsers, with_packages=False)) == 0


def launch_failures(browsers: list[str]) -> list[str]:
    """Start each browser once; one line per browser that does not start."""
    from playwright.sync_api import sync_playwright

    failures = []
    with sync_playwright() as playwright:
        for name in browsers:
            try:
                options = {} if name == "chromium" else {"channel": name}
                playwright.chromium.launch(**options).close()
            except Exception as error:  # noqa: BLE001 - any reason is a reason to fail the job
                failures.append(f"{name} does not start: {str(error).splitlines()[0]}")
    return failures


def main(argv: list[str] | None = None, *, resolve=socket.getaddrinfo, run=subprocess.call,
         launch=launch_failures) -> int:
    browsers = list(sys.argv[1:] if argv is None else argv) or ["chromium", "chrome"]
    # apt is only involved on Linux; elsewhere --with-deps asks for nothing.
    reachable = not sys.platform.startswith("linux") or mirrors_answer(resolve)
    if not install(browsers, packages_reachable=reachable, run=run):
        print("::error::The browsers could not be installed.")
        return 1
    failures = launch(browsers)
    for failure in failures:
        print(f"::error::{failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
