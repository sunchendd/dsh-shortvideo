"""deck：数据驱动的通用分镜题材包。

不写 Python 也能出片：content.json 里用 `deck` 描述每个镜头画什么，这个题材包负责渲染。
（对应"创意 → 视频"这一步：创意总监只要产出 narration + deck，流水线就能出片。）

支持的 kind：
  big      大字入场（1~3 行 + 可选副标）—— 钩子/结论
  cards    1~4 张卡片（序号 + 标题 + 说明），依次点亮 —— 规则/要点
  bars     条形对比（按数值比例），可选结论注 —— 数据/对比
  compare  左右两栏对比（大数值 + 标签），带 VS —— 反常识/翻倍
  formula  公式块（词块 + 等号）—— 原理/定理
  steps    竖排序号步骤，当前步高亮 —— 流程/实战
  list     逐条出现的清单 —— 三条建议/清单
  bits     格子阵（0/1、开/关），按 items 填色 —— 二进制/开关类

deck 写法（content.json 顶层）：
  "deck": {
    "hook":   {"kind": "big", "lines": ["0.1 + 0.2", "≠ 0.3"], "sub": "为什么？"},
    "rule":   {"kind": "cards", "items": [{"t": "0.1", "d": "二进制里是无限循环"}, ...]},
    "insight":{"kind": "bars", "items": [{"label": "0.1", "v": 1.0}, ...], "note": "..."},
    "reveal": {"kind": "formula", "parts": ["0.1+0.2", "=", "0.30000000000000004"]},
    "solve":  {"kind": "steps", "items": [{"t": "比较用容差"}, ...]}
  }
"""
import math

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from shortvideo import runtime as R
from shortvideo.scenes import SceneAPI as S  # noqa: F401  (供 scene_fn 签名使用)


def prepare(ctx):
    """把 deck 规范挂到 ctx 上，返回给 make.py 合并。"""
    content = ctx.get("content") or {}
    deck = ctx.get("deck") or content.get("deck") or {}
    return {"deck": deck}


# ---------------------------------------------------------------- 工具

def _note(img, text, y=None, size=42, alpha=1.0, color=None):
    """镜头注脚：放在字幕区上方（y≈1430），字号比字幕小、颜色更淡。"""
    if not text or alpha <= 0.02:
        return
    y = int(R.H * 0.745) if y is None else y
    S.text(img, (R.W / 2, y), str(text), size, fill=tuple(color or R.DIM),
           bold=False, anchor="mm", alpha=alpha)


def _ease(p):
    return 1 - (1 - R.clamp(p)) ** 3


def _appear(p, start, dur=0.18):
    """第 start 比例处开始出现，dur 比例内淡入完。返回 0~1 透明度。"""
    if p <= start:
        return 0.0
    return R.clamp((p - start) / max(1e-6, dur))


def _fade_frac(dur, seconds=0.22):
    """把"秒"换算成"镜头进度比例"：镜头越长，同一段动画占的比例越小。

    直接用 0.14 这种固定比例，在 7 秒镜头里就是 1 秒的缓慢淡入——开头又是空画面。
    """
    return min(0.5, max(0.04, seconds / max(0.5, dur)))


def _seq_at(i, n, p, span=(0.00, 0.50)):
    """第 i/n 个元素的出现起点（默认：第一个 0.0 就出现，最晚 0.5 到场）。

    早先的默认值是 (0.10, 0.78)：镜头开头 1~2 秒画面是空的（只剩字幕），
    实测被 qa.py 判为"画面太空"，直接伤完播。所以第一个元素必须立刻可见。
    """
    a, b = span
    return a + (b - a) * (i / max(1, n if n > 1 else 1))


def _fit_size(text, max_w, size, bold=True, min_size=24):
    s = size
    while s > min_size:
        if R.font(s, bold=bold).getlength(text) <= max_w:
            return s
        s -= 2
    return min_size


# ---------------------------------------------------------------- 各 kind

