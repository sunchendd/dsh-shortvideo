#!/usr/bin/env python3
"""发布可行性检查：**这些文件到底能不能发出去**。

前面 12 道门禁验的是"内容好不好"，这一道验"文件本身会不会被平台卡住"。
参数取自小红书创作服务平台的上传要求（2026-10 现行）：

  视频   MP4(H.264+AAC)｜竖屏优先｜单文件 ≤ 1GB｜时长 ≤ 15min
  封面   3:4 竖版，≤ 20MB
  标题   **≤ 20 字**（含标点），超了会被截断
  正文   ≤ 1000 字
  合规   AI 生成内容需标注（本项目统一写"AI 生成画面 / AI 配音"）
         不允许出现站外导流（微信号 / 二维码 / 链接）

用法：
  publishcheck.py --dir examples
  publishcheck.py --content examples/19-spf-sunscreen.content.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

MAX_SIZE_MB = 1024
MAX_SECONDS = 900
MAX_TITLE = 20
MAX_DESC = 1000
MAX_COVER_MB = 20

# 站外导流 / 平台红线（出现即不可发）
# 只抓**导流意图**，不抓题材本身：讲"二维码为什么残缺也能扫"的科普片
# 标题里出现「二维码」是正常内容，不是导流（第一版就这样误报了 10 号）。
BAN = re.compile(r"(微信号|加微信|vx[:：]|V信|QQ群|扫码加|扫码领|扫码关注|"
                 r"二维码加|加好友|https?://|www\.|私信领|点击链接|评论领取|"
                 r"淘宝|拼多多|带货)")


def ffprobe_exe():
    try:
        import imageio_ffmpeg
        p = imageio_ffmpeg.get_ffmpeg_exe()
        return p.replace("ffmpeg", "ffprobe") if "ffprobe" in os.path.basename(p) else None
    except Exception:  # noqa: BLE001
        return None


def probe(path):
    """返回 (宽, 高, 时长, 视频编码, 音频编码, 字节数)。ffprobe 不在就用 ffmpeg 兜底。"""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    out = subprocess.run([ff, "-hide_banner", "-i", path], capture_output=True, text=True).stderr
    w = h = dur = None
    vcodec = acodec = None
    m = re.search(r"Video: (\w+)", out)
    if m:
        vcodec = m.group(1)
    m = re.search(r"Audio: (\w+)", out)
    if m:
        acodec = m.group(1)
    m = re.search(r", (\d{2,5})x(\d{2,5})[, ]", out)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", out)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return w, h, dur, vcodec, acodec, os.path.getsize(path)


def check(content_path):
    cfg = json.load(open(content_path, encoding="utf-8"))
    slug = os.path.basename(content_path).replace(".content.json", "")
    base = os.path.join(os.path.dirname(content_path), "out", slug)
    mp4 = os.path.join(base, f"{slug}.mp4")
    problems, notes = [], []

    # 1) 标题
    title = str(cfg.get("publish_title") or cfg.get("title") or "").strip()
    if not title:
        problems.append("没有 publish_title / title")
    elif len(title) > MAX_TITLE:
        problems.append(f"标题 {len(title)} 字 > {MAX_TITLE} 字，平台会截断：{title}")
    if BAN.search(title):
        problems.append(f"标题含站外导流/红线词：{BAN.search(title).group(0)}")

    # 2) 正文（发布文案）
    desc = str(cfg.get("publish_desc") or cfg.get("subtitle") or "")
    if len(desc) > MAX_DESC:
        problems.append(f"正文 {len(desc)} 字 > {MAX_DESC}")
    if BAN.search(desc):
        problems.append(f"正文含站外导流/红线词：{BAN.search(desc).group(0)}")

    # 3) AI 标注
    dis = str(cfg.get("disclosure") or "")
    if not dis:
        problems.append("缺 disclosure（AI 生成标注）")
    elif not re.search(r"AI|人工智能", dis):
        notes.append(f"disclosure 里没有「AI」字样：{dis[:24]}")

    # 4) 成片
    if not os.path.exists(mp4):
        problems.append("成片不存在")
    else:
        w, h, dur, vc, ac, size = probe(mp4)
        mb = size / 1024 / 1024
        if mb > MAX_SIZE_MB:
            problems.append(f"文件 {mb:.0f}MB > {MAX_SIZE_MB}MB")
        if dur and dur > MAX_SECONDS:
            problems.append(f"时长 {dur:.0f}s > {MAX_SECONDS}s")
        if w and h:
            if h < w:
                problems.append(f"{w}x{h} 是横屏，小红书竖屏更吃流量")
            elif abs(w / h - 9 / 16) > 0.02 and abs(w / h - 3 / 4) > 0.05:
                notes.append(f"{w}x{h} 非 9:16/3:4，可能被加黑边")
        if vc and vc not in ("h264", "hevc"):
            problems.append(f"视频编码 {vc}，平台推荐 h264")
        if not ac:
            problems.append("没有音轨")

    # 5) 封面
    cover = os.path.join(base, "cover_short.png")
    if not os.path.exists(cover):
        cover = os.path.join(base, "cover_f1.png")
    if not os.path.exists(cover):
        problems.append("没有 3:4 封面")
    else:
        cmb = os.path.getsize(cover) / 1024 / 1024
        if cmb > MAX_COVER_MB:
            problems.append(f"封面 {cmb:.1f}MB > {MAX_COVER_MB}MB")

    # 6) 图文笔记（小红书常配图文）
    if not os.path.exists(os.path.join(base, "summary.png")):
        notes.append("没有一张图（summary.png），少一个图文位")

    return {"slug": slug, "title_len": len(title), "problems": problems, "notes": notes}


def main():
    ap = argparse.ArgumentParser(description="发布可行性检查")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--content")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = [args.content] if args.content else sorted(
        p for p in glob.glob(os.path.join(args.dir, "*.content.json"))
        if not os.path.basename(p).startswith("oneimage"))
    rows = [check(p) for p in paths]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 1 if any(r["problems"] for r in rows) else 0

    print(f"■ 发布可行性（{len(rows)} 条；标题 ≤{MAX_TITLE} 字、≤{MAX_SIZE_MB}MB、"
          f"h264+aac、有封面与 AI 标注）")
    bad = 0
    for r in rows:
        ok = not r["problems"]
        bad += 0 if ok else 1
        mark = "✅" if ok else "⛔"
        print(f"  {mark} {r['slug']:<28}标题 {r['title_len']:>2} 字"
              + ("" if ok else "  ← " + "；".join(r["problems"])))
        for n in r["notes"]:
            print(f"      · {n}")
    print(f"  {len(rows) - bad}/{len(rows)} 条可直接发")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
