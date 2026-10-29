#!/usr/bin/env python3
"""封面体检：按"信息流缩略图"的尺寸量封面，不靠肉眼。

为什么按缩略图量：小红书信息流里封面只有约 240px 宽，很多"看起来很满"的封面
一到流里就糊成一团。所以这里先把它缩到 240 宽再量。

四项指标（全部可复算）：
  1. 缩略图对比度 —— 240px 宽灰度图的 RMS 对比度，<18 基本糊
  2. 标题有效字号 —— 标题带内文字墨迹的行高（px，换算到原图 1080 宽），<90 偏小
  3. 安全边距 —— 墨迹离画布边缘的最小距离，<40 会被平台 UI 裁掉
  4. 信息密度 —— 标题带墨迹占比，>18% 太挤、<2% 太空

用法：
  coveraudit.py --dir examples/out --glob 'cover_*.png'
  coveraudit.py --file examples/out/19-spf-sunscreen/cover_short.png
  coveraudit.py --dir examples/out --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys

from PIL import Image

THUMB_W = 240
# —— 阈值靠实测校准，不是拍脑袋 ——
# 25 张发布封面的实测分布（2026-10）：
#   墨迹行高 86~148px（中位 90）；换算字号 = 墨迹 / 0.78（CJK 字形墨迹约占 em 框 78%）
#   ⇒ 实际字号 110~190px；缩略图 RMS 33~56；边距 45~47；标题带密度 1.6%~11.2%
# 第一版阈值（墨迹 90px）把 8 张 112px 字号的封面判成"字小"——**量错了单位**。
INK_TO_FONT = 0.78
MIN_FONT_PX = 96        # 字号下限（信息流里再小就吃力）
MIN_RMS = 26.0          # 缩略图对比度下限
MIN_MARGIN = 36         # 安全边距
DENSITY_BAND = (0.06, 0.42)   # 标题带：画布高度的 6%~42%
DENSITY_MIN, DENSITY_MAX = 0.012, 0.18


def _bg_luma(img):
    """估底亮度：取四条边带的中位亮度（文字很少贴边，边带基本是底）。"""
    g = img.convert("L")
    w, h = g.size
    px = g.load()
    vals = []
    for x in range(0, w, 4):
        vals += [px[x, 2], px[x, h - 3]]
    for y in range(0, h, 4):
        vals += [px[2, y], px[w - 3, y]]
    vals.sort()
    return vals[len(vals) // 2]


def _ink_mask(img, thresh=26):
    """墨迹掩码：**局部背景差分**，与底色明暗无关。

    踩过两次坑：
    1. 写死 `luma > 140`（假设深底亮字）→ 浅色主题整张图都成了"墨迹"（边距 0px、密度 57%）
    2. 改成"比底亮/比底暗"的全局阈值 → 浅底的**底图光晕与点阵**比底暗 60 以上，仍被算墨迹
    现在用高斯模糊估局部底，只看高频差异：文字的笔画是高频，渐变/光晕/点阵是低频或弱幅，
    这样深底浅字、浅底深字、带光晕的浅底都能用同一套判定。
    """
    from PIL import ImageChops, ImageFilter
    g = img.convert("L")
    bg = g.filter(ImageFilter.GaussianBlur(10))
    diff = ImageChops.difference(g, bg)
    # 阈值按**分位数**定，不用绝对值：封面底层有一层颗粒噪点，全图都是高频，
    # 绝对阈值不是把噪点算成墨迹（整张图），就是把细笔画漏掉。
    # 取差异最强的前 6% 像素当墨迹——文字只占画面一小块，噪点幅度小，两者天然分开。
    hist = diff.histogram()
    total = sum(hist)
    want, acc, cut = total * 0.06, 0, 255
    for v in range(255, 0, -1):
        acc += hist[v]
        if acc >= want:
            cut = v
            break
    cut = max(thresh, cut)
    return diff.point(lambda v: 255 if v >= cut else 0, mode="1")


def _rms(img):
    g = list(img.convert("L").get_flattened_data())
    mean = sum(g) / len(g)
    return (sum((v - mean) ** 2 for v in g) / len(g)) ** 0.5


def _row_heights(mask):
    """按行统计墨迹像素数，连续有墨迹的行段就是一行字，返回各行高度。"""
    w, h = mask.size
    px = mask.load()
    counts = []
    for y in range(h):
        n = 0
        for x in range(0, w, 2):
            if px[x, y]:
                n += 1
        counts.append(n)
    runs, start = [], None
    for y, n in enumerate(counts):
        if n > w * 0.01 and start is None:
            start = y
        elif n <= w * 0.01 and start is not None:
            if y - start > 8:
                runs.append((start, y - start))
            start = None
    if start is not None:
        runs.append((start, h - start))
    return runs


def audit(path):
    img = Image.open(path).convert("RGB")
    W, H = img.size

    # 1) 缩略图对比度
    thumb = img.resize((THUMB_W, int(H * THUMB_W / W)), Image.LANCZOS)
    rms = _rms(thumb)

    # 2)+4) 标题带字号与密度
    band = img.crop((0, int(H * DENSITY_BAND[0]), W, int(H * DENSITY_BAND[1])))
    mask = _ink_mask(band)
    ink = sum(mask.point(lambda v: 1 if v else 0).get_flattened_data())
    density = ink / (band.size[0] * band.size[1])
    runs = _row_heights(mask)
    ink_px = max((r[1] for r in runs), default=0)
    title_px = int(round(ink_px / INK_TO_FONT))   # 换算成字号口径

    # 3) 安全边距
    full = _ink_mask(img)
    bbox = full.getbbox()
    margin = min(bbox[0], bbox[1], W - bbox[2], H - bbox[3]) if bbox else 0

    flags = []
    if rms < MIN_RMS:
        flags.append(f"缩略图对比度低({rms:.0f}<{MIN_RMS:.0f})")
    if title_px < MIN_FONT_PX:
        flags.append(f"标题字号小({title_px}px<{MIN_FONT_PX})")
    if margin < MIN_MARGIN:
        flags.append(f"贴边({margin}px<{MIN_MARGIN})")
    if density > DENSITY_MAX:
        flags.append(f"标题带太挤({density*100:.0f}%)")
    elif density < DENSITY_MIN:
        flags.append(f"标题带太空({density*100:.1f}%)")

    return {"file": path, "size": f"{W}x{H}", "rms": round(rms, 1),
            "title_px": title_px, "margin": margin,
            "density": round(density * 100, 1), "flags": flags}


def _score(r):
    """综合分：达标是前提，然后比缩略图对比、字号、密度（信息量）。"""
    s = r["rms"]                       # 缩略图对比
    s += min(r["title_px"], 190) / 4   # 字号（到 190px 封顶）
    s += min(r["density"], 9) * 1.2    # 密度（太满不好，9% 封顶）
    if r["flags"]:
        s -= 25
    return round(s, 1)


def pick(out_dir):
    """每条片子挑一张最扛信息流的封面。

    每个片子本来就出 3~4 个 3:4 版式（f1 大字报 / f3 / f6 / short）。
    发之前该选哪张不是"看着顺眼"，而是按缩略图指标排。
    """
    import glob as _glob
    dirs = sorted(d for d in _glob.glob(os.path.join(out_dir, "*"))
                  if os.path.isdir(d) and os.path.exists(
                      os.path.join(d, os.path.basename(d) + ".mp4")))
    print(f"■ 封面选择建议（{len(dirs)} 条）")
    print(f"  {'条目':<28}{'推荐':<12}{'综合分':>7}   其它候选")
    for d in dirs:
        cands = []
        for name in ("cover_short.png", "cover_f1.png", "cover_f3.png", "cover_f6.png"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                r = audit(p)
                cands.append((name.replace("cover_", "").replace(".png", ""), _score(r), r))
        if not cands:
            continue
        cands.sort(key=lambda c: -c[1])
        best, bs, br = cands[0]
        others = "  ".join(f"{n}:{s}" for n, s, _ in cands[1:])
        print(f"  {os.path.basename(d)[:27]:<28}{best:<12}{bs:>7}   {others}")
        if br["flags"]:
            print(f"       [!] 推荐的那张也有问题：{'；'.join(br['flags'])}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="封面体检（按缩略图尺寸量）")
    ap.add_argument("--dir")
    ap.add_argument("--file")
    ap.add_argument("--glob", default="cover_short.png")
    ap.add_argument("--pick", action="store_true",
                    help="每条片子比较 3:4 版式（f1/f3/f6/short），推荐综合最好的一张")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    files = []
    if args.file:
        files = [args.file]
    elif args.dir:
        files = sorted(glob.glob(os.path.join(args.dir, "**", args.glob), recursive=True))
    if not files:
        print("[coveraudit] 没找到封面")
        return 1

    if args.pick:
        return pick(args.dir or "examples/out")

    rows = [audit(f) for f in files]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"■ 封面体检（{len(rows)} 张，按 240px 缩略图量）")
    print(f"  {'封面':<44}{'对比':>6}{'字号':>6}{'边距':>6}{'密度':>7}  判定")
    bad = 0
    for r in rows:
        name = "/".join(r["file"].split("/")[-2:])
        ok = not r["flags"]
        bad += 0 if ok else 1
        print(f"  {name[:43]:<44}{r['rms']:>6.1f}{r['title_px']:>6}{r['margin']:>6}"
              f"{r['density']:>6.1f}%  {'✅' if ok else '⚠️ ' + '；'.join(r['flags'])}")
    if rows:
        print("\n  中位：对比 %.1f / 字高 %.0fpx / 密度 %.1f%%" % (
            statistics.median(r["rms"] for r in rows),
            statistics.median(r["title_px"] for r in rows),
            statistics.median(r["density"] for r in rows)))
    print(f"  {len(rows) - bad}/{len(rows)} 张通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
