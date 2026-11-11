"""Every operator setting, declared once.

A setting is declared here with its label, type, default, allowed range and
when a change takes effect. The Settings page, the validation behind it and the
Event Log entry are all drawn from that declaration, and the code that uses the
setting reads it through ``get`` at the moment of use, so a saved change needs
no restart.

Where a value comes from, in order:

1. what was saved on the Settings page (``app_settings``, or the protected
   ``.env`` file for connection values a worker process also needs);
2. the environment variable the setting replaced, so an install configured
   through ``.env`` or the Windows service keeps its values after the update
   that introduces the page;
3. the built-in default.

Nothing is declared here without a reader: a setting that changes no behaviour
is worse than none.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable

from app import config, env_file, settings as store

logger = logging.getLogger("settings")

KINDS = ("int", "float", "bool", "choice", "time", "text", "path", "list", "secret")
NO_SETTINGS_FILE = (
    "Metronome has no settings file (.env) on this computer, so it cannot keep connection "
    "details. Run setup.ps1 on the host PC once."
)
APPLIES = {
    "now": "Applies immediately",
    "next_scan": "Applies at the next scan",
    "new_runs": "Applies to new runs",
    "restart": "Needs a restart",
}
TIME_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# (id, name, one-line description). The order is the order on the page.
SECTIONS = (
    ("connections", "Connections", "The databases and services Metronome signs in to. Passwords can be replaced or cleared, never shown."),
    ("folders", "Folders", "Where Metronome reads reports and keeps Flow files on the host PC."),
    ("scanner", "Scanner", "When scans run, what they read, and how long they wait on PostgreSQL and Power BI."),
    ("freshness", "Freshness and alerts", "The thresholds that decide when a source is outdated and what gets flagged."),
    ("flows", "Flows", "Workers, run limits and timeouts. Worker limits are copied into each run when it is queued, so a change never alters a run already in progress."),
    ("email", "Email", "Limits for emails Metronome sends through Outlook on the host PC."),
    ("updates", "Updates, backups and logs", "How Metronome updates itself, backs up its own database and keeps its logs in check."),
)
SECTION_IDS = tuple(section[0] for section in SECTIONS)
# Groups that offer a "Test connection" button: (section, group) -> test name.
GROUP_TESTS: dict[tuple[str, str], str] = {}
# Settings that keep their own page for now, listed in the section they belong
# to: (label, page, note).
SECTION_LINKS = {
    "connections": (
        ("SQL users and passwords", "users", "Each person's SQL identity for Flow writes."),
        ("AI assistant endpoint and key", "ai", ""),
        ("Power BI sign-in", "scanner", "Connect or disconnect the saved Microsoft account."),
        ("Portal site addresses", "flows", "Under Catalog."),
    ),
    "folders": (("Flows root and enforced paths", "paths", "Shows which Flows a change would affect."),),
    "flows": (("Worker status and per-portal limits", "flow-settings", ""),),
    "updates": (("Install now and update history", "updates", ""),),
}


class SettingError(ValueError):
    """One submitted value is not acceptable; the message is shown beside it."""


class SettingsRejected(ValueError):
    """Nothing was saved. ``errors`` maps each bad key to its message."""

    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))
        self.errors = errors


@dataclass(frozen=True)
class Setting:
    key: str
    section: str
    group: str
    label: str
    kind: str
    default: Any
    help: str = ""
    unit: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    choice_labels: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    applies: str = "now"
    storage_key: str | None = None
    placeholder: str = ""
    item: str = ""            # for lists: "email" validates each entry
    max_items: int = 100
    optional: bool = False    # text, path and numbers: empty is allowed
    # "db": Metronome's own database. "file": the protected .env settings file,
    # for connection values a worker process reads too; storage_key is then
    # the variable's name. Secrets always live in the file and are never read
    # back to a browser.
    store: str = "db"
    reader: Callable[[], Any] | None = None    # composite legacy settings
    writer: Callable[[Any], None] | None = None
    check: Callable[[Any], Any] | None = None  # extra validation; returns the value to save

    @property
    def stored_as(self) -> str:
        return self.storage_key or self.key


_registry: dict[str, Setting] = {}
_listeners: dict[str, list[Callable[[], None]]] = {}
_lock = threading.Lock()


_catalog_loaded = False


def _ensure_catalog() -> None:
    """Load the declarations on first use; importing them earlier would be circular."""
    global _catalog_loaded
    if not _catalog_loaded:
        _catalog_loaded = True
        from app import settings_catalog  # noqa: F401


def register(setting: Setting) -> Setting:
    if setting.kind not in KINDS:
        raise ValueError(f"Unknown setting kind: {setting.kind}")
    if setting.section not in SECTION_IDS:
        raise ValueError(f"Unknown settings section: {setting.section}")
    if setting.applies not in APPLIES:
        raise ValueError(f"Unknown apply rule: {setting.applies}")
    if setting.key in _registry:
        raise ValueError(f"Setting declared twice: {setting.key}")
    if setting.kind == "choice" and not setting.choices:
        raise ValueError(f"Choice setting without choices: {setting.key}")
    if bool(setting.reader) != bool(setting.writer):
        raise ValueError(f"A composite setting needs both a reader and a writer: {setting.key}")
    if setting.store not in ("db", "file"):
        raise ValueError(f"Unknown store for {setting.key}")
    if setting.kind == "secret" and setting.store != "file":
        raise ValueError(f"A secret belongs in the settings file: {setting.key}")
    if setting.store == "file" and not setting.storage_key and setting.writer is None:
        raise ValueError(f"A settings-file value needs its variable name: {setting.key}")
    _registry[setting.key] = setting
    return setting


def definition(key: str) -> Setting:
    _ensure_catalog()
    try:
        return _registry[key]
    except KeyError:
        raise KeyError(f"Unknown setting: {key}") from None


def all_settings() -> list[Setting]:
    _ensure_catalog()
    return list(_registry.values())


def subscribe(keys, callback: Callable[[], None]) -> None:
    """Call ``callback`` after any of ``keys`` is saved (to reschedule a job, say)."""
    for key in keys:
        definition(key)
        _listeners.setdefault(key, []).append(callback)


# --- Values ------------------------------------------------------------------

def _single_line(text: str) -> bool:
    return not any(ord(char) < 32 or ord(char) == 127 or char in "\x85\u2028\u2029" for char in text)


def _number(setting: Setting, value: Any) -> Any:
    whole = setting.kind == "int"
    if isinstance(value, bool):
        raise SettingError(_range_message(setting))
    try:
        if isinstance(value, str):
            value = value.strip()
            number = int(value) if whole else float(value)
        elif isinstance(value, float) and whole:
            if not value.is_integer():
                raise ValueError
            number = int(value)
        elif isinstance(value, (int, float)):
            number = int(value) if whole else float(value)
        else:
            raise ValueError
    except (TypeError, ValueError):
        raise SettingError(_range_message(setting)) from None
    if number != number or number in (float("inf"), float("-inf")):
        raise SettingError(_range_message(setting))
    if (setting.minimum is not None and number < setting.minimum) or \
            (setting.maximum is not None and number > setting.maximum):
        raise SettingError(_range_message(setting))
    return number


def _range_message(setting: Setting) -> str:
    noun = "whole number" if setting.kind == "int" else "number"
    if setting.minimum is not None and setting.maximum is not None:
        return f"Enter a {noun} from {_plain(setting.minimum)} to {_plain(setting.maximum)}."
    if setting.minimum is not None:
        return f"Enter a {noun} of at least {_plain(setting.minimum)}."
    if setting.maximum is not None:
        return f"Enter a {noun} of at most {_plain(setting.maximum)}."
    return f"Enter a {noun}."


def _plain(number: float) -> str:
    return f"{int(number):,}" if float(number).is_integer() else f"{number:g}"


def _list(setting: Setting, value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;\n]", value)
    if not isinstance(value, (list, tuple)):
        raise SettingError("Enter a list, separated by commas.")
    items: list[str] = []
    seen = set()
    for raw in value:
        if not isinstance(raw, str):
            raise SettingError("Enter a list, separated by commas.")
        item = raw.strip()
        if not item:
            continue
        if len(item) > 300 or not _single_line(item):
            raise SettingError("One of the entries is too long or contains a line break.")
        if setting.item == "email" and not EMAIL_RE.fullmatch(item):
            raise SettingError(f"{item} is not an email address.")
        if item.casefold() in seen:
            continue
        seen.add(item.casefold())
        items.append(item)
    if len(items) > setting.max_items:
        raise SettingError(f"No more than {setting.max_items} entries.")
    return items


def validate(setting: Setting, value: Any) -> Any:
    """A submitted value, normalised to the setting's type, or ``SettingError``."""
    kind = setting.kind
    if kind == "secret":
        if not isinstance(value, str) or not env_file.is_single_line(value):
            raise SettingError("Enter the value on one line, without line breaks.")
        return value
    if kind in ("int", "float") and setting.optional and (value is None or (isinstance(value, str) and not value.strip())):
        return ""
    if kind in ("int", "float"):
        result: Any = _number(setting, value)
    elif kind == "bool":
        if not isinstance(value, bool):
            raise SettingError("Choose on or off.")
        result = value
    elif kind == "choice":
        if value not in setting.choices:
            raise SettingError("Choose one of the listed options.")
        result = value
    elif kind == "time":
        if not isinstance(value, str) or not TIME_RE.fullmatch(value.strip()):
            raise SettingError("Choose a time as HH:MM.")
        result = value.strip()
    elif kind in ("text", "path"):
        if not isinstance(value, str):
            raise SettingError("Enter text.")
        result = value.strip()
        if not result and not setting.optional:
            raise SettingError("This cannot be empty.")
        if len(result) > 1000 or not _single_line(result):
            raise SettingError("Enter one line of at most 1,000 characters.")
        if kind == "path" and result and not _absolute(result):
            raise SettingError("Enter a full path, such as D:\\Folder or \\\\server\\share\\folder.")
    else:
        result = _list(setting, value)
    if setting.check is not None:
        try:
            result = setting.check(result)
        except SettingError:
            raise
        except ValueError as exc:
            raise SettingError(str(exc)) from None
    return result


