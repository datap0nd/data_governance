import os

from PIL import Image

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Sprite data lifted verbatim from AI365's chat pane (ChatPaneWeb.html, "pixel pal").
COLORS = {
    "B": (0x5c, 0x8f, 0xff), "D": (0x3f, 0x6c, 0xd1), "W": (0xff, 0xff, 0xff),
    "K": (0x22, 0x24, 0x2a), "Y": (0xf5, 0xc4, 0x51), "G": (0x3d, 0xdc, 0x97),
    "M": (0x6a, 0x6b, 0x72),
}

IDLE = [
    "......Y......",
    "......D......",
    "..BBBBBBBBB..",
    "..BWWBBBWWB..",
    "..BWKBBBKWB..",
    "..BBBBBBBBB..",
    "...BDDDDDB...",
    "....BBBBB....",
    "..B..BBB..B..",
    "..BB.....BB..",
    "...MMMMMMM...",
    "..MMMMMMMMM..",
]

WAVE = [
    "......G......",
    "......D......",
    "..BBBBBBBBB..",
    "..BWWBBBWWB..",
    "..BWKBBBKWB..",
    "..BBBBBBBBB..",
    "...BDDDDDB...",
    "....BBBBB....",
    ".....BBB.....",
    "..BB.....BB..",
    "..BMMMMMMMB..",
    "..MMMMMMMMM..",
]


def render(rows, scale, path, trim=True):
    # Trim fully-empty edge columns so the mark sits tight in its box.
    cols = range(len(rows[0]))
    if trim:
        used = [x for x in cols if any(r[x] != "." for r in rows)]
        x0, x1 = min(used), max(used) + 1
    else:
        x0, x1 = 0, len(rows[0])
    w, h = (x1 - x0) * scale, len(rows) * scale
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(rows):
        for x in range(x0, x1):
            c = COLORS.get(row[x])
            if not c:
                continue
            for dy in range(scale):
                for dx in range(scale):
                    px[(x - x0) * scale + dx, y * scale + dy] = c + (255,)
    img.save(path)
    print(path, img.size)


render(IDLE, 48, os.path.join(OUT, "pal_idle.png"))
render(WAVE, 48, os.path.join(OUT, "pal_wave.png"))
