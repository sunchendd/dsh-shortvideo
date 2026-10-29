"""发布前总检：一条命令跑完所有门禁，给出 go / no-go。

为什么需要：质检工具已经有 5 个（score / lint / qa / pace / coveraudit），
每次手工跑一遍容易漏项。这个脚本把它们串起来，输出一张表：
"这条片子现在能不能发"。任何 error 级问题 → NO-GO。

用法：
  python checklist.py --dir examples              # 全部条目
  python checklist.py --dir examples --only 13,14 # 只看某几条
  python checklist.py --dir examples --strict     # 警告也算 NO-GO
"""
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

sys.path.insert(0, HERE)


def run(args, timeout=900):
    r = subprocess.run([PY] + args, capture_output=True, text=True, timeout=timeout)
    return r.stdout + r.stderr


def check_one(content_path, out_dir, strict=False):
    slug = os.path.basename(content_path).replace(".content.json", "")
    mp4 = os.path.join(out_dir, slug, f"{slug}.mp4")
    row = {"slug": slug, "gates": {}, "problems": []}

    # 1) 爆款清单（score.py）：<90 = 不合格，<95 = 警告
    out = run([os.path.join(HERE, "score.py"), "--content", content_path])
    score = 0.0
    for line in out.splitlines():
        if "爆款潜力" in line and "/100" in line:
            try:
                score = float(line.split("爆款潜力")[1].split("/")[0].strip())
            except (IndexError, ValueError):
                pass
            break
    row["gates"]["清单"] = score
    if score < 90:
        row["problems"].append(f"清单完成度 {score} < 90")
    elif score < 95 and strict:
        row["problems"].append(f"清单完成度 {score} < 95（严格模式）")

    # 2) 脚本体检（lint，走 --preview，不重新编码）
    out = run([os.path.join(HERE, "make.py"), "--content", content_path, "--topic", "deck",
               "--out", os.path.join("/tmp", f"_ck_{slug}.mp4"), "--preview", "--no-cover"])
    errs = [l.split("] ", 1)[-1] for l in out.splitlines() if "[lint] [x]" in l]
    warns = [l.split("] ", 1)[-1] for l in out.splitlines() if "[lint] [!]" in l]
    row["gates"]["体检"] = "err" if errs else ("warn" if warns else "ok")
    row["problems"] += [f"体检错误：{e}" for e in errs]
    if strict:
        row["problems"] += [f"体检警告：{w}" for w in warns]

    # 3) 画面体检（qa.py）
    if os.path.exists(mp4):
        out = run([os.path.join(HERE, "qa.py"), "--video", mp4, "--fps", "1", "--json"])
        try:
            qa = json.loads(out)[0]
            flags = qa.get("flags") or []
        except (json.JSONDecodeError, IndexError):
            flags = ["画面体检解析失败"]
        row["gates"]["画面"] = "err" if flags else "ok"
        row["problems"] += [f"画面：{f}" for f in flags]
    else:
        row["gates"]["画面"] = "—"
        row["problems"].append("还没有成片（先 render）")

    # 4) 完播友好度（pace.py）——单条跑，避免整批开销
    try:
        import pace
        rows = pace.analyse(slug, content_path, mp4)
        risk = sum(r["risk"] for r in rows)
        row["gates"]["节奏"] = risk
        worst = sorted([r for r in rows if r["why"]], key=lambda r: -r["risk"])[:2]
        if risk > 5:
            row["problems"] += [f"节奏风险 {risk}：{r['shot']} {'；'.join(r['why'])}" for r in worst]
        elif risk > 2 and strict:
            row["problems"] += [f"节奏风险 {risk}（严格模式）"]
    except Exception as e:  # noqa: BLE001
        row["gates"]["节奏"] = "—"
        row["problems"].append(f"节奏体检失败：{e}")

    row["ok"] = not row["problems"]
    return row


def main():
    ap = argparse.ArgumentParser(description="发布前总检（go / no-go）")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--only", default=None, help="逗号分隔的序号前缀，如 13,14")
    ap.add_argument("--strict", action="store_true", help="警告也算 NO-GO")
    ap.add_argument("--publish-order", action="store_true",
                    help="额外输出建议投放顺序（按节奏风险从低到高）")
    args = ap.parse_args()

    only = [x.strip() for x in (args.only or "").split(",") if x.strip()]
    paths = sorted(glob.glob(os.path.join(args.dir, "*.content.json")))
    if only:
        paths = [p for p in paths if any(os.path.basename(p).startswith(o) for o in only)]
    if not paths:
        print("[checklist] 没有匹配的 content.json")
        return

    out_dir = os.path.join(args.dir, "out")
    rows = [check_one(p, out_dir, args.strict) for p in paths]

    print(f"\n■ 发布前总检（{len(rows)} 条{'，严格模式' if args.strict else ''}）\n")
    print(f"  {'条目':<26}{'清单':>7}{'体检':>7}{'画面':>7}{'节奏':>7}   结论")
    for r in rows:
        g = r["gates"]
        verdict = "✅ GO" if r["ok"] else "⛔ NO-GO"
        print(f"  {r['slug']:<26}{g.get('清单', '—'):>7}{str(g.get('体检', '—')):>7}"
              f"{str(g.get('画面', '—')):>7}{str(g.get('节奏', '—')):>7}   {verdict}")
        for p in r["problems"]:
            print(f"      - {p}")

    ok = sum(1 for r in rows if r["ok"])
    print(f"\n  {ok}/{len(rows)} 条可以发")

    if args.publish_order:
        # 新号先发"节奏最稳"的：开局几条的完播率决定账号被判定成什么档位，
        # 等权重起来再发信息更密、节奏更重的片子
        def risk_of(r):
            v = r["gates"].get("节奏")
            return v if isinstance(v, (int, float)) else 99
        ranked = sorted([r for r in rows if r["ok"]], key=risk_of)
        print("\n  建议投放顺序（节奏风险低的先发，先把账号权重做起来）：")
        for i, r in enumerate(ranked, 1):
            print(f"    {i:>2}. {r['slug']:<26} 节奏风险 {risk_of(r)}")
        held = [r["slug"] for r in rows if not r["ok"]]
        if held:
            print(f"  暂缓：{', '.join(held)}")
    print()
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
