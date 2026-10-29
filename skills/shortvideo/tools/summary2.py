#!/usr/bin/env python3
"""一张图 v2 —— 左对齐编辑化信息图（1080x1440）。

## 和 v1 的区别

v1 是"居中 + 圆角面板 + 彩色圆点 + 渐变底图"，命中同一批 slop 特征：
什么都居中、五颜六色的圆点编号、给每个要点套一个圆角盒子、底图是模糊渐变。
但一张图**不是封面**：它是点开看全屏、会被逐条读的图文笔记。所以 v2 的设计目标是
"读得下去"，不是"扫一眼被抓住"：

- **左对齐单栏**，正文 measure 控制在 ~20 字（约 60ch 的中文等价），不拉满整幅
- **序号用等宽数字**（01 02 …）放在左栏，不用彩色圆点
- **要点之间用细线分隔**，不套盒子（"给每条套圆角卡片"是最典型的 AI 味）
- **记忆锚点**从居中面板改成左对齐的大字 + 细线，像杂志的 pull-quote
- 底图只留一层极轻的竖向渐变，不用模糊色块
- 结构靠字号和留白，不靠彩色

用法：
  python summary2.py --spec oneimage-XX.json --out out/XX/summary.png
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cover2                                     # 复用强调色可用性判定
from oneimage import _spec                        # 复用 spec 解析（同一份契约）
from shortvideo import runtime as R

W, H = 1080, 1440
M = 88                                            # 外边距
MONO = "mono"


def _mono(size, bold=False):
    """等宽字体：PIL 的 truetype 在容器里未必有 mono，退回主字体但拉字距。"""
    return R.font(size, bold=bold)


def _track(d, xy, s, size, color, gap_ratio=0.18, bold=True, anchor="la"):
    f = _mono(size, bold)
    gap = size * gap_ratio
    ws = [f.getlength(ch) for ch in s]
    total = sum(ws) + gap * max(0, len(s) - 1)
    x = xy[0] - (total if anchor[0] == "r" else total / 2 if anchor[0] == "m" else 0)
    for ch, wid in zip(s, ws):
        d.text((x, xy[1]), ch, font=f, fill=tuple(color) + (255,), anchor="l" + anchor[1])
        x += wid + gap
    return total


def _wrap(s, max_w, size, bold=False):
    f = R.font(size, bold=bold)
    out, cur = [], ""
    for ch in str(s):
        if f.getlength(cur + ch) > max_w and cur:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def build(raw, out_path, w=W, h=H):
    R.set_theme(raw.get("theme"))
    c = _spec(raw)
    accent = cover2._hl_color() or tuple(c["accent"])
    bg_top, bg_bot = R.INK["900"], R.INK["950"]
    img = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), bg_bot),
                          Image.new("RGB", (w, h), bg_top), img)
    d = ImageDraw.Draw(img, "RGBA")
    fg, dim, faint = R.INK["0"], R.INK["300"], R.INK["400"]
    x0, x1 = M, w - M
    col = (x1 - x0) / 12

    # ---- 顶部：栏目 overline + 细线 ----
    _track(d, (x0, M - 6), c["kicker"], 26, accent, 0.22)
    d.line([(x0, M + 44), (x1, M + 44)], fill=tuple(fg) + (46,), width=2)
    y = M + 84

    # ---- 先算整页方案：标题和要点的字号必须一起决定 ----
    # 上一版只按"标题不超过 30% 页高"选字号，然后要点再单独找字号，
    # 结果 6 条要点 + 记忆锚点 + 页脚一共需要 1072px，实际只有 646px 可用 ——
    # 画出来直接叠字。现在改成**联合求解**：从大到小试，第一个装得下的方案胜出。
    hl_set = [t for t in c["highlight"] if t]
    tx_off = col * 1.5
    tw = x1 - (x0 + tx_off)
    title_tw = x1 - x0 - col * 2
    anchor_h = 210 if c["big"] else 0
    foot_gap = 96
    body_start = y                              # 标题从这里开始
    body_bottom = h - M - foot_gap - anchor_h

    def title_h(ts):
        return sum(len(_wrap(t, title_tw, ts)) for t in c["title"]) * ts * 1.12

    def rows_h(p, ts, ds, gap):
        heads = _wrap(p["t"], tw, ts, bold=True)[:2]
        descs = _wrap(p["d"], tw, ds)[:2] if p["d"] else []
        return len(heads) * ts * 1.26 + len(descs) * ds * 1.52 + gap

    def pts_h(ts, ds, gap):
        return sum(rows_h(p, ts, ds, gap) for p in c["points"]) if c["points"] else 0

    # 这段高度必须和下面的绘制代码逐项对齐：y+=18（标题后空隙）、track、y+=62、细线、y+=34。
    # 估成 98 时实际画 116，6 条要点的片子会溢出 21px 并被断言拦下（21/23 号实测）。
    META_H = 18 + 62 + 34 + 2
    plan = None
    # 候选要一直延到"能装下"为止：21/23 号是 6 条双行要点，最小档 (30,26,12) 仍差 61px，
    # 所以档位必须继续往下延，否则只能走兜底然后溢出（断言会拦下，但图就出不来）。
    for t_size in (92, 86, 80, 74, 68, 62, 56, 50, 46):
        for p_ts, p_ds, gap in ((46, 34, 26), (42, 32, 22), (38, 30, 18), (34, 28, 14),
                                (30, 26, 12), (28, 24, 10), (26, 22, 8), (24, 20, 6)):
            need = title_h(t_size) + META_H + pts_h(p_ts, p_ds, gap)
            if need <= body_bottom - body_start:
                plan = (t_size, p_ts, p_ds, gap)
                break
        if plan:
            break
    if plan is None:
        plan = (46, 24, 20, 6)                   # 兜底：最小号，后面还有断言兜着
    size, ts, ds, gap = plan
    for line in c["title"]:
        for seg in _wrap(line, tw, size):
            hit = next((k for k in hl_set if k in seg), None)
            if hit:
                i = seg.index(hit)
                cx = x0
                if seg[:i]:
                    d.text((cx, y), seg[:i], font=R.font(size, bold=True), fill=tuple(fg) + (255,))
                    cx += R.font(size, bold=True).getlength(seg[:i])
                d.text((cx, y), hit, font=R.font(size, bold=True), fill=accent + (255,))
                cx += R.font(size, bold=True).getlength(hit)
                if seg[i + len(hit):]:
                    d.text((cx, y), seg[i + len(hit):], font=R.font(size, bold=True),
                           fill=tuple(fg) + (255,))
            else:
                d.text((x0, y), seg, font=R.font(size, bold=True), fill=tuple(fg) + (255,))
            y += int(size * 1.12)
    y += 18
    _track(d, (x0, y), f"{len(c['points'])} 条要点", 22, dim, 0.16)
    y += 62
    d.line([(x0, y), (x1, y)], fill=tuple(fg) + (46,), width=2)
    y += 34

    # ---- 要点：左栏等宽序号 + 标题 + 说明，行间细线 ----
    # 排版必须**先算总高再落笔**：上一版一边画一边累加 y、又用 (i*per) 强行对齐，
    # 结果 6 条要点直接压到记忆锚点和页脚上（截图一眼就看出来叠字了）。
    pts = c["points"]

    if pts:
        y = body_start + title_h(size) + META_H
        for i, p in enumerate(pts, 1):
            _track(d, (x0, y + 6), f"{i:02d}", 24, accent, 0.12)
            heads = _wrap(p["t"], tw, ts, bold=True)[:2]
            for hline in heads:
                d.text((x0 + tx_off, y), hline, font=R.font(ts, bold=True),
                       fill=tuple(fg) + (255,))
                y += int(ts * 1.26)
            if p["d"]:
                for dline in _wrap(p["d"], tw, ds)[:2]:
                    d.text((x0 + tx_off, y + 2), dline, font=R.font(ds),
                           fill=tuple(dim) + (235,))
                    y += int(ds * 1.52)
            y += gap
            if i < len(pts):
                d.line([(x0, int(y - gap / 2)), (x1, int(y - gap / 2))],
                       fill=tuple(fg) + (34,), width=1)

    # 溢出必须报错，不能像上一版那样悄悄压到锚点上
    if pts and y - gap > body_bottom + 4:
        raise RuntimeError(f"要点溢出：需要到 y={y:.0f}，可用到 {body_bottom}")

    # ---- 记忆锚点：左对齐 pull-quote，不套面板 ----
    if c["big"]:
        y = body_bottom + 24
        d.line([(x0, y), (x1, y)], fill=tuple(accent) + (150,), width=2)
        _track(d, (x0, y + 22), "一句话记住", 22, faint, 0.2)
        vy = y + 62
        v = str(c["big"]["value"])
        vs = 92
        while vs > 40 and R.font(vs, bold=True).getlength(v) > col * 7:
            vs -= 4
        d.text((x0, vy), v, font=R.font(vs, bold=True), fill=accent + (255,))
        if c["big"]["label"]:
            lsize = 30
            lx = x0 + R.font(vs, bold=True).getlength(v) + 26
            for i, lline in enumerate(_wrap(c["big"]["label"], x1 - lx, lsize)[:2]):
                d.text((lx, vy + 16 + i * int(lsize * 1.45)), lline,
                       font=R.font(lsize), fill=tuple(dim) + (240,))

    # ---- 页脚：标签（左）+ 署名（右），细线上方 ----
    fy = h - M - 34
    d.line([(x0, fy - 26), (x1, fy - 26)], fill=tuple(fg) + (34,), width=1)
    if c["tags"]:
        _track(d, (x0, fy), "  ".join(c["tags"]), 24, faint, 0.06)
    if c["footer"]:
        _track(d, (x1, fy), c["footer"], 22, faint, 0.04, anchor="ra")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.convert("RGB").save(out_path, quality=95)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="一张图 v2（左对齐编辑化信息图）")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default="out/summary.png")
    ap.add_argument("--theme", default=None)
    a = ap.parse_args()
    raw = json.load(open(a.spec, encoding="utf-8"))
    if a.theme:
        raw["theme"] = a.theme
    print(f"[ok] {build(raw, a.out)}  {W}x{H}")


if __name__ == "__main__":
    main()