def _absolute(path: str) -> bool:
    return bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+|/)", path))


def _encode(setting: Setting, value: Any) -> str:
    if setting.kind == "bool":
        return "1" if value else "0"
    if setting.kind == "list":
        return json.dumps(list(value), ensure_ascii=False, separators=(",", ":"))
    if setting.kind == "float":
        return repr(float(value))
    return str(value)


def _decode(setting: Setting, raw: str) -> Any:
    """A stored or environment string as a typed value; ``SettingError`` if unusable."""
    if setting.kind == "bool":
        text = str(raw).strip().casefold()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        raise SettingError("Choose on or off.")
    if setting.kind == "list":
        text = str(raw).strip()
        if text.startswith("["):
            try:
                return _list(setting, json.loads(text))
            except (TypeError, ValueError):
                raise SettingError("Enter a list, separated by commas.") from None
        return _list(setting, text)
    # A hand-edited number outside the range is brought back inside it rather
    # than ignored, which is how the environment variables always behaved.
    if setting.kind in ("int", "float"):
        if setting.optional and not str(raw).strip():
            return ""
        try:
            number = float(str(raw).strip())
        except (TypeError, ValueError):
            raise SettingError(_range_message(setting)) from None
        if number != number:
            raise SettingError(_range_message(setting))
        if setting.minimum is not None:
            number = max(setting.minimum, number)
        if setting.maximum is not None:
            number = min(setting.maximum, number)
        return int(number) if setting.kind == "int" else float(number)
    return validate(setting, raw)


