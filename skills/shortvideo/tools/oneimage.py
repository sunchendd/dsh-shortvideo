"""一张图总结：把一条知识点/一条视频压成 1080x1440 竖版信息图（小红书图文笔记尺寸）。

设计原则（对应 docs/xhs-playbook.md）：
  - 一屏只讲一件事：标题 + 3~6 个要点 + 一个"记忆锚点"（大字结论/公式）
  - 要点用圆形序号 + 短句（≤14 字），不要长段落
  - 底图用 art.py 的程序化氛围图，避免纯色廉价感

用法：
  python oneimage.py --spec spec.json --out out/summary.png
  python oneimage.py --spec spec.json --style aurora --accent ffb547
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw

import art
from cover import _draw_headline, _draw_kicker, _draw_tags, _paste
from shortvideo import runtime as R
from shortvideo import scenes as _scenes

W, H = 1080, 1440


def _spec(raw):
    san = lambda x: R.sanitize(str(x), bold=True)  # noqa: E731
    pts = [{"t": san(p.get("t", "")), "d": san(p.get("d", ""))}
           for p in (raw.get("points") or [])]
    big = raw.get("big")
    return {
        "kicker": san(raw.get("kicker") or "硬核科普"),
        "title": [san(t) for t in (raw.get("title") or ["一句话总结"])],
        "highlight": [san(x) for x in (raw.get("highlight") or [])],
        "points": pts,
        "big": {"value": san(big.get("value", "")), "label": san(big.get("label", ""))}
        if isinstance(big, dict) else None,
        "tags": [san(x) for x in (raw.get("tags") or [])[:4]],
        "footer": san(raw.get("footer") or ""),
        "style": raw.get("style") or "mesh",
        "palette": raw.get("palette") or "default",
        "accent": tuple(raw.get("accent") or R.ACCENT),
        "seed": int(raw.get("seed") or 0),
    }


def _badge(img, cx, cy, n, r, color):
    """圆形序号（RGB 画布版本：画在图层上再混合）。"""
    layer, ox, oy = _scenes._layer((cx - r, cy - r, cx + r, cy + r), pad=2)
    d = ImageDraw.Draw(layer)
    d.ellipse((cx - r - ox, cy - r - oy, cx + r - ox, cy + r - oy), fill=tuple(color) + (255,))
    d.text((cx - ox, cy - oy), str(n), font=R.font(int(r * 1.24), bold=True),
           fill=(12, 16, 28, 255), anchor="mm")
    _paste(img, layer, ox, oy)


def _panel(img, box, fill=(10, 14, 26), alpha=0.72, radius=32, outline=None):
    """圆角半透明面板（RGB 画布版本）。"""
    x0, y0, x1, y1 = box
    layer, ox, oy = _scenes._layer(box, pad=2)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x0 - ox, y0 - oy, x1 - ox, y1 - oy), radius=radius,
                        fill=tuple(fill) + (int(255 * alpha),),
                        outline=(tuple(outline) + (220,)) if outline else None, width=3)
    _paste(img, layer, ox, oy)


def build(raw, out_path, w=W, h=H):
    c = _spec(raw)
    accent = c["accent"]
    img = art.make(c["style"], w, h, c["seed"], c["palette"])  # RGB 画布（混合正确）
    d = ImageDraw.Draw(img, "RGBA")

    # 顶部：栏目胶囊
    _draw_kicker(img, w / 2, int(h * 0.072), c["kicker"], accent, size=int(w * 0.031))

    # 标题（复用封面的大字排版：自适应字号 + 关键词高亮 + 描边）
    title = [str(t) for t in c["title"]][:3]
    base = int(w * (0.125 if len(title) <= 2 else 0.100))
    bottom = _draw_headline(img, (int(w * 0.07), int(h * 0.11), int(w * 0.93), int(h * 0.30)),
                            title, base, c["highlight"], accent)
    y = max(int(h * 0.31), bottom + int(h * 0.012))
    d.rounded_rectangle((w / 2 - 54, y, w / 2 + 54, y + 7), radius=4, fill=accent + (255,))
    y += int(h * 0.035)

    # 要点列表
    pts = [p for p in c["points"]][:6]
    if pts:
        box_x0, box_x1 = int(w * 0.08), int(w * 0.92)
        row_h = int(min(h * 0.095, (h * 0.62 - y) / max(1, len(pts))))
        num_r = int(row_h * 0.26)
        for i, p in enumerate(pts):
            cy = y + row_h * i + row_h * 0.5
            _badge(img, box_x0 + num_r + 6, cy, i + 1, num_r,
                   accent if i % 2 == 0 else R.TILE_ON)
            tx = box_x0 + num_r * 2 + 26
            fs = int(row_h * 0.30)
            d.text((tx, cy - row_h * 0.10), str(p.get("t", "")),
                   font=R.font(fs, bold=True), fill=R.TEXT + (255,), anchor="lm")
            if p.get("d"):
                d.text((tx, cy + row_h * 0.22), str(p["d"]),
                       font=R.font(int(fs * 0.78), bold=False), fill=R.DIM + (240,), anchor="lm")
        y += row_h * len(pts) + int(h * 0.012)

    # 记忆锚点（大字结论）
    if c["big"]:
        bh = int(h * 0.115)
        _panel(img, (int(w * 0.07), y, int(w * 0.93), y + bh),
               fill=(8, 12, 24), alpha=0.62, radius=28, outline=accent)
        val = str(c["big"].get("value", ""))
        lab = str(c["big"].get("label", ""))
        d.text((w / 2, y + bh * (0.40 if lab else 0.5)), val,
               font=R.font(int(bh * 0.46), bold=True), fill=accent + (255,), anchor="mm")
        if lab:
            d.text((w / 2, y + bh * 0.77), lab, font=R.font(int(bh * 0.24), bold=False),
                   fill=R.TEXT + (235,), anchor="mm")
        y += bh + int(h * 0.02)

    # 底部：标签 + 署名
    _draw_tags(img, w / 2, int(h * 0.905), c["tags"], accent)
    if c["footer"]:
        d.text((w / 2, int(h * 0.955)), c["footer"], font=R.font(int(w * 0.030), bold=False),
               fill=R.DIM + (220,), anchor="mm")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    img.convert("RGB").save(out_path, quality=95)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="一张图总结（1080x1440 信息图）")
    ap.add_argument("--spec", required=True, help="JSON 描述文件")
    ap.add_argument("--out", default="out/summary.png")
    ap.add_argument("--style", default=None, choices=art.STYLES)
    ap.add_argument("--accent", default=None, help="强调色，如 ffb547")
    args = ap.parse_args()
    raw = json.load(open(args.spec, encoding="utf-8"))
    if args.style:
        raw["style"] = args.style
    if args.accent:
        a = args.accent.lstrip("#")
        raw["accent"] = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    print(f"[ok] {build(raw, args.out)}  {W}x{H}")


if __name__ == "__main__":
    main()
