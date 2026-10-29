#!/usr/bin/env python3
"""创意预算换算器：把"字数"翻译成"音节/秒"，在写文案之前就知道会不会超速。

为什么需要它：创意侧按**字数**规划（钩子 ≤20 字、每镜 ≤26 字），
工具侧按**音节/秒**判（正文 5.2、钩子 6.0）。
两套口径之间没有换算器，结果就是"照字数写完、一渲就被 lint 判 [x]"。
而且越反常识的选题数字越密，数字是最贵的音节——字数相同，时长可以差一倍。

用法：
  budget.py --text "五十倍防晒，只多挡1.3%。"          # 单句
  budget.py --content examples/19-spf-sunscreen.json    # 逐镜检查
  budget.py --text "..." --rate -8%                     # 指定语速再看
  budget.py --table                                     # 常用语速档下的时长表
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo.textutil import syllables  # noqa: E402

# 工具侧的判定阈值（与 make.lint_script 一致）
# 每种情绪的**实测**语速中位（130 个已渲染镜头，2026-10 统计）：
#   hook 4.76 / rule 4.43 / explain 4.18 / reveal 4.37 / win 4.36
# 之前所有情绪都用 4.6 估算，--mood 只改阈值不改时长，等于没算——
# 结果钩子（实际 4.76）被低估 3%，explain（4.18）被高估 10%。
MOOD_RATE = {"hook": 4.76, "rule": 4.43, "explain": 4.18, "reveal": 4.37, "win": 4.36}
DEFAULT_RATE = 4.42

LIMIT_BODY = 5.2        # 正文：偏快提醒
LIMIT_BODY_HARD = 5.8   # 正文：不合格
LIMIT_HOOK = 6.0        # 钩子：偏快提醒
LIMIT_HOOK_HARD = 6.6   # 钩子：不合格
TARGET = 4.6            # 舒适区上限


def analyse(text, mood="rule", rate_pct=0.0):
    """返回该句在不同语速下的时长与音节/秒。

    rate_pct：额外的语速偏移（例如 mood_tuning 的 -8% → -8）。
    基准按 4.6 音节/秒（舒适区上限），rate 每 +10% 语速约快 10%。
    """
    n_syl = syllables(text)
    n_char = len("".join(str(text).split()))
    base = MOOD_RATE.get(str(mood).lower(), DEFAULT_RATE)
    rows = []
    pairs = [("+0%", 0.0)]
    if rate_pct:
        pairs.append((f"{rate_pct:+g}%", rate_pct))
    for label, pct in pairs:
        rate = base * (1 + pct / 100.0)
        dur = n_syl / rate if rate else 0
        rows.append({"rate": label, "secs": round(dur, 2),
                     "syl_per_s": round(n_syl / dur, 2) if dur else 0})
    hook = str(mood).lower() == "hook"
    limit = LIMIT_HOOK if hook else LIMIT_BODY
    hard = LIMIT_HOOK_HARD if hook else LIMIT_BODY_HARD
    # 在目标语速下，多少字以内才安全
    max_syl = TARGET * (n_syl / rows[0]["syl_per_s"]) if rows[0]["syl_per_s"] else 0
    return {"text": text, "mood": mood, "chars": n_char, "syllables": n_syl,
            "rows": rows, "limit": limit, "hard": hard,
            "max_chars_suggest": int(max_syl * n_char / n_syl) if n_syl else 0,
            "ok": rows[0]["syl_per_s"] <= limit}


def show(text, mood, rate_pct):
    r = analyse(text, mood, rate_pct)
    mark = "✅" if r["ok"] else ("⚠️" if r["rows"][0]["syl_per_s"] <= r["hard"] else "❌")
    print(f"{mark} {r['chars']:>3} 字 / {r['syllables']:>3} 音节  {text[:34]}")
    for row in r["rows"]:
        print(f"     语速 {row['rate']:<6} → {row['secs']:>5.2f}s"
              f"（{row['syl_per_s']:.2f} 音节/秒）")
    if not r["ok"]:
        print(f"     [!] 超过{'钩子' if mood == 'hook' else '正文'}阈值 {r['limit']}"
              f"，建议删到 {r['max_chars_suggest']} 字以内")
    return r["ok"]


def main():
    ap = argparse.ArgumentParser(description="创意预算换算器（字数 ↔ 音节/秒）")
    ap.add_argument("--text")
    ap.add_argument("--content")
    ap.add_argument("--mood", default="rule")
    ap.add_argument("--rate", type=float, default=0.0, help="额外语速偏移，如 -8 表示 -8%%")
    ap.add_argument("--table", action="store_true", help="常用语速档下的时长表")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.table:
        print(f"{'音节':>5}{'4.0/s':>9}{'4.6/s':>9}{'5.2/s':>9}   说明")
        for n in (10, 13, 16, 20, 24, 28, 34, 38):
            print(f"{n:>5}{n / 4.0:>8.2f}s{n / 4.6:>8.2f}s{n / 5.2:>8.2f}s"
                  f"   {'钩子上限(13音节约3.2s)' if n == 13 else ''}")
        print("\n钩子 ≤13 字（≈13~15 音节，2.8~3.2s）；正文每镜 ≤28 字（≈30 音节，6.5s 内）")
        return 0

    if args.content:
        content = json.load(open(args.content, encoding="utf-8"))
        bad = 0
        for shot in content.get("shots") or []:
            ok = show(shot.get("narration", ""), shot.get("id", "rule"), args.rate)
            bad += 0 if ok else 1
        print(f"\n{len(content.get('shots') or [])} 镜，超速 {bad} 镜")
        return 0 if not bad else 1

    if not args.text:
        ap.error("需要 --text / --content / --table 之一")
    ok = show(args.text, args.mood, args.rate)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
