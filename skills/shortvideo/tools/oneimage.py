"""一张图总结：把知识点/视频稿压成 1080x1440 竖版信息图（小红书图文笔记尺寸）。

版式（对应 skills/one-image-summary/SKILL.md）：
  栏目胶囊 → 大标题（关键词黄底高亮）→ 4~6 个序号要点 → 记忆锚点面板 → 标签 + 署名
底图用 art.py 的程序化氛围图（mesh/aurora/rays/grid/paper），文字对比度由深色面板保证。

用法：
  python oneimage.py --spec spec.json --out out/summary.png
  python oneimage.py --spec spec.json --style aurora --accent FF2D55
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw

import art
from cover import (ACCENT, ACCENT2, FG, GREY, MARGIN, _fit, _highlight_block, _kicker, _paste,
                   _tags, _text_outline)
from shortvideo import runtime as R
from shortvideo import scenes as _scenes

W, H = 1080, 1440


def _spec(raw):
    san = lambda x: R.sanitize(str(x), bold=True)  # noqa: E731
    pts = [{"t": san(p.get("t", "")), "d": san(p.get("d", ""))}
           for p in (raw.get("points") or [])]
    big = raw.get("big")
    accent = raw.get("accent") or ACCENT
    accent2 = raw.get("accent2") or ACCENT2
    return {
        "kicker": san(raw.get("kicker") or "硬核科普"),
        "title": [san(t) for t in (raw.get("title") or ["一句话总结"])][:3],
        "highlight": [san(x) for x in (raw.get("highlight") or [])],
        "points": pts[:6],
        "big": {"value": san(big.get("value", "")), "label": san(big.get("label", ""))}
        if isinstance(big, dict) else None,
        "tags": [san(x) for x in (raw.get("tags") or [])[:4]],
        "footer": san(raw.get("footer") or ""),
        "style": raw.get("style") or "mesh",
        "palette": raw.get("palette") or "default",
        "seed": int(raw.get("seed") or 0),
        "accent": tuple(accent),
        "accent2": tuple(accent2),
    }


def _badge(img, cx, cy, n, r, color):
    """圆形序号（RGB 画布：画在图层上再混合）。"""
    layer, ox, oy = _scenes._layer((cx - r, cy - r, cx + r, cy + r), pad=2)
    d = ImageDraw.Draw(layer)
    d.ellipse((cx - r - ox, cy - r - oy, cx + r - ox, cy + r - oy), fill=tuple(color) + (255,))
    d.text((cx - ox, cy - oy), str(n), font=R.font(int(r * 1.2), bold=True),
           fill=(12, 16, 28, 255), anchor="mm")
    _paste(img, layer, ox, oy)


def _panel(img, box, fill=(10, 14, 26), alpha=0.72, radius=32, outline=None):
    x0, y0, x1, y1 = box
    layer, ox, oy = _scenes._layer(box, pad=2)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0 - ox, y0 - oy, x1 - ox, y1 - oy), radius=radius,
                        fill=tuple(fill) + (int(255 * alpha),),
                        outline=(tuple(outline) + (220,)) if outline else None, width=3)
    _paste(img, layer, ox, oy)


def _draw_title(img, c, w, y0, y1):
    """标题：逐行自适应字号 + 关键词黄底深字 + 深色描边。返回底部 y。"""
    accent, accent2 = c["accent"], c["accent2"]
    lines = c["title"]
    max_w = w - MARGIN * 2
    hi = 116 if len(lines) <= 1 else (104 if len(lines) == 2 else 92)
    size = min(_fit(ln, max_w, hi=hi, lo=84)[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * 1.14)
    y = max(y0, int((y0 + y1) / 2 - line_h * len(lines) / 2))
    for ln in lines:
        tw = f.getlength(ln)
        x = (w - tw) / 2
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            hx0 = max(MARGIN - 4, x + pre - 10)
            hx1 = min(w - MARGIN + 4, x + pre + hw + 10)
            _highlight_block(img, (hx0, y + line_h * 0.14, hx1, y + line_h * 0.86), accent2)
            d = ImageDraw.Draw(img, "RGBA")
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x, y + line_h / 2), before, font=f, fill=FG + (255,), anchor="lm")
            d.text((x + pre, y + line_h / 2), hit, font=f, fill=(17, 17, 17, 255), anchor="lm")
            if after:
                d.text((x + pre + hw, y + line_h / 2), after, font=f, fill=FG + (255,), anchor="lm")
        else:
            _text_outline(img, (w / 2, y + line_h / 2), ln, size, FG, anchor="mm")
        y += line_h
    return y


def build(raw, out_path, w=W, h=H):
    c = _spec(raw)
    accent, accent2 = c["accent"], c["accent2"]
    img = art.make(c["style"], w, h, c["seed"], c["palette"]).convert("RGB")
    # 顶部压一层深色，保证标题对比度（底图亮的地方也不糊字）
    shade = Image.new("RGBA", (w, int(h * 0.42)), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    band = int(h * 0.42)
    for i in range(band):
        sd.line([(0, i), (w, i)], fill=(6, 8, 14, int(150 * (1 - i / band) ** 1.2)))
    _paste(img, shade, 0, 0)

    _kicker(img, w / 2, MARGIN + 30, c["kicker"], accent, size=int(w * 0.031))
    y = _draw_title(img, c, w, MARGIN + 110, int(h * 0.30))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((w / 2 - 54, y + 16, w / 2 + 54, y + 23), radius=4, fill=accent + (255,))
    y += int(h * 0.055)

    # 要点
    pts = c["points"]
    if pts:
        box_x0, box_x1 = MARGIN + 8, w - MARGIN - 8
        avail = int(h * 0.62) - y
        row_h = int(min(h * 0.10, avail / max(1, len(pts))))
        num_r = max(20, int(row_h * 0.25))
        for i, p in enumerate(pts):
            cy = y + row_h * i + row_h * 0.5
            _badge(img, box_x0 + num_r + 4, cy, i + 1, num_r,
                   accent if i % 2 == 0 else (255, 82, 116))
            tx = box_x0 + num_r * 2 + 26
            fs = int(row_h * 0.30)
            d.text((tx, cy - row_h * 0.10), p["t"], font=R.font(fs, bold=True),
                   fill=FG + (255,), anchor="lm")
            if p["d"]:
                d.text((tx, cy + row_h * 0.24), p["d"], font=R.font(int(fs * 0.78), bold=False),
                       fill=GREY + (245,), anchor="lm")
        y += row_h * len(pts) + int(h * 0.012)

    # 记忆锚点
    if c["big"] and y < int(h * 0.82):
        bh = int(h * 0.11)
        _panel(img, (MARGIN + 8, y, w - MARGIN - 8, y + bh), fill=(8, 12, 22),
               alpha=0.72, radius=26, outline=accent)
        vsize = int(bh * 0.40)
        while vsize > 22 and R.font(vsize, bold=True).getlength(c["big"]["value"]) > w - MARGIN * 2 - 80:
            vsize -= 2
        d.text((w / 2, y + bh * (0.38 if c["big"]["label"] else 0.5)), c["big"]["value"],
               font=R.font(vsize, bold=True), fill=accent + (255,), anchor="mm")
        if c["big"]["label"]:
            d.text((w / 2, y + bh * 0.76), c["big"]["label"], font=R.font(int(bh * 0.22)),
                   fill=FG + (240,), anchor="mm")

    _tags(img, w / 2, int(h * 0.905), c["tags"], accent)
    if c["footer"]:
        d.text((w / 2, int(h * 0.955)), c["footer"], font=R.font(int(w * 0.028), bold=False),
               fill=GREY + (230,), anchor="mm")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.convert("RGB").save(out_path, quality=95)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="一张图总结（1080x1440 信息图）")
    ap.add_argument("--spec", required=True, help="JSON 描述文件")
    ap.add_argument("--out", default="out/summary.png")
    ap.add_argument("--style", default=None, choices=art.STYLES)
    ap.add_argument("--accent", default=None, help="强调色，如 FF2D55")
    ap.add_argument("--variants", type=int, default=0,
                    help="出 N 版（不同底图风格+强调色），便于做图文封面 A/B")
    args = ap.parse_args()
    raw = json.load(open(args.spec, encoding="utf-8"))
    if args.style:
        raw["style"] = args.style
    if args.accent:
        a = args.accent.lstrip("#")
        raw["accent"] = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    if args.variants and args.variants > 0:
        # 三版差异只改"底图风格 + 强调色"，文案完全一致——和视频变体同一个原则：控制变量
        combos = [("aurora", [255, 45, 85]), ("mesh", [255, 212, 0]),
                  ("rays", [58, 216, 240]), ("grid", [56, 224, 168])]
        base, ext = os.path.splitext(args.out)
        for i in range(args.variants):
            style, accent = combos[i % len(combos)]
            cfg = dict(raw)
            cfg["style"] = args.style or style
            cfg["accent"] = accent
            path = f"{base}-v{i + 1}{ext}"
            print(f"[ok] {build(cfg, path)}  {W}x{H}   {cfg['style']}/"
                  f"#{accent[0]:02X}{accent[1]:02X}{accent[2]:02X}")
        return

    print(f"[ok] {build(raw, args.out)}  {W}x{H}")


if __name__ == "__main__":
    main()
