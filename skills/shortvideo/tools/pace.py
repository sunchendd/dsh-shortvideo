"""完播友好度体检：逐镜算"会不会让人划走"，并给出可改的地方。

为什么需要：完播率是小红书进下一个流量池的硬指标，而"这个镜头太长/信息太挤/画面太空"
三件事都能靠数字看出来——不用等发布后猜。

每个镜头四个指标：
  时长     镜头持续秒数（>9s 是明显风险，playbook 的甜区是 2~8s）
  语速     音节/秒（>5.4 偏挤，观众跟不上）
  画面墨量  内容区像素占比（<2% 说明这一镜视觉很薄）
  变化量    相邻抽帧的平均像素差（<1.5 说明画面几乎静止，容易被以为卡了）

用法：
  python pace.py --dir examples            # 全部条目
  python pace.py --dir examples --top 8    # 只看最危险的 N 个镜头
"""
import argparse
import glob
import json
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qa  # noqa: E402
from shortvideo import textutil  # noqa: E402

# 阈值来源：docs/xhs-playbook.md §2（单镜 ≤9s；正文 4.0~5.2 音节每秒；钩子 6.0~6.6 更快）
DUR_WARN, DUR_BAD = 9.0, 11.0
RATE_WARN, RATE_BAD = 5.2, 5.8          # 正文
HOOK_WARN, HOOK_BAD = 6.6, 7.2          # 钩子（钩子本来就该更快）
INK_WARN = 0.018                        # 内容区墨量
STATIC_WARN, STATIC_BAD = 3.5, 4.5      # 连续静止秒数
PAIR_LONG = 8.6                         # 相邻两镜都 ≥ 这个秒数算"连排长镜头"


def shot_wavs(build_dir):
    """按 00_hook.wav / 01_rule.wav 的顺序取每镜的实配音文件。"""
    if not os.path.isdir(build_dir):
        return []
    out = []
    for p in sorted(glob.glob(os.path.join(build_dir, "[0-9][0-9]_*.wav"))):
        out.append(p)
    return out


def wav_dur(path):
    try:
        import wave
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001
        return 0.0


def static_run(frames, fps):
    """最长"画面完全不动"的连续秒数。

    我们的视觉语言是"元素入场后停住"，所以静止本身不犯法——
    犯法的是**静止太久**（观众会以为卡了）。所以只看最长静止段。
    """
    from PIL import ImageChops
    if len(frames) < 3:
        return 0.0
    small = [Image.open(f).convert("L").resize((90, 160), Image.BILINEAR) for f in frames]
    run = best = 0
    for a, b in zip(small, small[1:]):
        hist = ImageChops.difference(a, b).histogram()
        moved = sum(hist[3:])          # 差异 >2 的像素数
        if moved < 40:                 # 几乎没变
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best / fps


def analyse(slug, content_path, video_path, fps=2.0):
    cfg = json.load(open(content_path, encoding="utf-8"))
    shots = cfg.get("shots") or []
    deck = cfg.get("deck") or {}
    build = os.path.join(os.path.dirname(video_path), "build")
    wavs = shot_wavs(build)
    frames, times = ([], [])
    if os.path.exists(video_path):
        frames, times = qa.grab_frames(video_path, fps, f"/tmp/pace_{slug}")

    rows, t0 = [], 0.0
    for i, shot in enumerate(shots):
        sid = shot.get("id") or str(i)
        adur = wav_dur(wavs[i]) if i < len(wavs) else 0.0
        syl = textutil.syllables(shot.get("narration", ""))
        if not adur:
            # 没出过片：用音节数估一个
            adur = syl / 4.2
        dur = max(float(shot.get("min_dur") or 0), adur + 0.15 + 0.55)
        rate = syl / adur if adur else 0.0
        seg = [(f, t) for f, t in zip(frames, times) if t0 <= t < t0 + dur]
        ink = sum(qa._stats(Image.open(f)).get("body", 0) for f, _ in seg) / max(1, len(seg))
        static_s = static_run([f for f, _ in seg], fps)
        kind = (deck.get(sid) or {}).get("kind", "big") if isinstance(deck.get(sid), dict) else "big"

        is_hook = (i == 0) or sid == "hook"
        rw, rb = (HOOK_WARN, HOOK_BAD) if is_hook else (RATE_WARN, RATE_BAD)
        risk, why = 0, []
        if dur > DUR_BAD:
            risk += 3
            why.append(f"镜头 {dur:.1f}s 太长")
        elif dur > DUR_WARN:
            risk += 2
            why.append(f"镜头 {dur:.1f}s 偏长")
        if static_s > STATIC_BAD:
            risk += 2
            why.append(f"画面静止 {static_s:.1f}s 太久")
        elif static_s > STATIC_WARN:
            risk += 1
            why.append(f"画面静止 {static_s:.1f}s 偏久")
        if rate > rb:
            risk += 2
            why.append(f"{rate:.1f} 音节/秒 太挤")
        elif rate > rw:
            risk += 1
            why.append(f"{rate:.1f} 音节/秒 偏快")
        if ink < INK_WARN:
            risk += 1
            why.append(f"画面墨量 {ink * 100:.1f}% 偏薄")
        rows.append({"slug": slug, "shot": sid, "start": round(t0, 1),
                     "dur": round(dur, 1), "rate": round(rate, 2),
                     "ink": round(ink * 100, 1), "static_s": round(static_s, 1),
                     "kind": kind, "risk": risk, "why": why})
        t0 += dur

    # 连排长镜：单镜不超 9s 也可能"连着两个 8s+",体感就是一段长时间的慢段落
    for a, b in zip(rows, rows[1:]):
        if a["dur"] >= PAIR_LONG and b["dur"] >= PAIR_LONG:
            msg = f"与相邻镜连排长镜头（{a['dur']:.1f}s+{b['dur']:.1f}s）"
            for r in (a, b):
                r["risk"] += 1
                if msg not in r["why"]:
                    r["why"].append(msg)
    return rows


