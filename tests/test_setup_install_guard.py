"""setup.ps1 refuses to start a second, empty Metronome beside the installed one.

setup.ps1 installs into the folder it is run from and keeps the database
beside that folder. Run from a second copy of the code, it built a new empty
database and pointed the services at it: the app opened blank while the real
database sat unused on disk.

Every case needs Windows PowerShell, so they run in the pull-request "Windows
verifier contracts" job, before a merge can install the script on a live PC.
"""
import os
import subprocess
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "tools" / "setup_install_guard.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="needs Windows PowerShell")
# The last case starts the real installer. It stops at the guard; if the guard
# ever failed to stop it, the installer would go on to install services, so it
# runs only on the disposable Windows CI runner and never on somebody's PC.
ci_runner_only = pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true",
    reason="starts the real setup.ps1; only on the disposable Windows CI runner",
)


def _powershell(script: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "case.ps1"
        path.write_text(script, encoding="ascii")
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)],
            capture_output=True, text=True, timeout=120,
        )


def _check(code_dir, db_path, installed_code="", installed_db="", allow_new=False):
    """The guard's verdict and its message for one situation."""
    result = _powershell(
        f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
        f"$check = Get-MetronomeInstallCheck -CodeDir '{code_dir}' -DbPath '{db_path}' "
        f"-Installed @{{ CodeDir = '{installed_code}'; Database = '{installed_db}' }}"
        + (" -AllowNewDatabase" if allow_new else "")
        + "; $check.Action; $check.Lines"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    return lines[0].strip(), "\n".join(lines[1:])


@pytest.fixture
def places(tmp_path):
    """An installed Metronome with data, and a second copy of the code elsewhere."""
    installed_code = tmp_path / "installed" / "data_governance-main"
    installed_code.mkdir(parents=True)
    (installed_code / "setup.ps1").write_text("# the installed copy\n", encoding="ascii")
    installed_db = tmp_path / "installed" / "governance.db"
    installed_db.write_bytes(b"d" * 5000)
    code = tmp_path / "download" / "data_governance-main"
    code.mkdir(parents=True)
    return SimpleNamespace(
        installed_code=installed_code, installed_db=installed_db,
        code=code, db=code.parent / "governance.db",
    )


@pytest.fixture
def service_settings():
    """Describe an installed service under a throwaway registry key."""
    root = rf"HKCU:\Software\MetronomeSetupTest\{uuid.uuid4().hex}"

    def write(code_dir, database):
        result = _powershell(
            "$ErrorActionPreference = 'Stop'; "
            f"$key = '{root}\\MXAnalytics\\Parameters'; "
            "New-Item -Path $key -Force | Out-Null; "
            f"New-ItemProperty -Path $key -Name AppDirectory -Value '{code_dir}' -PropertyType String -Force | Out-Null; "
            "New-ItemProperty -Path $key -Name AppEnvironmentExtra -PropertyType MultiString -Force "
            f"-Value @('DG_FLOWS_ROOT=C:\\elsewhere\\flows', 'DG_DB_PATH={database}', 'DG_AI_MOCK=true') | Out-Null"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return root

    yield write
    _powershell(f"Remove-Item -LiteralPath '{root}' -Recurse -Force -ErrorAction SilentlyContinue")


def test_a_second_copy_with_no_database_is_refused_and_told_where_to_run(places):
    action, message = _check(places.code, places.db, places.installed_code, places.installed_db)

    assert action == "stop"
    assert str(places.installed_db) in message
    assert str(places.installed_code / "setup.ps1") in message
    assert str(places.code) in message


def test_the_installed_folder_continues_without_comment(places):
    assert _check(places.installed_code, places.installed_db,
                  places.installed_code, places.installed_db) == ("continue", "")
    # The same file spelled the way nssm or a person might have typed it.
    respelled = str(places.installed_db).upper()
    assert _check(places.installed_code, places.installed_db,
                  places.installed_code, respelled) == ("continue", "")


def test_one_database_reached_through_a_junction_is_not_a_second_database(places, tmp_path):
    link = tmp_path / "another-route"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(places.installed_code.parent)],
                          capture_output=True, text=True)
    assert made.returncode == 0, made.stdout + made.stderr
    try:
        assert _check(link / "data_governance-main", link / "governance.db",
                      places.installed_code, places.installed_db) == ("continue", "")
    finally:
        os.rmdir(link)  # removes the junction, not what it points at


