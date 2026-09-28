"""The checked-in film should remain playable and editable after a clone."""

import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import wave


FILM = Path(__file__).resolve().parents[1] / "media" / "metronome-in-sync"


class _References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        for key in ("href", "src", "poster"):
            if key in values:
                self.paths.append(values[key])


def test_film_artifacts_match_manifest():
    manifest = json.loads((FILM / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["durationSeconds"] == 84
    for relative, expected in manifest["artifacts"].items():
        path = FILM / relative
        assert path.is_file(), relative
        assert path.stat().st_size == expected["bytes"], relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == expected["sha256"], relative

    with wave.open(str(FILM / "audio" / "score.wav"), "rb") as score:
        assert score.getnchannels() == 2
        assert score.getframerate() == 48000
        assert score.getsampwidth() == 3
        assert score.getnframes() == 84 * 48000


def test_film_review_pages_resolve_local_media():
    for name in ("animatic-review.html", "review.html"):
        page = FILM / name
        markup = page.read_text(encoding="utf-8")
        parser = _References()
        parser.feed(markup)
        references = parser.paths + re.findall(r"url\(['\"]?([^)'\"]+)", markup)
        for reference in references:
            if not reference or reference.startswith(("#", "http:", "https:", "data:")):
                continue
            target = (FILM / reference.split("?", 1)[0]).resolve()
            assert target.is_relative_to(FILM.resolve()), reference
            assert target.is_file(), f"{name}: {reference}"
    assert "renders/review/animatic.mp4" in (FILM / "animatic-review.html").read_text(encoding="utf-8")
