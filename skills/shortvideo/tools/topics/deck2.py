#!/usr/bin/env python3
"""deck v2 —— 视频画面的版式重写（编辑化：无卡片、左对齐、单强调色）。

## 为什么要重写

抽帧看视频画面（不是看封面），五个镜头是同一套语汇：
- **清一色圆角面板**：compare 两个卡片、flow 三个盒子、cards 三个圆点编号
- **居中**：所有文字 anchor="mm"
- **一帧里两三种强调色**：compare 用玫红+青，cards 用青+两个玫红
- **背后压着巨大的幽灵数字**：hook 的标题已经写了 1.3%，背后又放一个「1.3」

封面三流派治完之后，这几条就是视频里最响的"模板味"。

## v2 的做法

| v1 | v2 |
|---|---|
| 圆角面板装内容 | 只用细线、留白和字号拉开层级 |
| 居中 | 左对齐到 GUTTER，数字/标题占不同的列 |
| 玫红+青同时出现 | 中性色说话，**只给"结论/赢家"一个强调色** |
| 背景幽灵数字 | 去掉（重复信息，只是装饰） |
| 彩色圆点编号 | 等宽数字 01/02 |

调用契约不变：`(img, t, dur, spec)`，spec 字段与 v1 一致，所以可以直接替换 `_KINDS`。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo import runtime as R
from shortvideo.scenes import SceneAPI as S

from .deck import _appear, _fade_frac, _fit_size, _note, _seq_at


def _hair(img, x0, y0, x1, y1, color=None, alpha=36, width=2):
    S.panel(img, (int(x0), int(y0), int(x1), int(y1)),
            fill=tuple(color or R.INK["0"]), alpha=alpha, radius=1)


def _mono(img, xy, s, size, color, gap_ratio=0.14, alpha=1.0, anchor="la"):
    """等宽感小标：PIL 没 tracking，逐字画。"""
    f = R.font(size, bold=True)
    gap = size * gap_ratio
    ws = [f.getlength(ch) for ch in s]
    total = sum(ws) + gap * max(0, len(s) - 1)
    x = xy[0] - (total if anchor[0] == "r" else total / 2 if anchor[0] == "m" else 0)
    for ch, w in zip(s, ws):
        S.text(img, (x, xy[1]), ch, size, fill=color, alpha=alpha, anchor="l" + anchor[1])
        x += w + gap
    return total


# ---------------------------------------------------------------- 对比
def _compare(img, t, dur, spec):
    """两栏对比：左中性、右强调；不用卡片，用一条竖细线分开。"""
    left, right = spec.get("left") or {}, spec.get("right") or {}
    p = t / dur if dur else 1
    a1, a2 = _appear(p, 0.02), _appear(p, 0.12)
    g = R.GUTTER
    mid = R.W // 2
    top = int(R.H * 0.30)
    col_w = mid - g - 48

    for side, x0, a, col in ((left, g, a1, R.INK["0"]), (right, mid + 48, a2, R.ACCENT)):
        lab = str(side.get("label", ""))
        val = str(side.get("value", ""))
        _mono(img, (x0, top), lab, 34, R.DIM, alpha=a)
        vs = _fit_size(val, col_w, 210, min_size=84)
        S.text(img, (x0, top + 64), val, vs, fill=col, bold=True, alpha=a, anchor="la")
        un = str(side.get("unit") or "")
        if un:
            S.text(img, (x0 + R.font(vs, bold=True).getlength(val) + 12, top + 64 + vs * 0.52),
                   un, 48, fill=R.DIM, bold=True, alpha=a, anchor="la")
        if side.get("note"):
            S.text(img, (x0, top + 96 + vs), str(side["note"]), 36,
                   fill=R.INK["300"], bold=False, alpha=a * 0.95, anchor="la")

    # 中间竖细线 + 一个小号 VS（不再是 70px 的"游戏感"VS）。
    # VS 要落在两个数字的视觉中线上，不是分隔线末端（实测贴底很怪）。
    _hair(img, mid, top - 10, mid, top + 320, alpha=44 * min(a1, a2), width=2)
    if a1 > 0.5 and a2 > 0.5:
        _mono(img, (mid, top + 140), "VS", 26, R.INK["400"], anchor="ma")
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.55))


# ---------------------------------------------------------------- 因果链
def _flow(img, t, dur, spec):
    """竖向因果链：一条细导轨 + 节点，不用盒子。"""
    items = spec.get("items") or []
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    g = R.GUTTER
    rail_x = g + 10
    row_h = min(300, int(R.H * 0.60 / max(1, n)))
    top = int(R.H * 0.42 - row_h * n / 2)

    # 导轨：先画整条淡线，再按进度点亮已到达的部分（"推理在往前走"）
    _hair(img, rail_x, top - 30, rail_x, top + row_h * (n - 1) + 60, alpha=30)
    lit = max(0.0, min(1.0, (p - 0.15) / 0.7))
    if lit > 0:
        _hair(img, rail_x, top - 30, rail_x, top - 30 + (row_h * (n - 1) + 90) * lit,
              color=R.ACCENT, alpha=200, width=3)

    for i, it in enumerate(items):
        a = _appear(p, _seq_at(i, n, p), _fade_frac(dur))
        if a <= 0:
            continue
        y = top + row_h * i
        last = i == n - 1
        # 节点
        S.panel(img, (rail_x - 9, y + 8, rail_x + 9, y + 26),
                fill=tuple(R.ACCENT if last else R.INK["0"]), alpha=(235 if last else 120) * a, radius=9)
        _mono(img, (rail_x + 46, y + 2), f"{i + 1:02d}", 26,
              R.ACCENT if last else R.INK["400"], alpha=a)
        S.text(img, (rail_x + 120, y - 6), str(it.get("t", "")), 54,
               fill=R.ACCENT if last else R.INK["0"], bold=True, alpha=a, anchor="la")
        if it.get("d"):
            S.text(img, (rail_x + 120, y + 62), str(it["d"]), 34,
                   fill=R.INK["300"], bold=False, alpha=a * 0.95, anchor="la")
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.7))


# ---------------------------------------------------------------- 编号清单
def _cards(img, t, dur, spec):
    """编号清单：等宽序号 + 细线分隔，不画圆点也不套盒子。"""
    # items 既可能是纯字符串，也可能是 {"t","d"} 字典（v1 的 cards 用的就是字典）。
    # 一律 str() 会把整段 dict 原文画到画面上（实测 19 号 solve 镜出现 {"t": "一张脸 1.2 克"...}）。
    raw = spec.get("items") or []
    items = []
    for x in raw:
        if isinstance(x, dict):
            if str(x.get("t", "")).strip():
                items.append((str(x["t"]), str(x.get("d") or "")))
        elif str(x).strip():
            items.append((str(x), ""))
    if not items:
        return
    p = t / dur if dur else 1
    n = len(items)
    g = R.GUTTER
    row_h = min(260, int(R.H * 0.56 / max(1, n)))
    top = int(R.H * 0.42 - row_h * n / 2)
    for i, (title, desc) in enumerate(items):
        a = _appear(p, _seq_at(i, n, p), _fade_frac(dur))
        if a <= 0:
            continue
        y = top + row_h * i
        _mono(img, (g, y + 6), f"{i + 1:02d}", 30, R.ACCENT if i == n - 1 else R.INK["400"], alpha=a)
        size = _fit_size(title, R.W - 2 * g - 110, 54, min_size=34)
        S.text(img, (g + 96, y), title, size, fill=R.INK["0"], bold=True, alpha=a, anchor="la")
        if desc:
            S.text(img, (g + 96, y + size + 14), desc, 32, fill=R.INK["300"],
                   bold=False, alpha=a * 0.95, anchor="la")
        if i < n - 1:
            _hair(img, g + 96, y + row_h - 34, R.W - g, y + row_h - 34, alpha=26 * a)
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.7))


# ---------------------------------------------------------------- 数字冲击
def _stat(img, t, dur, spec):
    """巨型数字：左对齐、单位小一号贴右侧，标签在下；不做背景幽灵字。"""
    value = spec.get("value")
    if value is None:
        return
    p = t / dur if dur else 1
    a = _appear(p, 0.04, 0.25)
    g = R.GUTTER
    unit = str(spec.get("unit") or "")
    try:
        target = float(str(value).replace(",", ""))
        pct = _ease(R.clamp((p - 0.04) / 0.7))
        shown = target * (0.60 + 0.40 * pct)
        text = (f"{shown:.{spec.get('digits', 2) if target % 1 else 0}f}"
                if spec.get("count", True) else str(value))
        if spec.get("count", True) and target % 1 == 0:
            text = f"{int(round(shown))}"
    except ValueError:
        text = str(value)
    size = _fit_size(text, R.W - 2 * g - 140, 260, min_size=90)
    y = int(R.H * 0.40)
    _hair(img, g, y - 60, R.W - g, y - 60, alpha=40 * a)
    S.text(img, (g, y), text, size, fill=R.ACCENT, bold=True, alpha=a, anchor="la")
    if unit:
        S.text(img, (g + R.font(size, bold=True).getlength(text) + 16, y + size * 0.56),
               unit, int(size * 0.30), fill=R.DIM, bold=True, alpha=a, anchor="la")
    if spec.get("label"):
        _mono(img, (g, y + size + 40), str(spec["label"]), 30, R.INK["300"], alpha=a, gap_ratio=0.04)
    if spec.get("note"):
        _note(img, spec["note"], alpha=_appear(p, 0.55))


def _ease(x):
    return x * x * (3 - 2 * x)


# ---------------------------------------------------------------- 统一页眉
def head(img, label, a=1.0):
    """全片统一页眉：一条细线 + 左侧编号小标。

    两个作用：
    1. 视觉语法——所有镜头共用一条基线，画面才有"一套片子"的秩序感；
    2. 前 0.5s 的墨量——去掉幽灵数字后，5 条片子开场墨量掉到 1.16~1.73%（门槛 1.8%），
       这条页眉几乎**从第一帧就在**，直接把开场的"空"补上。
    """
    if a <= 0.02:
        return
    g = R.GUTTER
    y = 196
    S.panel(img, (int(g), y, int(R.W - g), y + 2),
            fill=tuple(R.INK["0"]), alpha=int(46 * a), radius=1)
    _mono(img, (g, y - 44), label, 26, R.INK["400"], alpha=a)


REPLACEMENTS = {"compare": _compare, "flow": _flow, "cards": _cards, "stat": _stat}
