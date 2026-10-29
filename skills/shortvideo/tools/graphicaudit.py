#!/usr/bin/env python3
"""图文事实链审计：一张图的"标题层"数字，必须在视频口播里真出现过。

为什么只查标题层：一张图合法地包含**推导值**（例如 SPF50 的 96.7%/98%、
600cm²、0.3 克），这些是把视频里的原始数字算出来的，不该要求旁白念一遍。
但**标题和高亮是这条图文对外的主张力**——那里出现一个视频从没提过的数字，
就是两条事实链对不上，用户会先看到图里的数、再发现视频里没有。

判定：
  · 标题 / 高亮 / anchor 里的数字 → 必须出现在任一口播稿
  · 要点正文里的数字 → 只在"整段一个数都没念过"时才提示（弱提示）

用法：
  graphicaudit.py --dir examples
  graphicaudit.py --spec examples/oneimage-19-spf-sunscreen.json
  graphicaudit.py --dir examples --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo.textutil import numbers_in  # noqa: E402


def _nums(node):
    out = set()
    if isinstance(node, dict):
        for v in node.values():
            out |= _nums(v)
    elif isinstance(node, list):
        for v in node:
            out |= _nums(v)
    elif isinstance(node, str):
        out |= numbers_in(node)
    return out


def audit(spec_path, content_path):
    spec = json.load(open(spec_path, encoding="utf-8"))
    content = json.load(open(content_path, encoding="utf-8"))
    spoken = set()
    for shot in content.get("shots") or []:
        spoken |= numbers_in(shot.get("narration", ""))

    def unspoken(nums):
        return sorted(n for n in nums
                      if not any(abs(n - s) < 0.011 for s in spoken))

    head = set()
    for key in ("title", "highlight", "anchor"):
        head |= _nums(spec.get(key))
    points = _nums(spec.get("points"))

    return {
        "slug": os.path.basename(content_path).replace(".content.json", ""),
        "headline_numbers": sorted(head),
        "point_numbers": sorted(points),
        "spoken": sorted(spoken),
        "headline_stray": unspoken(head),
        "points_stray": unspoken(points),
    }


def main():
    ap = argparse.ArgumentParser(description="图文事实链审计")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--spec")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pairs = []
    if args.spec:
        slug = os.path.basename(args.spec).replace("oneimage-", "").replace(".json", "")
        cp = os.path.join(os.path.dirname(args.spec), f"{slug}.content.json")
        if not os.path.exists(cp):
            cp = os.path.join(args.dir, f"{slug}.content.json")
        pairs.append((args.spec, cp))
    else:
        for spec in sorted(glob.glob(os.path.join(args.dir, "oneimage-*.json"))):
            slug = os.path.basename(spec).replace("oneimage-", "").replace(".json", "")
            cp = os.path.join(args.dir, f"{slug}.content.json")
            if os.path.exists(cp):
                pairs.append((spec, cp))

    reports = [audit(s, c) for s, c in pairs]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        print(f"■ 图文事实链（{len(reports)} 条）")
        bad = [r for r in reports if r["headline_stray"]]
        for r in bad:
            print(f"  ✗ {r['slug']}: 标题层有 {r['headline_stray']}，口播里没有")
        if not bad:
            print("  标题层数字全部在口播里出现过 ✅")
        weak = [r for r in reports
                if r["points_stray"] and not (set(r["point_numbers"]) & set(r["spoken"]))]
        if weak:
            print(f"\n  [i] {len(weak)} 条的要点数字与口播完全不重叠（可能是纯推导，人工确认）：")
            for r in weak[:6]:
                print(f"      {r['slug']}: {r['points_stray'][:6]}")
        print(f"\n  {len(reports) - len(bad)}/{len(reports)} 条通过")
    return 1 if any(r["headline_stray"] for r in reports) else 0


if __name__ == "__main__":
    sys.exit(main())
