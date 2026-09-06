"""Frozen playback pacing, separate from a recording's validated actions."""
SETTING = 'flows_recording_wait_seconds'
DEFAULT_SECONDS = 10
MAX_SECONDS = 600


def validate_seconds(value):
    if type(value) is not int or not 1 <= value <= MAX_SECONDS:
        raise ValueError('Recording wait must be a whole number from 1 to 600 seconds.')
    return value


def configured(db):
    from app.flow_paths import setting
    raw = setting(db, SETTING, str(DEFAULT_SECONDS))
    try:
        return validate_seconds(int(raw))
    except (TypeError, ValueError):
        return DEFAULT_SECONDS


def for_job(job):
    return validate_seconds((job.get('execution') or {}).get('recording_wait_seconds', DEFAULT_SECONDS))
