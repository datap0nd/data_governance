"""Run the real Wizard app (replay runtime over its SYNTHETIC fixtures) for the film capture (see README.md).

Replay answers instantly, so the capture paces it like a live answer: Gemini "thinks" before each tool call, and each
tool takes about as long as a real read would (a report a few seconds, a calculation under one). The pauses wrap
Wizard's own classes at run time; nothing in the Wizard repository changes."""
from __future__ import annotations
import argparse, asyncio, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
p = argparse.ArgumentParser()
p.add_argument('--wizard', required=True, help='a checkout of the Wizard repository (datap0nd/wizard)')
p.add_argument('--port', type=int, default=8779)
p.add_argument('--transcripts', default=str(HERE / 'transcripts'))
p.add_argument('--identities', default=str(HERE / 'identities.json'))
p.add_argument('--think', type=float, default=1.1, help='seconds before each tool call')
p.add_argument('--text-delay', type=float, default=0.22, help='seconds between streamed text chunks')
p.add_argument('--pace', type=float, default=1.0, help='multiplier for per-tool durations (0 = instant)')
p.add_argument('--data', help='data folder (default: a fresh temporary one)')
a = p.parse_args()
ROOT = Path(a.wizard).resolve()
for sub in ("api", "agent", "connectors", "checks", "documents"):
    sys.path.insert(0, str(ROOT / "services" / sub))
import uvicorn
from wizard_api.app import create_app
from wizard_api.config import load_settings
from wizard_agent import replay
from wizard_connectors.tools import registry


# Seconds a tool takes at pace 1: a source read takes longest, knowledge less, arithmetic least.
DURATION = {'run_report': 3.2, 'search_catalog': 1.6, 'lookup_definitions': 1.8, 'query_attachment': 2.2,
            'read_attachment': 2.0, 'calculate': 0.7, 'render_visual': 1.6, 'check_my_data': 4.5}
real_execute = registry.ToolRegistry.execute
def paced_execute(self, name, raw_args, ctx):
    started = time.monotonic()
    outcome = real_execute(self, name, raw_args, ctx)
    wanted = a.pace * next((s for k, s in DURATION.items() if name.endswith(k)), 1.0)
    time.sleep(max(0.0, wanted - (time.monotonic() - started)))
    return outcome
registry.ToolRegistry.execute = paced_execute

real_run = replay.ReplayRuntime.run
async def paced_run(self, request, emit, tools, cancelled):
    real_call = tools.call
    async def call(name, args):
        if a.think:
            await asyncio.sleep(a.think)
        return await real_call(name, args)
    tools.call = call
    return await real_run(self, request, emit, tools, cancelled)
replay.ReplayRuntime.run = paced_run

data = Path(a.data) if a.data else Path(tempfile.mkdtemp(prefix='wizard-film-'))
data.mkdir(parents=True, exist_ok=True)
env = {"WIZARD_DATA_DIR": str(data), "WIZARD_PORT": str(a.port), "WIZARD_AGENT_RUNTIME": "replay",
       "WIZARD_REPLAY_DELAY_S": str(a.text_delay), "WIZARD_ATTACHMENT_OFFICE": "never",
       "WIZARD_ATTACHMENT_FOLDERS": "none", "WIZARD_TRANSCRIPTS_DIR": a.transcripts,
       "WIZARD_IDENTITIES_FILE": a.identities}
settings = load_settings(env=env, env_file=data / "none.env")
uvicorn.run(create_app(settings), host="127.0.0.1", port=a.port, log_level="warning")
