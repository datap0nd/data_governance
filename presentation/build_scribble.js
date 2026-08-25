const pptxgen = require("pptxgenjs");
const path = require("path");

const ASSETS = path.join(__dirname, "assets");
const PAL_IDLE = path.join(ASSETS, "pal_idle.png");
const PAL_WAVE = path.join(ASSETS, "pal_wave.png");

// Palette taken from AI365's own pixel-pal sprite colours.
const INK = "1A1C24";
const INK2 = "262932";
const BLUE = "5C8FFF";
const BLUED = "3F6CD1";
const GOLD = "F5C451";
const MINT = "3DDC97";
const GREY = "6A6B72";
const SOFT = "9096A3"; // readable muted tone on dark backgrounds
const LIGHT = "F4F6FB";
const WHITE = "FFFFFF";
const TEXT = "2B2E38";
const MUTED = "6E7382";
const DIM = "C9CCD6";

const HEAD = "Arial";
const BODY = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.3 x 7.5
pres.author = "AI365";
pres.title = "AI365 - Scribble";

const cardShadow = () => ({
  type: "outer", color: "8A93A8", blur: 14, offset: 3, angle: 90, opacity: 0.2,
});

// The recurring motif: a small triad of pixels.
function pixelTriad(slide, x, y, colors) {
  const s = 0.13;
  colors.forEach((c, i) => {
    slide.addShape(pres.ShapeType.rect, {
      x: x + i * (s + 0.07), y, w: s, h: s, fill: { color: c }, line: { width: 0 },
    });
  });
}

function eyebrow(slide, x, y, text, color) {
  slide.addText(text, {
    x, y, w: 8, h: 0.28, fontFace: HEAD, fontSize: 11.5, bold: true,
    color, charSpacing: 2.2, margin: 0, valign: "middle",
  });
}

/* ------------------------------------------------------------------ */
/* 1. Title                                                            */
/* ------------------------------------------------------------------ */
const s1 = pres.addSlide();
s1.background = { color: INK };

s1.addShape(pres.ShapeType.rect, {
  x: 8.35, y: 1.5, w: 4.05, h: 4.5, fill: { color: INK2 }, line: { width: 0 },
});
// scattered pixels around the mark
[[8.15, 1.3, GOLD], [12.28, 1.3, BLUE], [8.15, 5.94, MINT], [12.28, 5.94, BLUED]]
  .forEach(([x, y, c]) => s1.addShape(pres.ShapeType.rect, {
    x, y, w: 0.2, h: 0.2, fill: { color: c }, line: { width: 0 },
  }));
s1.addImage({ path: PAL_IDLE, x: 9.185, y: 2.2, w: 2.4, h: 3.2 });

pixelTriad(s1, 0.9, 1.52, [BLUE, GOLD, MINT]);
eyebrow(s1, 1.62, 1.45, "LOCAL AI FOR CLASSIC OFFICE", GOLD);

s1.addText("AI365", {
  x: 0.9, y: 1.95, w: 7.0, h: 1.35, fontFace: HEAD, fontSize: 92, bold: true,
  color: WHITE, margin: 0, charSpacing: -1,
});
s1.addText(
  "Your own AI assistant, sitting quietly inside Outlook, Excel, PowerPoint and Word.",
  { x: 0.9, y: 3.45, w: 6.9, h: 1.0, fontFace: BODY, fontSize: 19, color: DIM, margin: 0, lineSpacing: 28 },
);

const chips = [
  ["Runs on your PC", BLUE],
  ["Nothing uploaded", MINT],
  ["No licence fee", GOLD],
];
chips.forEach(([label, color], i) => {
  const x = 0.9 + i * 2.3;
  s1.addShape(pres.ShapeType.rect, {
    x, y: 4.75, w: 2.05, h: 0.55, fill: { color: INK2 }, line: { color, width: 1 },
  });
  s1.addShape(pres.ShapeType.rect, {
    x: x + 0.18, y: 4.965, w: 0.12, h: 0.12, fill: { color }, line: { width: 0 },
  });
  s1.addText(label, {
    x: x + 0.38, y: 4.75, w: 1.6, h: 0.55, fontFace: BODY, fontSize: 12,
    color: WHITE, margin: 0, valign: "middle",
  });
});

