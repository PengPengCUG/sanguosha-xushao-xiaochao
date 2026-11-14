# -*- coding: utf-8 -*-
"""Build an offline glyph atlas for the 评鉴 OCR.

Every character that appears in the 79 战功 names is rendered with several local
Windows fonts, normalised exactly like a board cell glyph (crop ink bbox, scale
to 48x48, mean-subtract, L2-normalise, blur), then packed into one grayscale PNG
plus a small JSON manifest.  The HTML tool embeds both, so recognition needs no
network and no installed fonts.
"""
import json
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(BASE, "data")
NORM = 48
BLUR = 1.5
FONTS = [
    ("kai", r"C:\Windows\Fonts\simkai.ttf"),
    ("stkai", r"C:\Windows\Fonts\STKAITI.TTF"),
    ("fang", r"C:\Windows\Fonts\simfang.ttf"),
    ("song", r"C:\Windows\Fonts\simsun.ttc"),
]
COLS = 32


def norm_glyph(ink, size=NORM):
    ys, xs = np.where(ink > 55)
    if len(ys) == 0:
        return np.zeros((size, size), np.float32)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = ink[y0:y1, x0:x1]
    h, w = crop.shape
    s = (size * 0.92) / max(h, w)
    im = Image.fromarray(crop.astype(np.uint8)).resize(
        (max(1, int(round(w * s))), max(1, int(round(h * s)))), Image.LANCZOS)
    canvas = Image.new("L", (size, size), 0)
    canvas.paste(im, ((size - im.width) // 2, (size - im.height) // 2))
    a = np.asarray(canvas).astype(np.float32) / 255.0
    a -= a.mean()
    return a / (np.linalg.norm(a) + 1e-6)


def blur(a):
    im = Image.fromarray((np.clip(a, -1, 1) * 127 + 128).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(BLUR))
    x = (np.asarray(im).astype(np.float32) - 128) / 127.0
    x -= x.mean()
    return x / (np.linalg.norm(x) + 1e-6)


def charset():
    rows = json.load(open(os.path.join(BASE, "data", "video_rows.json"), encoding="utf-8"))
    s = set()
    for r in rows:
        s.update(r["title"])
    s.add("蛋")
    return sorted(s)


def main():
    chars = charset()
    # characters that must also be recognised when typed by hand in the game
    extra = "以时进讨慧眼识君朴的烛照官渡宪后叶临难死义天阶登志中才之相先大不惜鬼慨然怀汉坚北轻豪将扣弦勒意前明姿小"
    for ch in extra:
        if ch not in chars:
            chars.append(ch)
    chars = sorted(set(chars))
    n = len(chars) * len(FONTS)
    rows = (n + COLS - 1) // COLS
    atlas = np.zeros((rows * NORM, COLS * NORM), np.uint8)
    index = []
    i = 0
    for tag, path in FONTS:
        if not os.path.exists(path):
            raise SystemExit("font missing: " + path)
        font = ImageFont.truetype(path, 96)
        for ch in chars:
            c = Image.new("L", (180, 180), 255)
            ImageDraw.Draw(c).text((14, 14), ch, font=font, fill=0)
            arr = np.asarray(c).astype(np.float32)
            ink = np.clip((210 - arr) / 210, 0, 1) * 255
            g = blur(norm_glyph(ink))
            v = np.clip(np.round(g * 1000 + 128), 0, 255).astype(np.uint8)
            r, cc = i // COLS, i % COLS
            atlas[r * NORM:(r + 1) * NORM, cc * NORM:(cc + 1) * NORM] = v
            index.append([tag, ch])
            i += 1
    # transparent-free grayscale PNG
    Image.fromarray(atlas).save(os.path.join(OUT, "atlas.png"), optimize=True)
    meta = {"size": NORM, "scale": 1000, "cols": COLS, "rows": rows, "count": n,
            "index": index, "chars": chars}
    json.dump(meta, open(os.path.join(OUT, "atlas.json"), "w", encoding="utf-8"), ensure_ascii=False)
    png = os.path.join(OUT, "atlas.png")
    print("atlas", atlas.shape, "entries", n, "png", os.path.getsize(png), "bytes")
    print("chars", len(chars))


if __name__ == "__main__":
    main()
