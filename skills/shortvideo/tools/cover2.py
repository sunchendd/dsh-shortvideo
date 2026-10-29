#!/usr/bin/env python3
"""封面 v2 —— 按"反模板化"设计方法重做，三个流派可选。

## 为什么推倒重来

v1 的封面被用户一句"真的很挫"点破。用 docs/design/design-taste.md 的清单对照，
v1 命中了 **6 条 slop 特征**：

| slop 特征 | v1 的做法 |
|---|---|
| 什么都居中 | 标题、副标、标签、胶囊全部水平居中，没有张力也没有层级 |
| 每条片子同一个版式 | 33 张封面是同一个模板换字，信息流里一眼看出是批量生产的 |
| 效果堆叠 | 柔光 + 点阵 + 颗粒 + 描边 + 高亮块，五种效果同时上 |
| 强调色过多 | 青（结构）+ 琥珀（高亮）+ 玫（状态）三色抢戏 |
| 假结构标签 | 「37 秒速通」胶囊不说任何事，纯 filler |
|  timid 字号对比 | 标题 144 / 正文 48，只有 3 倍差，读起来像模板 |

## 三个流派（都是真实网格，不居中）

- `editorial` 编辑部：**左对齐 8 栏网格**，超大标题 + 极紧行距，细线分隔，
  数字做成 pull-quote，副标放底部左角。刊物感、克制。
- `blueprint` 蓝图：**可见模块网格**，角上的等宽小标（栏目 / 序号 / 时长），
  数字装进带边框的数据板，单一信号色。工程感、可信。
- `cinematic` 电影：**满幅渐变 + 一个超大标题**，行距 1.02，唯一强调色，
  装饰只留一条极细基线。戏剧感、留白最大。

共同纪律（来自 doctrine）：
1. 一个主色 + 至多一个强调色，中性色承担其余
2. 标题短而宽，行距 1.02~1.10，绝不折成 5 行
3. 外边距 > 元素间距（位置层级不许倒挂）
4. 效果每种只用一次，不叠加
5. 所有配置文案必须画出（含 manifest，供 lint 校验）
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo import runtime as R  # noqa: E402

W, H = 1080, 1440
TS = R.TS


# ---------------------------------------------------------------- 公共工具

def _canvas(bg, top=None, bottom=None):
    """竖向渐变底。top/bottom 缺省时用同一底色（平涂）。"""
    if top is None and bottom is None:
        return Image.new("RGB", (W, H), bg)
    top = top or bg
    bottom = bottom or bg
    grad = Image.linear_gradient("L").resize((W, H))
    return Image.composite(Image.new("RGB", (W, H), bottom),
                           Image.new("RGB", (W, H), top), grad)


def _hairline(d, x0, y0, x1, y1, color, alpha=46, width=2):
    d.line([(x0, y0), (x1, y1)], fill=tuple(color) + (alpha,), width=width)


def _fit(text, max_w, hi, lo, bold=True):
    """在 [lo, hi] 里找能塞进 max_w 的最大字号。"""
    size = hi
    while size > lo and R.font(size, bold=bold).getlength(text) > max_w:
        size -= 2
    return size


def _block_height(n_lines, size, leading):
    return int(size * leading) * n_lines


def _text(d, xy, s, size, color, anchor="la", bold=True, alpha=255):
    d.text(xy, s, font=R.font(size, bold=bold), fill=tuple(color) + (alpha,), anchor=anchor)


def _tracked(d, xy, s, size, color, tracking=0.18, anchor="la", bold=True, alpha=255):
    """带字距的小标（overline / eyebrow）。PIL 没有 tracking，逐字画。"""
    f = R.font(size, bold=bold)
    gap = size * tracking
    widths = [f.getlength(ch) for ch in s]
    total = sum(widths) + gap * max(0, len(s) - 1)
    if anchor.startswith("m"):
        x = xy[0] - total / 2
    elif anchor.startswith("r"):
        x = xy[0] - total          # 右对齐必须减总宽，否则整串向右画出画布
    else:
        x = xy[0]
    y = xy[1]
    for ch, w in zip(s, widths):
        d.text((x, y), ch, font=f, fill=tuple(color) + (alpha,), anchor="l" + anchor[1])
        x += w + gap
    return total


def _lum(c):
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(a, b):
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _sat(c):
    r, g, b = [v / 255.0 for v in c[:3]]
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _hl_color():
    """挑一个"真的能被看见"的高亮色。

    graphite 的品牌色是灰白 #bcbcc6，跟正文 #fcfcff 只有 1.84 对比、饱和度 0.05——
    高亮画上去跟正文一个样，等于没高亮（实测 10 号「三成」完全看不出来）。
    判据是**跟背景的对比 + 色相要跟正文分开**，不是跟正文比对比度：
    拿正文当参照物的话，深底上的青色（1.48）会被判为不合格，可它在深底上极其醒目。
    所以：对比(色, 底) ≥ 3.0 且 饱和度 ≥ 0.25 才算合格（饱和度用来排除灰白当强调色）。
    都不合格就取对比最高的；再不行返回 None，调用方只画下划线。
    """
    bg = R.INK["950"]
    # 阈值来自实测分布：8 套主题的品牌色对背景对比最低 5.06（arctic），
    # 饱和度最低 0.05（graphite 的灰白）。所以 4.0 / 0.25 这条线只筛掉 graphite，
    # 不会误伤任何一套正常主题——**按序取第一个合格的**，而不是取对比最高的，
    # 否则 midnight 会从品牌青跳到更浅的 400，设计语言就变了。
    for cand in (tuple(R.BRAND["500"]), tuple(R.AMBER), tuple(R.BRAND["400"])):
        if _contrast(bg, cand) >= 4.0 and _sat(cand) >= 0.25:
            return cand
    return None


def _meta(content):
    """从 content.json 抽出渲染需要的文案（与 v1 同一份契约）。"""
    cov = content.get("cover") or {}
    hl = cov.get("headline") or ([content.get("title")] if content.get("title") else [])
    if isinstance(hl, str):
        hl = [hl]
    secs = float(content.get("seconds") or 0)
    if not secs:
        secs = sum(float((s or {}).get("min_dur") or 0) for s in content.get("shots") or [])
    return {
        "kicker": str(cov.get("kicker") or content.get("badge") or "硬核科普"),
        "headline": [R.sanitize(str(x), bold=True) for x in hl if str(x).strip()][:3],
        "highlight": [R.sanitize(str(x), bold=True) for x in (cov.get("highlight") or [])],
        "sub": R.sanitize(str(cov.get("sub") or content.get("subtitle") or ""), bold=True),
        "tags": [R.sanitize(str(t), bold=True) for t in (cov.get("tags") or [])][:3],
        "serial": str(cov.get("serial") or ""),
        "cta": str(cov.get("cta") or ""),
        "seconds": round(secs),
    }


# ---------------------------------------------------------------- 流派一：编辑部

def editorial(m, manifest):
    """左对齐 8 栏网格。超大标题、极紧行距、细线、底部副标。"""
    ink_dark = not R.LIGHT
    bg = R.INK["950"]
    top, bottom = (R.INK["850"], R.INK["950"]) if ink_dark else (R.INK["900"], R.INK["950"])
    img = _canvas(bg, top, bottom)
    d = ImageDraw.Draw(img, "RGBA")
    M = 96                     # 外边距 > 所有元素间距（位置层级不许倒挂）
    col = (W - 2 * M) / 8
    fg, dim = R.INK["0"], R.INK["300"]
    accent = _hl_color() or R.BRAND["500"]
    hl_on = _hl_color() is not None

    # 顶部：栏目名（字距拉开的小标）+ 一条通到底的细线
    _tracked(d, (M, M + 10), m["kicker"], 32, accent, tracking=0.22)
    if m["serial"]:
        _tracked(d, (W - M, M + 10), f"NO.{m['serial']}", 30, dim, tracking=0.2, anchor="ra")
    _hairline(d, M, M + 74, W - M, M + 74, fg, 40)

    # 标题：左对齐、占 6 栏、行距 1.06
    lines = m["headline"]
    fs = min(_fit(ln, col * 6, hi=190, lo=104) for ln in lines)
    lead = 1.06
    y = int(H * 0.31)
    for ln in lines:
        hit = next((w for w in m["highlight"] if w and w in ln), None)
        if hit:
            k = ln.index(hit)
            pre, mid = ln[:k], hit
            post = ln[k + len(mid):]
            x = M
            if pre:
                _text(d, (x, y), pre, fs, fg)
                x += R.font(fs, bold=True).getlength(pre)
            # 强调：只加一条下划线（不是色块），刊物感
            _text(d, (x, y), mid, fs, accent)
            mw = R.font(fs, bold=True).getlength(mid)
            _hairline(d, x, y + fs * 1.02, x + mw, y + fs * 1.02, accent, 235, width=6)
            if post:
                _text(d, (x + mw, y), post, fs, fg)
        else:
            _text(d, (M, y), ln, fs, fg)
        y += int(fs * lead)
        manifest["drawn_headline"] = manifest.get("drawn_headline", 0) + 1

    # 数字 pull-quote：右上，与标题错位产生张力
    in_title = any(any(h in ln for h in m["highlight"]) for ln in lines)
    num = None if in_title else next(
        (h for h in m["highlight"] if any(c.isdigit() for c in h)), None)
    if num:
        big = _fit(num, col * 3, hi=132, lo=64)
        _text(d, (W - M, int(H * 0.20)), num, big, accent, anchor="ra")
        _hairline(d, W - M - col * 3, int(H * 0.20) + big * 1.1, W - M, int(H * 0.20) + big * 1.1, accent, 120, 2)

    # 底部：细线 → 副标 → 标签 → CTA，**各占一行**。
    # 之前副标(左)和标签(右)画在同一行，240px 缩略图下会撞在一起（实测叠字）。
    base = H - M
    _hairline(d, M, base - 176, W - M, base - 176, fg, 40)
    if m["sub"]:
        sfs = _fit(m["sub"], col * 8, hi=62, lo=40, bold=False)
        _text(d, (M, base - 162), m["sub"], sfs, dim, bold=False)
    if m["tags"]:
        _tracked(d, (M, base - 76), "  ".join(m["tags"]), 28, dim, tracking=0.06)
    if m["cta"]:
        _tracked(d, (W - M, base - 72), m["cta"], 26, accent, tracking=0.14, anchor="ra")
    manifest["texts"] = [m["kicker"], *lines, m["sub"], *m["tags"], m["cta"]]
    return img


# ---------------------------------------------------------------- 流派二：蓝图

def blueprint(m, manifest):
    """可见模块网格 + 等宽小标 + 数字数据板。单一信号色。"""
    ink_dark = not R.LIGHT
    bg = R.INK["950"]
    img = _canvas(bg)
    d = ImageDraw.Draw(img, "RGBA")
    M = 72
    fg, dim = R.INK["0"], R.INK["300"]
    accent = R.BRAND["500"]

    # 可见网格：8×10 的模块线，极低对比（结构感来自对齐，不来自粗线）
    for i in range(1, 8):
        x = M + (W - 2 * M) * i / 8
        _hairline(d, x, M, x, H - M, fg, 14, 1)
    for j in range(1, 10):
        y = M + (H - 2 * M) * j / 10
        _hairline(d, M, y, W - M, y, fg, 14, 1)
    # 外框：硬边，不用圆角
    d.rectangle([M, M, W - M, H - M], outline=tuple(fg) + (54,), width=2)

    # 四角元信息（等宽感全靠字距）
    _tracked(d, (M + 28, M + 28), m["kicker"], 28, accent, tracking=0.2)
    _tracked(d, (W - M - 28, M + 28), m["serial"] and f"NO.{m['serial']}" or "", 28, dim,
             tracking=0.2, anchor="ra")
    _tracked(d, (M + 28, H - M - 52), f"{m['seconds']} SEC", 26, dim, tracking=0.18)
    _tracked(d, (M + 28, H - M - 92), "  ".join(m["tags"]), 26, dim, tracking=0.06)

    # 标题：顶格三行，行距 1.1，占 7 栏
    lines = m["headline"]
    fs = min(_fit(ln, (W - 2 * M) * 7 / 8, hi=152, lo=88) for ln in lines)
    y = M + 120
    for ln in lines:
        hit = next((w for w in m["highlight"] if w and w in ln), None)
        if hit:
            k = ln.index(hit)
            x = M + 28
            if ln[:k]:
                _text(d, (x, y), ln[:k], fs, fg)
                x += R.font(fs, bold=True).getlength(ln[:k])
            _text(d, (x, y), hit, fs, accent)
            rest = ln[k + len(hit):]
            if rest:
                _text(d, (x + R.font(fs, bold=True).getlength(hit), y), rest, fs, fg)
        else:
            _text(d, (M + 28, y), ln, fs, fg)
        y += int(fs * 1.1)
        manifest["drawn_headline"] = manifest.get("drawn_headline", 0) + 1

    # 数据板：数字装进带边框的方块（图谱感）
    in_title = any(any(h in ln for h in m["highlight"]) for ln in lines)
    num = None if in_title else next(
        (h for h in m["highlight"] if any(c.isdigit() for c in h)), None)
    if num:
        bw, bh = 360, 200
        bx, by = W - M - 28 - bw, H - M - 28 - bh - 96
        d.rectangle([bx, by, bx + bw, by + bh], outline=tuple(accent) + (170,), width=3)
        _tracked(d, (bx + 26, by + 22), "KEY FIGURE", 22, dim, tracking=0.22)
        nfs = _fit(num, bw - 52, hi=118, lo=56)
        _text(d, (bx + bw / 2, by + bh / 2 + 14), num, nfs, accent, anchor="mm")

    # 副标：贴着左边模块线
    if m["sub"]:
        sfs = _fit(m["sub"], (W - 2 * M) * 6 / 8, hi=50, lo=34, bold=False)
        _text(d, (M + 28, H - M - 28 - 150), m["sub"], sfs, dim, bold=False)
    manifest["texts"] = [m["kicker"], *lines, m["sub"], *m["tags"]]
    return img


# ---------------------------------------------------------------- 流派三：电影

def cinematic(m, manifest):
    """满幅渐变 + 超大标题 + 唯一强调色。装饰只留一条基线。"""
    ink_dark = not R.LIGHT
    base = R.INK["950"]
    img = _canvas(base, R.INK["900"] if ink_dark else R.INK["850"], base)
    # 唯一的效果：一次径向光（不叠加点阵/颗粒）。
    # 浅色主题上不做光晕——浅底 + 模糊光斑看起来像污渍，不是光（对比 55.5 但观感脏）。
    if not R.LIGHT:
        # 光晕只在深色主题上用；浅底上的模糊光斑看着像污渍不像光。
        glow = R._glow_mask(int(W * 0.46))  # noqa: SLF001
        gcol = tuple(min(255, int(c * 1.15)) for c in R.BRAND["500"])
        sprite = Image.new("RGBA", glow.size, gcol + (255,))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        # 光晕中心往里收：贴边时 coveraudit 会把边缘光当成"墨"，量出 24px 判为贴边。
        layer.paste(sprite, (int(W * 0.34 - glow.size[0] / 2),
                             int(H * 0.26 - glow.size[1] / 2)), glow)
        layer = layer.filter(ImageFilter.GaussianBlur(90))
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    M = 88
    fg, dim, accent = R.INK["0"], R.INK["300"], R.BRAND["500"]

    # 标题：巨大、紧行距 1.02、贴近上三分之一
    lines = m["headline"]
    fs = min(_fit(ln, W - 2 * M - 8, hi=176, lo=96) for ln in lines)
    y = int(H * 0.31)
    for ln in lines:
        hit = next((w for w in m["highlight"] if w and w in ln), None)
        if hit:
            k = ln.index(hit)
            x = M
            if ln[:k]:
                _text(d, (x, y), ln[:k], fs, fg)
                x += R.font(fs, bold=True).getlength(ln[:k])
            _text(d, (x, y), hit, fs, accent)
            rest = ln[k + len(hit):]
            if rest:
                _text(d, (x + R.font(fs, bold=True).getlength(hit), y), rest, fs, fg)
        else:
            _text(d, (M, y), ln, fs, fg)
        y += int(fs * 1.02)
        manifest["drawn_headline"] = manifest.get("drawn_headline", 0) + 1

    # 强调色必须至少出现一次：若 highlight 不在标题行里（如 06 号的"36"被折到下一行），
    # 就在基线右侧补一个小号数字，避免整张封面没有一个彩色点。
    if not any(any(h in ln for h in m["highlight"]) for ln in lines):
        num = next((h for h in m["highlight"] if any(c.isdigit() for c in h)), None)
        if num:
            _text(d, (W - M, y + 40), num, 108, accent, anchor="ra")

    # 唯一装饰：标题下方一条渐隐基线
    _hairline(d, M, y + 26, W - M, y + 26, accent, 200, 3)

    # 副标：小、宽字距、低位置
    if m["sub"]:
        sfs = _fit(m["sub"], W - 2 * M, hi=46, lo=32, bold=False)
        _text(d, (M, int(H * 0.70)), m["sub"], sfs, dim, bold=False)
    _tracked(d, (M, int(H * 0.70) + 74), m["kicker"] + ("  ·  " + str(m["seconds"]) + " 秒" if m["seconds"] else ""),
             26, accent, tracking=0.16)
    if m["tags"]:
        _tracked(d, (M, H - M - 30), "  ".join(m["tags"]), 26, dim, tracking=0.06)
    if m["serial"]:
        # 用 rd（右下）锚点：ra 是"右上"，96px 的字会往下垂出安全边（实测只剩 24px）
        _text(d, (W - M, H - M), m["serial"], 92, fg, anchor="rd", alpha=54)
    manifest["texts"] = [m["kicker"], *lines, m["sub"], *m["tags"]]
    return img


DIRECTIONS = {"editorial": editorial, "blueprint": blueprint, "cinematic": cinematic}


def render(content, direction="editorial", out=None):
    """渲一张封面。返回 (img, manifest)。"""
    if direction not in DIRECTIONS:
        raise SystemExit(f"未知流派 {direction}，可选：{', '.join(DIRECTIONS)}")
    # 封面要**原主题**：theme_for_video 会把浅色主题映射成深色（那是视频的规则），
    # 用在封面上就等于浅色主题永远渲不出来。
    R.set_theme(content.get("theme"))
    m = _meta(content)
    manifest = {"direction": direction, "configured_headline": len(m["headline"])}
    img = DIRECTIONS[direction](m, manifest)
    if out:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        img.save(out)
    return img, manifest


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description="封面 v2（editorial / blueprint / cinematic）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--direction", default="editorial", choices=tuple(DIRECTIONS))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    cfg = json.load(open(a.content, encoding="utf-8"))
    R.set_theme(cfg.get("theme"))
    out = a.out or f"/tmp/cover2_{a.direction}.png"
    _, man = render(cfg, a.direction, out)
    print(f"[ok] {out}  {man}")


if __name__ == "__main__":
    main()
