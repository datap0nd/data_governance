"""setup.ps1's fast path: what an unattended update may skip, and log rotation.

The text contracts run everywhere. The PowerShell cases run on Windows only,
which is where the pull-request "Windows verifier contracts" job runs them
before a merge can install the script on a live PC.
"""
import json
import os
import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "setup.ps1"
FAST_PATH = ROOT / "tools" / "setup_fast_path.ps1"
APPLY_UPDATE = ROOT / "tools" / "apply_update.ps1"
RUN_WORKER = ROOT / "tools" / "run_flow_worker.ps1"
GIT_ATTRIBUTES = ROOT / ".gitattributes"
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
POWERSHELL_SCRIPTS = sorted([*ROOT.glob("*.ps1"), *(ROOT / "tools").glob("*.ps1")])
windows_only = pytest.mark.skipif(os.name != "nt", reason="needs Windows PowerShell")


def _powershell(script: str) -> subprocess.CompletedProcess:
    # Run from a file: passing script text on the command line would put its
    # quotes through Windows' argument rules first.
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "case.ps1"
        path.write_text(script, encoding="ascii")
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)],
            capture_output=True, text=True, timeout=120,
        )


def _helpers(body: str) -> str:
    return f"$ErrorActionPreference = 'Stop'; . '{FAST_PATH}'; {body}"


# --- Text contracts (every platform) -------------------------------------

def test_every_block_setup_opens_is_closed():
    # No PowerShell parser exists off Windows; an unbalanced brace is the
    # commonest way to break a script that cannot be run here.
    for script in POWERSHELL_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert source.count("{") == source.count("}"), script.name
    # Parentheses also appear in prose ("1) the folder"), so only the scripts
    # that are free of that are counted.
    for script in (SETUP, FAST_PATH, APPLY_UPDATE, RUN_WORKER):
        source = script.read_text(encoding="utf-8")
        assert source.count("(") == source.count(")"), script.name


def test_logs_rotate_while_a_service_runs_not_only_after_a_quiet_day():
    source = SETUP.read_text(encoding="utf-8")
    # 86400 meant "never rotate a file written to in the last day", which a
    # running service's log always is.
    assert "AppRotateSeconds 86400" not in source
    assert "AppRotateBytes 10485760" not in source
    assert "$LogRotateBytes = 10485760" in source
    for service in ("$ServiceName", "$AuditorServiceName", "$FlowServiceName", "$SlotService"):
        assert f"& $NssmExe set {service} AppRotateFiles 1" in source
        assert f"& $NssmExe set {service} AppRotateOnline 1" in source
        assert f"& $NssmExe set {service} AppRotateSeconds 0" in source
        assert f"& $NssmExe set {service} AppRotateBytes $LogRotateBytes" in source


def test_worker_transcript_is_moved_aside_under_a_name_the_cleanup_recognises():
    from app.log_housekeeping import ROTATED

    source = RUN_WORKER.read_text(encoding="utf-8")
    rotate = source.index("Move-Item -LiteralPath $WorkerLog")
    assert rotate < source.index("Start-Transcript -Path $WorkerLog -Append")
    assert '"flow_worker_{0}-{1}.log" -f $WorkerId, (Get-Date -Format "yyyyMMddTHHmmss.fff")' in source
    assert ROTATED.match("flow_worker_bi-desktop-headed-20261111T072500.123.log")


def test_a_relaunched_setup_resumes_after_extraction():
    source = SETUP.read_text(encoding="utf-8")
    guard = source.index('$ResumeAfterExtraction = ($env:DG_SETUP_RELAUNCHED -eq "1") -and (Test-Path $ZipPath -PathType Leaf)')
    download = source.index("# --- Download latest code ---")
    relaunch = source.index('$env:DG_SETUP_RELAUNCHED = "1"')
    resume = source.index("} # end of the download, stop and extract stages a relaunched copy skips")
    stamp = source.index('Set-Content "$CodeDir\\VERSION" $ver')
    assert guard < download < relaunch < resume < stamp
    # What the skipped stages used to define is defined for both paths.
    for name in ("$HadExistingService = [bool]$existing", "$existingFlowService = Get-Service",
                 "$ExistingAuditorService = Get-Service", "$Inner = $null"):
        assert source.index(name) < download
    # A resumed copy has no extracted folder name; the requested commit stamps VERSION.
    assert "if (-not $CommitSha -and $LatestSha)" in source


