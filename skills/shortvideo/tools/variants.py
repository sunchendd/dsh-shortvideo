"""A/B 变体生成：同一个正文，换钩子和封面 → 出 N 条只差"前 3 秒"的片子。

为什么只换钩子：小红书的分发是"封面+标题+前 3 秒"决定进不进下一个流量池，
正文（rule/insight/reveal/solve）通常不变。所以 A/B 应该控制变量——只动开场。

用法：
  # 1) 写一个变体清单（见示例 examples/variants-01-monty-hall.json）
  python variants.py --spec examples/variants-01-monty-hall.json --out examples/variants
  # 2) 交给流水线
  python studio.py --dir examples/variants --render
  # 3) 发布后回填 CTR，用 studio.py --ab 看哪种钩子更适合这个账号

变体清单格式：
{
  "base": "01-monty-hall.content.json",
  "variants": [
    {"id": "num",  "hook": "……", "emphasis": ["…"], "label": "数字冲击",
     "cover": {"headline": ["…", "…"], "highlight": ["…"]}},
    {"id": "ask",  "hook": "……", "label": "提问式"},
    {"id": "cost", "hook": "……", "label": "代价恐惧"}
  ]
}
"""
import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def build_variants(spec_path, out_dir):
    spec = json.load(open(spec_path, encoding="utf-8"))
    base_path = os.path.join(os.path.dirname(os.path.abspath(spec_path)), spec["base"])
    base = json.load(open(base_path, encoding="utf-8"))
    slug = os.path.basename(base_path).replace(".content.json", "")
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for v in spec.get("variants") or []:
        vid = str(v.get("id") or len(made) + 1)
        cfg = copy.deepcopy(base)
        hook = v.get("hook")
        if hook:
            cfg["shots"][0]["narration"] = hook
            if v.get("emphasis"):
                cfg["shots"][0]["emphasis"] = v["emphasis"]
            # 钩子镜的画面大字跟着文案走，避免"画面写一套、口播说另一套"
            lines = v.get("hook_lines")
            if lines:
                deck = cfg.setdefault("deck", {})
                if isinstance(deck.get("hook"), dict):
                    deck["hook"]["lines"] = lines
                else:
                    deck["hook"] = {"kind": "big", "lines": lines, "style": "slam"}
        cov = v.get("cover") or {}
        cfg.setdefault("cover", {}).update({k: cov[k] for k in cov})
        cfg["variant_of"] = slug
        cfg["variant_label"] = v.get("label") or vid
        if v.get("publish_title"):
            cfg["publish_title"] = v["publish_title"]
        out = os.path.join(out_dir, f"{slug}-v{vid}.content.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        made.append((os.path.basename(out), cfg["variant_label"],
                     cfg["shots"][0]["narration"]))
    return made


def main():
    ap = argparse.ArgumentParser(description="生成 A/B 变体（只换钩子与封面）")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default="examples/variants")
    args = ap.parse_args()
    made = build_variants(args.spec, args.out)
    print(f"\n[ok] 生成 {len(made)} 个变体 → {args.out}\n")
    for fn, label, hook in made:
        print(f"  {fn:<34}{label:<10}{hook}")
    print("\n下一步：python studio.py --dir " + args.out + " --render\n")


if __name__ == "__main__":
    main()
