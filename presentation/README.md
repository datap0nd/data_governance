# scribble

A six-slide deck introducing **AI365** (`datap0nd/ai365`) to a non-technical
audience: what it is, why it is private, why it costs nothing, and why its
answers can be trusted.

The output is `../scribble.pptx`.

## Rebuilding

```bash
python3 make_pixel_logo.py   # renders assets/pal_*.png (needs Pillow)
npm install pptxgenjs
node build_scribble.js       # writes ../scribble.pptx
```

## The logo

`assets/pal_idle.png` and `assets/pal_wave.png` are the AI365 "pixel pal" —
the little robot that types away in the chat sidebar while the model thinks.
`make_pixel_logo.py` carries its sprite rows and palette verbatim from
`src/OutlookLocalAIChat/UI/ChatPaneWeb.html` in the ai365 repository and
renders them at 48x scale, so the deck's mark and the product's mark are the
same drawing.