s1.addText("A presentation for people who do not want to think about the plumbing.", {
  x: 0.9, y: 6.25, w: 7.0, h: 0.35, fontFace: BODY, fontSize: 12, italic: true,
  color: SOFT, margin: 0,
});
s1.addNotes(
  "AI365 is an add-in for classic Office on Windows. It puts a chat sidebar in "
  + "Outlook, Excel, PowerPoint and Word. The three promises on this slide are "
  + "what the rest of the deck explains: it runs on your own machine, your "
  + "content stays there, and there is nothing to pay.",
);

/* ------------------------------------------------------------------ */
/* 2. What it is                                                       */
/* ------------------------------------------------------------------ */
const s2 = pres.addSlide();
s2.background = { color: LIGHT };

pixelTriad(s2, 0.9, 0.78, [BLUE, GOLD, MINT]);
eyebrow(s2, 1.62, 0.71, "WHAT YOU ACTUALLY GET", MUTED);
s2.addText("One installer, four apps", {
  x: 0.9, y: 1.12, w: 9.5, h: 0.7, fontFace: HEAD, fontSize: 40, bold: true,
  color: INK, margin: 0,
});
s2.addText(
  "The same sidebar appears in the Office apps you already use every day. You "
  + "type a question; it answers beside your work.",
  { x: 0.9, y: 1.85, w: 8.6, h: 0.5, fontFace: BODY, fontSize: 15, color: MUTED, margin: 0 },
);

const apps = [
  ["O", "Outlook", BLUE,
    "Ask about your inbox in plain English, then open a ready-to-read reply that stays unsent until you send it."],
  ["X", "Excel", MINT,
    "Ask about the open workbook. Answers land on a clearly marked draft sheet, live formulas and all."],
  ["P", "PowerPoint", GOLD,
    "Turn a page of rough notes into draft slides, laid out in the built-in corporate theme."],
  ["W", "Word", BLUED,
    "Ask about the open document and get the rewrite back as a brand-new, unsaved draft document."],
];
apps.forEach(([glyph, name, color, desc], i) => {
  const x = 0.9 + (i % 2) * 6.0;
  const y = 2.55 + Math.floor(i / 2) * 2.2;
  s2.addShape(pres.ShapeType.rect, {
    x, y, w: 5.5, h: 1.95, fill: { color: WHITE }, line: { width: 0 }, shadow: cardShadow(),
  });
  s2.addShape(pres.ShapeType.rect, {
    x: x + 0.42, y: y + 0.42, w: 0.66, h: 0.66, fill: { color }, line: { width: 0 },
  });
  s2.addText(glyph, {
    x: x + 0.42, y: y + 0.42, w: 0.66, h: 0.66, fontFace: HEAD, fontSize: 22, bold: true,
    color: WHITE, align: "center", valign: "middle", margin: 0,
  });
  s2.addText(name, {
    x: x + 1.32, y: y + 0.4, w: 3.7, h: 0.4, fontFace: HEAD, fontSize: 19, bold: true,
    color: INK, margin: 0, valign: "middle",
  });
  s2.addText(desc, {
    x: x + 1.32, y: y + 0.85, w: 3.75, h: 0.95, fontFace: BODY, fontSize: 12.5,
    color: MUTED, margin: 0, lineSpacing: 17,
  });
});
s2.addNotes(
  "One download, one installer. You choose which of the four apps get the "
  + "sidebar and can change your mind later by running the installer again. "
  + "In every app the write surface is a draft the add-in cannot save.",
);

/* ------------------------------------------------------------------ */
/* 3. Local + private                                                  */
/* ------------------------------------------------------------------ */
const s3 = pres.addSlide();
s3.background = { color: INK };

pixelTriad(s3, 0.9, 0.78, [MINT, MINT, MINT]);
eyebrow(s3, 1.62, 0.71, "PRIVACY", MINT);
s3.addText("Your words stay on your computer", {
  x: 0.9, y: 1.12, w: 11.0, h: 0.7, fontFace: HEAD, fontSize: 40, bold: true,
  color: WHITE, margin: 0,
});
s3.addText(
  "A “local LLM” is just an AI program installed on your own PC — the same way "
  + "Word is. Point AI365 at it and your questions never travel anywhere.",
  { x: 0.9, y: 1.85, w: 11.0, h: 0.55, fontFace: BODY, fontSize: 15, color: DIM, margin: 0 },
);

