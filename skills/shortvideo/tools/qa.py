"""成片画面体检：抽帧算指标，自动抓"画面太空""内容越界""死黑""字幕缺失"。

为什么需要它：文案有 lint、配音有响度检测，但"画面到底行不行"一直靠人眼一帧帧看，
9 条片子 × 每镜 2 帧就是几十次看图。这里把它变成可复现的数字，批量跑完只看告警。

四个指标（都从抽帧算，尺度无关）：
  ink      画面"有东西"的面积占比（与背景色的差异像素比例）——太低 = 空镜
  edge     距离边框 72px 安全区内的高亮像素比例——太高 = 内容贴边/越界
  luma     平均亮度（0~255）——太低 = 死黑
  caption  字幕带（y≈1620~1720）的墨量——太低 = 这几帧没字幕（预期：配音段必须有）

用法：
  python qa.py --video out/x.mp4 [--fps 2] [--json]
  python qa.py --dir examples            # 批量扫 examples/out/*/*.mp4
"""
import argparse
import glob
import json
import os
import subprocess
import sys

from PIL import Image

W, H = 1080, 1920
SAFE = 72                      # 平台安全边距
CAPTION_BAND = (1600, 1740)    # 字幕所在纵向区间
INK_MIN = 0.012                # 有内容的面积下限（1.2%）
EDGE_MAX = 0.02                # 安全区外高亮像素上限
LUMA_MIN = 10                  # 平均亮度过低的阈值
CAP_MIN = 0.004
HOOK_INK_MIN = 0.018       # 开场 0.5s 最低画面墨量（实测好片 ≥3%）                # 字幕带墨量下限
BODY_MIN = 0.003               # 内容区墨量下限。单个巨型数字（stat）只有 ~0.5%，
                               # 真正全空的帧是 0.0~0.2%，所以卡在 0.3% 更准


def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def probe_duration(path):
    ff = ffmpeg_exe()
    out = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if not m:
        return 0.0
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def grab_frames(path, fps=2.0, tmpdir="/tmp/qa_frames"):
    """按 fps 抽帧到 tmpdir，返回 (帧路径列表, 每帧时间戳)。"""
    os.makedirs(tmpdir, exist_ok=True)
    for f in glob.glob(os.path.join(tmpdir, "*.png")):
        os.remove(f)
    ff = ffmpeg_exe()
    subprocess.run([ff, "-v", "error", "-y", "-i", path, "-vf", f"fps={fps}",
                    os.path.join(tmpdir, "f%04d.png")], capture_output=True)
    frames = sorted(glob.glob(os.path.join(tmpdir, "f*.png")))
    return frames, [i / fps for i in range(len(frames))]