def _from_environment(setting: Setting) -> tuple[Any, bool]:
    for name in setting.env:
        raw = os.environ.get(name)
        if raw is None or not str(raw).strip():
            continue
        try:
            return _decode(setting, raw), True
        except SettingError:
            continue
    return None, False


def _resolve(setting: Setting, saved: dict[str, str] | None = None) -> tuple[Any, str]:
    """``(value, source)`` where source is ``app``, ``env`` or ``default``."""
    if setting.reader is not None:
        try:
            value = setting.reader()
        except Exception:
            logger.exception("Could not read setting %s", setting.key)
            return setting.default, "default"
        return value, ("default" if value == setting.default else "app")
    if setting.store == "file":
        return _resolve_file(setting)
    raw = saved.get(setting.stored_as) if saved is not None else _read_one(setting.stored_as)
    if raw is not None:
        try:
            return _decode(setting, raw), "app"
        except SettingError:
            pass
    value, found = _from_environment(setting)
    if found:
        return value, "env"
    return setting.default, "default"


def _resolve_file(setting: Setting) -> tuple[Any, str]:
    """A settings-file value: the file wins, then the Windows environment."""
    name = setting.stored_as
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return setting.default, "default"
    source = "app" if name in config.ENV_FILE_STATUS.get("loaded_names", ()) else "env"
    if setting.kind == "secret":
        return raw, source
    try:
        return _decode(setting, raw), source
    except SettingError:
        return setting.default, "default"


