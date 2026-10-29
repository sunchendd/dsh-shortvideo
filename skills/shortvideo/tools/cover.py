"""爆款封面生成（小红书视频封面只有 3:4 / 1:1 / 4:3，官方建议竖版 3:4）。

依据 docs/xhs-playbook.md §3：
  - 画布 1080x1440（3:4），MARGIN=72，主标题 84~132px（默认 120），描边 max(4, size/18)，
    行距 1.14；底 #0E1116，强调红 #FF2D55，数字高亮黄 #FFD400
  - 平台支持封面 A/B（最多 4 张，10 小时后按点击率选优）→ 一次产出 3 个差异化版式：
      F1 数字反差大字报（结论狠）/ F3 红圈标注截图式（有画面主体）/ F6 遮一半（制造信息缺口）
  - 再额外出 1 张 9:16，供视频首帧/主页头图复用

用法：
  python cover.py --content content.json --out out/ [--frame out/cover_frame.png]
  from cover import build_covers; build_covers(cfg, out_dir, frame_path=...)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFilter

from shortvideo import runtime as R
from shortvideo import scenes as _scenes

# 3:4 封面基线（playbook §3.1）
W34, H34 = 1080, 1440
MARGIN = 72
SIZE_MAX, SIZE_MIN = 132, 84
SUB_SIZE = 58
LINE_SPACING = 1.14
BG = (14, 17, 22)          # #0E1116
FG = (255, 255, 255)
ACCENT = (255, 45, 85)     # #FF2D55
ACCENT2 = (255, 212, 0)    # #FFD400
GREY = (154, 163, 174)     # #9AA3AE
_PROBE = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _paste(img, layer, ox, oy):
    """把 RGBA 小图层混合到画布（RGB 画布 paste(mask)；RGBA 画布 alpha_composite）。

    RGBA 画布上用 ImageDraw 写半透明颜色不会混合 RGB，只写 alpha —— 会把半透明胶囊
    变成实心色块，所以一律画在小图层再合成。
    """
    ox, oy = int(round(ox)), int(round(oy))
    lw, lh = layer.size
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(img.size[0], ox + lw), min(img.size[1], oy + lh)
    if x1 <= x0 or y1 <= y0:
        return
    if (x0 - ox, y0 - oy, x1 - ox, y1 - oy) != (0, 0, lw, lh):
        layer = layer.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy))
    if img.mode == "RGBA":
        img.alpha_composite(layer, (x0, y0))
    else:
        img.paste(layer, (x0, y0), layer)


def _fit(text, max_w, hi=120, lo=SIZE_MIN, bold=True):
    """自适应字号：从 hi 往下压到 lo，返回 (size, font)。"""
    s = hi
    while s > lo:
        f = R.font(s, bold=bold)
        if _PROBE.textlength(text, font=f) <= max_w:
            return s, f
        s -= 2
    return lo, R.font(lo, bold=bold)


def _cfg(content):
    c = dict(content.get("cover") or {})
    san = lambda x: R.sanitize(str(x), bold=True)  # noqa: E731
    headline = c.get("headline") or ([content.get("title")] if content.get("title") else [])
    if isinstance(headline, str):
        headline = [headline]
    headline = [san(x) for x in headline if x and str(x).strip()]
    if len(headline) > 1 and len(headline[-1]) <= 2:   # 末行 1~2 字头重脚轻 → 并到上一行
        headline = headline[:-2] + [headline[-2] + headline[-1]]
    highlight = c.get("highlight") or []
    if isinstance(highlight, str):
        highlight = [highlight]
    theme = content.get("theme") or {}
    accent = c.get("accent") or theme.get("accent") or ACCENT
    accent2 = c.get("accent2") or theme.get("accent2") or ACCENT2
    return {
        "kicker": san(c.get("kicker") or content.get("badge") or "硬核科普"),
        "headline": headline[:3] or ["硬核科普"],
        "highlight": [san(x) for x in highlight],
        "sub": san(c.get("sub") or content.get("subtitle") or ""),
        "tags": [san(x) for x in (c.get("tags") or [])[:3]],
        "cta": san(c.get("cta") or ""),
        "serial": c.get("serial") or "",
        "accent": tuple(accent),
        "accent2": tuple(accent2),
        "ring": c.get("ring"),          # [cx, cy, r] 红圈中心与半径（F3）
        "ring_label": san(c.get("ring_label") or ""),
        "f3_title": san(c.get("f3_title") or ""),
    }


# ---------------------------------------------------------------- 基础件

def _canvas(w, h, accent):
    """深底 + 顶部强调色柔光 + 斜向细纹（缩略图里也有质感）。"""
    img = Image.new("RGB", (w, h), BG)
    glow = R._glow_mask(int(min(w, h) * 0.62))  # noqa: SLF001
    sprite = Image.new("RGBA", glow.size, accent + (255,))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(sprite, (int(w * 0.78 - glow.size[0] / 2), int(h * 0.06 - glow.size[1] / 2)), glow)
    layer = layer.filter(ImageFilter.GaussianBlur(int(min(w, h) * 0.02)))
    _paste(img, layer, 0, 0)
    d = ImageDraw.Draw(img, "RGBA")
    step = max(56, w // 15)
    for i in range(-h, w, step):
        d.line([(i, 0), (i + h // 2, h)], fill=(255, 255, 255, 8), width=1)
    return img


def _kicker(img, cx, y, text, accent, size=36):
    f = R.font(size, bold=True)
    w = _PROBE.textlength(text, font=f) + 60
    layer, ox, oy = _scenes._layer((cx - w / 2, y - 32, cx + w / 2, y + 32), pad=2)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((cx - w / 2 - ox, y - 32 - oy, cx + w / 2 - ox, y + 32 - oy),
                        radius=32, fill=accent + (34,), outline=accent + (210,), width=2)
    d.text((cx - ox, y - oy), text, font=f, fill=accent + (255,), anchor="mm")
    _paste(img, layer, ox, oy)


def _tags(img, cx, y, tags, accent, size=34):
    if not tags:
        return
    f = R.font(size, bold=True)
    widths = [_PROBE.textlength(t, font=f) + 46 for t in tags]
    x = cx - (sum(widths) + 20 * (len(tags) - 1)) / 2
    for t, w in zip(tags, widths):
        layer, ox, oy = _scenes._layer((x, y - 30, x + w, y + 30), pad=2)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((x - ox, y - 30 - oy, x + w - ox, y + 30 - oy), radius=30,
                            fill=(255, 255, 255, 18), outline=(255, 255, 255, 64), width=1)
        d.text((x + w / 2 - ox, y - oy), t, font=f, fill=GREY + (255,), anchor="mm")
        _paste(img, layer, ox, oy)
        x += w + 20


def _serial(img, w, n, accent, r=None):
    r = r or int(w * 0.052)
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = w - MARGIN - r, MARGIN + r
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent + (255,))
    d.text((cx, cy), str(n), font=R.font(int(r * 1.0), bold=True),
           fill=(10, 12, 18, 255), anchor="mm")


def _text_outline(img, xy, s, size, fill, stroke=None, anchor="mm", stroke_w=None, font=None):
    """带描边的文字（RGB 画布会正确混合 alpha）。"""
    f = font or R.font(size, bold=True)
    sw = max(4, size // 18) if stroke_w is None else stroke_w
    ImageDraw.Draw(img, "RGBA").text(
        xy, s, font=f, fill=tuple(fill) + (255,), anchor=anchor,
        stroke_width=sw, stroke_fill=(tuple(stroke) if stroke else (0, 0, 0)) + (255,))


def highlight_box(cy, size, x0, x1, pad_x=10):
    """高亮块的纵向范围：必须包住整个 CJK 字形。

    旧写法用 line_h*0.14 ~ line_h*0.86，块只有 ~0.72 行高，
    而中文字形几乎占满 em 框——结果字的上下被切掉，
    深色字落到深色底上直接看不见（实测「三成」只剩中间一横）。
    """
    half = size * 0.60
    return (x0, cy - half, x1, cy + half)


def _highlight_block(img, box, color):
    """关键词高亮色块（F6 用：色块 + 深色字）。"""
    layer, ox, oy = _scenes._layer(box, pad=1)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy),
                        radius=int((box[3] - box[1]) * 0.22), fill=tuple(color) + (255,))
    _paste(img, layer, ox, oy)


# ---------------------------------------------------------------- 三个版式

def _variant_f1(c, w=W34, h=H34):
    """F1 数字反差大字报：主标题 1~3 行居中，副标一行强调色。"""
    accent, accent2 = c["accent"], c["accent2"]
    img = _canvas(w, h, accent)
    _kicker(img, w / 2, MARGIN + 34, c["kicker"], accent, size=36)
    if c["serial"]:
        _serial(img, w, c["serial"], accent)

    lines = c["headline"]
    max_w = w - MARGIN * 2
    hi = 120 if len(lines) <= 1 else (112 if len(lines) == 2 else 100)
    size = min(_fit(ln, max_w, hi=hi, lo=SIZE_MIN)[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * LINE_SPACING)
    block_h = line_h * len(lines)
    y = max(MARGIN + 120, int(h * 0.245) if len(lines) > 1 else int(h * 0.30))
    if len(lines) <= 2:                      # 行少时整体视觉居中，别留大片空底
        y = max(MARGIN + 120, int(h * 0.44 - block_h / 2))
    for ln in lines:
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        tw = f.getlength(ln)
        x0 = (w - tw) / 2
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            _highlight_block(img, highlight_box(
                y + line_h / 2, size,
                max(MARGIN - 4, x0 + pre - 10),
                min(w - MARGIN + 4, x0 + pre + hw + 10)), accent2)
            d = ImageDraw.Draw(img, "RGBA")
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x0, y + line_h / 2), before, font=f, fill=FG + (255,), anchor="lm")
            d.text((x0 + pre, y + line_h / 2), hit, font=f, fill=(17, 17, 17, 255), anchor="lm")
            if after:
                d.text((x0 + pre + hw, y + line_h / 2), after, font=f,
                       fill=FG + (255,), anchor="lm")
        else:
            _text_outline(img, (w / 2, y + line_h / 2), ln, size, FG, anchor="mm")
        y += line_h

    if c["sub"]:
        sub_size, fsub = _fit(c["sub"], w - MARGIN * 2, hi=SUB_SIZE, lo=36)
        _text_outline(img, (w / 2, y + int(h * 0.035)), c["sub"], sub_size, accent,
                      anchor="mm", stroke_w=0, font=fsub)
    _tags(img, w / 2, h - MARGIN - 54, c["tags"], accent)
    if c["cta"]:
        _text_outline(img, (w / 2, h - MARGIN + 6), c["cta"], 40, FG, anchor="mm", stroke_w=0)
    return img


def _variant_short(c, w=W34, h=H34):
    """短版封面：字数更少、字号更大、右上角带时长徽标。

    短版靠"一眼看懂"抢点击，3:4 里能放的字比长版更少，
    所以标题最多 2 行、副标一行、其余全部让位给字号。
    """
    accent, accent2 = c["accent"], c["accent2"]
    img = _canvas(w, h, accent)
    secs = c.get("seconds")
    _kicker(img, w / 2, MARGIN + 34, c["kicker"], accent, size=38)
    if c["serial"]:                 # 系列序号保留：短版也要能"认出是同一个号"
        _serial(img, w, c["serial"], accent)
    # 时长徽标放在栏目名下方左侧：短版的卖点就是"几十秒看完"，
    # 但不能顶掉右上角的系列序号（那是账号识别）
    if secs:
        d = ImageDraw.Draw(img, "RGBA")
        f = R.font(46, bold=True)
        label = f"{secs:g} 秒速通"
        tw = f.getlength(label)
        bx0, by0 = MARGIN, MARGIN + 84
        d.rounded_rectangle([bx0, by0, bx0 + tw + 52, by0 + 76], radius=38,
                            fill=accent + (40,), outline=accent + (190,), width=3)
        d.text((bx0 + 26, by0 + 38), label, font=f, fill=accent + (255,), anchor="lm")

    lines = c["headline"][:2]
    max_w = w - MARGIN * 2
    size = min(_fit(ln, max_w, hi=176, lo=SIZE_MIN)[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * 1.14)
    block_h = line_h * len(lines)
    y = int(h * 0.46 - block_h / 2)
    for ln in lines:
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        tw = f.getlength(ln)
        x0 = (w - tw) / 2
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            _highlight_block(img, highlight_box(
                y + line_h / 2, size,
                max(MARGIN - 6, x0 + pre - 14),
                min(w - MARGIN + 6, x0 + pre + hw + 14)), accent2)
            d = ImageDraw.Draw(img, "RGBA")
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x0, y + line_h / 2), before, font=f, fill=FG + (255,), anchor="lm")
            d.text((x0 + pre, y + line_h / 2), hit, font=f, fill=(17, 17, 17, 255), anchor="lm")
            if after:
                d.text((x0 + pre + hw, y + line_h / 2), after, font=f,
                       fill=FG + (255,), anchor="lm")
        else:
            _text_outline(img, (w / 2, y + line_h / 2), ln, size, FG, anchor="mm")
        y += line_h

    if c["sub"]:
        sub_size, fsub = _fit(c["sub"], w - MARGIN * 2, hi=56, lo=38)
        _text_outline(img, (w / 2, y + int(h * 0.045)), c["sub"], sub_size, accent,
                      anchor="mm", stroke_w=0, font=fsub)
    _tags(img, w / 2, h - MARGIN - 54, c["tags"], accent)
    if c["cta"]:
        _text_outline(img, (w / 2, h - MARGIN + 6), c["cta"], 44, FG, anchor="mm", stroke_w=0)
    return img


def _variant_f3(c, frame_path=None, w=W34, h=H34):
    """F3 红圈标注（截图式）：hook 镜头画面当底 + 红圈 + 标签。"""
    accent = c["accent"]
    if frame_path and os.path.exists(frame_path):
        src = Image.open(frame_path).convert("RGB")
        sw, sh = src.size
        need = w / h
        if sw / sh > need:                       # 太宽：左右裁
            nw = int(sh * need)
            src = src.crop(((sw - nw) // 2, 0, (sw + nw) // 2, sh))
        elif sh / sw > need:                     # 太高：按上部 25% 对齐裁
            nh = int(sw / need)
            top = max(0, int((sh - nh) * 0.25))
            src = src.crop((0, top, sw, top + nh))
        img = src.resize((w, h), Image.LANCZOS)
        # 顶部压暗，保证标题可读
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        band = int(h * 0.18)
        for i in range(band):
            sd.line([(0, i), (w, i)], fill=(8, 10, 16, int(150 * (1 - i / band) ** 1.4)))
        _paste(img, shade, 0, 0)
    else:
        img = _canvas(w, h, accent)

    d = ImageDraw.Draw(img, "RGBA")
    # 截图式封面：画面自己会说话。只有显式给了 f3_title 才叠标题，
    # 否则双标题会互相打架（视频里已经有自己的大字）。
    title = c.get("f3_title") or ""
    if title:
        size, f = _fit(title, w - MARGIN * 2, hi=104, lo=SIZE_MIN)
        d.text((MARGIN, MARGIN + 6), title, font=f, fill=FG + (255,), anchor="la",
               stroke_width=max(4, size // 18), stroke_fill=(0, 0, 0, 255))
    _kicker(img, MARGIN + 150, MARGIN + 40, c["kicker"], accent, size=34)

    # 红圈：默认落在画面中部偏右的"差异点"（可用 cover.ring 覆盖）
    cx, cy, r = c["ring"] or (int(w * 0.62), int(h * 0.52), 170)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=accent + (255,), width=10)
    if c["ring_label"]:
        lab = c["ring_label"][:6]
        f2 = R.font(64, bold=True)
        tw = d.textlength(lab, font=f2)
        bx, by = cx + r * 0.5, cy + r * 0.15
        d.rounded_rectangle((bx, by, bx + tw + 44, by + 100), radius=16, fill=accent + (255,))
        d.text((bx + 22, by + 50), lab, font=f2, fill=FG + (255,), anchor="lm")

    if c["sub"]:
        _text_outline(img, (w / 2, h - MARGIN - 104), c["sub"], 48, FG, anchor="mm", stroke_w=0)
    _tags(img, w / 2, h - MARGIN - 24, c["tags"], accent)
    return img


def _variant_f6(c, w=W34, h=H34):
    """F6 知识性好奇（遮一半）：关键字高亮黄块 + 其余 70% 白，制造"信息在封面外"。"""
    accent, accent2 = c["accent"], c["accent2"]
    img = _canvas(w, h, accent)
    _kicker(img, w / 2, MARGIN + 34, c["kicker"], accent, size=36)
    if c["serial"]:
        _serial(img, w, c["serial"], accent)

    lines = c["headline"]
    max_w = w - MARGIN * 2
    hi = 116 if len(lines) <= 1 else 108
    size = min(_fit(ln, max_w, hi=hi, lo=SIZE_MIN)[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * LINE_SPACING)
    block_h = line_h * len(lines)
    y = max(MARGIN + 120, int(h * 0.44 - block_h / 2))
    d = ImageDraw.Draw(img, "RGBA")
    for ln in lines:
        tw = f.getlength(ln)
        x = (w - tw) / 2
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            _highlight_block(img, highlight_box(
                y + line_h / 2, size,
                max(MARGIN - 4, x + pre - 10),
                min(w - MARGIN + 4, x + pre + hw + 10)), accent2)
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x, y + line_h / 2), before, font=f, fill=FG + (178,), anchor="lm")
            d.text((x + pre, y + line_h / 2), hit, font=f, fill=(17, 17, 17, 255), anchor="lm")
            if after:
                d.text((x + pre + hw, y + line_h / 2), after, font=f, fill=FG + (178,), anchor="lm")
        else:
            d.text((x, y + line_h / 2), ln, font=f, fill=FG + (178,), anchor="lm")
        y += line_h

    if c["sub"]:
        _text_outline(img, (w / 2, y + int(h * 0.045)), c["sub"], 50, accent,
                      anchor="mm", stroke_w=0)
    band_y = h - MARGIN - 160
    d.rounded_rectangle((MARGIN, band_y, w - MARGIN, band_y + 8), radius=4, fill=accent + (255,))
    _text_outline(img, (w / 2, band_y + 74), c["cta"] or "点开看完整推导", 42, FG,
                  anchor="mm", stroke_w=0)
    _tags(img, w / 2, h - MARGIN + 6, c["tags"], accent)
    return img


_BUILDERS = {"short": lambda c, fp: _variant_short(c),
             "f1": lambda c, fp: _variant_f1(c),
             "f3": lambda c, fp: _variant_f3(c, fp),
             "f6": lambda c, fp: _variant_f6(c)}


def build_covers(content, out_dir, frame_path=None, variants=None):
    """生成封面合集，返回文件路径列表（3:4 三个版式 + 一张 9:16）。

    短版（content 里 "format": "short"）默认出 short + f1 + 9x16：
    短版封面字更大、带时长徽标，与长版形成两种视觉层级。
    """
    os.makedirs(out_dir, exist_ok=True)
    c = _cfg(content)
    if variants is None:
        short = str((content or {}).get("format") or "").lower() in ("short", "15s", "quick")
        variants = ("short", "f1", "9x16") if short else ("f1", "f3", "f6", "9x16")
        if short:
            secs = float((content or {}).get("seconds") or 0)
            if not secs:
                secs = round(sum(float(sh.get("min_dur") or 0) for sh in (content or {}).get("shots") or []))
            c["seconds"] = secs
    made = []
    for v in variants:
        if v == "9x16":
            img, name = _variant_f1(c, 1080, 1920), "cover_9x16.png"
        elif v in _BUILDERS:
            img, name = _BUILDERS[v](c, frame_path), f"cover_{v}.png"
        else:
            continue
        path = os.path.join(out_dir, name)
        img.save(path, quality=95)
        made.append(path)
    return made


def main():
    ap = argparse.ArgumentParser(description="生成小红书爆款封面（3:4 三版式 + 9:16）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--frame", default=None, help="F3 用的画面底图（如 cover_frame.png）")
    ap.add_argument("--text", default=None, help="直接给主标题（覆盖 cover.headline）")
    args = ap.parse_args()
    content = json.load(open(args.content, encoding="utf-8"))
    if args.text:
        content.setdefault("cover", {})["headline"] = [args.text]
    for p in build_covers(content, args.out, frame_path=args.frame):
        print(f"[ok] {p}")


if __name__ == "__main__":
    main()