def _stats(img, scale=3):
    """一帧的四项指标。用 ImageChops 在 C 层算差异，比逐像素快一个数量级。

    scale：先缩小再算（比例量，缩了不改变判定）。
    """
    from PIL import ImageChops
    small = img.convert("RGB").resize((W // scale, H // scale), Image.BILINEAR)
    w, h = small.size
    px = small.load()
    # 背景基准：四角 12x12 的平均色（我们的底图四角总是背景）
    cs = [px[x, y] for x in range(0, 12) for y in range(0, 12)]
    cs += [px[w - 1 - x, h - 1 - y] for x in range(0, 12) for y in range(0, 12)]
    bg = tuple(int(sum(c[i] for c in cs) / len(cs)) for i in range(3))
    diff = ImageChops.difference(small, Image.new("RGB", small.size, bg)).convert("L")

    def above(box, thr=40):
        """box 内差异超过阈值的像素数。"""
        region = diff.crop(box)
        hist = region.histogram()
        return sum(hist[thr + 1:])

    total = w * h
    safe_x, safe_y = int(SAFE / scale), int(SAFE / scale)
    body0, body1 = int(140 / scale), int(1550 / scale)
    band0, band1 = int(CAPTION_BAND[0] / scale), int(CAPTION_BAND[1] / scale)
    ink = above((0, 0, w, h))
    edge = (above((0, 0, w, safe_y)) + above((0, h - safe_y, w, h))
            + above((0, 0, safe_x, h)) + above((w - safe_x, 0, w, h)))
    cap_ink = above((0, band0, w, band1))
    body = above((0, body0, w, body1))
    # 平均亮度：用亮度直方图加权，避免逐像素
    lum_hist = small.convert("L").histogram()
    luma = sum(i * c for i, c in enumerate(lum_hist)) / max(1, total)
    return {
        "ink": round(ink / total, 4),
        "edge": round(min(1.0, edge / total), 4),
        "luma": round(luma, 1),
        "cap": round(cap_ink / max(1, (band1 - band0) * w), 4),
        "body": round(body / max(1, (body1 - body0) * w), 4),
    }


def check_video(path, fps=2.0, keep_frames=False):
    dur = probe_duration(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    frames, times = grab_frames(path, fps, tmpdir=f"/tmp/qa_{stem}_{os.getpid()}")
    rows = []
    for fp, t in zip(frames, times):
        try:
            img = Image.open(fp)
            st = _stats(img)
        except Exception:  # noqa: BLE001
            continue
        st["t"] = round(t, 2)
        rows.append(st)
    if not keep_frames:
        for fp in frames:
            try:
                os.remove(fp)
            except OSError:
                pass
    if not rows:
        return {"video": path, "dur": dur, "error": "抽帧失败", "flags": ["抽帧失败"]}

    flags = []
    # 空镜：只看内容区墨量（不含字幕/进度条），连续 1.2s 都空才算问题
    run, best, best_at = 0, 0, 0.0
    for r in rows:
        if r.get("body", 1) < BODY_MIN:
            run += 1
            if run > best:
                best, best_at = run, r["t"] - (run - 1) / fps
        else:
            run = 0
    if best >= max(3, int(1.5 * fps)):
        flags.append(f"画面太空：内容区连续 {best / fps:.1f}s 无内容（从 {best_at:.1f}s 起，"
                     f"只剩字幕）")
    # 开场空白：前 1.5 秒是决定划走的窗口，前 3 帧（t ≤ 1.0s）只要都没有内容就是硬伤。
    # 这条是"短镜头按比例出场"那次的直接产物：4.9s 镜头按 0.5 比例出场，
    # 第二个元素 2.5s 才到，前 1 秒几乎空白，而 qa 原来的"连续 1.5s"规则抓不到。
    # 开场偏薄：前 0.5s 画面墨量太低 = "钩子还没画出来"。实测好片 3%~8%，
    # 而从 0 起数的 stat 钩子 / 空卡片对比只有 0.6%，差一个数量级。
    thin = [r for r in rows if r["t"] <= 0.5]
    if thin:
        avg = sum(r["body"] for r in thin) / len(thin)
        if avg < HOOK_INK_MIN:
            flags.append(f"开场偏薄：前 0.5s 平均画面墨量 {avg * 100:.2f}%"
                         f"（<{HOOK_INK_MIN * 100:.1f}%，钩子画面还没出来）")
    head = [r for r in rows if r["t"] <= 1.0]
    if len(head) >= 2 and all(r["body"] < BODY_MIN for r in head):
        flags.append(f"开场空白：前 {head[-1]['t']:.1f}s 内容区无内容（首帧必须立刻有信息）")
    edges = [r for r in rows if r["edge"] > EDGE_MAX]
    if edges:
        flags.append(f"内容进安全区：{len(edges)} 帧越界（最多 {max(r['edge'] for r in edges) * 100:.2f}%）")
    darks = [r for r in rows if r["luma"] < LUMA_MIN]
    if darks:
        flags.append(f"死黑帧：{len(darks)} 帧平均亮度 <{LUMA_MIN}")
    # 字幕：分句之间本来就会空一下（0.2~0.4s 的淡出），只报"连续空太久"的
    run, best, best_at = 0, 0, 0.0
    for r in rows:
        if r["cap"] < CAP_MIN:
            run += 1
            if run > best:
                best, best_at = run, r["t"] - (run - 1) / fps
        else:
            run = 0
    # 镜头尾部有 0.55s 尾巴 + 下一镜 0.15s 前摇，字幕会正常空 ~0.7s；
    # 只有连续空 >=1.0s（且至少 3 个采样点）才算异常
    if best >= 3 and best / fps >= 1.0:
        flags.append(f"字幕缺失：连续 {best / fps:.1f}s 字幕带为空（从 {best_at:.1f}s 起）")
    return {
        "video": os.path.basename(path), "dur": round(dur, 2), "frames": len(rows),
        "ink_avg": round(sum(r["ink"] for r in rows) / len(rows), 4),
        "body_avg": round(sum(r.get("body", 0) for r in rows) / len(rows), 4),
        "ink_min": min(r["ink"] for r in rows),
        "edge_max": max(r["edge"] for r in rows),
        "luma_avg": round(sum(r["luma"] for r in rows) / len(rows), 1),
        "cap_avg": round(sum(r["cap"] for r in rows) / len(rows), 4),
        "flags": flags,
    }


def main():
    ap = argparse.ArgumentParser(description="成片画面体检（抽帧算墨量/越界/亮度/字幕）")
    ap.add_argument("--video", default=None)
    ap.add_argument("--dir", default=None, help="批量：扫 <dir>/out/*/*.mp4")
    ap.add_argument("--fps", type=float, default=2.0, help="抽帧频率（默认 2 帧/秒）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    vids = []
    if args.video:
        vids = [args.video]
    elif args.dir:
        vids = sorted(glob.glob(os.path.join(args.dir, "out", "*", "*.mp4")))
    if not vids:
        print("[qa] 没有输入：用 --video 或 --dir")
        return

    results = [check_video(v, args.fps) for v in vids]
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return
    print(f"\n■ 画面体检（{len(results)} 条，{args.fps:g} 帧/秒抽样）\n")
    print(f"  {'成片':<26}{'时长':>7}{'画面墨量':>9}{'总墨量':>8}{'越界':>7}{'亮度':>7}{'字幕':>7}")
    bad = 0
    for r in results:
        if "error" in r:
            print(f"  {r['video']:<26}  ✗ {r['error']}")
            bad += 1
            continue
        print(f"  {r['video']:<26}{r['dur']:>7.1f}{r['body_avg'] * 100:>8.1f}%"
              f"{r['ink_avg'] * 100:>7.1f}%{r['edge_max'] * 100:>6.2f}%"
              f"{r['luma_avg']:>7.0f}{r['cap_avg'] * 100:>6.1f}%")
        for f in r["flags"]:
            print(f"      ! {f}")
        bad += bool(r["flags"])
    print(f"\n  {len(results) - bad}/{len(results)} 条无告警\n")


if __name__ == "__main__":
    main()
