"""Intentional recording waits, independent of browser event timeout budgets."""
from __future__ import annotations

import math
import time

from app import flow_recording, flow_recording_timing


class Pacing:
    def __init__(self, job):
        self.default = flow_recording_timing.for_job(job)
        self.credit = 0

    def interaction(self, step):
        requested = step.get('delay_before_seconds', self.default)
        explicit = self.credit
        self.credit = 0
        return {'default_seconds': self.default,
                'override_seconds': step.get('delay_before_seconds'),
                'explicit_wait_seconds': explicit,
                'effective_seconds': requested,
                'waited_seconds': max(0, requested - explicit)}

    def event_budget_ms(self, steps):
        """Count nested waits without consuming execution's explicit-wait credit."""
        credit, seconds = self.credit, 0
        for step in flow_recording.walk_steps(steps):
            if step['action'] == 'wait':
                seconds += step['seconds']
                credit += step['seconds']
            elif step['action'] in flow_recording.ACTIONS:
                seconds += max(0, step.get('delay_before_seconds', self.default) - credit)
                credit = 0
        return seconds * 1000


def wait(seconds, update):
    """Keep the existing progress/cancellation channel responsive during buffers."""
    deadline = time.monotonic() + seconds
    last_remaining = None
    while True:
        remaining = max(0, math.ceil(deadline - time.monotonic()))
        if remaining != last_remaining:
            update(remaining)
            last_remaining = remaining
        if not remaining:
            return
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
