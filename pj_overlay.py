# -*- coding: utf-8 -*-
"""评鉴浮窗 · 三国杀OL 许劭「评鉴」只读提示工具

Ctrl+F   抓取游戏窗口 → 认出 6×6 棋盘 → 浮窗按原棋盘排布显示可连战功
Ctrl+F7  校准棋盘区域（拖框，只需一次，会记住）
Ctrl+F9  显示 / 隐藏
Ctrl+Alt+Esc 退出

只读取屏幕截图，不注入游戏进程、不发送任何输入。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageGrab

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(APP_DIR, "pj_config.json")
LOG_PATH = os.path.join(APP_DIR, "pj_log.txt")

SIZE = 48
BLUR = 1.5
PHASE_CN = {"play": "出牌", "defense": "受击", "end": "结束"}
COLORS = ["#7ee787", "#79c0ff", "#ffa657", "#f778ba", "#d2a8ff", "#56d4dd"]
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x0001, 0x0002, 0x0004
MOD_NOREPEAT = 0x4000
KEYMAP = {}
for _i, _name in enumerate(["F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"]):
    KEYMAP[_name.lower()] = (0, 0x70 + _i)
for _ch in "abcdefghijklmnopqrstuvwxyz":
    KEYMAP[_ch] = (0, ord(_ch.upper()))
KEYMAP["空格"] = (0, 0x20)
KEYMAP["space"] = (0, 0x20)
KEYMAP["tab"] = (0, 0x09)
KEYMAP["`"] = (0, 0xC0)
KEYMAP["alt+空格"] = (MOD_ALT, 0x20)
KEYMAP["alt+space"] = (MOD_ALT, 0x20)
KEYMAP["ctrl+空格"] = (MOD_CONTROL, 0x20)
KEYMAP["ctrl+space"] = (MOD_CONTROL, 0x20)
WM_HOTKEY = 0x0312
VK_SPACE = 0x20
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
user32 = ctypes.windll.user32


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


def foreground_title():
    h = user32.GetForegroundWindow()
    n = user32.GetWindowTextLengthW(h)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value


def cursor_in_rect(rect):
    pt = wt.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    x, y, w, h = rect
    return x <= pt.x < x + w and y <= pt.y < y + h


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


# ---------------------------------------------------------------- Win32
def find_game_windows():
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if "三国杀" not in title:
            return True
        if any(x in title for x in ("Chrome", "Edge", "DeepSeek", "—", " - ")):
            return True
        r = window_rect(hwnd)
        if r[0] < -10000 or r[2] < 400 or r[3] < 300:
            return True
        found.append((hwnd, title, r))
        return True

    user32.EnumWindows(cb, 0)
    found.sort(key=lambda x: (x[1] != "三国杀", -x[2][2] * x[2][3]))
    return found


def window_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return [r.left, r.top, r.right - r.left, r.bottom - r.top]


# ---------------------------------------------------------------- 字形库
class Atlas:
    def __init__(self):
        meta = json.load(open(os.path.join(DATA, "atlas.json"), encoding="utf-8"))
        self.size = meta["size"]
        self.cols = meta["cols"]
        self.count = meta["count"]
        self.scale = meta.get("scale", 127)
        self.index = meta["index"]
        arr = np.asarray(Image.open(os.path.join(DATA, "atlas.png")).convert("L")).astype(np.float32)
        S = self.size
        vec = np.zeros((self.count, S * S), np.float32)
        for i in range(self.count):
            r, c = divmod(i, self.cols)
            blk = arr[r * S:(r + 1) * S, c * S:(c + 1) * S]
            v = ((blk - 128) / self.scale).ravel()
            v = v - v.mean()
            vec[i] = v / (np.linalg.norm(v) + 1e-6)
        self.vec = vec
        small = np.zeros((self.count, 64), np.float32)
        for i in range(self.count):
            s = vec[i].reshape(S, S).reshape(8, S // 8, 8, S // 8).mean(axis=(1, 3)).ravel()
            s = s - s.mean()
            small[i] = s / (np.linalg.norm(s) + 1e-6)
        self.small = small
        self.chars = [x[1] for x in self.index]
        self.char_rows = {}
        for i, ch in enumerate(self.chars):
            self.char_rows.setdefault(ch, []).append(i)

    def match(self, v, topk=8):
        v = np.asarray(v, dtype=np.float32).ravel()
        sm = v.reshape(SIZE, SIZE).reshape(8, SIZE // 8, 8, SIZE // 8).mean(axis=(1, 3)).ravel()
        sm = sm - sm.mean()
        sm /= (np.linalg.norm(sm) + 1e-6)
        pre = self.small @ sm
        order = np.argsort(pre)[::-1][:90]
        best = {}
        for i in order:
            s = float(np.dot(self.vec[i], v))
            ch = self.chars[i]
            if ch not in best or s > best[ch][0]:
                best[ch] = (s, ch)
        return sorted(best.values(), reverse=True)[:topk]


def normalize(a):
    a = np.asarray(a, dtype=np.float32).ravel()
    a = a - a.mean()
    return a / (np.linalg.norm(a) + 1e-6)


def blur_vec(a):
    a = np.asarray(a, dtype=np.float32).reshape(SIZE, SIZE)
    img = Image.fromarray(np.clip(a * 127 + 128, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(BLUR))
    x = (np.asarray(img).astype(np.float32) - 128) / 127.0
    return normalize(x)


def glyph_vec(ink):
    ys, xs = np.where(ink > 55)
    if len(ys) == 0:
        return None
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    crop = ink[y0:y1, x0:x1]
    h, w = crop.shape
    s = (SIZE * 0.92) / max(h, w)
    im = Image.fromarray(crop.astype(np.uint8)).resize(
        (max(1, int(round(w * s))), max(1, int(round(h * s)))), Image.LANCZOS)
    canvas = Image.new("L", (SIZE, SIZE), 0)
    canvas.paste(im, ((SIZE - im.width) // 2, (SIZE - im.height) // 2))
    v = np.asarray(canvas).astype(np.float32).ravel() / 255.0
    return blur_vec(normalize(v))


# ---------------------------------------------------------------- 图像处理
def cell_ink(lum, x0, y0, x1, y1, rgb=None, inset=0.10):
    """格子内「字」的墨迹：深灰字按暗度，浅蓝高亮字按偏蓝，取两者较大值。"""
    x0, y0, x1, y1 = float(x0), float(y0), float(x1), float(y1)
    iw, ih = (x1 - x0) * inset, (y1 - y0) * inset
    x0, y0, x1, y1 = int(x0 + iw), int(y0 + ih), int(x1 - iw), int(y1 - ih)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(lum.shape[1], x1), min(lum.shape[0], y1)
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    crop = lum[y0:y1, x0:x1]
    bg = float(np.percentile(crop, 88))
    ink = np.clip((bg - crop) / max(bg - 25.0, 1.0), 0, 1)
    if rgb is not None:
        R = rgb[y0:y1, x0:x1, 0]
        B = rgb[y0:y1, x0:x1, 2]
        ink = np.maximum(ink, np.clip((B - R - 18) / 90.0, 0, 1))
    ink = ink.copy()
    ink[ink < 0.20] = 0
    return (ink * 255).astype(np.float32)


def is_egg(rgb, x0, y0, x1, y1):
    if rgb is None:
        return False
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(rgb.shape[1], int(x1)), min(rgb.shape[0], int(y1))
    if x1 - x0 < 6 or y1 - y0 < 6:
        return False
    R = rgb[y0:y1, x0:x1, 0]
    G = rgb[y0:y1, x0:x1, 1]
    B = rgb[y0:y1, x0:x1, 2]
    m = (R > 170) & (R - B > 45) & (G > 120)
    return float(m.mean()) > 0.25


def _runs(mask1d, min_len=6):
    out, start = [], None
    for i, v in enumerate(mask1d):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask1d)))
    return [(a, b) for a, b in out if b - a >= min_len]


def detect_board(lum, region):
    rx, ry, rw, rh = [int(v) for v in region]
    x0, y0 = max(0, rx), max(0, ry)
    x1, y1 = min(lum.shape[1], rx + rw), min(lum.shape[0], ry + rh)
    sub = lum[y0:y1, x0:x1]
    if sub.size == 0:
        return None
    mask = sub > 213
    rp, cp = mask.sum(axis=1), mask.sum(axis=0)
    if rp.max() < 10 or cp.max() < 10:
        return None
    t = b = l = r = -1
    for i, v in enumerate(rp):
        if v > rp.max() * 0.55:
            t = i if t < 0 else t
            b = i
    for i, v in enumerate(cp):
        if v > cp.max() * 0.55:
            l = i if l < 0 else l
            r = i
    if t < 0 or l < 0:
        return None
    pad = int(min(r - l, b - t) * 0.016) + 1
    return [x0 + l - pad, y0 + t - pad, (r - l) + 1 + 2 * pad, (b - t) + 1 + 2 * pad]


def find_cells(lum, region, rows=6, cols=6):
    """按「格子比棋盘底色亮」找 6 条行带/列带，得到每格精确矩形。"""
    H, W = lum.shape
    rx, ry, rw, rh = [int(v) for v in region]
    x0, y0 = max(0, rx), max(0, ry)
    x1, y1 = min(W, rx + rw), min(H, ry + rh)
    if x1 - x0 < 60 or y1 - y0 < 60:
        return None
    sub = lum[y0:y1, x0:x1]
    for thr in (226, 230, 222, 234, 218, 214):
        mask = sub > thr
        rp, cp = mask.sum(axis=1), mask.sum(axis=0)
        if rp.max() < 10 or cp.max() < 10:
            continue
        rr = _runs(rp > rp.max() * 0.45, 6)
        cc = _runs(cp > cp.max() * 0.45, 6)
        if len(rr) != rows or len(cc) != cols:
            continue
        cr = [(a + b) / 2.0 for a, b in rr]
        cc_ = [(a + b) / 2.0 for a, b in cc]
        pr = [cr[i + 1] - cr[i] for i in range(rows - 1)]
        pc = [cc_[i + 1] - cc_[i] for i in range(cols - 1)]
        if min(pr) < 18 or min(pc) < 18:
            continue
        if max(pr) / min(pr) > 1.45 or max(pc) / min(pc) > 1.45:
            continue

        def bounds(centers, pitch):
            bs = [centers[0] - pitch / 2]
            for i in range(len(centers) - 1):
                bs.append((centers[i] + centers[i + 1]) / 2)
            bs.append(centers[-1] + pitch / 2)
            return bs

        br = bounds(cr, sum(pr) / len(pr))
        bc = bounds(cc_, sum(pc) / len(pc))
        cells = []
        for i in range(rows):
            for j in range(cols):
                cells.append((x0 + bc[j], y0 + br[i], x0 + bc[j + 1], y0 + br[i + 1]))
        return cells
    return None


# ---------------------------------------------------------------- 识别
def recognize(atlas, lum, rgb, box, rows=6, cols=6, cells_rect=None):
    bx, by, bw, bh = box
    cw, ch = bw / cols, bh / rows
    cands, confs, eggs = [], [], []
    for i in range(rows):
        for j in range(cols):
            if cells_rect:
                x0, y0, x1, y1 = cells_rect[i * cols + j]
            else:
                x0 = bx + j * cw
                x1 = bx + (j + 1) * cw
                y0 = by + i * ch
                y1 = by + (i + 1) * ch
            if is_egg(rgb, x0, y0, x1, y1):
                cands.append([(1.0, "蛋")])
                confs.append(1.0)
                eggs.append(True)
                continue
            ink = cell_ink(lum, x0, y0, x1, y1, rgb=rgb)
            v = glyph_vec(ink) if ink is not None else None
            top = atlas.match(v, 8) if v is not None else []
            if top and top[0][0] < 0.62:
                step = max(2, int((x1 - x0) * 0.06))
                trials = [(dx, dy, 0) for dx in (-step, 0, step) for dy in (-step, 0, step)]
                trials.append((0, 0, step))
                for dx, dy, grow in trials:
                    if not dx and not dy and not grow:
                        continue
                    ink2 = cell_ink(lum, x0 + dx - grow, y0 + dy - grow,
                                    x1 + dx + grow, y1 + dy + grow, rgb=rgb)
                    v2 = glyph_vec(ink2) if ink2 is not None else None
                    if v2 is None:
                        continue
                    t2 = atlas.match(v2, 8)
                    if t2 and (not top or t2[0][0] > top[0][0] + 0.02):
                        top = t2
            cands.append(top)
            confs.append(max(0.0, min(1.0, (top[0][0] - 0.25) / 0.45)) if top else 0.0)
            eggs.append(False)
    return cands, confs, eggs


# ---------------------------------------------------------------- 求解
def solve_candidates(words, cands, rows=6, cols=6, max_rank=5, min_score=0.72):
    """候选字级整词匹配：允许某格用的是第 2~N 候选，只要整条词都成立。

    返回 [{'item','cells','diffs','score','arrow'}]，diffs 里的格子表示「识别时要改的字」。
    """
    H, W = rows, cols
    dirs = ((1, 0, "↓"), (0, 1, "→"), (-1, 0, "↑"), (0, -1, "←"))
    out = []
    for item in words:
        w = item["w"]
        n = len(w)
        for r in range(H):
            for c in range(W):
                for dr, dc, arrow in dirs:
                    er, ec = r + dr * (n - 1), c + dc * (n - 1)
                    if not (0 <= er < H and 0 <= ec < W):
                        continue
                    score, diffs, ok = 0.0, [], True
                    for k in range(n):
                        idx = (r + dr * k) * W + (c + dc * k)
                        top = cands[idx]
                        if not top:
                            ok = False
                            break
                        best = top[0][1]
                        if w[k] == best:
                            score += 1.0
                            continue
                        if best == "蛋":
                            score += 0.85
                            continue
                        names = [t[1] for t in top[:max_rank]]
                        if w[k] in names:
                            rank = names.index(w[k])
                            score += 0.72 - 0.09 * rank
                            diffs.append((idx, best, w[k]))
                            continue
                        ok = False
                        break
                    if ok and score / n >= min_score:
                        out.append({"item": item, "cells": [(r + dr * k, c + dc * k) for k in range(n)],
                                    "diffs": diffs, "score": score / n, "arrow": arrow,
                                    "start": (r, c), "dir": (dr, dc)})
                        break  # 同一词只取第一个位置
                if out and out[-1]["item"] is item:
                    break
            if out and out[-1]["item"] is item:
                break
    out.sort(key=lambda m: (len(m["diffs"]), -m["score"]))
    # 去重（同一词取最优），并按分数排序
    seen, res = set(), []
    for m in out:
        if m["item"]["w"] in seen:
            continue
        seen.add(m["item"]["w"])
        res.append(m)
    return res


# ---------------------------------------------------------------- 浮窗
class OverlayApp:
    def __init__(self, atlas, words, selftest=None):
        import tkinter as tk
        self.tk = tk
        self.atlas = atlas
        self.words = words
        self.config = self.load_config()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("评鉴浮窗")
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#1b1626")
        self.frame = tk.Frame(self.win, bg="#1b1626", padx=10, pady=8)
        self.frame.pack(fill="both", expand=True)

        head = tk.Frame(self.frame, bg="#1b1626")
        head.pack(fill="x")
        tk.Label(head, text="评鉴浮窗", bg="#1b1626", fg="#8d84a6",
                 font=("Microsoft YaHei", 8)).pack(side="left")
        close = tk.Label(head, text="✕", bg="#1b1626", fg="#c9c0dd",
                         font=("Microsoft YaHei", 11, "bold"), padx=6, cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.quit())
        close.bind("<Enter>", lambda e: close.config(fg="#ff9a93"))
        close.bind("<Leave>", lambda e: close.config(fg="#c9c0dd"))
        hide = tk.Label(head, text="—", bg="#1b1626", fg="#c9c0dd",
                        font=("Microsoft YaHei", 10, "bold"), padx=6, cursor="hand2")
        hide.pack(side="right")
        hide.bind("<Button-1>", lambda e: self.toggle_visible())
        hide.bind("<Enter>", lambda e: hide.config(fg="#ffd166"))
        hide.bind("<Leave>", lambda e: hide.config(fg="#c9c0dd"))

        self.cv = tk.Canvas(self.frame, width=6 * 40 + 5 * 4, height=6 * 40 + 5 * 4,
                            bg="#1b1626", highlightthickness=0)
        self.cv.pack()
        self.text = tk.Text(self.frame, width=26, height=7, bg="#1b1626", fg="#ece7f6",
                            bd=0, highlightthickness=0, font=("Microsoft YaHei", 9),
                            wrap="word", cursor="arrow")
        self.text.pack(fill="both", expand=True, pady=(6, 0))
        for tag, cfg in {
            "h1": {"font": ("Microsoft YaHei", 11, "bold"), "foreground": "#e9c46a", "spacing1": 2},
            "w": {"font": ("KaiTi", 13, "bold"), "foreground": "#ffffff", "spacing1": 4},
            "meta": {"font": ("Microsoft YaHei", 9), "foreground": "#a79bc4"},
            "new": {"font": ("Microsoft YaHei", 8, "bold"), "foreground": "#ffd166"},
            "warn": {"font": ("Microsoft YaHei", 9), "foreground": "#ff9a93"},
            "ok": {"font": ("Microsoft YaHei", 8), "foreground": "#7ee787"},
            "hint": {"font": ("Microsoft YaHei", 8), "foreground": "#7a6f96"},
        }.items():
            self.text.tag_configure(tag, **cfg)
        self.text.config(state="disabled")
        self.hidden = False
        self._space_hook = None
        self._last_space = 0.0
        self._cool = 0.0
        self.win.bind("<Button-1>", self.start_drag)
        self.win.bind("<B1-Motion>", self.on_drag)
        self.menu = tk.Menu(self.win, tearoff=0)
        self.menu.add_command(label="校准棋盘区域", command=lambda: self.root.after(0, self.start_calibration))
        self.menu.add_command(label="隐藏浮窗", command=lambda: self.root.after(0, self.toggle_visible))
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=lambda: self.root.after(0, self.quit))
        self.win.bind("<Button-3>", lambda e: self.menu.tk_popup(e.x_root, e.y_root))
        self._drag = None
        self.place_default()
        self.draw_grid(None, {}, None)
        self.show_hint()
        self.win.update_idletasks()
        self.win.deiconify()
        self.register_hotkeys()
        if selftest:
            self.root.after(300, lambda: self.selftest(selftest))
        if "--once" in sys.argv:
            self.root.after(1000, self.capture_and_solve)

    # ---------- 配置/拖动 ----------
    def load_config(self):
        try:
            return json.load(open(CONFIG_PATH, encoding="utf-8"))
        except Exception:
            return {}

    def save_config(self):
        try:
            json.dump(self.config, open(CONFIG_PATH, "w", encoding="utf-8"), ensure_ascii=False)
        except Exception:
            pass

    def start_drag(self, e):
        self._drag = (e.x_root - self.win.winfo_x(), e.y_root - self.win.winfo_y())

    def on_drag(self, e):
        if self._drag:
            self.win.geometry("+%d+%d" % (e.x_root - self._drag[0], e.y_root - self._drag[1]))

    def place_default(self):
        pos = self.config.get("pos")
        if pos:
            self.win.geometry("+%d+%d" % (pos[0], pos[1]))
            return
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.win.update_idletasks()
        w = max(320, self.win.winfo_reqwidth())
        h = max(300, self.win.winfo_reqheight())
        x = max(0, min(sw - w - 8, sw - w - 40))
        y = max(0, min(sh - h - 60, sh // 4))
        self.win.geometry("+%d+%d" % (x, y))

    # ---------- 文本 ----------
    def set_text(self, blocks):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for content, tag in blocks:
            self.text.insert("end", content, tag)
        self.text.config(state="disabled")
        self.win.update_idletasks()

    def show_hint(self):
        k = getattr(self, "keys", {"capture": "Ctrl+F", "calib": "Ctrl+F7",
                                   "toggle": "Ctrl+F9", "quit": "Ctrl+Alt+Esc"})
        self.set_text([
            ("评鉴浮窗\n", "h1"),
            ("%s  抓屏识别（棋盘弹出时按）\n" % k["capture"], "meta"),
            ("    若与输入法冲突，可改用 %s\n" % k.get("backup", "Ctrl+Alt+空格"), "hint"),
            ("%s  校准棋盘区域（拖一次，记住）\n" % k["calib"], "meta"),
            ("%s  显示/隐藏    %s 退出\n" % (k["toggle"], k["quit"]), "meta"),
            ("\n只读屏幕截图，不注入游戏、不发输入。", "hint"),
        ])

    # ---------- 6×6 棋盘显示 ----------
    def draw_grid(self, grid, word_colors, low_conf):
        cv = self.cv
        cv.delete("all")
        cell, gap = 40, 4
        for i in range(6):
            for j in range(6):
                x = j * (cell + gap)
                y = i * (cell + gap)
                ch = ""
                if grid:
                    ch = grid[i][j] or ""
                color = None
                if word_colors:
                    color = word_colors.get((i, j))
                if ch == "蛋":
                    fill, outline, fg = "#efe0bd", "#c9a227", "#8a6b21"
                    cv.create_rectangle(x, y, x + cell, y + cell, fill=fill,
                                        outline=outline, width=2)
                    cv.create_text(x + cell / 2, y + cell / 2, text="🥚",
                                   fill=fg, font=("Microsoft YaHei", 14))
                    continue
                if color:
                    cv.create_rectangle(x, y, x + cell, y + cell, fill="#241d33",
                                        outline=color, width=3)
                    cv.create_text(x + cell / 2, y + cell / 2, text=ch, fill="#ffffff",
                                   font=("KaiTi", 18, "bold"))
                elif ch:
                    cv.create_rectangle(x, y, x + cell, y + cell, fill="#221b2e",
                                        outline="#3a3350", width=1)
                    cv.create_text(x + cell / 2, y + cell / 2, text=ch, fill="#8d84a6",
                                   font=("KaiTi", 18))
                else:
                    cv.create_rectangle(x, y, x + cell, y + cell, fill="#1f1929",
                                        outline="#332c47", width=1)
                    cv.create_text(x + cell / 2, y + cell / 2, text="·", fill="#4a4160",
                                   font=("Microsoft YaHei", 12))

    # ---------- 抓屏 ----------
    def grab(self):
        rect = self.config.get("rect")
        if rect:
            x, y, w, h = rect
            return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)
        wins = find_game_windows()
        if not wins:
            return None
        hwnd = wins[0][0]
        x, y, w, h = window_rect(hwnd)
        if user32.GetForegroundWindow() != hwnd:
            try:
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.15)
            except Exception:
                pass
        return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)

    # ---------- 主流程 ----------
    def capture_and_solve(self):
        log("hotkey capture -> start")
        try:
            shot = self.grab()
            if shot is None:
                self.set_text([("没找到「三国杀」窗口\n", "warn"),
                               ("请确认游戏没最小化，再按一次。", "meta")])
                return
            rgb = np.asarray(shot.convert("RGB")).astype(np.float32)
            lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
            box = detect_board(lum, (0, 0, lum.shape[1], lum.shape[0]))
            if box is None:
                self.draw_grid(None, {}, None)
                self.set_text([("没定位到棋盘\n", "warn"),
                               ("等评鉴棋盘弹出再按，或按 %s 拖框圈住棋盘（只需一次）。" %
                                self.keys.get("calib", "Ctrl+F7"), "meta")])
                return
            win_area = lum.shape[0] * lum.shape[1]
            if box[2] * box[3] > win_area * 0.55:
                self.draw_grid(None, {}, None)
                self.set_text([("没定位到棋盘\n", "warn"),
                               ("当前可能不在「评鉴」界面。等棋盘弹出再按。" , "meta")])
                return
            cells = find_cells(lum, box)
            if cells:
                xs = [c[0] for c in cells] + [c[2] for c in cells]
                ys = [c[1] for c in cells] + [c[3] for c in cells]
                box = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
            cands, confs, eggs = recognize(self.atlas, lum, rgb, box, cells_rect=cells)
            grid = [[(cands[i * 6 + j][0][1] if cands[i * 6 + j] else "") for j in range(6)]
                    for i in range(6)]
            matches = solve_candidates(self.words, cands)
            log("recognize board=%s mean=%.2f matches=%s" % (
                [round(v) for v in box], sum(confs) / len(confs),
                [(m["item"]["w"], len(m["diffs"])) for m in matches]))
            self.render(grid, matches, confs)
            try:
                shot.crop((int(box[0]), int(box[1]), int(box[0] + box[2]), int(box[1] + box[3]))).save(
                    os.path.join(APP_DIR, "last_board.png"))
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            import traceback
            log("ERROR " + traceback.format_exc(limit=3))
            self.set_text([("识别出错：\n", "warn"), (str(e), "meta")])

    def render(self, grid, matches, confs):
        # 按「词」给格子上色，并把候选补正后的字显示出来
        word_colors = {}
        show = [row[:] for row in grid]
        for k, m in enumerate(matches[:6]):
            color = COLORS[k % len(COLORS)]
            for (r, c) in m["cells"]:
                word_colors[(r, c)] = color
            for idx, _best, want in m["diffs"]:
                # 表示「识别时是 X，实际应是 want」
                pass
            for k2, (r, c) in enumerate(m["cells"]):
                show[r][c] = m["item"]["w"][k2]
        self.draw_grid(show, word_colors, confs)

        blocks = [("评鉴 · 可连 %d 条\n" % len(matches), "h1" if matches else "warn")]
        if not matches:
            blocks.append(("没认出可连的战功，按 %s 重校一次棋盘。\n"
                           % self.keys.get("calib", "Ctrl+F7"), "meta"))
        for k, m in enumerate(matches[:7]):
            it = m["item"]
            mark = "★" if it["n"] else "·"
            blocks.append((mark + " ", "new" if it["n"] else "meta"))
            blocks.append((it["w"] + "\n", "w"))
            blocks.append(("   " + it["s"] + " · " + it["r"] + " · " + PHASE_CN.get(it["p"], ""), "meta"))
            if m["diffs"]:
                detail = " ".join("%s→%s" % (d[1], d[2]) for d in m["diffs"])
                blocks.append(("   校正 " + detail, "warn"))
            blocks.append(("\n", "meta"))
        mc = sum(confs) / len(confs) if confs else 0
        blocks.append(("识别置信 %.0f%%" % (mc * 100), "ok" if mc > 0.6 else "warn"))
        self.set_text(blocks)
        # 词多的时候把浮窗撑高，保证 7 条都能看到
        self.text.config(height=min(16, max(4, 2 * len(matches) + 1)))
        self.win.update_idletasks()
        self.win.geometry("")

    # ---------- 热键 ----------
    def register_hotkeys(self):
        MOD_CONTROL, MOD_ALT, MOD_SHIFT = 0x0002, 0x0001, 0x0004
        candidates = {
            "capture": [(0, 0x71, "F2"), (0, 0x79, "F10"), (0, 0x73, "F4"), (0, 0x72, "F3"),
                        (0, 0x70, "F1"), (0, 0x7A, "F11"),
                        (MOD_ALT, 0x20, "Alt+空格"),
                        (MOD_CONTROL | MOD_ALT, 0x20, "Ctrl+Alt+空格"),
                        (MOD_CONTROL | MOD_SHIFT, 0x46, "Ctrl+Shift+F"),
                        (MOD_CONTROL, 0x77, "Ctrl+F8")],
            "calib": [(MOD_CONTROL, 0x76, "Ctrl+F7"), (MOD_CONTROL | MOD_ALT, 0x76, "Ctrl+Alt+F7"),
                      (MOD_CONTROL, 0x74, "Ctrl+F5")],
            "toggle": [(MOD_CONTROL, 0x78, "Ctrl+F9"), (MOD_CONTROL, 0x79, "Ctrl+F10")],
            "quit": [(MOD_CONTROL | MOD_ALT, 0x51, "Ctrl+Alt+Q"), (MOD_CONTROL | MOD_ALT, 0x1B, "Ctrl+Alt+Esc")],
        }

        # 配置文件优先：{"capture_key": "F10"} 或 "alt+空格" 之类
        want = str(self.config.get("capture_key", "")).strip().lower()
        if want in KEYMAP:
            mods, vk = KEYMAP[want]
            candidates["capture"] = [(mods, vk, self.config.get("capture_key"))] + candidates["capture"]

        def worker():
            labels, ids, hid = {}, {}, 0
            for name, opts in candidates.items():
                for mods, vk, label in opts:
                    hid += 1
                    if user32.RegisterHotKey(None, hid, mods | MOD_NOREPEAT, vk):
                        labels[name] = label
                        ids[hid] = (name, vk)
                        break
                    log("hotkey %s %s busy" % (name, label))
            self.keys = {
                "capture": labels.get("capture", "F4"),
                "calib": labels.get("calib", "Ctrl+F7"),
                "toggle": labels.get("toggle", "Ctrl+F9"),
                "quit": labels.get("quit", "Ctrl+Alt+Q"),
            }
            log("hotkeys: " + json.dumps(self.keys, ensure_ascii=False))
            self.root.after(0, self.show_hint)
            msg = wt.MSG()
            while True:
                r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r <= 0:
                    break
                if msg.message == WM_HOTKEY:
                    name = ids.get(msg.wParam, (None, None))[0]
                    if name == "capture":
                        self.root.after(0, self.capture_and_solve)
                    elif name == "calib":
                        self.root.after(0, self.start_calibration)
                    elif name == "toggle":
                        self.root.after(0, self.toggle_visible)
                    elif name == "quit":
                        self.root.after(0, self.quit)
        threading.Thread(target=worker, daemon=True).start()

    def toggle_visible(self):
        self.hidden = not self.hidden
        self.win.withdraw() if self.hidden else self.win.deiconify()

    def quit(self):
        try:
            self.save_config()
        finally:
            self.root.after(0, self.root.destroy)

    # ---------- 校准 ----------
    def start_calibration(self):
        self.win.withdraw()
        tk = self.tk
        cal = tk.Toplevel(self.root)
        cal.overrideredirect(True)
        cal.attributes("-topmost", True)
        sw, sh = cal.winfo_screenwidth(), cal.winfo_screenheight()
        cal.geometry("%dx%d+0+0" % (sw, sh))
        cal.attributes("-alpha", 0.25)
        cv = tk.Canvas(cal, bg="black", highlightthickness=0)
        cv.pack(fill="both", expand=True)
        cv.create_text(sw // 2, 40, text="拖动框住 6×6 棋盘（松手保存），Esc 取消",
                       fill="white", font=("Microsoft YaHei", 16))
        st = {"a": None, "rect": None}

        def down(e):
            st["a"] = (e.x_root, e.y_root)
            cv.delete("sel")

        def move(e):
            if not st["a"]:
                return
            x0, y0 = st["a"]
            cv.delete("sel")
            cv.create_rectangle(x0, y0, e.x_root, e.y_root, outline="#ffd166", width=3, tags="sel")

        def up(e):
            if not st["a"]:
                return
            x0, y0 = st["a"]
            x1, y1 = e.x_root, e.y_root
            rect = [min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)]
            cal.destroy()
            if rect[2] > 40 and rect[3] > 40:
                self.config["rect"] = rect
                self.save_config()
                self.win.deiconify()
                self.draw_grid(None, {}, None)
                self.set_text([("已校准棋盘区域\n", "h1"),
                               ("%s\n\n按 %s 识别" % (rect, self.keys.get("capture", "Ctrl+F")), "meta")])

        def esc(_=None):
            cal.destroy()
            self.win.deiconify()

        cv.bind("<Button-1>", down)
        cv.bind("<B1-Motion>", move)
        cv.bind("<ButtonRelease-1>", up)
        cal.bind("<Escape>", esc)
        cal.focus_force()

    # ---------- 自测 ----------
    def selftest(self, path):
        img = Image.open(path).convert("RGB")
        rgb = np.asarray(img).astype(np.float32)
        lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        rect = self.config.get("rect")
        if rect:
            box = [0, 0, lum.shape[1], lum.shape[0]]
        else:
            box = detect_board(lum, (0, 0, lum.shape[1], lum.shape[0]))
        if not box:
            self.set_text([("selftest: 定位失败", "warn")])
            self.root.after(8000, self.root.destroy)
            return
        cells = find_cells(lum, box)
        if cells:
            xs = [c[0] for c in cells] + [c[2] for c in cells]
            ys = [c[1] for c in cells] + [c[3] for c in cells]
            box = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
        cands, confs, eggs = recognize(self.atlas, lum, rgb, box, cells_rect=cells)
        grid = [[(cands[i * 6 + j][0][1] if cands[i * 6 + j] else "") for j in range(6)]
                for i in range(6)]
        matches = solve_candidates(self.words, cands)
        print("GRID", ["".join(r) for r in grid])
        print("MATCH", [(m["item"]["w"], len(m["diffs"])) for m in matches])
        self.render(grid, matches, confs)
        self.root.after(9000, self.root.destroy)

    def run(self):
        self.root.mainloop()


def main():
    # 单实例：避免重复双击启动出两个浮窗、抢热键
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "pj_overlay_single_instance_v1")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            None,
            "评鉴浮窗已经在运行了。\n\n关闭方式：点浮窗右上角 ✕，或按 Ctrl+Alt+Esc。",
            "评鉴浮窗", 0x40)
        sys.exit(0)
    atlas = Atlas()
    words = json.load(open(os.path.join(DATA, "words.json"), encoding="utf-8"))
    selftest = None
    if "--selftest" in sys.argv:
        selftest = sys.argv[sys.argv.index("--selftest") + 1]
    OverlayApp(atlas, words, selftest=selftest).run()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        with open(os.path.join(APP_DIR, "pj_error.txt"), "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        raise
