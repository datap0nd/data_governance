"""Refresh the enrolled portal password from setup's process environment.

Only the profile path is passed on the command line. Never print credentials
or exception details: corrupt credential files may themselves contain secrets.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The installer uses an isolated embedded Python and invokes this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.flow_credentials import load_asap_credentials, save_asap_credentials


def sync_password(profile_dir: Path) -> str:
    password = os.environ.get("DG_SVC_PASSWORD")
    if not password:
        return "No DG_SVC_PASSWORD supplied; existing ASAP/GSCM credential preserved."
    credential = load_asap_credentials(profile_dir)
    if credential is None:
        return (
            "ASAP/GSCM user ID is not enrolled. Open Metronome > Flows > Catalog > "
            "ASAP and save the user ID and password once, then rerun setup."
        )
    save_asap_credentials(credential["username"], password, profile_dir)
    return "ASAP/GSCM password refreshed from DG_SVC_PASSWORD; saved user ID preserved."


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: sync_flow_sso_password.py PROFILE_DIRECTORY", file=sys.stderr)
        return 2
    try:
        message = sync_password(Path(sys.argv[1]))
    except Exception:
        print(
            "ASAP/GSCM password refresh failed. Use the Windows account that "
            "enrolled the credential, or save it again in Metronome > Flows > "
            "Catalog > ASAP, then rerun setup. Portal sign-in was not started.",
            file=sys.stderr,
        )
        return 1
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