const cols = [
  ["The usual cloud assistant", GREY, INK2, GREY, [
    "Your email is copied to their servers",
    "A monthly bill for every person who uses it",
    "Their outage is your outage",
  ]],
  ["AI365 with a local model", MINT, "1E2A45", MINT, [
    "The AI program runs on your machine",
    "Your email never leaves the building",
    "Works with the network unplugged",
  ]],
];
cols.forEach(([heading, headColor, fill, dot, items], i) => {
  const x = 0.9 + i * 6.0;
  s3.addShape(pres.ShapeType.rect, {
    x, y: 2.6, w: 5.5, h: 2.75, fill: { color: fill },
    line: i === 1 ? { color: BLUED, width: 1 } : { width: 0 },
  });
  s3.addText(heading, {
    x: x + 0.45, y: 2.9, w: 4.6, h: 0.4, fontFace: HEAD, fontSize: 16, bold: true,
    color: headColor, margin: 0, valign: "middle",
  });
  items.forEach((t, j) => {
    const ty = 3.48 + j * 0.6;
    s3.addShape(pres.ShapeType.rect, {
      x: x + 0.45, y: ty + 0.13, w: 0.14, h: 0.14, fill: { color: dot }, line: { width: 0 },
    });
    s3.addText(t, {
      x: x + 0.78, y: ty, w: 4.3, h: 0.42, fontFace: BODY, fontSize: 13.5,
      color: i === 1 ? WHITE : DIM, margin: 0, valign: "middle",
    });
  });
});

s3.addShape(pres.ShapeType.rect, {
  x: 0.9, y: 5.75, w: 11.5, h: 0.85, fill: { color: INK2 }, line: { width: 0 },
});
s3.addShape(pres.ShapeType.rect, {
  x: 1.25, y: 6.09, w: 0.16, h: 0.16, fill: { color: GOLD }, line: { width: 0 },
});
s3.addText(
  "Honest footnote: AI365 can also talk to Google Gemini if you prefer it. The model "
  + "you pick in the drop-down decides where your words go — and that choice is always yours.",
  { x: 1.6, y: 5.75, w: 10.4, h: 0.85, fontFace: BODY, fontSize: 12.5, color: DIM, margin: 0, valign: "middle" },
);
s3.addNotes(
  "The point to land: 'local' means the AI is software on your own PC, not a "
  + "website. With a local model nothing is uploaded. Be straight about the "
  + "Gemini option - the model picker decides where each request goes, and a "
  + "gemini-* model is the one case where a request leaves the machine.",
);

/* ------------------------------------------------------------------ */
/* 4. Free of charge                                                   */
/* ------------------------------------------------------------------ */
const s4 = pres.addSlide();
s4.background = { color: LIGHT };

pixelTriad(s4, 0.9, 0.78, [GOLD, GOLD, GOLD]);
eyebrow(s4, 1.62, 0.71, "COST", MUTED);
s4.addText("Free of charge, genuinely", {
  x: 0.9, y: 1.12, w: 9.5, h: 0.7, fontFace: HEAD, fontSize: 40, bold: true,
  color: INK, margin: 0,
});
s4.addText(
  "No seat licences, no monthly invoice, no sales call. The add-in is open source "
  + "and the models it talks to can be free ones running on your own hardware.",
  { x: 0.9, y: 1.85, w: 9.8, h: 0.55, fontFace: BODY, fontSize: 15, color: MUTED, margin: 0 },
);

const stats = [
  ["€0", "to install and to keep using", BLUE],
  ["4", "Office apps from one installer", MINT],
  ["0", "accounts to create", GOLD],
  ["MIT", "open source licence", BLUED],
];
stats.forEach(([big, label, color], i) => {
  const x = 0.9 + i * 2.95;
  s4.addShape(pres.ShapeType.rect, {
    x, y: 2.65, w: 2.65, h: 1.95, fill: { color: WHITE }, line: { width: 0 }, shadow: cardShadow(),
  });
  s4.addShape(pres.ShapeType.rect, {
    x: x + 0.32, y: 2.97, w: 0.16, h: 0.16, fill: { color }, line: { width: 0 },
  });
  s4.addText(big, {
    x: x + 0.3, y: 3.25, w: 2.1, h: 0.75, fontFace: HEAD, fontSize: 44, bold: true,
    color: INK, margin: 0, valign: "middle",
  });
  s4.addText(label, {
    x: x + 0.32, y: 4.0, w: 2.05, h: 0.55, fontFace: BODY, fontSize: 12,
    color: MUTED, margin: 0, lineSpacing: 15, valign: "top",
  });
});

