"""Cross-platform local verification for Metronome.

This is the Python counterpart of ``tools/check.ps1``. It creates the same
checkout-owned Python 3.13 ``.venv`` from ``requirements-ci.lock``, runs one
explicit focused selection under isolated database, temporary, browser-profile
and Flow-root paths, and records the same compact ``result.json`` under an
ignored per-run ``.test-runs/<run id>/`` directory. Use it wherever PowerShell
is unavailable (Linux and macOS sessions, containers); the PowerShell command
remains the Windows entry point.

    python tools/check.py setup [--python PATH] [--install-browsers]
    python tools/check.py preflight
    python tools/check.py verify --test SELECTOR [--test ...] [--syntax PATH ...]
    python tools/check.py verify --full --diagnostic-reason "why" [--syntax ...]
    python tools/check.py verify --reuse --test SELECTOR ...

The local test fixtures are disposable and have no production access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
REQUIRED_MODULES = ("pytest", "fastapi", "playwright", "sqlalchemy", "psycopg2")
LOCK_MARKER = ".metronome-ci-lock.sha256"
ISOLATION_DIRNAME = "MetronomeTestRuns"


class CheckError(Exception):
    """A verification step could not proceed; the message is actionable."""

    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or utc_now()).strftime("%Y%m%dT%H%M%S%fZ")[:-4] + "Z"
    return f"{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def venv_python(repo_root: Path) -> Path:
    if os.name == "nt":
        return repo_root / ".venv" / "Scripts" / "python.exe"
    return repo_root / ".venv" / "bin" / "python"


def lock_path(repo_root: Path) -> Path:
    return repo_root / "requirements-ci.lock"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def isolation_base() -> Path:
    """Directory outside the checkout that holds per-run Flow roots."""
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / ISOLATION_DIRNAME
    return Path(tempfile.gettempdir()) / ISOLATION_DIRNAME


def cleanup_isolation_root(root: Path, base: Path | None = None) -> None:
    """Remove a per-run external Flow root, refusing anything outside the base."""
    allowed = (base or isolation_base()).resolve()
    resolved = root.resolve()
    if allowed not in resolved.parents:
        raise CheckError(f"Refusing to clean unexpected Flow test root: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)


def isolated_environment(repo_root: Path, run_root: Path, run_id: str) -> dict[str, str]:
    """Environment overrides applied before pytest starts.

    Every path is unique to this run: the application database, temporary
    files, browser profiles and the Flow root all live under disposable
    directories, so local runs never touch a real Metronome installation.
    """
    temp_root = run_root / "tmp"
    profile_root = run_root / "browser-profiles"
    flow_root = isolation_base() / run_id / "flows"
    overrides = {
        "TEMP": str(temp_root),
        "TMP": str(temp_root),
        "TMPDIR": str(temp_root),
        "DG_DB_PATH": str(run_root / "governance-test.db"),
        "DG_TEST_RUN_ROOT": str(run_root),
        "DG_BROWSER_PROFILE_ROOT": str(profile_root),
        "DG_FLOWS_ROOT": str(flow_root),
    }
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        overrides["PLAYWRIGHT_BROWSERS_PATH"] = str(repo_root / ".playwright-browsers")
    return overrides


def validate_selection(tests: Sequence[str], full: bool, diagnostic_reason: str | None) -> list[str]:
    if full and not (diagnostic_reason or "").strip():
        raise CheckError(
            "A local full suite is diagnostic-only. Supply --full --diagnostic-reason "
            "with the failure or equivalence question being investigated."
        )
    if not full and not tests:
        raise CheckError(
            "Verify requires explicit --test selectors. Example: "
            "python tools/check.py verify --test tests/test_flows.py::test_name"
        )
    return ["tests"] if full else list(tests)


def tracked_source(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "app", "tools"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CheckError("This command must run from a Git checkout with readable tracked files.")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def source_fingerprint(repo_root: Path, tests: Sequence[str], syntax: Sequence[str]) -> str:
    """Hash of the tracked source plus the selection, for evidence reuse."""
    items = {"requirements-ci.lock", *tracked_source(repo_root), *tests, *syntax}
    rows = []
    for item in sorted(items):
        file_part = item.split("::", 1)[0]
        candidate = repo_root / file_part
        if candidate.is_file():
            rows.append(f"{file_part}:{sha256_file(candidate).upper()}")
        else:
            rows.append(f"selector:{item}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def find_reusable_result(repo_root: Path, fingerprint: str, selection: dict, current: Path) -> Path | None:
    runs = repo_root / ".test-runs"
    if not runs.is_dir():
        return None
    candidates = sorted(runs.glob("*/result.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    wanted = json.dumps(selection, sort_keys=True)
    for candidate in candidates:
        if candidate.resolve() == current.resolve():
            continue
        try:
            prior = json.loads(candidate.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if (
            prior.get("status") == "passed"
            and prior.get("source_fingerprint") == fingerprint
            and json.dumps(prior.get("selection"), sort_keys=True) == wanted
        ):
            return candidate
    return None


def python_version(executable: Path) -> str:
    completed = subprocess.run(
        [str(executable), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def assert_python313(executable: Path, purpose: str) -> str:
    if not executable.is_file():
        raise CheckError(
            f"{purpose} Python was not found at '{executable}'. Run 'python tools/check.py setup' "
            "after installing Python 3.13, or pass --python to setup."
        )
    if "codex-runtimes" in {part.lower() for part in executable.parts}:
        raise CheckError(
            "The bundled coding-agent Python runtime is not a supported test interpreter. "
            "Install Python 3.13 and create the checkout-owned .venv."
        )
    version = python_version(executable)
    if not version.startswith("3.13."):
        raise CheckError(f"{purpose} requires Python 3.13; '{executable}' reported '{version or 'no version'}'.")
    return version


def bootstrap_python(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    candidates: list[list[str]] = []
    if os.name == "nt":
        user_install = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python313" / "python.exe"
        if user_install.is_file():
            return user_install
        candidates.append(["py", "-3.13"])
    candidates.extend([["python3.13"], ["python3"], ["python"]])
    for command in candidates:
        if shutil.which(command[0]) is None:
            continue
        completed = subprocess.run(
            [*command, "-c", "import sys; print(sys.executable) if sys.version_info[:2] == (3, 13) else sys.exit(1)"],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            return Path(completed.stdout.strip())
    if sys.version_info[:2] == (3, 13):
        return Path(sys.executable)
    raise CheckError(
        "Python 3.13 is missing. Install the official Python 3.13 package, then run "
        "'python tools/check.py setup'; no temporary or sibling environment was searched."
    )


class Runner:
    def __init__(self, mode: str, repo_root: Path = REPO_ROOT) -> None:
        self.repo_root = repo_root
        self.run_id = new_run_id()
        self.run_root = repo_root / ".test-runs" / self.run_id
        self.result_path = self.run_root / "result.json"
        self.started = utc_now()
        self.external_isolation_root: Path | None = None
        self.result: dict = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "mode": mode,
            "status": "running",
            "revision": None,
            "source_fingerprint": None,
            "environment": {
                "os": f"{os.name} {sys.platform}",
                "verifier": "tools/check.py",
                "python": None,
            },
            "selection": {"full_suite": False, "diagnostic_reason": "", "tests": [], "syntax": []},
            "started_utc": self.started.isoformat(),
            "finished_utc": None,
            "duration_seconds": None,
            "exit_code": None,
            "reused_from": None,
            "artifacts": {},
            "test_summary": None,
            "diagnostic": None,
        }

    def save(self, status: str, exit_code: int, diagnostic: str | None) -> None:
        self.run_root.mkdir(parents=True, exist_ok=True)
        finished = utc_now()
        self.result.update(
            status=status,
            exit_code=exit_code,
            finished_utc=finished.isoformat(),
            duration_seconds=round((finished - self.started).total_seconds(), 3),
            diagnostic=diagnostic,
        )
        if self.external_isolation_root is not None:
            cleanup_isolation_root(self.external_isolation_root)
        self.result_path.write_text(json.dumps(self.result, indent=2), encoding="utf-8")
        print(f"Result: {self.result_path}")

    def revision(self) -> str:
        completed = subprocess.run(
            ["git", "-C", str(self.repo_root), "rev-parse", "HEAD"], text=True, capture_output=True, check=False
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            raise CheckError("This command must run from a Git checkout with a readable HEAD revision.")
        return completed.stdout.strip()

    def lock_fingerprint(self) -> str:
        lock = lock_path(self.repo_root)
        if not lock.is_file():
            raise CheckError(f"Dependency lock is missing: {lock}")
        return sha256_file(lock)

    def preflight(self) -> None:
        python = venv_python(self.repo_root)
        version = assert_python313(python, "Verification")
        self.result["environment"]["python"] = version
        marker = self.repo_root / ".venv" / LOCK_MARKER
        installed = marker.read_text(encoding="ascii").strip() if marker.is_file() else ""
        if installed != self.lock_fingerprint():
            raise CheckError("The checkout-owned .venv does not match requirements-ci.lock. Run 'python tools/check.py setup'.")
        if subprocess.run([str(python), "-m", "pip", "check"], check=False).returncode != 0:
            raise CheckError("The checkout-owned .venv has incompatible dependencies. Run 'python tools/check.py setup'.")
        probe = (
            "import importlib.util,sys; missing=[n for n in "
            + repr(REQUIRED_MODULES)
            + " if importlib.util.find_spec(n) is None]; "
            "print('Missing modules: '+', '.join(missing) if missing else 'Dependency imports: ready'); sys.exit(bool(missing))"
        )
        if subprocess.run([str(python), "-c", probe], check=False).returncode != 0:
            raise CheckError("Required test modules are missing. Run 'python tools/check.py setup'.")
        print(f"Preflight ready: Python {version}, locked dependencies, isolated run root {self.run_root}")

    def setup(self, explicit_python: str | None, install_browsers: bool) -> None:
        bootstrap = bootstrap_python(explicit_python)
        bootstrap_version = assert_python313(bootstrap, "Setup")
        python = venv_python(self.repo_root)
        if not python.is_file():
            if subprocess.run([str(bootstrap), "-m", "venv", str(self.repo_root / ".venv")], check=False).returncode != 0:
                raise CheckError("Python failed to create the checkout-owned .venv.")
        assert_python313(python, "Checkout environment")
        install = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--requirement", str(lock_path(self.repo_root))]
        if subprocess.run(install, check=False).returncode != 0:
            raise CheckError("Locked dependency installation failed.")
        (self.repo_root / ".venv" / LOCK_MARKER).write_text(self.lock_fingerprint(), encoding="ascii")
        if install_browsers:
            browsers = [str(python), "-m", "playwright", "install", "chromium", "chrome", "msedge"]
            if subprocess.run(browsers, check=False).returncode != 0:
                raise CheckError("Playwright browser setup failed.")
        self.result["environment"]["python"] = bootstrap_version

    def check_syntax(self, item: str, python: Path) -> None:
        target = self.repo_root / item
        if not target.is_file():
            raise CheckError(f"Syntax target not found: {item}")
        suffix = target.suffix.lower()
        if suffix == ".py":
            command = [str(python), "-m", "py_compile", str(target)]
        elif suffix in {".js", ".mjs"}:
            if shutil.which("node") is None:
                raise CheckError(f"node is required to syntax-check {item} and was not found on PATH.")
            command = ["node", "--check", str(target)]
        elif suffix == ".ps1":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if shell is None:
                raise CheckError(f"PowerShell is required to parse {item}; run tools/check.ps1 on Windows or install pwsh.")
            script = (
                "$t=$null;$e=$null;[Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$t,[ref]$e)|Out-Null;"
                "if($e.Count){$e|ForEach-Object Message;exit 1}"
            )
            command = [shell, "-NoProfile", "-NonInteractive", "-Command", script, str(target)]
        else:
            raise CheckError(f"No syntax checker is configured for: {item}")
        completed = subprocess.run(command, check=False)
        if completed.returncode != 0:
            raise CheckError(f"Syntax check failed: {item}", exit_code=completed.returncode or 2)

    def verify(self, tests: Sequence[str], syntax: Sequence[str], full: bool, reason: str | None, reuse: bool) -> None:
        self.result["selection"] = {
            "full_suite": bool(full),
            "diagnostic_reason": reason or "",
            "tests": list(tests),
            "syntax": list(syntax),
        }
        selected = validate_selection(tests, full, reason)
        self.result["selection"]["tests"] = list(selected)
        self.preflight()
        self.result["source_fingerprint"] = source_fingerprint(self.repo_root, selected, syntax)
        if reuse:
            prior = find_reusable_result(self.repo_root, self.result["source_fingerprint"], self.result["selection"], self.result_path)
            if prior is not None:
                self.result["reused_from"] = str(prior)
                self.save("passed", 0, "Reused unchanged successful local evidence.")
                return

        overrides = isolated_environment(self.repo_root, self.run_root, self.run_id)
        self.external_isolation_root = Path(overrides["DG_FLOWS_ROOT"]).parent
        for key in ("TEMP", "DG_BROWSER_PROFILE_ROOT", "DG_FLOWS_ROOT"):
            Path(overrides[key]).mkdir(parents=True, exist_ok=True)
        os.environ.update(overrides)
        junit = self.run_root / "pytest.xml"
        self.result["artifacts"]["junit"] = str(junit)

        python = venv_python(self.repo_root)
        for item in syntax:
            self.check_syntax(item, python)

        command = [
            str(python), "-m", "pytest", *selected,
            "-q", "-ra", "--durations=20", f"--basetemp={overrides['TEMP']}", f"--junitxml={junit}",
        ]
        completed = subprocess.run(command, cwd=str(self.repo_root), check=False)
        if completed.returncode != 0:
            raise CheckError(
                f"Focused verification failed with exit code {completed.returncode}. Rerun the failed case and only its "
                "necessary integration companions.",
                exit_code=completed.returncode,
            )
        if junit.is_file():
            self.result["test_summary"] = junit_summary(junit)
        self.save("passed", 0, None)


def junit_summary(path: Path) -> dict[str, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    summary = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in summary:
            summary[key] += int(suite.get(key, "0") or 0)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    modes = parser.add_subparsers(dest="mode", required=True)
    setup = modes.add_parser("setup", help="create or refresh the checkout-owned Python 3.13 .venv")
    setup.add_argument("--python", help="explicit Python 3.13 interpreter for creating the .venv")
    setup.add_argument("--install-browsers", action="store_true", help="also install Playwright browsers")
    modes.add_parser("preflight", help="diagnose the checkout-owned environment")
    verify = modes.add_parser("verify", help="run one explicit focused selection under isolated paths")
    verify.add_argument("--test", action="append", default=[], metavar="SELECTOR", help="pytest file or node id; repeatable")
    verify.add_argument("--syntax", action="append", default=[], metavar="PATH", help=".py, .js/.mjs or .ps1 file; repeatable")
    verify.add_argument("--full", action="store_true", help="diagnostic-only full suite; requires --diagnostic-reason")
    verify.add_argument("--diagnostic-reason", default=None)
    verify.add_argument("--reuse", action="store_true", help="reuse a matching successful prior result")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    runner = Runner(args.mode)
    try:
        os.chdir(runner.repo_root)
        runner.run_root.mkdir(parents=True, exist_ok=True)
        runner.result["revision"] = runner.revision()
        if args.mode == "setup":
            runner.setup(args.python, args.install_browsers)
        elif args.mode == "preflight":
            runner.preflight()
        else:
            runner.verify(args.test, args.syntax, args.full, args.diagnostic_reason, args.reuse)
            return 0
        runner.save("passed", 0, None)
        return 0
    except CheckError as exc:
        runner.save("failed", exc.exit_code, str(exc))
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - record the failure before re-raising
        if runner.result["status"] == "running":
            runner.save("failed", 2, f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    sys.exit(main())