def _write_file(setting: Setting, value: Any) -> None:
    """Keep or remove one line of the settings file, then apply it everywhere."""
    if config.ENV_FILE is None:
        raise SettingError(NO_SETTINGS_FILE)
    text = None if value in (None, "") else _encode(setting, value)
    try:
        env_file.write_value(config.ENV_FILE, setting.stored_as, text)
    except FileNotFoundError:
        raise SettingError(NO_SETTINGS_FILE) from None
    except ValueError as exc:
        raise SettingError(str(exc)) from None
    config.refresh()


def file_value_writer(name: str):
    """A writer for composite settings that keep one variable in the settings file."""
    def write(value) -> None:
        _write_file(Setting(key=name, section=SECTION_IDS[0], group="", label=name, kind="text",
                            default="", optional=True, store="file", storage_key=name), value)

    return write


def _read_one(key: str) -> str | None:
    try:
        return store.get_setting(key)
    except Exception:
        # The database is briefly locked or not there yet; defaults still apply.
        logger.warning("Could not read setting %s; using its default", key)
        return None


def get(key: str) -> Any:
    """The setting's value right now."""
    return _resolve(definition(key))[0]


def snapshot(keys) -> dict[str, Any]:
    """Several settings read together, for freezing into a queued job."""
    wanted = [definition(key) for key in keys]
    saved = _read_all()
    return {setting.key: _resolve(setting, saved)[0] for setting in wanted}


def _read_all() -> dict[str, str] | None:
    try:
        return store.get_all()
    except Exception:
        logger.warning("Could not read saved settings; using defaults")
        return {}


# --- The page ----------------------------------------------------------------

def describe() -> list[dict]:
    """Sections, groups and settings with their current values, for the page."""
    _ensure_catalog()
    config.refresh_if_changed()  # a hand edit to the settings file shows at once
    saved = _read_all()
    sections = []
    for section_id, name, blurb in SECTIONS:
        groups: dict[str, list[dict]] = {}
        for setting in _registry.values():
            if setting.section != section_id:
                continue
            value, source = _resolve(setting, saved)
            if setting.kind == "secret":
                # Never the value: only whether one is saved, and where.
                value = None
            groups.setdefault(setting.group, []).append({
                "key": setting.key, "label": setting.label, "help": setting.help, "kind": setting.kind,
                "unit": setting.unit, "minimum": setting.minimum, "maximum": setting.maximum,
                "choices": list(setting.choices),
                "choice_labels": list(setting.choice_labels or setting.choices),
                "placeholder": setting.placeholder, "optional": setting.optional,
                "default": setting.default, "value": value, "source": source,
                "applies": setting.applies, "applies_text": APPLIES[setting.applies],
                "source_text": _source_text(setting, source),
                "saved": source != "default" if setting.kind == "secret" else None,
            })
        sections.append({
            "id": section_id, "name": name, "blurb": blurb,
            "groups": [{"title": title, "settings": items, "test": GROUP_TESTS.get((section_id, title))}
                       for title, items in groups.items()],
            "links": [{"label": label, "page": page, "note": note}
                      for label, page, note in SECTION_LINKS.get(section_id, ())],
        })
    return sections


