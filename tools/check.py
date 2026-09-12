"""Local verification entry point for Metronome on Linux, macOS and Windows.

This mirrors ``tools/check.ps1``: the same modes, the same ``result.json``
schema and the same focused-selection rules. Agent sessions run on Linux where
PowerShell is usually absent, so this module is the supported command there and
keeps their evidence comparable with Windows runs. Isolation follows CI rather
than the PowerShell script in one place, noted on ``isolated_environment``.

    python tools/check.py setup
    python tools/check.py preflight
    python tools/check.py verify --test tests/test_x.py::test_y --syntax app/x.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ElementTree
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = REPO_ROOT / "requirements-ci.lock"
RUNS_ROOT = REPO_ROOT / ".test-runs"
LOCK_MARKER_NAME = ".metronome-ci-lock.sha256"
ISOLATION_DIRECTORY = "MetronomeTestRuns"
REQUIRED_MODULES = ("pytest", "fastapi", "playwright", "sqlalchemy", "psycopg2")


class CheckError(Exception):
    """A verification precondition failed; the run records it and stops."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def venv_python(root: Path = REPO_ROOT) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def isolation_base() -> Path:
    """Scratch space outside the checkout, because a Flow root inside it is refused."""

    if os.name == "nt":
        home = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        home = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(home) / ISOLATION_DIRECTORY


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


class Run:
    """One invocation: its isolated directories and its machine-readable result."""

    def __init__(self, mode: str, selection: "OrderedDict[str, object]") -> None:
        self.run_id = "{0}-{1}-{2}".format(
            _utc_now().strftime("%Y%m%dT%H%M%S%f")[:-3] + "Z",
            os.getpid(),
            uuid.uuid4().hex[:8],
        )
        self.root = RUNS_ROOT / self.run_id
        self.result_path = self.root / "result.json"
        self.started = _utc_now()
        self.external_root: Path | None = None
        self.result: "OrderedDict[str, object]" = OrderedDict(
            (
                ("schema_version", SCHEMA_VERSION),
                ("run_id", self.run_id),
                ("mode", mode),
                ("status", "running"),
                ("revision", None),
                ("source_fingerprint", None),
                (
                    "environment",
                    OrderedDict(
                        (
                            ("os", "{0} {1}".format(os.name, sys.platform)),
                            ("runner", "python {0} tools/check.py".format(
                                ".".join(str(part) for part in sys.version_info[:3])
                            )),
                            ("python", None),
                        )
                    ),
                ),
                ("selection", selection),
                ("started_utc", self.started.isoformat()),
                ("finished_utc", None),
                ("duration_seconds", None),
                ("exit_code", None),
                ("reused_from", None),
                ("artifacts", OrderedDict()),
                ("test_summary", None),
                ("diagnostic", None),
            )
        )

    def save(self, status: str, exit_code: int, diagnostic: str | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        finished = _utc_now()
        self.result["status"] = status
        self.result["exit_code"] = exit_code
        self.result["finished_utc"] = finished.isoformat()
        self.result["duration_seconds"] = round((finished - self.started).total_seconds(), 3)
        self.result["diagnostic"] = diagnostic
        self._clean_external_root(status)
        self.result_path.write_text(
            json.dumps(self.result, indent=2) + "\n", encoding="utf-8"
        )
        print("Result: {0}".format(self.result_path))

    def _clean_external_root(self, status: str) -> None:
        """Remove the run's out-of-checkout scratch, but keep it after a failure."""

        root = self.external_root
        if root is None or not root.exists():
            return
        if status != "passed":
            print("Kept scratch for diagnosis: {0}".format(root))
            return
        allowed = isolation_base().resolve()
        resolved = root.resolve()
        if allowed not in resolved.parents:
            raise CheckError("Refusing to clean unexpected scratch root: {0}".format(resolved))
        shutil.rmtree(resolved, ignore_errors=True)


def run_process(command: list[str], env: dict[str, str] | None = None) -> int:
    completed = subprocess.run(command, cwd=str(REPO_ROOT), env=env, check=False)
    return completed.returncode


def capture(command: list[str]) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            command, cwd=str(REPO_ROOT), text=True, capture_output=True, check=False
        )
    except OSError as error:
        return 1, str(error)
    return completed.returncode, (completed.stdout or "").strip()


