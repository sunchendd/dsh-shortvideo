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
        # 列里显示 A/B/C 而不是裸数字：原来显示 risk 数（如 2），很容易被读成"2 分"
        grade = "A" if risk <= 2 else ("B" if risk <= 5 else "C")
        row["gates"]["节奏"] = f"{grade}({risk})"
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


def add_graphic_gate(rows, content_paths):
    """图文事实链：一张图标题层的数字必须在口播里出现过（推导值放正文不算违规）。"""
    try:
        sys.path.insert(0, HERE)
        import graphicaudit
    except Exception:  # noqa: BLE001
        return
    by_slug = {os.path.basename(p).replace(".content.json", ""): p for p in content_paths}
    for row in rows:
        spec = os.path.join("examples", f"oneimage-{row['slug']}.json")
        content = by_slug.get(row["slug"])
        if not (content and os.path.exists(spec)):
            continue
        try:
            rep = graphicaudit.audit(spec, content)
        except Exception:  # noqa: BLE001
            continue
        if rep["headline_stray"]:
            row["problems"].append(
                f"图文：一张图标题层 {rep['headline_stray']} 口播里没有")
            row["gates"]["图文"] = "warn"
        else:
            row["gates"]["图文"] = "ok"


def add_cover_gate(rows, out_dir):
    """封面体检：按 240px 信息流缩略图量对比度/字号/边距/密度（阈值靠实测校准）。"""
    try:
        sys.path.insert(0, HERE)
        import coveraudit
    except Exception:  # noqa: BLE001
        return
    for row in rows:
        d = os.path.join(out_dir, row["slug"])
        path = next((os.path.join(d, n) for n in ("cover_short.png", "cover_editorial.png", "cover_blueprint.png",
                            "cover_cinematic.png")
                     if os.path.exists(os.path.join(d, n))), None)
        if not path:
            row["gates"]["封面"] = "—"
            continue
        try:
            rep = coveraudit.audit(path)
        except Exception:  # noqa: BLE001
            row["gates"]["封面"] = "—"
            continue
        if rep["flags"]:
            row["gates"]["封面"] = "warn"
            row["problems"].append("封面：" + "；".join(rep["flags"]))
        else:
            row["gates"]["封面"] = "ok"


def add_asset_gate(rows, out_dir):
    """发布齐套：视频 + 封面 + 一张图 + 发布标题，四件缺一不可。

    实测：24 条里只有 2 条真正齐套——视频和封面都在，但 21 条没有 summary.png、
    3 条短版连 oneimage spec 都没有。这种缺失不影响成片质量，但发不出去。
    """
    for row in rows:
        slug = row["slug"]
        d = os.path.join(out_dir, slug)
        missing = []
        if not os.path.exists(os.path.join(d, f"{slug}.mp4")):
            missing.append("视频")
        if not (os.path.exists(os.path.join(d, "cover_short.png"))
                or os.path.exists(os.path.join(d, "cover_editorial.png"))):
            missing.append("封面")
        if not os.path.exists(os.path.join(d, "summary.png")):
            missing.append("一张图")
        row["gates"]["齐套"] = "warn" if missing else "ok"
        if missing:
            row["problems"].append(f"发布物料缺：{'/'.join(missing)}")


def add_voice_gate(rows, content_paths, strict=False):
    """配音门：情绪是否真的生效（mood_tuning 白写 / 全片扁平都算不合格）。"""
    try:
        sys.path.insert(0, HERE)
        import voiceaudit
    except Exception:  # noqa: BLE001
        return
    by_slug = {os.path.basename(p).replace(".content.json", ""): p for p in content_paths}
    for row in rows:
        path = by_slug.get(row["slug"])
        if not path:
            continue
        try:
            rep = voiceaudit.audit_one(path)
        except Exception as e:  # noqa: BLE001
            row["problems"].append(f"配音审计失败：{e}")
            continue
        issues = []
        if rep["dead_tuning"]:
            issues.append(f"mood_tuning 白写（{', '.join(rep['dead_tuning'])}）")
        if all(r["mood"] == "-" for r in rep["shots"]):
            issues.append("全片扁平（没有任何镜头带情绪）")
        row["gates"]["配音"] = "warn" if issues else "ok"
        if issues:
            row["problems"] += [f"配音：{i}" for i in issues]
        if issues and strict:
            row["ok"] = False


