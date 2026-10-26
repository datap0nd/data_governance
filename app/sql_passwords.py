"""Per-person SQL passwords, kept in the host PC's settings file (.env).

The host PC saves or clears anyone's; each person saves or clears their own
(Users > SQL password). The API reports whether one is saved, never its value.
A Flow job names its owner and the worker reads the password when it connects
(``flow_sql.person_password``).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from app import config, env_file
from app.flow_sql import PERSON_PASSWORD_PREFIX, person_password_setting


def saved_ids() -> set[int]:
    """The people whose SQL password is in the settings file."""
    values = os.environ if config.ENV_FILE is None else env_file.read(config.ENV_FILE)[0]
    return {
        int(name[len(PERSON_PASSWORD_PREFIX):]) for name, value in values.items()
        if value and name.startswith(PERSON_PASSWORD_PREFIX) and name[len(PERSON_PASSWORD_PREFIX):].isdigit()
    }


# Each save rewrites the whole file, so two people saving at once must not
# interleave: the later rewrite would drop the earlier person's password.
_write_lock = threading.Lock()


def store(person_id: int, password: str | None) -> None:
    """Save one person's SQL password, or remove it when ``password`` is None."""
    if config.ENV_FILE is None:
        raise RuntimeError("Metronome has no settings file (.env), so it cannot keep SQL passwords. "
                           "Run setup on the host PC.")
    try:
        with _write_lock:
            env_file.write_value(Path(config.ENV_FILE), person_password_setting(person_id), password)
    except FileNotFoundError as exc:
        raise RuntimeError(f"The settings file {config.ENV_FILE} does not exist. Run setup on the host PC.") from exc