const money = [
  ["Nothing to subscribe to", "You download an installer and run it. There is no portal, no seat count and no renewal date."],
  ["Free models exist", "Capable open models run on an ordinary work laptop at no cost per question."],
  ["No surprise bills", "A local model has no meter running, so a long chat costs exactly the same as a short one."],
];
money.forEach(([title, body], i) => {
  const x = 0.9 + i * 3.95;
  s4.addShape(pres.ShapeType.rect, {
    x, y: 5.05, w: 0.16, h: 0.16, fill: { color: GOLD }, line: { width: 0 },
  });
  s4.addText(title, {
    x: x + 0.36, y: 4.93, w: 3.2, h: 0.4, fontFace: HEAD, fontSize: 14, bold: true,
    color: INK, margin: 0, valign: "middle",
  });
  s4.addText(body, {
    x: x + 0.36, y: 5.35, w: 3.25, h: 1.0, fontFace: BODY, fontSize: 12.5,
    color: MUTED, margin: 0, lineSpacing: 17,
  });
});
s4.addNotes(
  "The add-in is MIT-licensed and published on GitHub. The only thing that can "
  + "cost money is the model you choose to point it at - a paid cloud model "
  + "bills per use, a local model does not.",
);

/* ------------------------------------------------------------------ */
/* 5. Accurate and in control                                          */
/* ------------------------------------------------------------------ */
const s5 = pres.addSlide();
s5.background = { color: LIGHT };

pixelTriad(s5, 0.9, 0.78, [BLUE, BLUE, BLUE]);
eyebrow(s5, 1.62, 0.71, "ACCURACY AND CONTROL", MUTED);
s5.addText("Answers drawn from your actual work", {
  x: 0.9, y: 1.12, w: 10.5, h: 0.7, fontFace: HEAD, fontSize: 40, bold: true,
  color: INK, margin: 0,
});

const points = [
  ["Grounded in real content",
    "It answers from the emails, sheets and slides in front of you — not from vague memory."],
  ["You can see what it read",
    "Everything it opened appears as a card you can expand and check for yourself."],
  ["Small, deliberate portions",
    "Ten emails per question at most. Your mailbox is never bulk-uploaded or indexed."],
  ["You always have the last word",
    "Whatever it writes arrives as a draft, in Office's own editor, waiting for you."],
];
points.forEach(([title, body], i) => {
  const y = 2.15 + i * 1.12;
  s5.addShape(pres.ShapeType.rect, {
    x: 0.9, y: y + 0.09, w: 0.3, h: 0.3, fill: { color: BLUE }, line: { width: 0 },
  });
  s5.addText(String(i + 1), {
    x: 0.9, y: y + 0.09, w: 0.3, h: 0.3, fontFace: HEAD, fontSize: 12, bold: true,
    color: WHITE, align: "center", valign: "middle", margin: 0,
  });
  s5.addText(title, {
    x: 1.42, y, w: 5.9, h: 0.42, fontFace: HEAD, fontSize: 16, bold: true,
    color: INK, margin: 0, valign: "middle",
  });
  s5.addText(body, {
    x: 1.42, y: y + 0.42, w: 6.0, h: 0.6, fontFace: BODY, fontSize: 12.5,
    color: MUTED, margin: 0, lineSpacing: 17,
  });
});

