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
from cover import (ACCENT, ACCENT2, DARK, FG, GREY, MARGIN, _fit, _highlight_block,
                   _kicker, _paste, _tags, _text_outline, highlight_box)
from shortvideo import runtime as R
from shortvideo import scenes as _scenes

W, H = 1080, 1440
TS, LEAD, SPACE = R.TS, R.LEADING, R.SPACE
# 一张图是"点开看全屏"的图文笔记（1080 原尺寸 ≈ 390pt），
# 可读性下限比封面低一档：正文 ≥ TS["micro"](32)，说明行 ≥ 30；封面才要 ≥48（240px 缩略图口径）。
PT_MIN, PT_MAX = 36, TS["label"]


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
           fill=DARK + (255,), anchor="mm")
    _paste(img, layer, ox, oy)


def _panel(img, box, fill=None, alpha=0.72, radius=32, outline=None):
    fill = R.INK["900"] if fill is None else fill
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
    hi = TS["h1"] if len(lines) <= 1 else (TS["h2"] if len(lines) == 2 else TS["h3"])
    size = min(_fit(ln, max_w, hi=hi, lo=TS["h3"])[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * LEAD["title"])
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
            _highlight_block(img, highlight_box(y + line_h / 2, size, hx0, hx1), accent2)
            d = ImageDraw.Draw(img, "RGBA")
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x, y + line_h / 2), before, font=f, fill=FG + (255,), anchor="lm")
            d.text((x + pre, y + line_h / 2), hit, font=f, fill=DARK + (255,), anchor="lm")
            if after:
                d.text((x + pre + hw, y + line_h / 2), after, font=f, fill=FG + (255,), anchor="lm")
        else:
            _text_outline(img, (w / 2, y + line_h / 2), ln, size, FG, anchor="mm")
        y += line_h
    return y


def _wrap(text, f, max_w):
    """按实际字宽折行（CJK 逐字断，ASCII 词内不断）。返回行列表。"""
    out, cur = [], ""
    for ch in str(text or ""):
        if f.getlength(cur + ch) <= max_w:
            cur += ch
            continue
        if not cur:
            cur = ch
            continue
        out.append(cur)
        cur = ch
    if cur:
        out.append(cur)
    return out


