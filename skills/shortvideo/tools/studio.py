"""量产流水线：一个目录里的所有 content.json → 评分 → 出片 → 封面 → 信息图 → 台账。

为什么要有它：单条出片只是"能做"，能按选题批量跑、按分数择优、把结果记成台账，才是"能持续产出"。

用法：
  python studio.py --dir examples                     # 只评分，出报告（秒级）
  python studio.py --dir examples --render            # 评分 + 出片 + 封面 + 信息图
  python studio.py --dir examples --render --from-score 90   # 只渲达标的
  python studio.py --dir examples --only 01,03 --render

产物（都在 --dir 下）：
  studio-report.md     人看：分数 / 时长 / 体积 / 音量 / 缺失项
  studio-report.json   机器看：同上 +
  studio-index.html    封面 A/B 上传页（三版封面并排，点开大图，用它挑封面）
  每条片子落在 <dir>/out/<slug>/：成片 + cover_short + 三流派 + cover_9x16 + cover_frame + summary.png
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PY = sys.executable
GRADE_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}


def _run(cmd, quiet=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and not quiet:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
    return r


def qa_one(video_path, fps=1.0):
    """调 qa.py 做画面体检；失败就返回 None（不阻塞出片流程）。"""
    if not video_path or not os.path.exists(video_path):
        return None
    r = _run([PY, os.path.join(HERE, "qa.py"), "--video", video_path,
              "--fps", str(fps), "--json"])
    try:
        data = json.loads(r.stdout)
        return data[0] if data else None
    except (json.JSONDecodeError, IndexError):
        return None


def score_one(content_path):
    r = _run([PY, os.path.join(HERE, "score.py"), "--content", content_path, "--json"])
    if r.returncode != 0:
        return {"score": 0, "grade": "D 解析失败", "extra": [["错误", 0, 0, r.stderr[-300:]]]}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"score": 0, "grade": "D 解析失败", "extra": []}


def render_one(content_path, out_dir, jobs):
    t0 = time.time()
    slug = os.path.basename(content_path).replace(".content.json", "")
    os.makedirs(out_dir, exist_ok=True)
    mp4 = os.path.join(out_dir, slug + ".mp4")
    r = _run([PY, os.path.join(HERE, "make.py"), "--content", content_path, "--topic", "deck",
              "--out", mp4, "--jobs", str(jobs)], quiet=False)
    ok = os.path.exists(mp4)
    facts = {}
    if ok:
        sc = _run([PY, os.path.join(HERE, "score.py"), "--content", content_path,
                   "--video", mp4, "--json"])
        try:
            facts = json.loads(sc.stdout).get("video") or {}
        except json.JSONDecodeError:
            facts = {}
    # 信息图（有 spec 就出）
    d = os.path.dirname(os.path.abspath(content_path))
    specs = glob.glob(os.path.join(d, "oneimage-" + slug.replace("content.json", "") + "*.json"))
    if not specs:
        specs = [p for p in glob.glob(os.path.join(d, "oneimage-*.json")) if slug in p]
    summary = ""
    if specs:
        summary = os.path.join(out_dir, slug + "-summary.png")
        _run([PY, os.path.join(HERE, "oneimage.py"), "--spec", specs[0], "--out", summary])
        if not os.path.exists(summary):
            summary = ""
    qa = qa_one(mp4 if ok else "") or {}
    return {"slug": slug, "mp4": mp4 if ok else "", "summary": summary,
            "video": facts, "qa": qa, "secs": round(time.time() - t0, 1),
            "log_tail": (r.stdout or "").strip().splitlines()[-6:]}


def write_report(rows, d, rendered):
    md = ["# 生产台账", "",
          f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　条目：{len(rows)}　"
          f"已出片：{sum(1 for r in rows if r['mp4'])}", "",
          "| 条目 | 清单完成度 | 时长 | 体积 | 峰值 | 画面墨量 | 画面体检 | 封面 | 信息图 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        v = r["video"]
        cov = "✅" if r["mp4"] and os.path.exists(os.path.join(os.path.dirname(r["mp4"]),
                                                              "cover_f1.png")) else "—"
        q = r.get("qa") or {}
        qa_flag = "✅" if q and not q.get("flags") else ("⚠" if q else "—")
        md.append(f"| {r['slug']} | {r['score']} {r['grade'][0]} | {v.get('dur', '—')}s | "
                  f"{v.get('size_mb', '—')}MB | {v.get('peak_db', '—')}dB | "
                  f"{(q.get('body_avg') or 0) * 100:.1f}% | {qa_flag} | "
                  f"{cov} | {'✅' if r['summary'] else '—'} |")
    holes = [(r["slug"], e) for r in rows for e in (r.get("extra") or [])
             if not e.get("pass", False)]
    qa_holes = [(r["slug"], f) for r in rows for f in ((r.get("qa") or {}).get("flags") or [])]
    if holes or qa_holes:
        md += ["", "## 待改进", ""]
    for slug, e in holes:
        md.append(f"- **{slug}** · 清单·{e['dim']}：{e['msg']}")
    for slug, f in qa_holes:
        md.append(f"- **{slug}** · 画面：{f}")
    with open(os.path.join(d, "studio-report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    with open(os.path.join(d, "studio-report.json"), "w", encoding="utf-8") as f:
        json.dump({"generated": time.time(), "items": rows}, f, ensure_ascii=False, indent=2)
    return holes + qa_holes


def ab_load(d):
    """读 ab-log.csv，返回 {(slug, cover): {"ctr": float, "impressions": int, ...}}。"""
    path = ab_path(d)
    out = {}
    if not os.path.exists(path):
        return out
    import csv
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug = (row.get("slug") or "").strip()
            cover = (row.get("cover") or "").strip()
            if not slug:
                continue
            try:
                ctr = float(row.get("ctr") or 0)
            except ValueError:
                ctr = 0.0
            out[(slug, cover)] = {"ctr": ctr,
                                  "impressions": int(float(row.get("impressions") or 0))}
    return out


def write_index(rows, d):
    """封面挑选页：三版封面并排，方便上传前对比（也当作 A/B 记录表用）。"""
    ab = ab_load(d)
    cards = []
    for r in rows:
        if not r["mp4"]:
            continue
        od = os.path.dirname(r["mp4"])
        rel = os.path.relpath(od, d)
        covs = [("f1 数字反差", "cover_f1.png"), ("f3 截图红圈", "cover_f3.png"),
                ("f6 遮一半", "cover_f6.png"), ("9x16 竖版", "cover_9x16.png")]
        def _fig(name, fn):
            key = (r["slug"], fn.replace("cover_", "").replace(".png", ""))
            hit = ab.get(key) or {}
            badge = ""
            if hit:
                badge = (f'<b class="ctr">{hit["ctr"]:.1f}%</b>'
                         f'<i>{hit["impressions"]} 曝光</i>')
            return (f'<figure><a href="{rel}/{fn}" target="_blank">'
                    f'<img src="{rel}/{fn}" loading="lazy"></a>'
                    f'<figcaption>{name}{badge}</figcaption></figure>')
        imgs = "".join(_fig(name, fn) for name, fn in covs
                       if os.path.exists(os.path.join(od, fn)))
        cards.append(
            f'<section><h2>{r["slug"]} <small>{r["score"]} · {r["grade"][0]}</small></h2>'
            f'<div class="row">{imgs}</div></section>')
    html = f"""<!doctype html><meta charset="utf-8">
