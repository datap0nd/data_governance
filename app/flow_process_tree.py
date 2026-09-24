"""Worker-only observation of a Python script and the processes it starts."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import psutil
except ImportError:  # portable scripts and minimal workers still run
    psutil = None


class ProcessTreeObserver:
    def __init__(self):
        self.root_pid = None
        self._known = {}
        self._events = []
        self.dropped = 0

    def start(self, process):
        self.root_pid = process.pid
        if psutil is not None:
            try:
                self._record(psutil.Process(process.pid), None)
            except psutil.Error:
                pass

    @staticmethod
    def _label(process):
        try:
            args = process.cmdline()
            for item in args[1:]:
                if str(item).casefold().endswith(".py"):
                    return Path(item).name
            return Path(args[0]).name if args else process.name()
        except (psutil.Error, IndexError):
            return "process"

    def _record(self, process, parent_pid):
        try:
            pid, created = process.pid, process.create_time()
            key = (pid, created)
            if key in self._known:
                return self._known[key]
            name = self._label(process)
            if name.casefold() == "conhost.exe":
                return None
            item = {
                "pid": pid, "parent_pid": parent_pid, "label": name,
                "started_at": datetime.fromtimestamp(created, timezone.utc).isoformat(),
                "state": "running", "exit_code": None, "cpu_percent": 0.0,
                "memory_bytes": 0, "duration_seconds": 0,
            }
            self._known[key] = (process, item)
            self._events.append(("started", dict(item)))
            return self._known[key]
        except psutil.Error:
            return None

    def sample(self):
        if psutil is None:
            return
        for _key, (process, item) in list(self._known.items()):
            try:
                for child in process.children(recursive=True):
                    self._record(child, process.pid if child.ppid() == process.pid else child.ppid())
            except psutil.Error:
                pass
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    item["cpu_percent"] = round(process.cpu_percent(interval=None), 1)
                    item["memory_bytes"] = process.memory_info().rss
                    item["duration_seconds"] = max(0, round(time.time() - process.create_time()))
                    continue
            except psutil.Error:
                pass
            if item["state"] == "running":
                item["state"] = "finished"
                item["duration_seconds"] = max(0, round(time.time() - _key[1]))
                try:
                    item["exit_code"] = process.wait(timeout=0)
                except psutil.Error:
                    # An orphan or a process created by another interpreter
                    # may not expose its return code to this worker.
                    pass
                self._events.append(("finished", dict(item)))

    def running_descendants(self):
        self.sample()
        return [item for (_process, item) in self._known.values()
                if item["pid"] != self.root_pid and item["state"] == "running"]

    def snapshot(self):
        self.sample()
        return [dict(item) for _process, item in list(self._known.values())[:300]]

    def drain_events(self):
        events, self._events = self._events, []
        return events

    def note_drop(self):
        self.dropped += 1

    def kill_all(self):
        if psutil is None:
            return
        for process, item in reversed(list(self._known.values())):
            if item["state"] == "running":
                try:
                    process.kill()
                    item["state"] = "stopped"
                except psutil.Error:
                    pass