def test_fast_path_only_applies_to_unattended_updates_and_fails_towards_full_work():
    source = SETUP.read_text(encoding="utf-8")
    helper_load = source.index(". $SetupFastPath; $SetupFastPathLoaded = $true")
    assert source.index("if (Test-Path $SetupFastPath -PathType Leaf)") < helper_load
    for condition in (
        "$DependenciesCurrent = $Unattended -and (Test-SetupStepCurrent -Step 'dependencies'",
        "if ($Unattended -and (Test-SetupStepCurrent -Step 'flows_acl'",
        "if ($Unattended -and -not $env:DG_SVC_PASSWORD -and $HadExistingService -and $existingFlowService -and $ExistingAuditorService -and",
        "$HeadedTasksReusable = $Unattended -and (Test-SetupStepCurrent -Step 'headed_tasks'",
        "if ($Unattended -and (Test-SetupStepCurrent -Step 'rdp_guard'",
    ):
        assert condition in source, condition
    # The wrappers answer "not current" without the helper file or a fingerprint.
    assert "if (-not $script:SetupFastPathLoaded -or -not $Fingerprint) { return $false }" in source
    assert "$ApplyServiceConfig = $true" in source
    # A changed installer re-applies services and tasks without a version bump.
    assert source.count("(Get-SetupScriptsFingerprint)") >= 2
    # Extraction keeps the original command as its fallback.
    assert "if (Get-Command Expand-MetronomeArchive -ErrorAction SilentlyContinue)" in source
    assert "Expand-Archive -Path $ZipPath -DestinationPath $TempExtract -Force" in source


def test_services_are_asked_to_stop_together_before_the_per_service_waits():
    source = SETUP.read_text(encoding="utf-8")
    relaxed = source.index('$ErrorActionPreference = "Continue"')
    request = source.index("& sc.exe stop $RunningService.Name 2>&1 | Out-Null")
    first_wait = source.index("& $NssmExe stop $ServiceName 2>&1 | Out-Null")
    strict = source.index('$ErrorActionPreference = "Stop"', relaxed)
    # Native stderr under "Stop" would end the script, so the request sits in
    # the relaxed region, before the stops whose waits it shortens.
    assert relaxed < request < first_wait < strict
    assert 'throw "Metronome did not stop within 30 seconds. Update aborted before replacing code."' in source


def test_unattended_update_does_not_wait_two_minutes_for_worker_registration():
    source = SETUP.read_text(encoding="utf-8")
    assert "$WorkerPollAttempts = if ($Unattended) { 10 } else { 60 }" in source
    assert "for ($attempt = 0; $attempt -lt $WorkerPollAttempts; $attempt++)" in source
    assert 'throw "Metronome failed its localhost health check after unattended setup' in source


def test_bridge_accepts_the_commit_prefix_setup_stamps():
    # VERSION is "<timestamp>-<first 9 characters of the commit>". Comparing
    # it with EndsWith(<40 characters>) reported every good update as failed.
    source = APPLY_UPDATE.read_text(encoding="utf-8")
    assert "$deployed.EndsWith($TargetSha" not in source
    assert "$TargetSha.StartsWith($deployedCommit)" in source
    stamp = re.search(r"comment\.decode\(\)\[:(\d+)\]", SETUP.read_text(encoding="utf-8"))
    assert stamp and 7 <= int(stamp.group(1)) <= 40


def test_update_archive_leaves_out_release_test_packages(tmp_path):
    checkout = tmp_path / "checkout"
    for relative in (
        "docs/testing/README.md", "docs/testing/releases/2026-11-11-x/test-report.md",
        "docs/flow_standalone.md", "docs/testing-notes.md", "app/main.py", "tests/test_flows.py",
        "tools/setup_fast_path.ps1", "setup.ps1",
    ):
        path = checkout / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    (checkout / ".gitattributes").write_bytes(GIT_ATTRIBUTES.read_bytes())
    git = ["git", "-C", str(checkout), "-c", "user.name=Fixture",
           "-c", "user.email=fixture@example.invalid", "-c", "core.autocrlf=false"]
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True)
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "fixture"], check=True, capture_output=True)
    archive = tmp_path / "update.zip"
    subprocess.run([*git, "archive", "--format=zip", "-o", str(archive), "HEAD"], check=True, capture_output=True)

    with zipfile.ZipFile(archive) as bundle:
        names = set(bundle.namelist())
    assert not [name for name in names if name.startswith("docs/testing/")]
    # The app reads this document at run time, and the installer needs its helpers.
    assert {"docs/flow_standalone.md", "docs/testing-notes.md", "app/main.py", "tests/test_flows.py",
            "tools/setup_fast_path.ps1", "setup.ps1"} <= names


