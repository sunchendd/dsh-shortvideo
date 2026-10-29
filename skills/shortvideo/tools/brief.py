#!/usr/bin/env python3
"""选题简报评分：把"这个选题值不值得做"变成可复算的四维分。

创意总监的评分表（反常识/痛点/可验证/可视化，各 5 分，总分 ≥15 才做）一直是**主观分**，
没有任何工具能验证。这里用可测的替代信号把它落地：

  反常识度  —— 标题里有没有"数字对比"或"否定/反转"结构
                （有数字 + 有对比词 = 5；只有数字 = 3.5；都没有 = 2）
  痛点      —— 是否触及钱/时间/健康/身体/日常误会（命中 1 个 3 分，2 个 4 分，3 个 5 分）
  可验证    —— content.json 有没有 kernel（算式）与 refs（依据），两者都有 = 5
  可视化    —— 每个 shot 是否都有 deck_hint（画面意图），比例 × 5

这些是**代理指标**，不等于真实爆款率；但它们能被机器复算、能防止"凭感觉给 19 分"。

用法：
  brief.py --content examples/19-spf-sunscreen.content.json
  brief.py --dir examples                 # 全库分布，校准阈值
  brief.py --title "彩虹其实是圆的" --hint  # 只评一个还没做的选题
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import statistics

from shortvideo.textutil import numbers_in  # noqa: E402

PAIN = {
    # 观众身边的东西：出现在标题里说明"这事跟我有关"
    "日常物品": ("微波炉", "二维码", "手机", "电脑", "冰箱", "空调", "耳机", "咖啡",
                 "防晒", "眼镜", "馒头", "镜子", "冰", "身份证", "钢琴", "舷窗", "飞机"),
    "钱": ("元", "块", "价格", "贵", "便宜", "花钱", "省钱", "时薪", "月薪", "工资", "智商税", "白买"),
    "时间": ("小时", "分钟", "秒", "天", "年", "一辈子", "熬夜", "加班", "通勤"),
    "健康": ("病", "癌", "伤", "毒", "致癌", "发炎", "过敏", "近视", "防晒", "晒黑", "失眠", "困"),
    "身体": ("眼睛", "皮肤", "牙", "胃", "腰", "颈椎", "手", "脚", "头", "大脑", "血"),
    "日常误会": ("其实", "原来", "并不是", "根本不是", "一直以为", "错了", "假的", "骗"),
}
CONTRAST_WORDS = ("只", "才", "竟然", "居然", "不是", "反而", "却", "别", "其实", "根本不是", "差")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _text_of(content):
    parts = [str(content.get("publish_title") or ""), str(content.get("title") or "")]
    for s in content.get("shots") or []:
        parts.append(str(s.get("narration") or ""))
    cover = content.get("cover") or {}
    for x in cover.get("headline") or []:
        parts.append(str(x))
    return " ".join(parts)


def score(content):
    text = _text_of(content)
    title = str(content.get("publish_title") or content.get("title") or "")

    # 1) 反常识度：数字对比 / 反转结构
    # 中文数字也要算：「三扇门」「两千倍」都是数字，只认 \d 会漏掉一半选题
    title_nums = numbers_in(title)
    has_num = bool(title_nums) or len(numbers_in(text)) >= 3
    has_flip = any(w in title for w in CONTRAST_WORDS) or any(
        w in title for w in ("为什么", "凭什么"))
    surprise = 5.0 if (has_num and has_flip) else 3.5 if (has_num or has_flip) else 2.0

    # 2) 痛点：命中几类
    hits = [k for k, words in PAIN.items() if any(w in text for w in words)]
    pain = {0: 1.5, 1: 3.0, 2: 4.0}.get(len(hits), 5.0)

    # 3) 可验证：kernel + refs
    has_kernel = bool(str(content.get("kernel") or "").strip())
    has_refs = bool(content.get("refs"))
    verify = 5.0 if (has_kernel and has_refs) else 3.5 if (has_kernel or has_refs) else 1.5

    # 4) 可视化：deck_hint 覆盖率
    shots = content.get("shots") or []
    hinted = sum(1 for s in shots if str(s.get("deck_hint") or "").strip())
    visual = 5.0 * (hinted / len(shots)) if shots else 0.0

    total = surprise + pain + verify + visual
    # 两个维度必须分开看：「选题好不好」和「文档齐不齐」是两件事。
    # 混成一个分数会得出荒谬结论——好选题因为没写 kernel 被判"不该做"。
    topic = surprise + pain
    delivery = verify + visual
    return {"total": round(total, 1), "topic": round(topic, 1), "delivery": round(delivery, 1),
            "surprise": surprise, "pain": round(pain, 1),
            "verify": verify, "visual": round(visual, 1), "pain_hits": hits,
            "hinted": f"{hinted}/{len(shots)}"}


def show(label, r):
    bar = lambda v: "●" * int(round(v)) + "○" * (5 - int(round(v)))
    print(f"  {label[:30]:<32}选题{r['topic']:>5.1f}/10  交付{r['delivery']:>5.1f}/10  "
          f"反{r['surprise']:>4.1f}{bar(r['surprise'])}"
          f" 痛{r['pain']:>4.1f}{bar(r['pain'])}"
          f" 验{r['verify']:>4.1f}{bar(r['verify'])}"
          f" 画{r['visual']:>4.1f}{bar(r['visual'])}")


def main():
    ap = argparse.ArgumentParser(description="选题简报评分（四维，≥15 才做）")
    ap.add_argument("--content")
    ap.add_argument("--dir")
    ap.add_argument("--title")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.content:
        c = json.load(open(args.content, encoding="utf-8"))
        r = score(c)
        if args.json:
            print(json.dumps(r, ensure_ascii=False, indent=2))
        else:
            show(os.path.basename(args.content).replace(".content.json", ""), r)
            print(f"\n  痛点命中：{', '.join(r['pain_hits']) or '无'}｜画面意图 {r['hinted']}")
            print("  选题分 %.1f/10 %s｜交付分 %.1f/10 %s" % (
                r["topic"], "✅" if r["topic"] >= 6.5 else "⚠️ 换角度",
                r["delivery"], "✅" if r["delivery"] >= 6.0 else "⚠️ 补 kernel/refs/deck_hint"))
        return 0

    if args.title:
        r = score({"publish_title": args.title})
        show(args.title, r)
        return 0

    paths = sorted(glob.glob(os.path.join(args.dir or "examples", "*.content.json")))
    if not paths:
        print("[brief] 没找到 content.json")
        return 1
    rows = [(os.path.basename(p).replace(".content.json", ""),
             score(json.load(open(p, encoding="utf-8")))) for p in paths]
    if args.json:
        print(json.dumps(dict(rows), ensure_ascii=False, indent=2))
        return 0

    print(f"■ 选题简报评分（{len(rows)} 条，满分 20，≥15 才做）")
    for label, r in sorted(rows, key=lambda x: -x[1]["total"]):
        show(label, r)
    topics = [r["topic"] for _, r in rows]
    deliv = [r["delivery"] for _, r in rows]
    weak_topic = [l for l, r in rows if r["topic"] < 6.5]
    weak_deliv = [l for l, r in rows if r["delivery"] < 6.0]
    print("\n  选题分 中位 %.1f｜低于 6.5 的 %d 条" % (statistics.median(topics), len(weak_topic)))
    print("  交付分 中位 %.1f｜低于 6.0 的 %d 条（缺 kernel/refs/deck_hint）"
          % (statistics.median(deliv), len(weak_deliv)))
    if weak_deliv:
        print("  → 补文档：kernel（算式）+ refs（依据）+ 每镜 deck_hint，不用重渲"
              "：" + ", ".join(weak_deliv[:8]) + ("…" if len(weak_deliv) > 8 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
