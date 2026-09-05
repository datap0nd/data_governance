"""Canonical export matrix shared by serial and parallel Flow execution."""
from __future__ import annotations

import json

CAPABILITY = 'flow_download_tasks_v1'
HEADED_CAPABILITY = 'flow_headed_download_tasks_v1'


def supported(job: dict, capabilities: dict) -> bool:
    return bool(capabilities.get(CAPABILITY) and (
        job.get('execution', {}).get('browser_mode') != 'headed' or capabilities.get(HEADED_CAPABILITY)))


def task_key(export_view, period) -> str:
    return json.dumps({'export_view': export_view, 'period_key': period}, sort_keys=True)


def task_matrix(job: dict) -> list[dict]:
    periods = job['downloads'].get('periods') or [None]
    report = job.get('report') or {}
    links = report.get('download_links') or []
    dashboard = job.get('site', {}).get('adapter') == 'asap_portal' and bool(links)
    exports = links if dashboard else report.get('export_views') or [None]
    result = []
    for export in exports:
        for period in periods:
            result.append({'ordinal': len(result) + 1, 'key': task_key(export, period),
                           'period': period, 'export_view': export, 'download_link': export if dashboard else None})
    return result


def parallelism(job: dict) -> int:
    execution = job.get('execution') or {}
    if job.get('job_type') == 'sql_retry' or job.get('flow', {}).get('source_type', 'portal') != 'portal':
        return 1
    return max(1, min(5, int(execution.get('download_parallelism') or 1)))


def enabled(job: dict) -> bool:
    return parallelism(job) > 1 and len(task_matrix(job)) > 1
