"""Keep application tests independent of an installed Metronome settings file."""
import os

os.environ["DG_ENV_FILE"] = ""
