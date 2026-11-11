"""CI installs its browsers even when the runner cannot reach the package mirrors.

A day when the Ubuntu mirrors did not resolve from the runners failed every job
in the browser-install step, minutes in, before a single test had run. The
decisions that step makes are exercised here with stand-ins for the network,
the install command and the browser start.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("install_browsers", ROOT / "tools" / "ci" / "install_browsers.py")
install_browsers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install_browsers)


def _unresolvable(name, port):
    raise OSError("Temporary failure in name resolution")


def _recorder(results):
    """A stand-in for running a command: records it and returns the next exit code."""
    commands = []

    def run(command):
        commands.append(command)
        return results.pop(0)

    return commands, run


def test_mirrors_that_answer_get_the_install_with_system_packages(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    commands, run = _recorder([0])

    code = install_browsers.main(["chromium", "chrome"], resolve=lambda name, port: [object()], run=run,
                                 launch=lambda browsers: [])

    assert code == 0
    assert [command[3:] for command in commands] == [["install", "--with-deps", "chromium", "chrome"]]


def test_mirrors_that_do_not_resolve_are_not_waited_for(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    commands, run = _recorder([0])

    code = install_browsers.main(["chromium", "chrome"], resolve=_unresolvable, run=run,
                                 launch=lambda browsers: [])

    assert code == 0
    assert [command[3:] for command in commands] == [["install", "chromium", "chrome"]]
    assert "mirrors do not resolve" in capsys.readouterr().out


def test_a_failed_install_with_packages_is_followed_by_the_browsers_alone(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    commands, run = _recorder([100, 0])

    code = install_browsers.main(["chromium"], resolve=lambda name, port: [object()], run=run,
                                 launch=lambda browsers: [])

    assert code == 0
    assert [command[3:] for command in commands] == [["install", "--with-deps", "chromium"], ["install", "chromium"]]


def test_a_browser_that_does_not_start_fails_the_job_instead_of_letting_tests_skip(monkeypatch, capsys):
    # Several browser tests skip themselves where no browser starts. An
    # install that "succeeded" without a working browser must not get that far.
    monkeypatch.setattr(sys, "platform", "linux")
    commands, run = _recorder([0])

    code = install_browsers.main(["chromium", "chrome"], resolve=_unresolvable, run=run,
                                 launch=lambda browsers: ["chrome does not start: missing library"])

    assert code == 1
    assert "::error::chrome does not start" in capsys.readouterr().out


def test_nothing_installable_fails_the_job(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    commands, run = _recorder([100, 1])
    started = []

    code = install_browsers.main(["chromium"], resolve=lambda name, port: [object()], run=run,
                                 launch=lambda browsers: started.append(browsers) or [])

    assert code == 1 and started == []


def test_other_systems_install_as_before_without_asking_about_ubuntu_mirrors(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    commands, run = _recorder([0])

    code = install_browsers.main(["chromium", "chrome"], resolve=_unresolvable, run=run,
                                 launch=lambda browsers: [])

    assert code == 0
    assert [command[3:] for command in commands] == [["install", "--with-deps", "chromium", "chrome"]]


def test_the_workflow_installs_browsers_through_this_script():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "python tools/ci/install_browsers.py chromium chrome" in workflow
    # No step is left that dies with apt when the mirrors are unreachable.
    assert "playwright install --with-deps chromium chrome" not in workflow
