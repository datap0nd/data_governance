"""setup.ps1 signs in a few worker browsers of each kind, not every slot.

Setup used to open one browser per configured worker slot for every portal:
with twelve background slots and two portals, more than two dozen sign-ins,
one after another, on every run. It now signs in the first two background and
the first two visible worker browsers; the number is the Settings value
"Worker browsers signed in during setup". Every other worker signs in by
itself, with the saved portal sign-in, the first time a Flow needs a portal.

The cases run the real sign-in block of setup.ps1 with stand-in helper
scripts: no browser opens and no portal is contacted. They need Windows
PowerShell, so they run in the pull-request "Windows verifier contracts" job.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POOL = ROOT / "tools" / "flow_pool.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="needs Windows PowerShell")


def _powershell(script: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=120, env=env,
    )


def _chosen(tmp_path, headless, headed, limit=None):
    script = tmp_path / "chosen.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{POOL}'\n"
        f"$FlowSlots = @(1..{headless} | ForEach-Object {{ Get-MetronomeFlowSlot -Slot $_ -BaseProfile 'C:\\P\\bg' }})\n"
        f"$HeadedSlots = @(1..{headed} | ForEach-Object {{ Get-MetronomeFlowSlot -Slot $_ -BaseProfile 'C:\\P\\vis' -Headed }})\n"
        "$Chosen = @(Get-MetronomeSignInProfiles -FlowSlots $FlowSlots -HeadedSlots $HeadedSlots"
        + (f" -Limit {limit}" if limit else "") + ")\n"
        "$Chosen | ForEach-Object { \"$($_.Label)|$($_.Path)\" }\n",
        encoding="ascii",
    )
    result = _powershell(script, dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    return [tuple(line.split("|")) for line in result.stdout.splitlines() if line.strip()]


def test_the_first_two_background_and_two_visible_worker_browsers_are_chosen(tmp_path):
    assert _chosen(tmp_path, headless=12, headed=3) == [
        ("headless service", r"C:\P\bg"), ("headed on-demand", r"C:\P\vis"),
        ("headless slot 2", r"C:\P\bg-2"), ("headed slot 2", r"C:\P\vis-2"),
    ]


def test_a_kind_with_fewer_slots_than_the_number_gives_what_it_has(tmp_path):
    # The default installation: twelve background slots, one visible.
    assert [label for label, _path in _chosen(tmp_path, headless=12, headed=1)] == [
        "headless service", "headed on-demand", "headless slot 2",
    ]
    assert len(_chosen(tmp_path, headless=1, headed=1)) == 2


def test_a_higher_number_from_settings_signs_in_more(tmp_path):
    chosen = _chosen(tmp_path, headless=12, headed=3, limit=5)
    assert len(chosen) == 8  # five background, all three visible
    assert chosen[-1] == ("headless slot 5", r"C:\P\bg-5")


# --- The real sign-in block of setup.ps1 --------------------------------------------

def _stand_in_code(folder: Path) -> Path:
    """A code folder whose helper scripts record what setup asks of them."""
    code = folder / "code"
    (code / "tools").mkdir(parents=True)
    (code / "app").mkdir()
    (code / "tools" / "flow_pool.ps1").write_bytes(POOL.read_bytes())
    (code / "tools" / "get_flow_auth_url.py").write_text(
        "print('asap_portal\\thttps://asap.example.test/login')\n"
        "print('gscm_portal\\thttps://gscm.example.test/login')\n", encoding="utf-8")
    (code / "tools" / "flow_setup_sign_in.py").write_text(
        "import os, sys\n"
        "answer = os.environ.get('SIGN_IN_TEST_ANSWER', '')\n"
        "if answer == 'fail':\n"
        "    sys.exit(1)\n"
        "print(answer)\n", encoding="utf-8")
    (code / "app" / "flow_worker.py").write_text(
        "import json, os, sys\n"
        "arguments = sys.argv[1:]\n"
        "value = lambda name: arguments[arguments.index(name) + 1]\n"
        "with open(os.environ['SIGN_IN_TEST_LOG'], 'a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps([value('--authenticate-adapter'), value('--profile-dir')]) + '\\n')\n",
        encoding="utf-8")
    return code


def _run_sign_in_block(tmp_path, answer, headless=12, headed=3):
    source = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    block = source.split("# Bootstrap the dedicated automation browser profile once.", 1)[1].split(
        "# The worker starts before the app on purpose", 1)[0]
    code = _stand_in_code(tmp_path)
    database = tmp_path / "governance.db"
    database.write_bytes(b"")
    settings = tmp_path / ".env"
    settings.write_text("DG_PORTAL_USER=maya.k\nDG_PORTAL_PASSWORD=fictional-password\n", encoding="ascii")
    log = tmp_path / "signed-in.jsonl"
    script = tmp_path / "sign-in-block.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$Unattended = $false\n"
        f"$PyExe = '{sys.executable}'\n"
        f"$CodeDir = '{code}'\n"
        f"$DbPath = '{database}'\n"
        f"$EnvFile = '{settings}'\n"
        f"$FlowProfile = '{tmp_path / 'bg'}'\n"
        f"$HeadedFlowProfile = '{tmp_path / 'vis'}'\n"
        ". (Join-Path $CodeDir 'tools\\flow_pool.ps1')\n"
        f"$FlowSlots = @(1..{headless} | ForEach-Object {{ Get-MetronomeFlowSlot -Slot $_ -BaseProfile $FlowProfile }})\n"
        f"$HeadedSlots = @(1..{headed} | ForEach-Object {{ Get-MetronomeFlowSlot -Slot $_ -BaseProfile $HeadedFlowProfile -Headed }})\n"
        "#" + block + "\nWrite-Output 'SIGN_IN_STEP_FINISHED'\n",
        encoding="utf-8-sig",
    )
    result = _powershell(script, dict(os.environ, SIGN_IN_TEST_ANSWER=answer, SIGN_IN_TEST_LOG=str(log)))
    assert result.returncode == 0 and "SIGN_IN_STEP_FINISHED" in result.stdout, result.stdout + result.stderr
    signed_in = [tuple(json.loads(line)) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return signed_in, result.stdout


@pytest.mark.parametrize("answer", ["2", "fail", "not a number", "0", "99"])
def test_setup_signs_in_two_of_each_kind_for_each_portal_and_says_what_it_left(tmp_path, answer):
    # "fail" and the unusable answers: setup falls back to two and carries on.
    signed_in, output = _run_sign_in_block(tmp_path, answer)

    expected = [str(tmp_path / name) for name in ("bg", "vis", "bg-2", "vis-2")]
    assert signed_in == [(portal, profile) for portal in ("asap_portal", "gscm_portal") for profile in expected]
    assert "11 more worker browser(s) were not signed in here" in output  # 12 + 3 slots, 4 signed in
    assert "Worker browsers signed in during setup" in output


def test_setup_signs_in_as_many_as_settings_asks_for(tmp_path):
    signed_in, output = _run_sign_in_block(tmp_path, "12")

    assert len(signed_in) == 2 * (12 + 3)
    assert "were not signed in here" not in output


def test_one_slot_of_each_kind_is_signed_in_once_per_portal(tmp_path):
    signed_in, output = _run_sign_in_block(tmp_path, "2", headless=1, headed=1)

    assert signed_in == [(portal, str(tmp_path / name))
                         for portal in ("asap_portal", "gscm_portal") for name in ("bg", "vis")]
    assert "were not signed in here" not in output