def revision() -> str:
    code, output = capture(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"])
    if code != 0 or not output:
        raise CheckError(
            "This command must run from a Git checkout with a readable HEAD revision."
        )
    return output


def assert_python_313(executable: Path, purpose: str) -> str:
    if not executable.is_file():
        raise CheckError(
            "{0} Python was not found at '{1}'. Run 'python tools/check.py setup' after "
            "installing Python 3.13, or pass --python to setup.".format(purpose, executable)
        )
    if "codex-runtimes" in str(executable).replace("\\", "/"):
        raise CheckError(
            "The bundled coding-agent Python runtime is not a supported test interpreter. "
            "Install Python 3.13 and create the checkout-owned .venv."
        )
    code, version = capture(
        [str(executable), "-c", 'import sys; print(".".join(map(str, sys.version_info[:3])))']
    )
    if code != 0 or not version.startswith("3.13."):
        raise CheckError(
            "{0} requires Python 3.13; '{1}' reported '{2}'.".format(purpose, executable, version)
        )
    return version


def bootstrap_python(requested: str | None) -> Path:
    """Find a Python 3.13 to build the checkout-owned .venv from."""

    if requested:
        candidate = Path(requested).expanduser()
        if not candidate.is_file():
            raise CheckError("--python does not point at an executable: {0}".format(requested))
        return candidate.resolve()
    if sys.version_info[:2] == (3, 13):
        return Path(sys.executable).resolve()
    for name in ("python3.13", "python3.13.exe"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    if os.name == "nt":
        launcher = shutil.which("py.exe") or shutil.which("py")
        if launcher:
            code, output = capture([launcher, "-3.13", "-c", "import sys; print(sys.executable)"])
            if code == 0 and output:
                return Path(output).resolve()
    uv = shutil.which("uv")
    if uv:
        code, output = capture([uv, "python", "find", "3.13"])
        if code == 0 and output:
            return Path(output.splitlines()[-1].strip()).resolve()
    raise CheckError(
        "Python 3.13 is missing. Install Python 3.13 (or 'uv python install 3.13'), then run "
        "'python tools/check.py setup'; no temporary or sibling environment was searched."
    )


def lock_fingerprint() -> str:
    if not LOCK_PATH.is_file():
        raise CheckError("Dependency lock is missing: {0}".format(LOCK_PATH))
    return file_digest(LOCK_PATH).lower()


def preflight(run: Run) -> Path:
    interpreter = venv_python()
    version = assert_python_313(interpreter, "Verification")
    run.result["environment"]["python"] = version  # type: ignore[index]
    marker = interpreter.parent.parent / LOCK_MARKER_NAME
    installed = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if installed != lock_fingerprint():
        raise CheckError(
            "The checkout-owned .venv does not match requirements-ci.lock. "
            "Run 'python tools/check.py setup'."
        )
    if run_process([str(interpreter), "-m", "pip", "check"]) != 0:
        raise CheckError(
            "The checkout-owned .venv has incompatible dependencies. "
            "Run 'python tools/check.py setup'."
        )
    probe = (
        "import importlib.util,sys;"
        "missing=[name for name in {0} if importlib.util.find_spec(name) is None];"
        'print("Missing modules: "+", ".join(missing) if missing else "Dependency imports: ready");'
        "sys.exit(bool(missing))".format(REQUIRED_MODULES)
    )
    if run_process([str(interpreter), "-c", probe]) != 0:
        raise CheckError(
            "Required test modules are missing. Run 'python tools/check.py setup'."
        )
    print(
        "Preflight ready: Python {0}, locked dependencies, isolated run root {1}".format(
            version, run.root
        )
    )
    return interpreter


def source_fingerprint(tests: list[str], syntax: list[str]) -> str:
    code, listing = capture(["git", "-C", str(REPO_ROOT), "ls-files", "app", "tools"])
    tracked = listing.splitlines() if code == 0 else []
    items = sorted(set(["requirements-ci.lock"] + tracked + tests + syntax))
    rows = []
    for item in items:
        candidate = REPO_ROOT / item.split("::", 1)[0]
        if candidate.is_file():
            rows.append("{0}:{1}".format(item.split("::", 1)[0], file_digest(candidate)))
        else:
            rows.append("selector:{0}".format(item))
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def reusable_result(run: Run) -> str | None:
    if not RUNS_ROOT.is_dir():
        return None
    candidates = sorted(
        RUNS_ROOT.glob("*/result.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for path in candidates:
        if path == run.result_path:
            continue
        try:
            prior = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if (
            prior.get("status") == "passed"
            and prior.get("source_fingerprint") == run.result["source_fingerprint"]
            and prior.get("selection") == run.result["selection"]
        ):
            return str(path)
    return None


def check_syntax(interpreter: Path, targets: list[str]) -> None:
    for item in targets:
        target = REPO_ROOT / item
        if not target.is_file():
            raise CheckError("Syntax target not found: {0}".format(item))
        suffix = target.suffix.lower()
        if suffix == ".py":
            code = run_process([str(interpreter), "-m", "py_compile", str(target)])
        elif suffix in (".js", ".mjs"):
            node = shutil.which("node")
            if not node:
                raise CheckError(
                    "Node.js is required to check {0}; install Node or drop the selector.".format(item)
                )
            code = run_process([node, "--check", str(target)])
        elif suffix == ".ps1":
            pwsh = shutil.which("pwsh") or shutil.which("powershell")
            if not pwsh:
                raise CheckError(
                    "PowerShell is required to check {0}; run that selector on a machine with "
                    "pwsh, or let CI cover it.".format(item)
                )
            script = (
                "$tokens=$null;$errors=$null;"
                "[Management.Automation.Language.Parser]::ParseFile("
                "'{0}',[ref]$tokens,[ref]$errors)|Out-Null;"
                "if ($errors.Count) {{ Write-Error (($errors | ForEach-Object Message) -join '; '); exit 1 }}"
            ).format(str(target).replace("'", "''"))
            code = run_process([pwsh, "-NoLogo", "-NoProfile", "-Command", script])
        else:
            raise CheckError("No syntax checker is configured for: {0}".format(item))
        if code != 0:
            raise CheckError("Syntax check failed: {0}".format(item), exit_code=code)


def isolated_environment(run: Run) -> dict[str, str]:
    """Point every writable path at this run before application imports happen.

    `DG_FLOWS_ROOT` is deliberately left unset, exactly as CI leaves it: each
    test derives its Flow root from its own database path, so one root shared
    across a run would make two tests that create the same Flow name collide.
    Those per-test roots have to sit outside the checkout — the application
    refuses a Flows root inside it — so the temporary root lives beside the
    run instead of inside `.test-runs/`, and is removed when the run passes.
    """

    temp_root = run.external_root / "tmp"  # type: ignore[union-attr]
    profile_root = run.root / "browser-profiles"
    temp_root.mkdir(parents=True, exist_ok=True)
    profile_root.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.pop("DG_FLOWS_ROOT", None)
    environment["TEMP"] = str(temp_root)
    environment["TMP"] = str(temp_root)
    environment["TMPDIR"] = str(temp_root)
    environment["DG_DB_PATH"] = str(run.root / "governance-test.db")
    environment["DG_TEST_RUN_ROOT"] = str(run.root)
    environment["DG_BROWSER_PROFILE_ROOT"] = str(profile_root)
    environment.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(REPO_ROOT / ".playwright-browsers"))
    return environment


def summarize_junit(path: Path) -> "OrderedDict[str, int] | None":
    if not path.is_file():
        return None
    try:
        root = ElementTree.parse(str(path)).getroot()
    except ElementTree.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    summary = OrderedDict((("tests", 0), ("failures", 0), ("errors", 0), ("skipped", 0)))
    for suite in suites:
        for key in summary:
            summary[key] += int(suite.get(key, 0) or 0)
    return summary


def do_setup(run: Run, arguments: argparse.Namespace) -> int:
    bootstrap = bootstrap_python(arguments.python)
    version = assert_python_313(bootstrap, "Setup")
    interpreter = venv_python()
    if not interpreter.is_file():
        if run_process([str(bootstrap), "-m", "venv", str(REPO_ROOT / ".venv")]) != 0:
            raise CheckError("Python failed to create the checkout-owned .venv.")
    assert_python_313(interpreter, "Checkout environment")
    installed = run_process(
        [str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
         "--requirement", str(LOCK_PATH)]
    )
    if installed != 0:
        raise CheckError("Locked dependency installation failed.")
    (REPO_ROOT / ".venv" / LOCK_MARKER_NAME).write_text(lock_fingerprint(), encoding="ascii")
    if arguments.install_browsers:
        browsers = ["chromium", "chrome", "msedge"] if os.name == "nt" else ["chromium", "chrome"]
        if run_process([str(interpreter), "-m", "playwright", "install", *browsers]) != 0:
            raise CheckError("Playwright browser setup failed.")
    run.result["environment"]["python"] = version  # type: ignore[index]
    run.save("passed", 0)
    return 0


def do_verify(run: Run, arguments: argparse.Namespace) -> int:
    tests = list(arguments.test)
    if arguments.full and not (arguments.diagnostic_reason or "").strip():
        raise CheckError(
            "A local full suite is diagnostic-only. Supply --full --diagnostic-reason with the "
            "failure or equivalence question being investigated."
        )
    if not arguments.full and not tests:
        raise CheckError(
            "Verify requires explicit --test selectors. Example: "
            "python tools/check.py verify --test tests/test_flows.py::test_name"
        )
    if arguments.full:
        tests = ["tests"]
    run.result["selection"]["tests"] = list(tests)  # type: ignore[index]

    interpreter = preflight(run)
    run.result["source_fingerprint"] = source_fingerprint(tests, list(arguments.syntax))

    if arguments.reuse:
        prior = reusable_result(run)
        if prior:
            run.result["reused_from"] = prior
            run.save("passed", 0, "Reused unchanged successful local evidence.")
            return 0

    run.external_root = isolation_base() / run.run_id
    environment = isolated_environment(run)
    junit = run.root / "pytest.xml"
    run.result["artifacts"]["junit"] = str(junit)  # type: ignore[index]
    run.result["artifacts"]["temp_root"] = environment["TEMP"]  # type: ignore[index]
    check_syntax(interpreter, list(arguments.syntax))

    command = [str(interpreter), "-m", "pytest", *tests, "-q", "-ra", "--durations=20",
               "--basetemp={0}".format(environment["TEMP"]), "--junitxml={0}".format(junit)]
    exit_code = run_process(command, env=environment)
    run.result["test_summary"] = summarize_junit(junit)
    if exit_code != 0:
        raise CheckError(
            "Focused verification failed with exit code {0}. Rerun the failed case and only its "
            "necessary integration companions.".format(exit_code),
            exit_code=exit_code,
        )
    run.save("passed", 0)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python tools/check.py", description=__doc__.splitlines()[0]
    )
    modes = parser.add_subparsers(dest="mode", required=True)

    setup = modes.add_parser("setup", help="create or refresh the checkout-owned Python 3.13 .venv")
    setup.add_argument("--python", help="path to the Python 3.13 used to build .venv")
    setup.add_argument("--install-browsers", action="store_true", help="install Playwright browsers")

    modes.add_parser("preflight", help="diagnose the checkout-owned environment")

    verify = modes.add_parser("verify", help="run a focused, isolated test selection")
    verify.add_argument("--test", action="append", default=[], metavar="SELECTOR",
                        help="pytest file or node selector; repeat for more")
    verify.add_argument("--syntax", action="append", default=[], metavar="PATH",
                        help="file to syntax-check (.py, .js, .mjs, .ps1); repeat for more")
    verify.add_argument("--full", action="store_true", help="diagnostic-only whole-suite run")
    verify.add_argument("--diagnostic-reason", default="",
                        help="the failure or equivalence question --full is investigating")
    verify.add_argument("--reuse", action="store_true",
                        help="reuse a matching successful result with the same fingerprint")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    selection = OrderedDict(
        (
            ("full_suite", bool(getattr(arguments, "full", False))),
            ("diagnostic_reason", getattr(arguments, "diagnostic_reason", "") or None),
            ("tests", list(getattr(arguments, "test", []))),
            ("syntax", list(getattr(arguments, "syntax", []))),
        )
    )
    run = Run(arguments.mode, selection)
    try:
        run.root.mkdir(parents=True, exist_ok=True)
        run.result["revision"] = revision()
        if arguments.mode == "setup":
            return do_setup(run, arguments)
        if arguments.mode == "preflight":
            preflight(run)
            run.save("passed", 0)
            return 0
        return do_verify(run, arguments)
    except CheckError as error:
        run.save("failed", error.exit_code, str(error))
        print(str(error), file=sys.stderr)
        return error.exit_code
    except Exception as error:  # keep evidence for an unexpected failure too
        run.save("failed", 2, "{0}: {1}".format(type(error).__name__, error))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