def _draw_points(img, pts, x0, x1, y_top, y_bot, accent):
    """要点列表：按"能装下"选字号，装不下就折行，最后才让字号掉一档。

    旧实现按 row_h*0.30 定字号且不折行：6 条要点时 row_h 只有 65px，
    标题 19px、说明 **14px**——1080 画布上 14px 等于手机上的 5pt，
    等于没有（而一行 69 字的说明还会直接冲出右边距）。
    现在先量后画：从 label(48) 往下试，谁的"标题 + 折行后的说明"总高装得下就用谁。
    """
    d = ImageDraw.Draw(img, "RGBA")
    n = max(1, len(pts))
    area = max(120, y_bot - y_top)
    pick = None
    for fs in (48, 44, 40, 36, 32, 28):
        ds = max(24, int(fs * 0.78))
        num_r = max(18, int(fs * 0.60))
        tx = x0 + num_r * 2 + int(fs * 0.55)
        maxw = x1 - tx
        ft, fd = R.font(fs, bold=True), R.font(ds)
        rows, total = [], 0
        for p in pts:
            dl = _wrap(p["d"], fd, maxw) if p["d"] else []
            th = int(fs * 1.30)
            dh = int(ds * 1.34) * len(dl)
            h = th + dh + SPACE["sm"]
            rows.append((p, dl, th, dh, h))
            total += h
        widest = max((ft.getlength(p["t"]) for p in pts), default=0)
        if total <= area and widest <= maxw:
            pick = (fs, ds, num_r, tx, rows, total)
            break
    if pick is None:                                  # 极端长文案：用最小档兜底
        fs, ds, num_r, tx = 28, 24, 18, x0 + 36 + 15
        ft, fd = R.font(fs, bold=True), R.font(ds)
        maxw = x1 - tx
        rows, total = [], 0
        for p in pts:
            dl = _wrap(p["d"], fd, maxw) if p["d"] else []
            h = int(fs * 1.30) + int(ds * 1.34) * len(dl) + SPACE["sm"]
            rows.append((p, dl, int(fs * 1.30), int(ds * 1.34) * len(dl), h))
            total += h
        pick = (fs, ds, num_r, tx, rows, total)
    fs, ds, num_r, tx, rows, total = pick
    # 装得下就整体居中在可用区里（不留顶部大洞），装不下就紧贴顶部
    y = y_top + max(0, (area - total) // 2)
    ft, fd = R.font(fs, bold=True), R.font(ds)
    for i, (p, dl, th, dh, h) in enumerate(rows):
        cy = y + th / 2
        _badge(img, x0 + num_r + 4, cy, i + 1, num_r, accent)   # 静态图无私"状态"，统一品牌青
        d.text((tx, cy), p["t"], font=ft, fill=FG + (255,), anchor="lm")
        for j, ln in enumerate(dl):
            d.text((tx, cy + th / 2 + int(ds * 1.34) * (j + 0.5)), ln, font=fd,
                   fill=GREY + (245,), anchor="lm")
        y += h
    return y, total


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

    _kicker(img, w / 2, MARGIN + 30, c["kicker"], accent, size=TS["caption"])
    y = _draw_title(img, c, w, MARGIN + 110, int(h * 0.30))
    d = ImageDraw.Draw(img, "RGBA")
    d.rounded_rectangle((w / 2 - 54, y + 16, w / 2 + 54, y + 23), radius=4, fill=accent + (255,))
    y += int(h * 0.055)

    # 要点
    pts = c["points"]
    if pts:
        box_x0 = MARGIN + 8
        y, block_h = _draw_points(img, pts, box_x0, w - MARGIN - 8, y, int(h * 0.665), accent)
        y += SPACE["sm"]

    # 记忆锚点
    if c["big"] and y < int(h * 0.82):
        bh = int(h * 0.11)
        _panel(img, (MARGIN + 8, y, w - MARGIN - 8, y + bh), fill=R.INK["950"],
               alpha=0.72, radius=26, outline=accent)
        vsize = int(bh * 0.40)
        while vsize > 22 and R.font(vsize, bold=True).getlength(c["big"]["value"]) > w - MARGIN * 2 - 80:
            vsize -= 2
        d.text((w / 2, y + bh * (0.38 if c["big"]["label"] else 0.5)), c["big"]["value"],
               font=R.font(vsize, bold=True), fill=accent + (255,), anchor="mm")
        if c["big"]["label"]:
            d.text((w / 2, y + bh * 0.76), c["big"]["label"], font=R.font(int(bh * 0.22)),
                   fill=FG + (240,), anchor="mm")

    _tags(img, w / 2, int(h * 0.905), c["tags"], accent, size=TS["caption"])
    if c["footer"]:
        d.text((w / 2, int(h * 0.955)), c["footer"],
               font=R.font(34, bold=False), fill=GREY + (230,), anchor="mm")

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
    ap.add_argument("--theme", default=None,
                    help="配色主题（默认读 spec 的 theme 字段，再退回 midnight）")
    args = ap.parse_args()
    raw = json.load(open(args.spec, encoding="utf-8"))
    # 先定主题再画：底图配色、强调色、文字层级都由 token 推导
    R.set_theme(args.theme or raw.get("theme"))
    art.refresh_theme()
    if args.style:
        raw["style"] = args.style
    if args.accent:
        a = args.accent.lstrip("#")
        raw["accent"] = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    if args.variants and args.variants > 0:
        # 三版差异只改"底图风格 + 强调色"，文案完全一致——和视频变体同一个原则：控制变量
        combos = [("aurora", list(R.BRAND["500"])), ("mesh", list(R.STATE["500"])),
                  ("rays", list(R.AMBER_SCALE["500"])), ("grid", list(R.GREEN_SCALE["500"]))]
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
