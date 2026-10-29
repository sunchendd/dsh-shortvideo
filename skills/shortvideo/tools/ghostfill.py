"""给"画面偏薄"的镜头自动补背景水印（ghost）。

规则刻意保守，避免水印变成噪音：
  1. 只处理 kind 为 big / cards / steps 的镜头（这几类天生字数少、画面薄）
  2. 只在该镜旁白里**本来就有数字**时加水印，且直接复用那个数字——
     这样绝不会出现"画面写 30%、口播说三成"的对不上号
  3. 已经有 ghost 的镜头不动
  4. 钩子默认跳过（钩子常常已经有水印，且它最重要，人工确认更稳）

用法：
  python ghostfill.py --dir examples --dry-run
  python ghostfill.py --dir examples
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re  # noqa: E402

THIN_KINDS = ("big", "cards", "steps", "compare")


def fill(path, dry_run=False):
    cfg = json.load(open(path, encoding="utf-8"))
    deck = cfg.get("deck") or {}
    shots = {s.get("id"): s for s in (cfg.get("shots") or [])}
    changed = []
    for sid, spec in deck.items():
        if not isinstance(spec, dict) or spec.get("ghost"):
            continue
        if sid == "hook":
            continue
        if str(spec.get("kind") or "big") not in THIN_KINDS:
            continue
        # 取旁白里的**字面**数字（不要归一化后的 1.0/2.0 这种），挑最长的那个最有辨识度
        toks = re.findall(r"\d[\d,]*(?:\.\d+)?%?|[零一二三四五六七八九十百千两]+", 
                          shots.get(sid, {}).get("narration", ""))
        toks = [t for t in toks if len(t) >= 2 or t.isdigit() and int(t) > 1]
        if not toks:
            continue
        g = max(toks, key=len)
        spec["ghost"] = g
        changed.append((sid, g))
    if changed and not dry_run:
        json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return changed


def main():
    ap = argparse.ArgumentParser(description="给偏薄的镜头自动补背景水印")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    total = 0
    for cp in sorted(glob.glob(os.path.join(args.dir, "*.content.json"))):
        slug = os.path.basename(cp).replace(".content.json", "")
        ch = fill(cp, args.dry_run)
        if ch:
            total += len(ch)
            print(f"  {slug:<26}" + "  ".join(f"{sid}→{g}" for sid, g in ch))
    print(f"\n{'[dry-run] 将' if args.dry_run else '[ok] 已'}补 {total} 个水印\n")


if __name__ == "__main__":
    main()
