"""Worker-only live console, marker and process-tree reporting."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveObserver:
    def __init__(self, post, progress, environment=None):
        self.post = post
        self.progress_event = progress
        self.lines = []
        self.next_line = 1
        self.dropped = 0
        self.last_post = 0.0
        self.last_output_at = None
        self.stage = None
        self.progress = None
        self.script = None
        self.stop_flag = False
        self._process = None
        self._observer = None
        self._process_events = {"started": 0, "finished": 0}
        self._summarized = set()
        environment = os.environ if environment is None else environment
        self._secrets = sorted({value for key, value in environment.items()
                                if re.search(r"PASS|TOKEN|SECRET|KEY|CREDENTIAL", key, re.I)
                                and isinstance(value, str) and len(value) >= 4},
                               key=len, reverse=True)

    def start_step(self, record):
        self.script = record.get("script_name")
        self.progress = None
        self.flush(force=True)

    def _redact(self, value):
        text = str(value)
        for secret in self._secrets:
            text = text.replace(secret, "[redacted]")
        return text

    def line(self, stream, value, pid):
        clean = self._redact(value)[:2000]
        now = _utc_now()
        self.last_output_at = now
        marker = clean.strip()
        if marker.startswith("::stage::"):
            stage = marker[9:].strip()[:200]
            if stage and stage != self.stage:
                self.stage = stage
                self.progress_event("running", {"stage": "python_stage", "message": stage})
        elif marker.startswith("::progress::"):
            candidate = marker[12:].strip()
            if re.fullmatch(r"(?:\d{1,6}/\d{1,6}|\d{1,3}%)", candidate):
                self.progress = candidate
        line = {"line_no": self.next_line, "stream": stream, "text": clean,
                "pid": pid, "at": now}
        if len(self.lines) < 5000:
            self.lines.append(line)
            self.next_line += 1
        else:
            self.dropped += 1
        return clean

    def _process_timeline(self, observer):
        if observer is None or not hasattr(observer, "drain_events"):
            return
        for state, process in observer.drain_events():
            if state not in self._process_events:
                continue
            count = self._process_events[state]
            self._process_events[state] += 1
            if count < 100:
                self.progress_event("running", {
                    "stage": "python_process_" + state,
                    "message": f"{process.get('label', 'Process')} {state} (PID {process.get('pid')}).",
                    "pid": process.get("pid"), "parent_pid": process.get("parent_pid"),
                    "script": process.get("label"), "exit_code": process.get("exit_code"),
                })
            elif state not in self._summarized:
                self._summarized.add(state)
                self.progress_event("running", {
                    "stage": "python_process_" + state,
                    "message": f"More than 100 process {state} events; see the process tree.",
                })

    def tick(self, process, observer):
        self._process, self._observer = process, observer
        self._process_timeline(observer)
        self.flush(process=process, observer=observer)

    def flush(self, *, process=None, observer=None, force=False, final=False):
        now = time.monotonic()
        if not (force or final or len(self.lines) >= 500 or now - self.last_post >= 2):
            return
        batch = self.lines[:1000]
        processes = observer.snapshot() if observer is not None and hasattr(observer, "snapshot") else []
        payload = {
            "lines": batch, "processes": processes[:300],
            "stage": self.stage, "progress": self.progress, "script": self.script,
            "waiting": len(observer.running_descendants()) if observer is not None else 0,
            "main_exited": process.poll() is not None if process is not None else False,
            "dropped": self.dropped + (getattr(observer, "dropped", 0) if observer is not None else 0),
            "last_output_at": self.last_output_at, "final": final,
        }
        try:
            reply = self.post(payload)
        except Exception:
            # Keep the batch for the next retry; the process itself can continue.
            return
        self.lines = self.lines[len(batch):]
        self.last_post = now
        if reply.get("terminal"):
            self.stop_flag = True

    def stop_requested(self):
        return self.stop_flag

    def finish(self, *, process=None, observer=None, stopped=False):
        process = process or self._process
        observer = observer or self._observer
        if stopped:
            self.progress_event("running", {"stage": "python_stopped",
                                            "message": "Stopped the Python script tree."})
        # Drain the bounded queue, then mark the last snapshot final.
        for _ in range(6):
            if not self.lines:
                break
            self.flush(process=process, observer=observer, force=True)
        self.flush(process=process, observer=observer, force=True, final=True)