def test_the_app_reads_no_document_the_archive_leaves_out():
    for source in (ROOT / "app").rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        assert "docs/testing" not in text and "'docs' / 'testing'" not in text, source


def test_installer_scripts_are_checked_on_windows_before_merge():
    workflow = WORKFLOW.read_text(encoding="utf-8")
    step = workflow[workflow.index("Check Windows verifier contracts before merge"):]
    command = step[:step.index("- uses:")]
    for test_file in ("tests/test_check_command.py", "tests/test_setup_fast_path.py",
                      "tests/test_unattended_update_scripts.py", "tests/test_setup_worker_health.py"):
        assert test_file in command, test_file


# --- PowerShell behaviour (Windows) ---------------------------------------

@windows_only
@pytest.mark.parametrize("script", POWERSHELL_SCRIPTS, ids=lambda path: path.name)
def test_powershell_parses_every_script(script):
    command = (
        "$tokens = $null; $errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tokens, [ref]$errors); "
        "$errors | ForEach-Object { \"line $($_.Extent.StartLineNumber): $($_.Message)\" }; "
        "exit $errors.Count"
    )
    result = _powershell(command)
    assert result.returncode == 0, result.stdout + result.stderr


@windows_only
def test_fingerprints_are_stable_and_sensitive():
    result = _powershell(_helpers(
        "$a = Get-MetronomeFingerprint -Parts @('one','two'); "
        "$b = Get-MetronomeFingerprint -Parts @('one','two'); "
        "$c = Get-MetronomeFingerprint -Parts @('one','three'); "
        "$d = Get-MetronomeFingerprint -Parts @('onetwo'); "
        "\"$a|$b|$c|$d\""
    ))
    assert result.returncode == 0, result.stderr
    first, second, changed, joined = result.stdout.strip().split("|")
    assert first == second and re.fullmatch(r"[0-9a-f]{64}", first)
    assert len({first, changed, joined}) == 3


@windows_only
def test_file_fingerprint_follows_content_and_presence(tmp_path):
    one, two = tmp_path / "requirements.txt", tmp_path / "wheel.whl"
    one.write_text("fastapi==1\n", encoding="utf-8")
    two.write_bytes(b"wheel")
    command = _helpers(f"Get-MetronomeFileFingerprint -Paths @('{one}','{two}')")
    before = _powershell(command).stdout.strip()
    assert _powershell(command).stdout.strip() == before
    one.write_text("fastapi==2\n", encoding="utf-8")
    edited = _powershell(command).stdout.strip()
    two.unlink()
    removed = _powershell(command).stdout.strip()
    assert len({before, edited, removed}) == 3 and all(re.fullmatch(r"[0-9a-f]{64}", item)
                                                      for item in (before, edited, removed))


@windows_only
def test_step_state_round_trips_and_survives_a_damaged_file(tmp_path):
    state = tmp_path / "updates" / "setup_state.json"
    result = _powershell(_helpers(
        f"$p = '{state}'; "
        "$s0 = Get-MetronomeSetupState -Path $p; "
        "$r1 = Set-MetronomeStepCurrent -Path $p -Step 'dependencies' -Fingerprint 'abc'; "
        "$r2 = Set-MetronomeStepCurrent -Path $p -Step 'services' -Fingerprint 'def'; "
        "$s1 = Get-MetronomeSetupState -Path $p; "
        "\"$($s0.Count)|$r1|$r2|"
        "$(Test-MetronomeStepCurrent -State $s1 -Step 'dependencies' -Fingerprint 'abc')|"
        "$(Test-MetronomeStepCurrent -State $s1 -Step 'dependencies' -Fingerprint 'ABC')|"
        "$(Test-MetronomeStepCurrent -State $s1 -Step 'services' -Fingerprint '')|"
        "$(Test-MetronomeStepCurrent -State $s1 -Step 'missing' -Fingerprint 'abc')|"
        "$(Test-MetronomeStepCurrent -State $null -Step 'services' -Fingerprint 'def')\""
    ))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0|True|True|True|False|False|False|False"
    assert json.loads(state.read_text(encoding="utf-8")) == {"dependencies": "abc", "services": "def"}

    state.write_text("{ this is not json", encoding="utf-8")
    damaged = _powershell(_helpers(
        f"$p = '{state}'; $s = Get-MetronomeSetupState -Path $p; "
        "$r = Set-MetronomeStepCurrent -Path $p -Step 'services' -Fingerprint 'xyz'; \"$($s.Count)|$r\""
    ))
    assert damaged.stdout.strip() == "0|True", damaged.stderr
    assert json.loads(state.read_text(encoding="utf-8")) == {"services": "xyz"}


