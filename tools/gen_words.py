# -*- coding: utf-8 -*-
"""Build data/words.json: the 79 评鉴 entries in a compact form for the overlay app."""
import json
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(BASE, "data")

rows = json.load(open(os.path.join(D, "video_rows.json"), encoding="utf-8"))
pool = {r["seq"]: r for r in json.load(open(os.path.join(D, "video_pool.json"), encoding="utf-8"))}
out = []
for r in rows:
    p = pool.get(r["seq"], {})
    out.append({
        "w": r["title"],
        "s": p.get("skill") or r.get("skill"),
        "r": p.get("role") or r.get("role"),
        "p": r["phase"],
        "n": bool(r.get("is_new")),
        "e": (p.get("official_desc") or "").strip(),
    })
json.dump(out, open(os.path.join(D, "words.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("words.json:", len(out), "entries,", sum(1 for x in out if x["n"]), "new")
