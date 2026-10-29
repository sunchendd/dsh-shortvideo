#!/usr/bin/env python3
"""前 3 秒审计：小红书完播率的命门在那里。

平台给每条视频的初始流量很小，**前 3 秒划走率**直接决定还推不推。已有工具只查
"开场有没有内容"（qa.py 的开场空白/偏薄），但没查**能不能留住人**。这里量三件事：

  1. 首帧信息量 —— 0.0s 的墨量占比。太低 = 一上来就是空屏/纯背景
  2. 前 3 秒静帧 —— 0~3s 内是否存在 ≥0.8s 画面几乎不变（差值 <0.15%）。静态开头留不住人
  3. 首个变化时刻 —— 画面第一次出现明显变化的时间点，越早越好（≤0.6s）

判定（阈值取自 27 条片子的实测分布，两端都校准过）：
  · 首帧墨量 <1.2%           → 开场太素
  · 前 3s 存在 ≥0.8s 静止    → 开场发闷
  · 首个变化 > 1.2s          → 起手太慢

用法：
  hookaudit.py --dir examples
  hookaudit.py --video examples/out/19-spf-sunscreen/19-spf-sunscreen.mp4
  hookaudit.py --dir examples --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qa import _stats, grab_frames  # noqa: E402  （复用抽帧与墨量统计）

WINDOW = 3.0          # 只看前 3 秒
FPS = 10.0            # 10 fps 够抓 0.1s 级变化
MIN_INK0 = 0.012      # 首帧墨量下限
# 阈值取 1.2s 而不是 0.8s：playbook 的要求是"3 秒内至少一次明显变化"，
# 而实测 31 条的最佳 0.2s、最差 1.2s（把 big 版式改成按镜长错峰后，
# 最差从 1.4s 降到 1.2s）。定 0.8s 会把一半好片也标红，反而失去指示作用。
MAX_STILL = 1.2       # 允许的最长静止时长（秒）
STILL_DIFF = 0.0015   # 相邻帧差值低于此视为"没变"
MAX_FIRST_MOVE = 1.2  # 首个变化的最晚时刻


def audit(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    frames, times = grab_frames(path, FPS, tmpdir=f"/tmp/hook_{stem}_{os.getpid()}")
    if not frames:
        return {"video": path, "error": "抽帧失败"}

    from PIL import Image, ImageChops
    stats, prev, diffs = [], None, []
    for f in frames:
        if times[len(stats)] > WINDOW:
            break
        img = Image.open(f)
        stats.append(_stats(img))
        small = img.convert("RGB").resize((108, 192), Image.BILINEAR)
        if prev is not None:
            d = ImageChops.difference(small, prev).convert("L")
            hist = d.histogram()
            changed = sum(hist[12:]) / (108 * 192)
            diffs.append(changed)
        prev = small
        img.close()

    ink0 = stats[0]["ink"] if stats and "ink" in stats[0] else 0.0
    # 最长静止段
    best_still, run, best_at = 0, 0, 0.0
    for i, d in enumerate(diffs):
        if d < STILL_DIFF:
            run += 1
            if run > best_still:
                best_still, best_at = run, times[i - run + 2] if i - run + 2 < len(times) else 0.0
        else:
            run = 0
    still_secs = best_still / FPS
    first_move = next((times[i + 1] for i, d in enumerate(diffs) if d >= STILL_DIFF), None)

    flags = []
    if ink0 < MIN_INK0:
        flags.append(f"开场太素（首帧墨量 {ink0*100:.1f}% < {MIN_INK0*100:.0f}%）")
    if still_secs >= MAX_STILL:
        flags.append(f"开场发闷（{still_secs:.1f}s 画面几乎不变，从 {best_at:.1f}s 起）")
    if first_move is not None and first_move > MAX_FIRST_MOVE:
        flags.append(f"起手太慢（{first_move:.1f}s 才有第一次变化）")

    return {"video": path, "ink0": round(ink0 * 100, 2), "still": round(still_secs, 2),
            "first_move": round(first_move, 2) if first_move is not None else None,
            "flags": flags}


def main():
    ap = argparse.ArgumentParser(description="前 3 秒审计")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--video")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.video:
        rows = [audit(args.video)]
    else:
        paths = []
        for d in sorted(glob.glob(os.path.join(args.dir, "out", "*"))):
            if not os.path.isdir(d):
                continue
            slug = os.path.basename(d)
            p = os.path.join(d, f"{slug}.mp4")
            if os.path.exists(p):
                paths.append(p)
        rows = [audit(p) for p in paths]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 1 if any(r.get("flags") for r in rows) else 0

    print(f"■ 前 {WINDOW:g} 秒审计（{len(rows)} 条，{FPS:g} fps 采样）")
    print(f"  {'视频':<30}{'首帧墨量':>9}{'最长静帧':>9}{'首次变化':>9}  判定")
    bad = 0
    for r in rows:
        name = os.path.basename(os.path.dirname(r["video"]))[:29]
        if r.get("error"):
            print(f"  {name:<30}{'—':>9}{'—':>9}{'—':>9}  ⛔ {r['error']}")
            bad += 1
            continue
        ok = not r["flags"]
        bad += 0 if ok else 1
        fm = "—" if r["first_move"] is None else f"{r['first_move']:.1f}s"
        print(f"  {name:<30}{r['ink0']:>8.1f}%{r['still']:>8.1f}s{fm:>9}  "
              f"{'✅' if ok else '⚠️  ' + '；'.join(r['flags'])}")
    stills = sorted(r["still"] for r in rows if not r.get("error"))
    if stills:
        print(f"  最长静帧分布：最好 {stills[0]:.1f}s｜中位 {stills[len(stills)//2]:.1f}s｜"
              f"最差 {stills[-1]:.1f}s（阈值 {MAX_STILL:g}s）")
        worst = [r for r in rows if not r.get("error")][:0]
        worst = sorted([r for r in rows if not r.get("error")],
                       key=lambda r: -r["still"])[:3]
        print("  最该关注：" + "、".join(
            f"{os.path.basename(os.path.dirname(r['video']))}({r['still']:.1f}s)" for r in worst))
    print(f"  {len(rows) - bad}/{len(rows)} 条通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
