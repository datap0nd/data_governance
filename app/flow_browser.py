"""One Playwright implementation, with global browser choice frozen per job."""
from __future__ import annotations

import os
from pathlib import Path

SETTING = 'flows_browser_channel'
DEFAULT = 'chrome'
CHANNELS = {'chrome': 'Google Chrome', 'msedge': 'Microsoft Edge'}
CAPABILITY = 'browser_switch_v1'


def configured(db):
    from app.flow_paths import setting
    value = setting(db, SETTING, DEFAULT)
    if value not in CHANNELS:
        raise ValueError('Choose Google Chrome or Microsoft Edge in Flows Settings.')
    return value


def channel_for(job):
    # Jobs queued before this feature retain the original Edge behavior.
    channel = job.get('execution', {}).get('browser_channel', 'msedge')
    if channel not in CHANNELS:
        raise ValueError('Unsupported Flow browser channel.')
    return channel


def profile_for(base, channel):
    if channel not in CHANNELS:
        raise ValueError('Unsupported Flow browser channel.')
    # Keep the established Edge profile in place. The enclosing worker lock
    # owns this entire tree, including the separate Chrome user-data directory.
    return Path(base) / 'chrome-profile' if channel == 'chrome' else Path(base)


def can_claim(job, capabilities):
    if job.get('flow', {}).get('source_type') in {'file', 'outlook'} or job.get('job_type') == 'sql_retry':
        return True
    channel = channel_for(job)
    return channel == 'msedge' or bool(capabilities.get(CAPABILITY))


def launch(playwright, base, channel, *, headed, downloads=None, timezone=None):
    options = {'channel': channel, 'headless': not headed, 'accept_downloads': True}
    if downloads is not None:
        options['downloads_path'] = str(downloads)
    if timezone:
        options.update(timezone_id=timezone, viewport={'width': 1440, 'height': 900})
    return playwright.chromium.launch_persistent_context(str(profile_for(base, channel)), **options)