s5.addShape(pres.ShapeType.rect, {
  x: 8.1, y: 2.05, w: 4.3, h: 4.35, fill: { color: INK }, line: { width: 0 },
});
s5.addText("AI365 can never", {
  x: 8.5, y: 2.35, w: 3.5, h: 0.4, fontFace: HEAD, fontSize: 17, bold: true,
  color: WHITE, margin: 0, valign: "middle",
});
["Send an email", "Save or overwrite a file", "Delete anything"].forEach((t, i) => {
  const y = 3.0 + i * 0.6;
  s5.addShape(pres.ShapeType.rect, {
    x: 8.5, y: y + 0.13, w: 0.14, h: 0.14, fill: { color: GOLD }, line: { width: 0 },
  });
  s5.addText(t, {
    x: 8.83, y, w: 3.3, h: 0.4, fontFace: BODY, fontSize: 13.5, color: DIM,
    margin: 0, valign: "middle",
  });
});
s5.addText(
  "Those limits are built into the program itself, not into a polite instruction "
  + "the AI could talk its way around.",
  { x: 8.5, y: 4.85, w: 3.5, h: 0.8, fontFace: BODY, fontSize: 11.5, italic: true,
    color: SOFT, margin: 0, lineSpacing: 15 },
);
s5.addImage({ path: PAL_IDLE, x: 11.35, y: 5.65, w: 0.5, h: 0.667 });
s5.addNotes(
  "Accuracy here means grounded, checkable answers rather than a promise that a "
  + "model is never wrong: it reads your real content, shows you what it read, "
  + "and hands back a draft you review. The hard limits - no send, no save, no "
  + "delete - are enforced in code and checked by the build.",
);

/* ------------------------------------------------------------------ */
/* 6. Getting started                                                  */
/* ------------------------------------------------------------------ */
const s6 = pres.addSlide();
s6.background = { color: INK };

pixelTriad(s6, 0.9, 0.78, [BLUE, GOLD, MINT]);
eyebrow(s6, 1.62, 0.71, "GETTING STARTED", GOLD);
s6.addText("Five minutes, three steps", {
  x: 0.9, y: 1.12, w: 10.5, h: 0.7, fontFace: HEAD, fontSize: 40, bold: true,
  color: WHITE, margin: 0,
});

const steps = [
  ["01", "Install it", "Close the Office apps, download AI365Setup.exe and run it for your own Windows account.", BLUE],
  ["02", "Point it at a model", "Open Settings, paste in the address of your model, and press Check endpoint.", MINT],
  ["03", "Ask something", "Click AI365 on the ribbon and type a question, exactly as you would ask a colleague.", GOLD],
];
steps.forEach(([num, title, body, color], i) => {
  const x = 0.9 + i * 4.0;
  s6.addShape(pres.ShapeType.rect, {
    x, y: 2.35, w: 3.5, h: 2.55, fill: { color: INK2 }, line: { width: 0 },
  });
  s6.addText(num, {
    x: x + 0.4, y: 2.6, w: 1.5, h: 0.7, fontFace: HEAD, fontSize: 34, bold: true,
    color, margin: 0, valign: "middle",
  });
  s6.addText(title, {
    x: x + 0.4, y: 3.32, w: 2.7, h: 0.4, fontFace: HEAD, fontSize: 17, bold: true,
    color: WHITE, margin: 0, valign: "middle",
  });
  s6.addText(body, {
    x: x + 0.4, y: 3.75, w: 2.75, h: 0.95, fontFace: BODY, fontSize: 12.5,
    color: DIM, margin: 0, lineSpacing: 17,
  });
});

s6.addImage({ path: PAL_WAVE, x: 0.9, y: 5.4, w: 0.825, h: 1.1 });
s6.addText("github.com/datap0nd/ai365", {
  x: 1.95, y: 5.5, w: 6.0, h: 0.45, fontFace: HEAD, fontSize: 19, bold: true,
  color: WHITE, margin: 0, valign: "middle",
});
s6.addText("Open source, MIT licensed, rebuilt on every change.", {
  x: 1.95, y: 5.95, w: 7.0, h: 0.4, fontFace: BODY, fontSize: 13, color: SOFT,
  margin: 0, valign: "middle",
});
s6.addText("Windows · classic Office 2021", {
  x: 7.9, y: 5.75, w: 4.5, h: 0.45, fontFace: BODY, fontSize: 12.5, color: SOFT,
  align: "right", margin: 0, valign: "middle",
});
s6.addNotes(
  "The new Outlook for Windows does not load COM add-ins, so this is for classic "
  + "Office. Updates are a single Update AI365 button in Settings, which "
  + "refreshes all four add-ins together.",
);

pres.writeFile({ fileName: path.join(__dirname, "..", "scribble.pptx") })
  .then((f) => console.log("wrote", f));
