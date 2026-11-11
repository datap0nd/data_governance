"""Keep application tests independent of an installed Metronome settings file,
and keep them from paying for work that proves nothing in a test."""
import os
import sys

import pytest

os.environ["DG_ENV_FILE"] = ""


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_flow_files: this test reads the files Metronome keeps beside a Flow, so they are "
        "really generated even where the module asks for skip_flow_files",
    )


@pytest.fixture
def no_settle_pause(monkeypatch):
    """Do not wait between the two reads of a file the test wrote itself.

    The worker pauses half a second between two reads of a download, in case
    the tail is still being flushed to a network share. Where a test writes
    the complete file to local disk first, the pause only adds time; the two
    reads and the comparison still happen. A module opts in with
    ``pytestmark = pytest.mark.usefixtures("no_settle_pause")``. Modules that
    download through a real browser do not use it, and the tests of the
    settling rule itself stub the sleep and do not depend on it.
    """
    for name in ("app.flow_worker", "flow_worker"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "SOURCE_SETTLE_SECONDS"):
            monkeypatch.setattr(module, "SOURCE_SETTLE_SECONDS", 0)


NOT_GENERATED = {
    "state": "not_generated",
    "message": "This test does not generate a Flow's files; mark it real_flow_files if it reads them.",
}


@pytest.fixture
def skip_flow_files(request, monkeypatch):
    """Do not regenerate every Flow's standalone script and manifest on each save.

    Metronome mirrors each Flow into files beside it after every commit that
    touches a Flow. That is about 0.4 seconds per saved Flow, and a test that
    creates dozens of Flows to exercise something else spent most of its time
    there. A module opts in with ``pytestmark = pytest.mark.usefixtures(
    "skip_flow_files")``; a test that reads those files is marked
    ``real_flow_files`` and gets the real mirror. The result carries a state
    no real mirror returns, so a test that depends on it fails loudly.
    """
    if request.node.get_closest_marker("real_flow_files"):
        return
    from app import flow_handover

    def not_generated(db_path, *, flow_id=None, force=False):
        return {flow_id: dict(NOT_GENERATED)} if flow_id is not None else {}

    monkeypatch.setattr(flow_handover, "synchronize", not_generated)
