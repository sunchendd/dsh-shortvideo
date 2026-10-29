"""把体检数据画成图：节奏心电图 + BGM 波形对比。

文字表格看不出"两镜挨着都是 9 秒"这种形态问题，一张横条图一眼就看出来。
输出（默认写到 examples/）：
  rhythm.png        每条片子五个镜头的时长横条，颜色=风险等级
  bgm-compare.png   lofi / pulse 两种 BGM 的波形与能量包络

用法：
  python viz.py --dir examples
  python viz.py --bgm /tmp/bed.lofi.wav /tmp/bed.pulse.wav --out /tmp/bgm.png
"""
import argparse
import array
import glob
import json
import math
import os
import sys
import wave

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shortvideo import runtime as R  # noqa: E402
import pace  # noqa: E402

BG = (14, 17, 22)
TXT = (238, 242, 255)
DIM = (143, 160, 184)
ACCENT = (255, 45, 85)
AMBER = (255, 212, 0)
GREEN = (56, 224, 168)
CYAN = (58, 216, 240)


RATE_LO, RATE_HI = 3.2, 7.0     # 语速映射区间（音节/秒）


def rhythm(rows_by_video, out_path, w=1200, row_h=62):
    """每条一条泳道；横条长度=镜头时长；颜色=风险（绿/黄/红）。"""
    slugs = list(rows_by_video)
    label_w = 250
    h = 90 + row_h * len(slugs)
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    f_title = R.font(30, bold=True)
    f_lbl = R.font(22, bold=True)
    f_num = R.font(18)
    d.text((24, 18), "节奏心电图：条长 = 时长占比 · 条上数字 = 时长 / 语速（音节每秒）· 颜色 = 风险",
           font=f_title, fill=TXT)
    d.text((24, 52), "绿 = 无风险 · 黄 = 1~2 分 · 红 = 3 分以上；钩子语速档 6.6、正文 5.2",
           font=f_num, fill=DIM)

    # 每条泳道按自身总时长归一化（各条总长不同，用绝对秒数会画出画布）
    scale_w = w - label_w - 60

    for r, slug in enumerate(slugs):
        y = 78 + r * row_h
        rs = rows_by_video[slug]
        total = sum(x["dur"] for x in rs)
        d.text((24, y + 6), slug[:22], font=f_lbl, fill=TXT)
        d.text((24, y + 30), f"{total:.1f}s / {len(rs)} 镜", font=f_num, fill=DIM)
        x = label_w
        for shot in rs:
            bw = max(2, scale_w * shot["dur"] / max(1e-6, total))
            col = GREEN if shot["risk"] == 0 else (AMBER if shot["risk"] <= 2 else ACCENT)
            d.rounded_rectangle([x, y + 4, x + bw - 3, y + row_h - 14], radius=6, fill=col)
            if bw > 46:
                d.text((x + 8, y + 12), f"{shot['dur']:.1f}", font=f_num, fill=BG)
                d.text((x + 8, y + 30), shot["shot"][:6], font=f_num, fill=(40, 40, 40))
            x += bw
    img.save(out_path)
    return out_path


def waveform(paths, out_path, w=1200, panel_h=230):
    """多条 wav 的波形 + 能量包络对比。"""
    n = len(paths)
    img = Image.new("RGB", (w, 70 + panel_h * n), BG)
    d = ImageDraw.Draw(img)
    d.text((24, 18), "BGM 波形对比（上半：波形，下半：能量包络）", font=R.font(28, bold=True), fill=TXT)
    for k, p in enumerate(paths):
        with wave.open(p) as wf:
            frames = wf.getnframes()
            sr = wf.getframerate()
            data = array.array("h", wf.readframes(frames))
        y0 = 66 + k * panel_h
        name = os.path.basename(p)
        d.text((24, y0 + 4), name, font=R.font(22, bold=True), fill=TXT)
        mid = y0 + 80
        step = max(1, len(data) // (w - 60))
        # 波形
        for x in range(w - 60):
            chunk = data[x * step:(x + 1) * step]
            if not chunk:
                break
            amp = max(abs(v) for v in chunk) / 32768
            d.line([(30 + x, mid - amp * 70), (30 + x, mid + amp * 70)],
                   fill=(90, 200, 230) if k % 2 == 0 else (255, 140, 170))
        # 能量包络
        win = max(1, sr // 20)   # 50ms
        env = []
        for s in range(0, len(data) - win, win):
            seg = data[s:s + win]
            env.append(math.sqrt(sum((v / 32768) ** 2 for v in seg) / len(seg)))
        if env:
            mx = max(env) or 1
            base = y0 + 200
            pts = []
            for i, e in enumerate(env):
                x = 30 + (w - 60) * i / len(env)
                pts.append((x, base - (e / mx) * 60))
            d.line(pts, fill=AMBER, width=2)
        d.text((w - 250, y0 + 4), f"{frames / sr:.1f}s · {sr}Hz", font=R.font(20), fill=DIM)
    img.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="体检可视化：节奏心电图 / BGM 波形对比")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--bgm", nargs="*", default=None, help="要比对的 wav 列表")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.bgm:
        out = args.out or os.path.join(args.dir, "bgm-compare.png")
        print("[ok]", waveform(args.bgm, out))
        return

    rows = {}
    for cp in sorted(glob.glob(os.path.join(args.dir, "*.content.json"))):
        slug = os.path.basename(cp).replace(".content.json", "")
        vp = os.path.join(args.dir, "out", slug, f"{slug}.mp4")
        rows[slug] = pace.analyse(slug, cp, vp)
    out = args.out or os.path.join(args.dir, "rhythm.png")
    print("[ok]", rhythm(rows, out))


if __name__ == "__main__":
    main()
