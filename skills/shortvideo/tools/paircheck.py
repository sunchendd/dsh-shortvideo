#!/usr/bin/env python3
"""长短版一致性检查：同题材的长版和短版，**数字不能互相打架**。

为什么需要：短版是从长版改写的（砍 insight、压字数），改写时最容易把
「十倍以上」写成「21 倍」、把「34 分贝」写成「34%」——两条片子同时挂在账号上，
评论区会立刻发现。而现有的门禁只验单条内部自洽（graphicaudit 是"画面数字要在口播里出现"），
没有任何一道验**跨条目**。

配对方式：长版 slug 与短版 slug 的词干重合 ≥1 个有效词即视为同题材
（18-plane-window ↔ short-06-plane-window）。

只做"差异报告"，不自动判对错——同一个概念用不同精度表述是允许的
（长版说「十倍以上」，短版说「十倍」，都对）。要人看的只是**交叉出现的异常**。

用法：
  paircheck.py --dir examples
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo.textutil import numbers_in  # noqa: E402

STOP = {"short", "the", "and", "vs", "18s", "33cm", "13pct"}


def stem(slug):
    """把 slug 拆成有效词（去掉序号与短版标记）。

    同时给出一份"连写形式"，因为长版 slug 常把复合词拆开、短版常连写：
    长版 `05-blind-spot` vs 短版 `short-03-blindspot-18s`——
    不处理连写就配不上（实测漏了一对）。
    """
    parts = [p for p in slug.replace("_", "-").split("-") if p]
    words = [p for p in parts if not p.isdigit() and p not in STOP]
    return set(words) | {"".join(words)}


def narration(cfg):
    return " ".join(str(s.get("narration") or "") for s in cfg.get("shots") or [])


def main():
    ap = argparse.ArgumentParser(description="长短版数字一致性")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    items = []
    for p in sorted(glob.glob(os.path.join(args.dir, "*.content.json"))):
        slug = os.path.basename(p).replace(".content.json", "")
        cfg = json.load(open(p, encoding="utf-8"))
        items.append({"slug": slug, "short": str(cfg.get("format") or "").lower() == "short",
                      "cfg": cfg, "nums": numbers_in(narration(cfg))})

    longs = [i for i in items if not i["short"]]
    shorts = [i for i in items if i["short"]]
    pairs, reports = [], []
    for s in shorts:
        ss = stem(s["slug"])
        best, score = None, 0
        for l in longs:
            ov = len(ss & stem(l["slug"]))
            if ov > score:
                best, score = l, ov
        if not best:
            continue
        pairs.append((best, s))
        only_long = sorted(best["nums"] - s["nums"])
        only_short = sorted(s["nums"] - best["nums"])
        reports.append({"long": best["slug"], "short": s["slug"],
                        "only_long": only_long, "only_short": only_short})

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0

    print(f"■ 长短版配对：{len(pairs)} 对")
    for r in reports:
        print(f"\n  {r['long']}  ↔  {r['short']}")
        if not r["only_long"] and not r["only_short"]:
            print("    ✅ 数字集合完全一致")
            continue
        if r["only_long"]:
            print(f"    长版独有：{r['only_long']}（短版没提，通常是砍掉了——确认不是砍掉了关键依据）")
        if r["only_short"]:
            print(f"    短版独有：{r['only_short']}  ← **短版新出现的数字，必须能在长版找到出处**")
    unpaired = [s["slug"] for s in shorts if not any(s is p[1] for p in pairs)]
    if unpaired:
        print(f"\n  没配上的短版：{unpaired}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