def _source_text(setting: Setting, source: str) -> str:
    if source == "app":
        return "Set here"
    if source == "env":
        # For a settings-file value the file is "here", so anything else that
        # supplies it is the Windows environment the service was started with.
        return "From the Windows environment" if setting.store == "file" else "From the .env file"
    return "Not set" if setting.store == "file" and setting.default in ("", None) else "Default"


def save(changes: dict[str, Any]) -> list[dict]:
    """Validate every change, then save them all, or save none.

    A value of ``None`` returns the setting to its default. Returns one record
    per setting whose value changed: ``{key, label, old, new}``.
    """
    _ensure_catalog()
    errors: dict[str, str] = {}
    planned: list[tuple[Setting, Any]] = []
    for key, value in changes.items():
        setting = _registry.get(key)
        if setting is None:
            errors[key] = "This setting does not exist."
            continue
        if setting.kind == "secret":
            errors[key] = "A password is replaced or cleared with its own buttons."
            continue
        try:
            planned.append((setting, None if value is None else validate(setting, value)))
        except SettingError as exc:
            errors[key] = str(exc)
    if errors:
        raise SettingsRejected(errors)
    if any(setting.store == "file" and setting.writer is None for setting, _ in planned) and config.ENV_FILE is None:
        raise SettingsRejected({
            setting.key: NO_SETTINGS_FILE for setting, _ in planned if setting.store == "file" and setting.writer is None
        })

    changed: list[dict] = []
    with _lock:
        saved = _read_all()
        plain: dict[str, str | None] = {}
        for setting, value in planned:
            old, _source = _resolve(setting, saved)
            new = setting.default if value is None else value
            if setting.writer is not None:
                if new == old:
                    continue
                try:
                    setting.writer(new)
                except ValueError as exc:
                    raise SettingsRejected({setting.key: str(exc)}) from None
            elif setting.store == "file":
                if value is not None and value == old and _resolve_file(setting)[1] == "app":
                    continue
                try:
                    _write_file(setting, value)
                except SettingError as exc:
                    raise SettingsRejected({setting.key: str(exc)}) from None
                new = _resolve_file(setting)[0]
            else:
                # Saving the default explicitly still pins it against an
                # environment value; resetting removes the saved row.
                plain[setting.stored_as] = None if value is None else _encode(setting, value)
                if value is None:
                    new = _resolve(setting, {k: v for k, v in (saved or {}).items() if k != setting.stored_as})[0]
            if new != old:
                changed.append({"key": setting.key, "label": setting.label, "old": old, "new": new})
        if plain:
            store.set_many(plain)
    for record in changed:
        for callback in _listeners.get(record["key"], ()):
            try:
                callback()
            except Exception:
                logger.exception("A setting was saved but could not be applied at once: %s", record["key"])
    return changed


def secret(key: str) -> str | None:
    """A secret's value, for the code that signs in with it. Never for a response."""
    setting = definition(key)
    if setting.kind != "secret":
        raise KeyError(f"Not a secret: {key}")
    value, source = _resolve_file(setting)
    return value if source != "default" else None


def save_secret(key: str, value: str | None) -> dict:
    """Replace a secret, or clear it with ``None``. Returns ``{key, label, saved}``."""
    setting = definition(key)
    if setting.kind != "secret":
        raise SettingsRejected({key: "This setting is not a password."})
    with _lock:
        try:
            _write_file(setting, None if value is None else validate(setting, value))
        except SettingError as exc:
            raise SettingsRejected({key: str(exc)}) from None
    for callback in _listeners.get(key, ()):
        try:
            callback()
        except Exception:
            logger.exception("A password was saved but could not be applied at once: %s", key)
    return {"key": key, "label": setting.label, "saved": _resolve_file(setting)[1] != "default"}


def display(value: Any) -> str:
    """A value as the Event Log shows it."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "none"
    if value in ("", None):
        return "empty"
    return str(value)