def _big(img, t, dur, spec):
    lines = [str(x) for x in (spec.get("lines") or [spec.get("text") or ""]) if str(x).strip()][:3]
    if not lines:
        return
    p = t / dur if dur else 1
    n = len(lines)
    sizes = [_fit_size(ln, R.W - 200, 132 if n == 1 else 112) for ln in lines]
    size = min(sizes)
    line_h = int(size * 1.24)
    y0 = int(R.H * 0.40 - line_h * n / 2)
    accent = tuple(spec.get("accent") or R.ACCENT)
    for i, ln in enumerate(lines):
        # 每行错峰砸入（0.0 / 0.12 / 0.24 秒），比整块一起出现有节奏
        S.kinetic_text(img, (R.W / 2, y0 + line_h * i + line_h / 2), ln, size,
                       fill=tuple(spec.get("color") or R.TEXT),
                       t=t - i * 0.12, dur=0.5, style=spec.get("style") or "slam",
                       stroke=tuple(spec.get("stroke") or R.BG_BOT), stroke_w=max(3, size // 22),
                       shadow=0.8)
    if spec.get("sub"):
        a = _appear(p, 0.45)
        _note(img, spec["sub"], y=int(y0 + line_h * n + 96), size=int(size * 0.34),
              alpha=a, color=accent)


def _cards(img, t, dur, spec):
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    top, bottom = int(R.H * 0.30), int(R.H * 0.78)
    row_h = min(int((bottom - top) / n), 210)
    total_h = row_h * n
    y = top + ((bottom - top) - total_h) // 2
    for i, it in enumerate(items):
        a = _appear(p, _seq_at(i, n, p))
        if a <= 0:
            continue
        cy = y + row_h * i + row_h / 2
        col = R.ACCENT if i % 2 == 0 else R.TILE_ON
        S.number_badge(img, 150, cy, i + 1, r=int(row_h * 0.24), color=col, alpha=a)
        title = str(it.get("t") or "")
        size = _fit_size(title, R.W - 380, int(row_h * 0.36))
        S.text(img, (240, cy - row_h * 0.12), title, size, fill=R.TEXT, bold=True,
               anchor="lm", alpha=a)
        if it.get("d"):
            S.text(img, (240, cy + row_h * 0.24), str(it["d"]), int(row_h * 0.24),
                   fill=R.DIM, bold=False, anchor="lm", alpha=a * 0.95)


def _bars(img, t, dur, spec):
    """横向条形对比：左侧标签、条、右侧数值；最长的一条用强调色。"""
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    reveal = _ease(R.clamp((p - 0.05) / 0.55))
    vals = [float(it.get("v", 0)) for it in items]
    vmax = max(vals) or 1.0
    n = len(items)
    top = int(R.H * 0.34)
    row_h = min(int(R.H * 0.44 / max(1, n)), 150)
    x0, x1 = 110, R.W - 110
    track_w = x1 - x0
    peak = max(range(n), key=lambda i: vals[i])
    for i, (it, v) in enumerate(zip(items, vals)):
        a = _appear(p, _seq_at(i, n, p), _fade_frac(dur))
        if a <= 0:
            continue
        cy = top + row_h * i + row_h / 2
        col = R.ACCENT if i == peak else R.TILE_ON
        S.text(img, (x0, cy - row_h * 0.24), str(it.get("label", "")), 40,
               fill=R.DIM, bold=True, anchor="lm", alpha=a)
        if it.get("value"):
            # 数值右对齐在同一行的右端：两根条的数值成列，不跟着条长跑
            S.text(img, (x1, cy - row_h * 0.24), str(it["value"]), 44,
                   fill=col, bold=True, anchor="rm", alpha=a)
        bh = int(row_h * 0.28)
        S.panel(img, (x0, cy + 8, x1, cy + 8 + bh), fill=(255, 255, 255), alpha=0.06 * a,
                radius=bh // 2)
        fill_w = (track_w - 4) * (v / vmax) * reveal
        if fill_w > 2:
            S.panel(img, (x0 + 2, cy + 10, x0 + 2 + fill_w, cy + 6 + bh),
                    fill=col, alpha=0.95 * a, radius=bh // 2)

    if spec.get("note"):
        _note(img, spec["note"], y=int(R.H * 0.70), alpha=_appear(p, 0.5))


def _compare(img, t, dur, spec):
    left, right = spec.get("left") or {}, spec.get("right") or {}
    p = t / dur if dur else 1
    # 两栏尽快出现（只留一点点错峰），否则前半段画面是"半张脸"
    a1 = _appear(p, 0.02)
    a2 = _appear(p, 0.14)
    cy = int(R.H * 0.46)
    bw, bh = int(R.W * 0.38), int(R.H * 0.30)
    for side, box, a, col in (
            (left, (int(R.W * 0.06), cy - bh // 2), a1, R.TILE_ON),
            (right, (int(R.W * 0.56), cy - bh // 2), a2, R.ACCENT)):
        x0, y0 = box
        S.panel(img, (x0, y0, x0 + bw, y0 + bh), fill=(10, 14, 26), alpha=0.55 * a,
                radius=28, outline=col)
        S.text(img, (x0 + bw / 2, y0 + bh * 0.26), str(side.get("label", "")), 40,
               fill=R.DIM, bold=True, anchor="mm", alpha=a)
        S.text(img, (x0 + bw / 2, y0 + bh * 0.58), str(side.get("value", "")), 96,
               fill=col, bold=True, anchor="mm", alpha=a)
        if side.get("note"):
            S.text(img, (x0 + bw / 2, y0 + bh * 0.85), str(side["note"]), 36,
                   fill=R.TEXT, bold=False, anchor="mm", alpha=a)
    S.kinetic_text(img, (R.W / 2, cy), "VS", 64, fill=R.TEXT, t=t - 0.35, dur=0.4,
                   style="pop", stroke=R.BG_BOT, stroke_w=4, shadow=0.9)
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.6))


def _formula(img, t, dur, spec):
    """公式块：自动缩字号，太宽自动拆两行（长数字串很常见）。"""
    parts = [str(x) for x in (spec.get("parts") or []) if str(x).strip()]
    if not parts:
        return
    p = t / dur if dur else 1
    a = _appear(p, 0.0, _fade_frac(dur))
    max_w = R.W - 160

    def mk(chunk, size):
        ps = []
        for i, x in enumerate(chunk):
            opts = set(x.strip())
            if opts and opts <= set("+-*/=<>≈≠"):
                x = f" {x.strip()} "        # 运算符两侧留气口
            ps.append((x, size, (R.ACCENT if i % 2 else R.TEXT),
                       any(c.isdigit() or c in "+-*/=.<>" for c in x), 0))
        return ps

    def width(chunk, size):
        return sum(R.font(sz, math=m).getlength(x) for x, sz, _c, m, _d in mk(chunk, size))

    # 贪心断行 × 字号搜索：优先大字，其次行数
    def wrap_at(size):
        out, cur = [], []
        for x in parts:
            cand = cur + [x]
            if cur and width(cand, size) > max_w:
                out.append(cur)
                cur = [x]
            else:
                cur = cand
        if cur:
            out.append(cur)
        return out

    size, lines = 96, wrap_at(96)
    while size > 40 and (len(lines) > 3 or max(width(ch, size) for ch in lines) > max_w):
        size -= 4
        lines = wrap_at(size)
    cy0 = int(R.H * 0.42) - (len(lines) - 1) * size * 0.7
    for i, ch in enumerate(lines):
        S.formula(img, R.W / 2, int(cy0 + i * size * 1.4), mk(ch, size), alpha=a)
    if spec.get("note"):
        _note(img, spec["note"], y=int(R.H * 0.66), alpha=_appear(p, 0.45))


def _steps(img, t, dur, spec):
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    top = int(R.H * 0.32)
    row_h = min(int(R.H * 0.42 / max(1, n)), 140)
    cur = min(n - 1, int(R.clamp(p * 1.15) * n))
    for i, it in enumerate(items):
        y = top + row_h * i
        on = i <= cur
        col = R.ACCENT if i == cur else (R.TILE_ON if on else R.FAINT)
        S.number_badge(img, 150, y + row_h / 2, i + 1, r=38, color=col,
                       alpha=1.0 if on else 0.45)
        S.text(img, (230, y + row_h / 2), str(it.get("t") or it), 46,
               fill=R.TEXT if on else R.DIM, bold=on, anchor="lm",
               alpha=1.0 if on else 0.55)
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.7))


def _list(img, t, dur, spec):
    _cards(img, t, dur, {"items": [{"t": x} if isinstance(x, str) else x
                                   for x in (spec.get("items") or [])]})


def _bits(img, t, dur, spec):
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    cols = n if n <= 7 else 6      # 七种颜色这类刚好一行排满
    rows = math.ceil(n / cols)
    gap = 16
    cell = min(150, int((R.W - 220 - gap * (cols - 1)) / cols))
    gx = R.W / 2 - (cell * cols + gap * (cols - 1)) / 2
    gy = int(R.H * 0.40) - (cell * rows + gap * (rows - 1)) / 2
    for i, it in enumerate(items):
        a = _appear(p, _seq_at(i, n, p), _fade_frac(dur))
        if a <= 0:
            continue
        r_, c_ = divmod(i, cols)
        x = gx + c_ * (cell + gap)
        y = gy + r_ * (cell + gap)
        on = bool(it.get("on", it.get("v", 1)))
        S.panel(img, (x, y, x + cell, y + cell), fill=(10, 14, 26),
                alpha=(0.9 if on else 0.35) * a, radius=14,
                outline=R.ACCENT if on else R.FAINT)
        S.text(img, (x + cell / 2, y + cell / 2), str(it.get("label", "1" if on else "0")),
               int(cell * 0.46), fill=R.ACCENT if on else R.DIM, bold=True,
               anchor="mm", alpha=a)
    if spec.get("note"):
        _note(img, spec["note"], y=int(R.H * 0.72), alpha=_appear(p, 0.6))


def _digits(img, t, dur, spec):
    """数字/二进制串：等宽逐字排，可高亮重复段、可在某处画"截断"标记。

    spec: {"text": "0.0001100110011…", "highlight": "0011", "cut": 20,
           "note": "...", "size": 64}
    """
    text = str(spec.get("text") or "")
    if not text:
        return
    p = t / dur if dur else 1
    a = _appear(p, 0.0, _fade_frac(dur))
    hl = spec.get("highlight") or ""
    cut = spec.get("cut")
    size = int(spec.get("size") or 64)
    per_line = int(spec.get("per_line") or 18)
    max_w = R.W - 200
    # 按字符宽度自适应字号
    while size > 26 and R.font(size, bold=True).getlength("0" * min(per_line, len(text))) > max_w:
        size -= 2
    f = R.font(size, bold=True)
    chunk = max(6, int(max_w // max(1, f.getlength("0"))))
    lines = [text[i:i + chunk] for i in range(0, len(text), chunk)][:3]
    line_h = int(size * 1.5)
    y0 = int(R.H * 0.40 - line_h * len(lines) / 2)
    hl_ranges = []
    if hl:
        start = 0
        while True:
            k = text.find(hl, start)
            if k < 0:
                break
            hl_ranges.append((k, k + len(hl)))
            start = k + len(hl)
    idx = 0
    for li, ln in enumerate(lines):
        x = R.W / 2 - f.getlength(ln) / 2
        y = y0 + li * line_h
        for ch in ln:
            on = any(a0 <= idx < a1 for a0, a1 in hl_ranges)
            col = R.AMBER if on else R.TEXT
            if cut is not None and idx >= cut:
                col = R.FAINT
            S.text(img, (x, y), ch, size, fill=col, bold=True, anchor="lm", alpha=a)
            x += f.getlength(ch)
            idx += 1
    if cut is not None:
        # 截断处的竖线 + 标记
        cut_line = [i for i, ln in enumerate(lines)
                    if cut <= sum(len(x) for x in lines[:i + 1]) and cut > sum(len(x) for x in lines[:i])]
        if cut_line:
            li = cut_line[0]
            off = cut - sum(len(x) for x in lines[:li])
            seg = lines[li][:off]
            x = R.W / 2 - f.getlength(lines[li]) / 2 + f.getlength(seg)
            y = y0 + li * line_h
            S.panel(img, (x - 3, y - size * 0.72, x + 3, y + size * 0.72),
                    fill=R.ACCENT, alpha=0.95 * a, radius=2)
    if spec.get("note"):
        S.caption(img, str(spec["note"]), _appear(p, 0.5), y=int(R.H * 0.66), size=44)


def _stat(img, t, dur, spec):
    """巨型数字 + 单位 + 说明（数字冲击镜；带滚动计数动画）。"""
    value = spec.get("value")
    if value is None:
        return
    p = t / dur if dur else 1
    a = _appear(p, 0.04, 0.25)
    try:
        target = float(str(value).replace(",", ""))
        pct = _ease(R.clamp((p - 0.04) / 0.7))
        shown = target * pct
        text = (f"{shown:.{spec.get('digits', 2) if target % 1 else 0}f}"
                if spec.get("count", True) else str(value))
        if spec.get("count", True) and target % 1 == 0:
            text = f"{int(round(shown))}"
    except ValueError:
        text = str(value)
    unit = str(spec.get("unit") or "")
    size = 200
    while size > 60 and R.font(size, bold=True).getlength(text + unit) > R.W - 160:
        size -= 6
    S.kinetic_text(img, (R.W / 2, int(R.H * 0.38)), text, size, fill=tuple(spec.get("color") or R.ACCENT),
                   t=t - 0.05, dur=0.45, style="slam", stroke=R.BG_BOT,
                   stroke_w=max(4, size // 24), shadow=0.9, alpha=a)
    if unit:
        S.text(img, (R.W / 2, int(R.H * 0.38) + size * 0.62), unit, int(size * 0.30),
               fill=R.TEXT, bold=True, anchor="mm", alpha=a)
    if spec.get("label"):
        S.text(img, (R.W / 2, int(R.H * 0.38) + size * 0.62 + 90), str(spec["label"]),
               int(size * 0.24), fill=R.DIM, bold=False, anchor="mm", alpha=_appear(p, 0.25))
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.55))


def _timeline(img, t, dur, spec):
    """横向时间轴：节点依次点亮，当前节点放大并高亮（过程/历史/因果链）。"""
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    y = int(R.H * 0.44)
    x0, x1 = 140, R.W - 140
    step = (x1 - x0) / max(1, n - 1) if n > 1 else 0
    cur = min(n - 1, int(R.clamp(p * 1.1) * n))
    S.panel(img, (x0 - 20, y - 3, x1 + 20, y + 3), fill=(255, 255, 255), alpha=0.10, radius=3)
    grown = x0 + step * (cur + R.clamp((p * 1.1 * n) - cur))
    S.panel(img, (x0 - 20, y - 3, max(x0, grown), y + 3), fill=R.ACCENT, alpha=0.9, radius=3)
    for i, it in enumerate(items):
        cx = x0 + step * i
        done = i <= cur
        r = 26 if i == cur else 18
        col = R.ACCENT if i == cur else (R.TILE_ON if done else R.FAINT)
        S.panel(img, (cx - r, y - r, cx + r, y + r), fill=col, alpha=1.0 if done else 0.5, radius=r)
        S.text(img, (cx, y), str(it.get("k", it.get("at", i + 1))), 30,
               fill=R.BG_BOT if done else R.DIM, bold=True, anchor="mm")
        a = _appear(p, _seq_at(i, n, p), _fade_frac(dur))
        S.text(img, (cx, y + 74), str(it.get("t", "")), 38,
               fill=R.TEXT if done else R.DIM, bold=(i == cur), anchor="mm", alpha=max(a, 0.25))
        if it.get("d"):
            S.text(img, (cx, y + 122), str(it["d"]), 28, fill=R.DIM, bold=False,
                   anchor="mm", alpha=max(a, 0.2))
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.6))


def _flow(img, t, dur, spec):
    """竖向因果链：A → B → C，逐级点亮（讲机制/推理用）。"""
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    row_h = min(230, int(R.H * 0.56 / max(1, n)))
    top = int(R.H * 0.40 - row_h * n / 2)
    bw = R.W - 300
    for i, it in enumerate(items):
        a = _appear(p, _seq_at(i, n, p), _fade_frac(dur))
        if a <= 0:
            continue
        y = top + row_h * i
        box_h = row_h * 0.78
        col = R.ACCENT if i == n - 1 else R.TILE_ON
        S.panel(img, (150, y, 150 + bw, y + box_h), fill=(10, 14, 26), alpha=0.62 * a,
                radius=22, outline=col)
        if it.get("d"):
            S.text(img, (150 + bw / 2, y + box_h * 0.34), str(it.get("t", "")), 42,
                   fill=R.TEXT, bold=True, anchor="mm", alpha=a)
            S.text(img, (150 + bw / 2, y + box_h * 0.72), str(it["d"]), 28,
                   fill=R.DIM, bold=False, anchor="mm", alpha=a)
        else:
            S.text(img, (150 + bw / 2, y + box_h / 2), str(it.get("t", "")), 44,
                   fill=R.TEXT, bold=True, anchor="mm", alpha=a)
        if i < n - 1:
            ay = y + box_h
            S.arrow(img, (150 + bw / 2, ay + 6), (150 + bw / 2, ay + row_h * 0.20),
                    color=col, width=4, head=14, alpha=a)
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.7))


_KINDS = {
    "stat": _stat, "timeline": _timeline, "flow": _flow,"big": _big, "cards": _cards, "bars": _bars, "compare": _compare,
          "formula": _formula, "steps": _steps, "list": _list, "bits": _bits,
          "digits": _digits}


# ---------------------------------------------------------------- 题材包入口

def build(ctx):
    deck = ctx.get("deck") or {}
    out = {}
    for shot_id, spec in deck.items():
        fn = _KINDS.get(str((spec or {}).get("kind") or "big"))
        if fn is None:
            fn = _big
        out[shot_id] = (lambda f, s: (lambda img, t, dur, _ctx, _S: f(img, t, dur, s)))(fn, spec)
    return out
