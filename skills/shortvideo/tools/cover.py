"""爆款封面生成：1080x1440（3:4，小红书首图）+ 1080x1920（9:16，视频内封面）。

竖屏爆款封面的共同点（社区方法论 + 平台视觉习惯）：
  1. 一个大到手机上能看清的主标题（3:4 图里每行 ≤ 6 字最佳），2~3 行封顶
  2. 关键词用对比色 + 底纹块强调，让人 0.3 秒抓到信息点
  3. 顶部一个"栏目名/身份"胶囊，建立账号识别（如「反常识数学」）
  4. 底部 2~3 个标签 + 一句行动号召，提高点击后的完播预期
  5. 深底 + 高饱和强调色，缩略图状态下比浅底更跳

用法：
  from cover import build_covers
  build_covers(content_cfg, out_dir)         # 生成 cover_3x4.png / cover_9x16.png
  python cover.py --content content.json --out out/   # 单独跑
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw

from shortvideo import runtime as R
from shortvideo import scenes as _scenes
from shortvideo.scenes import SceneAPI as _S


# ---------------------------------------------------------------- 小工具

def _paste(img, layer, ox, oy):
    """把 RGBA 小图层混合到画布上（RGB 画布用 paste(mask)，RGBA 画布用 alpha_composite）。

    为什么要绕这一圈：ImageDraw 在 RGBA 画布上写半透明颜色时不会混合 RGB，
    只把 alpha 写进画布，半透明胶囊/底板会变成实心色块。画在小图层再合成才是对的。
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


def _fit_size(draw, text, max_w, size, bold=True, min_size=24):
    """把字号从 size 往下压，直到文本宽度 <= max_w。"""
    s = size
    while s > min_size:
        f = R.font(s, bold=bold)
        if draw.textlength(text, font=f) <= max_w:
            return s, f
        s -= 3
    return min_size, R.font(min_size, bold=bold)


def _gradient_bg(w, h, top, bot):
    grad = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bot), Image.new("RGB", (w, h), top), grad)


