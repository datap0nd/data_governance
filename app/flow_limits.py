"""Deployment defaults shared by worker scheduling and the stdlib installer."""

MAX_SLOTS = 32
DEFAULT_TOTAL_CAPACITY = 12
DEFAULT_HEADLESS_CAPACITY = 12
DEFAULT_HEADED_CAPACITY = 1
# Worker browsers of each kind that setup.ps1 signs in. Signing in every
# configured slot for every portal made each setup run take many minutes.
DEFAULT_SETUP_SIGN_IN_BROWSERS = 2
DEFAULT_PORTAL_CAPACITY = 4