<title>封面挑选 / A/B 台账</title>
<style>
body{{background:#0e1116;color:#eef2ff;font:15px/1.6 -apple-system,"PingFang SC",sans-serif;margin:0;padding:32px}}
h1{{font-size:22px;margin:0 0 4px}} small{{color:#8b9bb4;font-weight:400}}
p.hint{{color:#8b9bb4;margin:0 0 28px}}
section{{margin:0 0 36px;border-top:1px solid #1d2431;padding-top:18px}}
.row{{display:flex;gap:14px;flex-wrap:wrap}}
figure{{margin:0;width:210px}} img{{width:100%;border-radius:10px;display:block;border:1px solid #1d2431}}
.ctr{{color:#FFD400;margin-left:6px}}.ctr+i{{color:#8fa0b8;font-style:normal;font-size:11px;margin-left:4px}}
figcaption{{color:#8b9bb4;font-size:12px;margin-top:6px;text-align:center}}
</style>
<h1>封面挑选 / A/B 台账</h1>
<p class="hint">平台只支持 3:4 / 1:1 / 4:3 封面；同一条最多挂 4 张，10 小时后按点击率保留最优。</p>
{''.join(cards) or '<p>还没有出片，先跑 --render。</p>'}
"""
    path = os.path.join(d, "studio-index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ---------------------------------------------------------------- 封面 A/B 记录

AB_FIELDS = ["slug", "cover", "posted_at", "impressions", "ctr", "likes", "saves",
             "comments", "follows", "note"]


def ab_path(d):
    return os.path.join(d, "ab-log.csv")


def ab_record(d, kv):
    """记一行发布数据：--ab-record slug=01-x,cover=f1,impressions=12000,ctr=9.1,..."""
    row = {k: "" for k in AB_FIELDS}
    for pair in kv.split(","):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        row[k.strip()] = v.strip()
    if not row["slug"]:
        print("[ab] 缺 slug，忽略")
        return
    if not row["posted_at"]:
        row["posted_at"] = time.strftime("%Y-%m-%d %H:%M")
    path = ab_path(d)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(",".join(AB_FIELDS) + "\n")
        f.write(",".join(str(row[k]).replace(",", "，") for k in AB_FIELDS) + "\n")
    print(f"[ab] 已记录 {row['slug']} cover={row['cover']} ctr={row['ctr']} → {path}")


def ab_report(d):
    """按封面版式聚合点击率：积累几十条后就能看出"我们的账号吃哪种封面"。"""
    path = ab_path(d)
    if not os.path.exists(path):
        print("[ab] 还没有 ab-log.csv，先用 --ab-record 记几条")
        return {}
    import csv
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                r["ctr"] = float(r["ctr"])
            except (TypeError, ValueError):
                continue
            rows.append(r)
    if not rows:
        print("[ab] ab-log.csv 里没有可用的 ctr 数据")
        return {}
    by = {}
    for r in rows:
        by.setdefault(r["cover"] or "?", []).append(r)
    print(f"\n■ 封面 A/B（{len(rows)} 条记录）")
    print(f"  {'封面':<6}{'条数':>4}{'平均CTR':>9}{'最高':>8}{'平均互动率':>12}")
    stat = {}
    for cover, rs in sorted(by.items()):
        ctr = sum(x["ctr"] for x in rs) / len(rs)
        eng = []
        for x in rs:
            try:
                imp = float(x["impressions"])
            except (TypeError, ValueError):
                continue
            if imp > 0:
                tot = sum(float(x.get(k) or 0) for k in ("likes", "saves", "comments", "follows"))
                eng.append(tot / imp * 100)
        e = f"{sum(eng) / len(eng):.2f}%" if eng else "—"
        stat[cover] = {"n": len(rs), "ctr": round(ctr, 2), "max": max(x["ctr"] for x in rs),
                       "eng": round(sum(eng) / len(eng), 2) if eng else None}
        print(f"  {cover:<6}{len(rs):>4}{ctr:>8.2f}%{max(x['ctr'] for x in rs):>7.2f}%{e:>12}")
    best = max(stat.items(), key=lambda kv: kv[1]["ctr"])
    print(f"  目前最优版式：{best[0]}（平均 {best[1]['ctr']}%，样本 {best[1]['n']}）"
          f"{'　样本还太少，结论不稳' if best[1]['n'] < 3 else ''}")
    return stat


def main():
    ap = argparse.ArgumentParser(description="批量：评分 → 出片 → 封面 → 信息图 → 台账")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--render", action="store_true", help="真的出片（默认只评分）")
    ap.add_argument("--jobs", type=int, default=3, help="每条片子并行分段数")
    ap.add_argument("--only", default="", help="只跑这些（逗号分隔的 slug 片段）")
    ap.add_argument("--from-score", type=float, default=0, help="低于该分数不出片")
    ap.add_argument("--ab", action="store_true", help="看封面 A/B 汇总")
    ap.add_argument("--ab-record", default=None,
                    help="记一条发布数据：slug=..,cover=f1,impressions=..,ctr=..,likes=..,"
                         "saves=..,comments=..,follows=..")
    ap.add_argument("--skip-existing", action="store_true",
                    help="成片已存在就跳过渲染（只更新台账/封面页）")
    args = ap.parse_args()

    d = os.path.abspath(args.dir)
    if args.ab_record:
        ab_record(d, args.ab_record)
        return
    if args.ab:
        ab_report(d)
        return
    files = sorted(glob.glob(os.path.join(d, "*.content.json")))
    if args.only:
        keys = [k.strip() for k in args.only.split(",") if k.strip()]
        files = [f for f in files if any(k in os.path.basename(f) for k in keys)]
    if not files:
        print(f"[studio] {d} 下没有 *.content.json")
        return

    rows = []
    print(f"[studio] {len(files)} 条，{'出片' if args.render else '仅评分'}模式\n")
    for f in files:
        slug = os.path.basename(f).replace(".content.json", "")
        sc = score_one(f)
        row = {"slug": slug, "score": sc.get("score"), "grade": sc.get("grade", "?"),
               "extra": sc.get("extra") or [], "mp4": "", "summary": "", "video": {}}
        out_dir = os.path.join(d, "out", slug)
        existing = os.path.join(out_dir, slug + ".mp4")
        if args.render and args.skip_existing and os.path.exists(existing):
            v = {}
            try:
                v = json.loads(_run([PY, os.path.join(HERE, "score.py"), "--content", f,
                                     "--video", existing, "--json"]).stdout).get("video") or {}
            except json.JSONDecodeError:
                pass
            summ = os.path.join(out_dir, slug + "-summary.png")
            row.update({"mp4": existing, "video": v, "qa": qa_one(existing) or {},
                        "summary": summ if os.path.exists(summ) else "", "secs": 0})
            print(f"  {slug:<20} {row['score']:>5} {row['grade'][0]}  → 已存在，跳过渲染")
        elif args.render and (sc.get("score") or 0) >= args.from_score:
            got = render_one(f, out_dir, args.jobs)
            row.update(got)
            print(f"  {slug:<20} {row['score']:>5} {row['grade'][0]}  → {got['mp4'] or '失败'} "
                  f"({got['secs']}s)")
        else:
            print(f"  {slug:<20} {row['score']:>5} {row['grade'][0]}")
            if row["extra"]:
                for e in [x for x in row["extra"] if not x.get("pass", False)][:3]:
                    print(f"      · {e['dim']}: {e['msg']}")
        rows.append(row)

    holes = write_report(rows, d, args.render)
    idx = write_index(rows, d)
    total = sum(1 for r in rows if r["mp4"])
    avg = sum(r["score"] or 0 for r in rows) / max(1, len(rows))
    print(f"\n[studio] 平均完成度 {avg:.1f}　出片 {total}/{len(rows)}")
    print(f"[studio] 台账 {os.path.join(d, 'studio-report.md')}")
    print(f"[studio] 封面页 {idx}")


if __name__ == "__main__":
    main()