def _decor(img, accent, seed=7, rows=26):
    """斜向网格 + 两团强调色光斑，让纯色底有质感。"""
    d = ImageDraw.Draw(img, "RGBA")
    w, h = img.size
    step = max(48, w // 16)
    for i in range(-h, w, step):
        d.line([(i, 0), (i + h // 2, h)], fill=(255, 255, 255, 10), width=1)
    R.glow(img, int(w * 0.86), int(h * 0.14), int(w * 0.42), accent, strength=0.5)
    R.glow(img, int(w * 0.08), int(h * 0.9), int(w * 0.38), R.TILE_ON, strength=0.35)
    return img


# ---------------------------------------------------------------- 主流程

def _cfg(content):
    """把 content.json 归一成封面要用的字段，缺的从 title/subtitle 兜底。"""
    c = dict(content.get("cover") or {})
    title = (content.get("title") or "").strip()
    sub = (content.get("subtitle") or "").strip()
    headline = c.get("headline") or ([title] if title else ["看不懂算我输"])
    if isinstance(headline, str):
        headline = [headline]
    headline = [ln.strip() for ln in headline if ln and ln.strip()]
    # 末行只有 1~2 个字会导致排版头重脚轻：并到上一行
    if len(headline) > 1 and len(headline[-1]) <= 2:
        headline = headline[:-2] + [headline[-2] + headline[-1]]
    highlight = c.get("highlight") or []
    if isinstance(highlight, str):
        highlight = [highlight]
    san = lambda x: R.sanitize(str(x), bold=True)  # noqa: E731
    headline = [san(x) for x in headline]
    return {
        "kicker": san(c.get("kicker") or content.get("badge") or "硬核科普"),
        "headline": headline[:3] or ["硬核科普"],
        "highlight": [san(x) for x in highlight],
        "sub": san(c.get("sub") or sub or ""),
        "tags": [san(x) for x in (c.get("tags") or [])[:3]],
        "cta": san(c.get("cta") or ""),
        "accent": tuple(c.get("accent") or R.ACCENT),
        "serial": c.get("serial") or "",
    }


def _draw_headline(img, box, lines, size, highlight, accent, line_gap=1.12):
    """画主标题：逐行自适应字号 + 关键词高亮 + 深色描边（缩略图更跳）。"""
    x0, y0, x1, y1 = box
    max_w = x1 - x0
    d = ImageDraw.Draw(img, "RGBA")
    sizes = [_fit_size(d, ln, max_w, size, bold=True)[0] for ln in lines]
    sizes = [min(sizes)] * len(lines)          # 统一起始字号，视觉更整
    f = R.font(sizes[0], bold=True)
    line_h = int(sizes[0] * line_gap)
    total_h = line_h * len(lines)
    y = y0 + max(0, ((y1 - y0) - total_h) // 2)
    bottom = y
    for ln, sz in zip(lines, sizes):
        f = R.font(sz, bold=True)
        w = d.textlength(ln, font=f)
        x = x0 + (max_w - w) / 2
        # 关键词高亮：先画底纹块，再画字
        for word in highlight:
            k = ln.find(word)
            if k < 0:
                continue
            pre = d.textlength(ln[:k], font=f)
            ww = d.textlength(word, font=f)
            d.rounded_rectangle((x + pre - sz * 0.12, y + line_h * 0.10,
                                 x + pre + ww + sz * 0.12, y + line_h * 0.96),
                                radius=int(sz * 0.18), fill=accent + (255,))
        d.text((x, y), ln, font=f, anchor="la", fill=R.TEXT + (255,),
               stroke_width=max(2, sz // 26), stroke_fill=(6, 10, 20, 255))
        y += line_h
        bottom = y
    return bottom


def _draw_kicker(img, cx, y, text, accent, size=36):
    # 注意：直接在 RGBA 画布上用 "RGBA" 画笔并不会混合 RGB（只把 alpha 写进画布），
    # 半透明胶囊会变成实心色块。所以这里必须画在小图层上再合成。
    f = R.font(size, bold=True)
    probe = ImageDraw.Draw(_scenes._layer((0, 0, 1, 1))[0])
    w = probe.textlength(text, font=f) + 56
    layer, ox, oy = _scenes._layer((cx - w / 2, y - 32, cx + w / 2, y + 32), pad=2)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((cx - w / 2 - ox, y - 32 - oy, cx + w / 2 - ox, y + 32 - oy),
                        radius=32, fill=accent + (34,), outline=accent + (200,), width=2)
    d.text((cx - ox, y - oy), text, font=f, fill=accent + (255,), anchor="mm")
    _paste(img, layer, ox, oy)


def _draw_tags(img, cx, y, tags, accent, size=None):
    if not tags:
        return
    f = R.font(size or int(img.size[0] * 0.030), bold=True)
    probe = ImageDraw.Draw(_scenes._layer((0, 0, 1, 1))[0])
    widths = [probe.textlength(t, font=f) + 44 for t in tags]
    gap = 18
    total = sum(widths) + gap * (len(tags) - 1)
    x = cx - total / 2
    for t, w in zip(tags, widths):
        layer, ox, oy = _scenes._layer((x, y - 28, x + w, y + 28), pad=2)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((x - ox, y - 28 - oy, x + w - ox, y + 28 - oy), radius=28,
                            fill=(255, 255, 255, 20), outline=(255, 255, 255, 70), width=1)
        d.text((x + w / 2 - ox, y - oy), t, font=f, fill=R.DIM + (255,), anchor="mm")
        _paste(img, layer, ox, oy)
        x += w + gap


def build_covers(content, out_dir, sizes=((1080, 1440, "cover_3x4.png"),
                                         (1080, 1920, "cover_9x16.png"))):
    """按 content.json 生成封面，返回生成的文件路径列表。"""
    os.makedirs(out_dir, exist_ok=True)
    c = _cfg(content)
    accent = c["accent"]
    paths = []
    for w, h, name in sizes:
        img = _gradient_bg(w, h, R.BG_TOP, R.BG_BOT)
        _decor(img, accent)
        d = ImageDraw.Draw(img, "RGBA")

        # 顶部栏目胶囊
        _draw_kicker(img, w / 2, int(h * 0.085), c["kicker"], accent, size=int(w * 0.033))
        # 序号贴纸（系列号，建立连续追更感）
        if c["serial"]:
            r = int(w * 0.058)
            d.ellipse((w - r * 2 - 44, 44, w - 44, 44 + r * 2), fill=accent + (255,))
            d.text((w - r - 44, 44 + r), str(c["serial"]), font=R.font(int(r * 1.0), bold=True),
                   fill=(8, 12, 22, 255), anchor="mm")

        # 主标题区（行数越多字号越小，避免和副标打架）
        n_lines = max(1, min(3, len(c["headline"])))
        base = int(w * (0.165 if n_lines <= 2 else 0.128))
        head_box = (int(w * 0.08), int(h * 0.24), int(w * 0.92), int(h * 0.62))
        head_bottom = _draw_headline(img, head_box, c["headline"], base,
                                     c["highlight"], accent)
        ly = min(int(h * 0.63), head_bottom + int(h * 0.035))

        # 强调短线
        d.rounded_rectangle((w / 2 - 60, ly, w / 2 + 60, ly + 8), radius=4, fill=accent + (255,))

        # 副标题（自动折行，最多 2 行）
        if c["sub"]:
            lines = _S.wrap(c["sub"], int(w * 0.040), int(w * 0.84), bold=False)[:2]
            y = ly + 52
            for ln in lines:
                d.text((w / 2, y), ln, font=R.font(int(w * 0.040), bold=False),
                       fill=R.TEXT + (225,), anchor="ma")
                y += int(w * 0.055)

        # 底部标签 + 行动号召
        _draw_tags(img, w / 2, int(h * 0.855), c["tags"], accent)
        if c["cta"]:
            d.text((w / 2, int(h * 0.925)), c["cta"],
                   font=R.font(int(w * 0.038), bold=True), fill=accent + (255,), anchor="mm")

        path = os.path.join(out_dir, name)
        img.convert("RGB").save(path, quality=95)
        paths.append(path)
    return paths


def main():
    ap = argparse.ArgumentParser(description="生成爆款封面（3:4 + 9:16）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--text", default=None, help="直接给主标题（覆盖 content.title）")
    args = ap.parse_args()
    content = json.load(open(args.content, encoding="utf-8"))
    if args.text:
        content.setdefault("cover", {})["headline"] = [args.text]
    R.W, R.H = content.get("size", [1080, 1920])
    for p in build_covers(content, args.out):
        print(f"[ok] {p}")


if __name__ == "__main__":
    main()
