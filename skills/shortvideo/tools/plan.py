#!/usr/bin/env python3
"""投放排期：把整个片库排成一份可执行的发布日历。

排序逻辑（不是随便排）：
  1. **同题不连发**：相邻两条尽量不同学科，避免"这账号只讲光学"的印象
  2. **强弱交替**：开头放钩子最强、时长最短的（新号前几条决定推不推），
     后面穿插深度长版养收藏
  3. **长短搭配**：短版（≤22s）负责完播和涨粉，长版（30~46s）负责收藏，
     同一周里 2 短 1 长或 2 长 1 短
  4. **同题长短不隔周**：同一选题的长版和短版放同一周内（互相导流）

用法：
  plan.py --dir examples                     # 输出排期表（Markdown）
  plan.py --dir examples --per-week 3        # 每周发 3 条
  plan.py --dir examples --start 2026-11-01  # 指定起发日期
  plan.py --dir examples --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys

SUBJECT_KEYWORDS = [
    ("数学/概率", ("monty", "float", "temperament", "probability", "prime")),
    # "blind" 会子串命中 "colorblind"，把 23 号色盲片吃成物理/光学——用更长的词
    ("视光学", ("myopia", "eye", "colorblind", "色盲", "色觉", "视锥", "石原")),
    ("物理/光学", ("rayleigh", "blindspot", "blind-spot", "盲点", "memory", "gps",
                   # 别写 "screen"：它是 "sunscreen" 的子串，会把 19 号防晒片抢过来
                   # （跟当年 "blind" 命中 "colorblind" 是同一类错）。
                   # 也别写单字 "光"：太宽。用"物理"接栏目名、用双字词接内容。
                   "mercator", "ice", "屏幕", "反光", "亮度", "物理")),
    ("化学/生物", ("caffeine", "adenosine", "enzyme", "baking", "soda", "小苏打", "碱面", "化学")),
    ("声学/听觉", ("loudness", "bass", "acoustic", "sound", "beat", "拍频", "音")),
    ("航空/结构", ("plane", "window", "stress")),
    ("皮肤科学", ("spf", "sunscreen", "skin")),
    ("微波/电磁", ("microwave", "wave", "faraday", "cage", "法拉第", "电磁", "信号")),

    ("编码/信息", ("qr", "utf8", "hex", "checksum", "id-", "code")),
    ("生活经济", ("commute", "wage", "moon", "recession")),
]


# 同题关键词：长短版讲同一件事时要排在同一周（互相导流），
# 否则会出现"短版讲二维码在 W1、长版讲二维码在 W4"这种自相竞争
TOPIC_KEYS = [
    ("二维码", ("qr", "二维码")),
    ("咖啡因", ("caffeine", "咖啡")),
    ("视觉盲点", ("blind", "盲点")),
    ("微波驻波", ("microwave", "微波")),
    ("近视远点", ("myopia", "近视")),
]


def topic_of(slug, content):
    text = (slug + " " + str(content.get("publish_title") or "")).lower()
    for name, keys in TOPIC_KEYS:
        if any(k in text for k in keys):
            return name
    return ""


def subject_of(slug, content):
    """判学科：**slug 优先**，栏目名只做兜底。

    顺序很重要——栏目名（cover.kicker，如"生活物理""反常识概率"）比学科宽，
    放前面会把 22 号法拉第笼（kicker 写"生活物理"）从"微波/电磁"抢到"物理/光学"。
    slug 里通常带具体关键词（faraday / monty / sunscreen），更准。
    """
    low = str(slug or "").lower()
    for name, keys in SUBJECT_KEYWORDS:
        if any(k in low for k in keys):
            return name
    cat = str((content.get("category") or content.get("kicker")
               or (content.get("cover") or {}).get("kicker") or "")).lower()
    for name, keys in SUBJECT_KEYWORDS:
        if any(k in cat for k in keys):
            return name
    return "其他"


def collect(example_dir):
    rows = []
    for path in sorted(glob.glob(os.path.join(example_dir, "*.content.json"))):
        slug = os.path.basename(path).replace(".content.json", "")
        content = json.load(open(path, encoding="utf-8"))
        mp4 = os.path.join(example_dir, "out", slug, f"{slug}.mp4")
        cover = os.path.join(example_dir, "out", slug, "cover_short.png")
        if not os.path.exists(cover):
            cover = os.path.join(example_dir, "out", slug, "cover_f1.png")
        rows.append({
            "slug": slug,
            "title": content.get("publish_title") or content.get("title") or slug,
            "subject": subject_of(slug, content),
            "topic": topic_of(slug, content),
            "short": str(content.get("format") or "").lower() in ("short", "15s", "quick"),
            "secs": round(sum(float(s.get("min_dur") or 0) for s in content.get("shots") or []), 1),
            "has_video": os.path.exists(mp4),
            "cover": os.path.relpath(cover, example_dir) if os.path.exists(cover) else "",
        })
    return [r for r in rows if r["has_video"]]


def schedule(rows, per_week=3, start=None):
    """按"学科轮转 + 长短交替"排：先按学科分组，再一轮一轮取。"""
    groups = {}
    for r in rows:
        groups.setdefault(r["subject"], []).append(r)
    # 组内：短的、钩子强的优先（新号前几条要短平快）
    for g in groups.values():
        g.sort(key=lambda r: (not r["short"], r["secs"]))
    order, keys = [], sorted(groups, key=lambda k: -len(groups[k]))
    while any(groups[k] for k in keys):
        for k in keys:
            if groups[k]:
                order.append(groups[k].pop(0))

    # 长短交替微调：连续 3 条同类长度就换一条
    for i in range(2, len(order)):
        if order[i]["short"] == order[i - 1]["short"] == order[i - 2]["short"]:
            for j in range(i + 1, len(order)):
                if order[j]["short"] != order[i]["short"]:
                    order[i], order[j] = order[j], order[i]
                    break

    # 把"同题对"当成一个整体来填周：必然同周，也不会被挤掉
    units, taken = [], set()
    for item in order:
        if item["slug"] in taken:
            continue
        taken.add(item["slug"])
        unit = [item]
        if item["topic"]:
            mate = next((x for x in order if x["slug"] not in taken
                         and x["topic"] == item["topic"]), None)
            if mate:
                taken.add(mate["slug"])
                unit.append(mate)
        units.append(unit)

    buckets, cur = [], []
    for unit in units:
        if len(cur) + len(unit) > per_week and cur:
            buckets.append(cur)
            cur = []
        cur += unit
    if cur:
        buckets.append(cur)
    # 首尾周不满（只剩 1~2 条）时并入相邻周，避免"发一周停一周"
    while len(buckets) > 1 and len(buckets[0]) < per_week - 1:
        buckets[1] = buckets[0] + buckets[1]
        buckets.pop(0)
    while len(buckets) > 1 and len(buckets[-1]) <= 1:
        buckets[-2] += buckets[-1]
        buckets.pop()

    order = [x for b in buckets for x in b]
    assert len(order) == len(rows), "plan dropped items: %d != %d" % (len(order), len(rows))

    base = start or dt.date.today()
    # 日期必须按"桶"分配，不能按全局下标 // per_week——
    # 桶可能是 2 条（同题对占两格），按全局下标算会把第 3 条挤到下一周，
    # 于是"同题同周"又失效了。
    out = []
    for w, bucket in enumerate(buckets):
        for slot, r in enumerate(bucket):
            day = base + dt.timedelta(days=w * 7 + slot * 2)
            out.append({**r, "date": day.isoformat(), "week": w + 1, "slot": slot + 1})
    return out


def main():
    ap = argparse.ArgumentParser(description="投放排期")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--per-week", type=int, default=3)
    ap.add_argument("--start")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stats", action="store_true", help="只看学科分布与长短配比")
    ap.add_argument("--out", help="把 Markdown 写到文件")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start) if args.start else None
    rows = collect(args.dir)
    if not rows:
        print("[plan] 没有找到已出片的 content.json")
        return 1
    plan = schedule(rows, args.per_week, start)

    if args.stats:
        from collections import Counter
        subj = Counter(r["subject"] for r in plan)
        kinds = Counter("短版" if r["short"] else "长版" for r in plan)
        print(f"■ 片库结构（{len(plan)} 条）")
        for name, n in subj.most_common():
            bar = "█" * n
            print(f"  {name:<10}{n:>3}  {bar}")
        print(f"\n  长短配比：长版 {kinds['长版']} / 短版 {kinds['短版']}")
        weak = [k for k, v in subj.items() if v == 1]
        if weak:
            print(f"  薄弱学科（只有 1 条）：{'、'.join(weak)} → 下一批选题优先补")
        top = subj.most_common(1)[0]
        if top[1] >= len(plan) * 0.25:
            print(f"  [!] {top[0]} 占 {top[1]/len(plan)*100:.0f}%，超过 25%，注意选题多样性")
        return 0

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0

    lines = ["# 投放排期", "",
             f"共 {len(plan)} 条，每周 {args.per_week} 条，"
             f"{len({p['week'] for p in plan})} 周发完（{plan[0]['date']} 起）", "",
             "| 周 | 日期 | 类型 | 学科 | 时长 | 标题 | 封面 |",
             "|---|---|---|---|---|---|---|"]
    for p in plan:
        kind = "短版" if p["short"] else "长版"
        lines.append(f"| W{p['week']} | {p['date']} | {kind} | {p['subject']} | "
                     f"{p['secs']:g}s | {p['title']} | `{p['cover']}` |")
    lines += ["", "## 排期规则", "",
              "1. **同题不连发**：相邻两条尽量不同学科（按学科轮转取件）",
              "2. **强弱交替**：短的、钩子强的排在前面（新号前几条决定推不推）",
              "3. **长短搭配**：短版搏完播，长版养收藏，同周混排",
              "4. 同一选题的长短版放在同一周（互相导流）", ""]
    md = "\n".join(lines)
    if args.out:
        open(args.out, "w", encoding="utf-8").write(md)
        print(f"[plan] 已写入 {args.out}（{len(plan)} 条）")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