def test_two_folders_with_a_database_each_are_shown_side_by_side_for_a_person_to_choose(places):
    # Going on would make Metronome show another database. That is how a PC is
    # pointed back after a second copy was installed by mistake, and equally
    # how the mistake is repeated, so setup has to ask.
    places.db.write_bytes(b"s" * 300)

    action, message = _check(places.installed_code, places.installed_db, places.code, places.db)

    assert action == "ask"
    assert str(places.db) in message and str(places.installed_db) in message
    assert message.count(" KB, last changed ") == 2


def test_no_service_or_a_database_that_is_gone_is_an_ordinary_first_install(places):
    assert _check(places.code, places.db) == ("continue", "")
    gone = places.installed_db.with_name("moved-away.db")
    assert _check(places.code, places.db, places.installed_code, gone) == ("continue", "")


def test_a_new_empty_database_beside_an_installed_one_has_to_be_asked_for(places):
    action, message = _check(places.code, places.db, places.installed_code, places.installed_db,
                             allow_new=True)

    assert action == "notice"
    assert "DG_SETUP_NEW_DATABASE" in message and str(places.installed_db) in message


def test_the_installed_location_is_read_from_the_service_settings(places, service_settings):
    root = service_settings(places.installed_code, places.installed_db)

    result = _powershell(
        f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
        f"$found = Get-MetronomeInstalledLocation -RegistryRoot '{root}'; "
        f"$none = Get-MetronomeInstalledLocation -ServiceName 'NotInstalled' -RegistryRoot '{root}'; "
        "\"$($found.CodeDir)|$($found.Database)|[$($none.CodeDir)$($none.Database)]\""
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"{places.installed_code}|{places.installed_db}|[]"


def test_sizes_are_shown_in_the_unit_that_tells_them_apart():
    # A new empty database is about 1 MB and a real one can be 1 GB. Printed
    # in KB they were "1364 KB" and "1364512 KB".
    result = _powershell(
        f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
        "\"$(Get-MetronomeSizeText -Bytes 300)|$(Get-MetronomeSizeText -Bytes 1396736)|"
        "$(Get-MetronomeSizeText -Bytes 1397260288)\""
    )

    assert result.returncode == 0, result.stdout + result.stderr
    small, medium, large = result.stdout.strip().split("|")
    assert small == "1 KB"
    assert medium.endswith(" MB") and medium.startswith("1")
    assert large.endswith(" GB") and large.startswith("1")


def test_restarting_services_that_are_not_installed_does_nothing():
    result = _powershell(
        f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
        "$started = @(Start-MetronomeInstalledServices -Names @('MetronomeTestNoSuchService', 'MetronomeTestNone*')); "
        "\"started=$($started.Count)\""
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "started=0"


def _data_folder_check(folder):
    result = _powershell(
        f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
        f"$check = Get-MetronomeDataFolderCheck -CodeDir '{folder}'; $check.Action; $check.Lines"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    return lines[0].strip(), "\n".join(lines[1:])


@pytest.fixture
def data_folder(tmp_path):
    """Metronome's data folder as an installation lays it out, with a stray copy of the code in it."""
    folder = tmp_path / "Metronome"
    code = folder / "data_governance-main"
    (code / "app").mkdir(parents=True)
    (code / "setup.ps1").write_text("# the installed copy\n", encoding="ascii")
    (code / "app" / "main.py").write_text("# the installed app\n", encoding="ascii")
    (folder / "governance.db").write_bytes(b"d" * 5000)
    (folder / "logs").mkdir()
    return SimpleNamespace(folder=folder, code=code, db=folder / "governance.db")


def test_setup_run_from_the_data_folder_is_sent_to_the_code_folder_inside_it(data_folder):
    # Reported from a work PC: setup.ps1 was run from the folder that holds
    # governance.db. Setup keeps the database one folder above where it runs,
    # looked there, found nothing and started an empty Metronome.
    action, message = _data_folder_check(data_folder.folder)

    assert action == "stop"
    assert str(data_folder.db) in message
    assert str(data_folder.code / "setup.ps1") in message


def test_a_code_folder_is_not_mistaken_for_the_data_folder(data_folder):
    # The code folder itself, even with a database file inside it, has no
    # subfolder that is a code folder.
    (data_folder.code / "governance.db").write_bytes(b"x" * 10)
    assert _data_folder_check(data_folder.code) == ("continue", "")
    # A folder with the code folder in it but no database is not the data folder.
    data_folder.db.unlink()
    assert _data_folder_check(data_folder.folder) == ("continue", "")


@ci_runner_only
def test_setup_itself_stops_in_the_data_folder_and_creates_nothing_above_it(data_folder):
    places = SimpleNamespace(code=data_folder.folder)  # the stray copy sits beside the database
    _second_copy(places)
    above = sorted(path.name for path in data_folder.folder.parent.iterdir())
    inside = sorted(str(path.relative_to(data_folder.folder)) for path in data_folder.folder.rglob("*"))
    empty_registry = rf"HKCU:\Software\MetronomeSetupTest\{uuid.uuid4().hex}"

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(data_folder.folder / "setup.ps1"), "-Unattended"],
        capture_output=True, text=True, timeout=180,
        env=_setup_environment(DG_SETUP_TEST_SERVICE_ROOT=empty_registry),
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "SETUP STOPPED: this folder holds Metronome's data" in output, output
    assert str(data_folder.code / "setup.ps1") in output, output
    assert "Nothing was changed." in output, output
    # No empty database, Python or anything else one folder up, where setup would have put them.
    assert sorted(path.name for path in data_folder.folder.parent.iterdir()) == above, output
    assert sorted(str(path.relative_to(data_folder.folder)) for path in data_folder.folder.rglob("*")) == inside
    assert data_folder.db.read_bytes() == b"d" * 5000


def test_only_the_copy_a_relaunching_setup_started_counts_as_relaunched():
    def relaunched(marker, recorded, actual):
        result = _powershell(
            f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
            f"Test-MetronomeRelaunchedCopy -Marker '{marker}' -RecordedParent '{recorded}' -ActualParent {actual}"
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    assert relaunched("1", "4242", 4242) == "True"
    # What an older setup.ps1 leaves behind in the window it was run from.
    assert relaunched("1", "", 4242) == "False"
    # Recorded by some other process than the one that started this copy.
    assert relaunched("1", "4242", 5151) == "False"
    assert relaunched("", "4242", 4242) == "False"


def test_a_marker_left_in_the_window_is_true_for_the_started_copy_only():
    # One PowerShell process plays the window: it sets what a relaunching
    # setup.ps1 sets, starts a child the way setup does, then asks itself the
    # same question, as the next setup.ps1 run in that window would.
    result = _powershell(
        f"$ErrorActionPreference = 'Stop'; . '{GUARD}'; "
        "$env:DG_SETUP_RELAUNCHED = '1'; $env:DG_SETUP_RELAUNCH_PARENT = \"$PID\"; "
        "$child = & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        f"-Command \". '{GUARD}'; Test-MetronomeRelaunchedCopy\"; "
        "$here = Test-MetronomeRelaunchedCopy; "
        "\"child=$child here=$here\""
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "child=True here=False"


def _second_copy(places):
    """The real setup.ps1 and its helpers, placed in the second copy of the code."""
    (places.code / "setup.ps1").write_bytes((ROOT / "setup.ps1").read_bytes())
    (places.code / "tools").mkdir()
    for script in (ROOT / "tools").glob("*.ps1"):
        (places.code / "tools" / script.name).write_bytes(script.read_bytes())
    (places.code / "app").mkdir()
    (places.code / "app" / "main.py").write_text("# stand-in\n", encoding="ascii")


def _setup_environment(**extra):
    environment = {
        name: value for name, value in os.environ.items()
        if name not in {"DG_FLOWS_ROOT", "DG_GITHUB_TOKEN", "DG_UPDATE_ZIP_URL", "DG_SETUP_NEW_DATABASE",
                        "DG_SETUP_RELAUNCHED", "DG_SETUP_RELAUNCH_PARENT", "DG_SETUP_SWITCH_CONFIRMED"}
    }
    environment["DG_UPDATE_COMMIT_SHA"] = "0" * 40  # no call to GitHub
    environment.update(extra)
    return environment


@ci_runner_only
def test_a_marker_left_in_the_window_does_not_make_setup_skip_the_stop_and_download(tmp_path):
    # Reported from a work PC: setup.ps1 run a second time in the same
    # PowerShell window went straight to the end, never stopped Metronome and
    # finished with "An instance of the service is already running". The
    # earlier run had left its relaunch marker in that window, and an archive
    # from an earlier update was still on disk.
    places = SimpleNamespace(code=tmp_path / "installed" / "data_governance-main")
    places.code.mkdir(parents=True)
    _second_copy(places)
    project = places.code.parent
    (project / "python313").mkdir()
    (project / "python313" / "python.exe").write_bytes(b"")  # found, so nothing is downloaded
    (project / "_update.zip").write_bytes(b"an archive from an earlier update")
    empty_registry = rf"HKCU:\Software\MetronomeSetupTest\{uuid.uuid4().hex}"

    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(places.code / "setup.ps1"), "-Unattended"],
        capture_output=True, text=True, timeout=300,
        env=_setup_environment(
            DG_SETUP_RELAUNCHED="1", DG_SETUP_TEST_SERVICE_ROOT=empty_registry,
            DG_UPDATE_ZIP_URL="http://127.0.0.1:9/nothing-listens-here.zip",
        ),
    )

    output = result.stdout + result.stderr
    # It starts from the beginning: the download is attempted (and cannot
    # succeed here), and Metronome is only ever stopped after a download.
    assert "already downloaded and extracted" not in output, output
    assert "[3/5] Downloading latest version" in output, output
    assert result.returncode == 1 and "download failed" in output, output


@ci_runner_only
@pytest.mark.parametrize("situation", ["no database here", "another database here",
                                       "another database here and an answer left in the window"])
def test_setup_itself_stops_before_it_creates_or_replaces_anything(places, service_settings, situation):
    # The real setup.ps1, placed in a second copy of the code while the
    # service settings name the installed one.
    _second_copy(places)
    if situation != "no database here":
        places.db.write_bytes(b"s" * 300)
    beside = sorted(path.name for path in places.code.parent.iterdir())
    inside = sorted(str(path.relative_to(places.code)) for path in places.code.rglob("*"))

    environment = _setup_environment(
        DG_SETUP_TEST_SERVICE_ROOT=service_settings(places.installed_code, places.installed_db))
    if situation == "another database here and an answer left in the window":
        # SWITCH typed in an earlier run must not answer for this one.
        environment.update(DG_SETUP_RELAUNCHED="1", DG_SETUP_SWITCH_CONFIRMED="1")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(places.code / "setup.ps1"), "-Unattended"],
        capture_output=True, text=True, timeout=180, env=environment,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "SETUP STOPPED" in output and str(places.installed_db) in output, output
    if situation == "no database here":
        assert "No file at this path." in output, output
    else:
        assert "This file exists: 1 KB" in output, output
        assert "never switches Metronome to another database" in output, output
    assert "Nothing was changed." in output, output
    # No database, Python, download, Flow folders or state beside the copy...
    assert sorted(path.name for path in places.code.parent.iterdir()) == beside, output
    # ...nothing added to the copy itself, and neither database touched.
    assert sorted(str(path.relative_to(places.code)) for path in places.code.rglob("*")) == inside
    assert places.installed_db.read_bytes() == b"d" * 5000
    if situation != "no database here":
        assert places.db.read_bytes() == b"s" * 300