def main():
    ap = argparse.ArgumentParser(description="完播友好度体检（逐镜）")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--top", type=int, default=10, help="只列出最危险的 N 个镜头")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--order", action="store_true", help="给出建议发布顺序（A 级先发）")
    args = ap.parse_args()

    allrows = []
    for cp in sorted(glob.glob(os.path.join(args.dir, "*.content.json"))):
        slug = os.path.basename(cp).replace(".content.json", "")
        vp = os.path.join(args.dir, "out", slug, f"{slug}.mp4")
        allrows += analyse(slug, cp, vp)

    if args.json:
        print(json.dumps(allrows, ensure_ascii=False, indent=2))
        return

    by_video = {}
    for r in allrows:
        by_video.setdefault(r["slug"], []).append(r)

    print(f"\n■ 完播友好度（{len(by_video)} 条 / {len(allrows)} 个镜头）\n")
    print(f"  {'条目':<24}{'镜头':>4}{'最长':>7}{'最挤':>7}{'最薄':>7}{'最静':>7}{'风险':>5}  评级")
    for slug, rs in by_video.items():
        longest = max(r["dur"] for r in rs)
        densest = max(r["rate"] for r in rs)
        thinnest = min(r["ink"] for r in rs)
        still = max(r["static_s"] for r in rs)
        risk = sum(r["risk"] for r in rs)
        grade = "A 稳" if risk <= 2 else ("B 可" if risk <= 5 else "C 险")
        print(f"  {slug:<24}{len(rs):>4}{longest:>6.1f}s{densest:>7.2f}{thinnest:>6.1f}%"
              f"{still:>6.1f}s{risk:>5}  {grade}")

    bad = sorted([r for r in allrows if r["why"]], key=lambda r: -r["risk"])[:args.top]
    if bad:
        print(f"\n  最该改的 {len(bad)} 个镜头：")
        for r in bad:
            print(f"    {r['slug']}·{r['shot']:<8}{r['start']:>5.1f}s 起 {r['dur']:>4.1f}s "
                  f"[{r['kind']}] {'；'.join(r['why'])}")
    else:
        print("\n  ✅ 没有触发任何风险项")

    if args.order:
        # 新账号先发 A 级：完播率高的片子更容易把账号推进下一个流量池，
        # 账号权重起来后再发 B 级，避免开局就被判定"完播差"
        ranked = sorted(by_video.items(), key=lambda kv: sum(r["risk"] for r in kv[1]))
        n_a = sum(1 for _, rs in ranked if sum(r["risk"] for r in rs) <= 2)
        print(f"\n  建议发布顺序（风险低的先发，先把账号权重做起来；其中 {n_a} 条 A 级适合开局）：")
        for i, (slug, rs) in enumerate(ranked, 1):
            risk = sum(r["risk"] for r in rs)
            tag = "A 稳" if risk <= 2 else ("B 可" if risk <= 5 else "C 险")
            print(f"    {i:>2}. {slug:<26} 风险 {risk}  {tag}")
    print()


if __name__ == "__main__":
    main()
