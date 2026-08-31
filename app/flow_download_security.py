"""Shared rejection signals for portal downloads and HTTP export replay."""

from __future__ import annotations


SIGN_IN_TEXT_MARKERS = (
    "single sign on",
    "please enter your password",
    "verification code",
    "session expired",
    "sign in to continue",
    "login required",
    "access denied",
    "not authorized",
    "an unexpected error occurred",
)


def looks_like_sign_in(value: bytes | str) -> bool:
    if isinstance(value, bytes):
        text = value.decode("latin-1", errors="replace")
    else:
        text = value
    folded = text.casefold()
    return any(marker in folded for marker in SIGN_IN_TEXT_MARKERS)