@windows_only
def test_archive_extraction_matches_the_archive_and_replaces_a_leftover_folder(tmp_path):
    archive = tmp_path / "update.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("data_governance-abc1234/app/main.py", "print('new')\n")
        bundle.writestr("data_governance-abc1234/tools/setup_fast_path.ps1", "# helpers\n")
        bundle.writestr("data_governance-abc1234/docs/flow standalone.md", "spaces in names\n")
    target = tmp_path / "_extract_temp"
    (target / "data_governance-old").mkdir(parents=True)
    (target / "data_governance-old" / "stale.txt").write_text("left by a failed run", encoding="utf-8")

    result = _powershell(_helpers(f"Expand-MetronomeArchive -ZipPath '{archive}' -Destination '{target}'"))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"framework", "expand-archive"}
    assert [path.name for path in target.iterdir()] == ["data_governance-abc1234"]
    inner = target / "data_governance-abc1234"
    assert (inner / "app" / "main.py").read_text(encoding="utf-8") == "print('new')\n"
    assert (inner / "docs" / "flow standalone.md").exists()


@windows_only
def test_task_is_reused_only_when_its_command_is_exactly_the_intended_one():
    task = ("[pscustomobject]@{ Actions = @([pscustomobject]@{ Execute = 'C:\\py\\pythonw.exe'; "
            "Arguments = '\"C:\\app\\flow_worker.py\" --headed'; WorkingDirectory = 'C:\\app' }) }")
    result = _powershell(_helpers(
        f"$t = {task}; "
        "$same = Test-MetronomeTaskActionCurrent -Task $t -Execute 'C:\\py\\pythonw.exe' -Arguments '\"C:\\app\\flow_worker.py\" --headed' -WorkingDirectory 'C:\\app'; "
        "$args = Test-MetronomeTaskActionCurrent -Task $t -Execute 'C:\\py\\pythonw.exe' -Arguments '\"C:\\app\\flow_worker.py\"' -WorkingDirectory 'C:\\app'; "
        "$dir = Test-MetronomeTaskActionCurrent -Task $t -Execute 'C:\\py\\pythonw.exe' -Arguments '\"C:\\app\\flow_worker.py\" --headed' -WorkingDirectory 'C:\\other'; "
        "$none = Test-MetronomeTaskActionCurrent -Task $null -Execute 'x' -Arguments 'y' -WorkingDirectory 'z'; "
        "$two = Test-MetronomeTaskActionCurrent -Task ([pscustomobject]@{ Actions = @(1, 2) }) -Execute 'x' -Arguments 'y' -WorkingDirectory 'z'; "
        "\"$same|$args|$dir|$none|$two\""
    ))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True|False|False|False|False"


@windows_only
def test_bridge_version_rule_accepts_a_prefix_and_rejects_another_commit():
    target = "bd09c944a8f33675167851bd6f96d6c7c0667fb0"
    rule = (
        "function Check($deployed, $TargetSha) { $deployedCommit = ''; "
        "if ($deployed -match '-([0-9a-fA-F]{7,40})$') { $deployedCommit = $Matches[1].ToLowerInvariant() }; "
        "return [bool]($deployedCommit -and $TargetSha.StartsWith($deployedCommit)) }; "
    )
    # The rule under test must be the one the bridge runs.
    source = APPLY_UPDATE.read_text(encoding="utf-8")
    assert "if ($deployed -match '-([0-9a-fA-F]{7,40})$') { $deployedCommit = $Matches[1].ToLowerInvariant() }" in source
    result = _powershell(
        rule
        + f"\"$(Check '20261111-101500-bd09c944a' '{target}')|$(Check '20261111-101500-BD09C944A' '{target}')|"
        + f"$(Check '20261111-101500-{target}' '{target}')|$(Check '20261111-101500-aaaaaaaaa' '{target}')|"
        + f"$(Check '20261111-101500' '{target}')|$(Check '' '{target}')\""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True|True|True|False|False|False"
