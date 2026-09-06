"""Check the deployed direct-file entry point without launching a worker."""
import subprocess
import sys
from pathlib import Path


def test_isolated_direct_worker_entrypoint_from_another_directory(tmp_path):
    worker = Path(__file__).resolve().parents[1] / 'app' / 'flow_worker.py'
    # Embedded Python ignores the working directory/PYTHONPATH. Importing the
    # module inside pytest hides import-order failures in this deployed path.
    result = subprocess.run(
        [sys.executable, '-I', str(worker), '--help'], cwd=tmp_path,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr
    assert '--headed' in result.stdout
    assert '--profile-dir' in result.stdout
    assert not list(tmp_path.iterdir()), '--help must not start a worker or create a profile'