def add_loudness_gate(rows, out_dir, tol=1.2):
    """第 5 道门禁：横向响度。单条爆不爆由 loudaudit 之外的检查管，
    这里只问"和同批其他条比，这条是不是明显偏轻/偏响"。"""
    try:
        import loudaudit
    except ImportError:
        return
    stats = {}
    for r in rows:
        build = os.path.join(out_dir, r["slug"], "build")
        wav = os.path.join(build, "audio.wav")
        if not os.path.exists(wav):
            continue
        st = loudaudit.stats_of(wav, os.path.join(build, "audio_bgm.wav"))
        if st:
            stats[r["slug"]] = st
    if len(stats) < 3:          # 条数太少，中位数没有意义（--only 单条时走这里）
        for r in rows:
            st = stats.get(r["slug"])
            # 必须取整：原始值是 float，直接塞进 {:>7} 会撑爆列宽、把两列粘在一起
            r["gates"]["响度"] = round(st["voice"], 1) if st else "—"
        return
    vals = sorted(v["voice"] for v in stats.values())
    med = vals[len(vals) // 2]
    for r in rows:
        st = stats.get(r["slug"])
        if not st:
            r["gates"]["响度"] = "—"
            continue
        dv = st["voice"] - med
        r["gates"]["响度"] = round(st["voice"], 1)
        if abs(dv) > tol:
            r["problems"].append(f"人声比同批中位{'高' if dv > 0 else '低'} {abs(dv):.1f}dB"
                                 f"（{st['voice']:.1f} vs {med:.1f}）")
        if st["peak"] > -1.0:
            r["problems"].append(f"峰值 {st['peak']:.1f}dB 偏高")
        r["ok"] = not r["problems"]


def main():
    ap = argparse.ArgumentParser(description="发布前总检（go / no-go）")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--only", default=None, help="逗号分隔的序号前缀，如 13,14")
    ap.add_argument("--strict", action="store_true", help="警告也算 NO-GO")
    ap.add_argument("--json", action="store_true", help="机器可读输出（便于接 CI）")
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

    add_loudness_gate(rows, out_dir)
    add_voice_gate(rows, paths, args.strict)
    add_asset_gate(rows, out_dir)
    add_graphic_gate(rows, paths)
    add_cover_gate(rows, out_dir)

    if args.json:
        payload = {"total": len(rows), "go": sum(1 for r in rows if r["ok"]),
                   "rows": rows}
        if args.publish_order:
            payload["order"] = [r["slug"] for r in
                                sorted([r for r in rows if r["ok"]],
                                       key=lambda r: (r["gates"].get("节奏")
                                                      if isinstance(r["gates"].get("节奏"), (int, float))
                                                      else 99))]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["go"] == payload["total"] else 1

    print(f"\n■ 发布前总检（{len(rows)} 条{'，严格模式' if args.strict else ''}）\n")
    print(f"  {'条目':<24}{'清单':>7}{'体检':>7}{'画面':>7}{'节奏':>7}{'响度':>7}{'封面':>6}   结论")
    for r in rows:
        g = r["gates"]
        verdict = "✅ GO" if r["ok"] else "⛔ NO-GO"
        print(f"  {r['slug']:<24}{str(g.get('清单', '—')):>7}{str(g.get('体检', '—')):>7}"
              f"{str(g.get('画面', '—')):>7}{str(g.get('节奏', '—')):>7}"
              f"{str(g.get('响度', '—')):>7}{str(g.get('封面', '—')):>6}   {verdict}")
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
