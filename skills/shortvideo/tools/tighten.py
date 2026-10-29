"""收紧镜头时长：让 min_dur 跟着实际配音走，而不是手工写死。

为什么需要：手工设的 min_dur 常常越往后面越长（钩子 6.4s、收尾 9.0s），
节奏心电图一眼就能看出"前快后慢"——而小红书观众最先划走的就是拖长的结尾。
这里按 `实际配音时长 + 余量` 重算每一镜，并给一个上限，避免长镜拖节奏。

  min_dur = clamp(adur + pad, floor, cap)   默认 pad=1.5s, floor=4.5s, cap=8.0s

用法：
  python tighten.py --dir examples --dry-run
  python tighten.py --dir examples --cap 8 --pad 1.5
"""
import argparse
import glob
import json
import os
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def wav_dur(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001
        return 0.0


# 动效密集的 kind 需要更多余量：bars/timeline/flow 要一条条长出来，
# 余量不够会"画面还没长完就切走"。big/cards 只是文字入场，1.8s 够。
# 注意：渲染里的动画进度是"按比例"走的（p = t/dur），所以缩短镜头不会让动画演不完；
# 余量的真正作用是给"最后一个元素出现后"留阅读时间。这里只给密集版式多 0.3s，
# 给多了会让所有镜头都顶到 cap，反而让全片节奏变平。
# 分档依据"最后一帧之后还需要多久才读得完"：
#   bars/timeline/flow —— 元素逐个长出来，最后一项出现得晚，留 2.1~2.2s
#   stat/formula/digits —— 单元素场景（数字数完就到齐了），1.6s 足够，
#                          留多了会让相邻两镜都顶到 cap，形成"连排长镜"的闷段落
KIND_PAD = {"bars": 2.1, "timeline": 2.1, "flow": 2.0, "bits": 2.0,
            "compare": 1.9, "stat": 1.6, "formula": 1.6, "digits": 1.6}

# 短版（format=short）的留白要整体砍半：短版的卖点就是"不浪费一秒"，
# 用长版的 pad 会把 3.1s 的配音撑成 5.1s 的镜头（净留白 2s），
# 实测短版照长版 pad 收，4 镜里有 2 镜在空转。这里按比例压到 45%。
SHORT_PAD_SCALE = 0.45


def tighten(path, pad=1.5, floor=4.5, cap=8.0, dry_run=False):
    cfg = json.load(open(path, encoding="utf-8"))
    slug = os.path.basename(path).replace(".content.json", "")
    build = os.path.join(os.path.dirname(path), "out", slug, "build")
    changed = []
    deck = cfg.get("deck") or {}
    is_short = str(cfg.get("format") or "").lower() in ("short", "15s", "quick")
    for i, shot in enumerate(cfg.get("shots") or []):
        sid = shot.get("id") or str(i)
        adur = wav_dur(os.path.join(build, f"{i:02d}_{sid}.wav"))
        if not adur:
            # 静默跳过最坑：--out 写成 out/<slug>.mp4 时 build 不在 out/<slug>/ 下，
            # 这里读不到 wav 就 continue，用户只会看到"调整 0 个镜头"而不知为何
            if not dry_run and i == 0:
                print(f"  [!] {os.path.basename(path)}: 读不到 {build}/{i:02d}_{sid}.wav，"
                      f"未收紧（确认 --out 是否为 out/<slug>/<slug>.mp4）")
            continue
        kind = str((deck.get(sid) or {}).get("kind") or "big")
        my_pad = KIND_PAD.get(kind, pad)
        if is_short:
            my_pad = round(my_pad * SHORT_PAD_SCALE, 2)
        new = round(min(cap, max(floor, adur + my_pad)), 1)
        old = float(shot.get("min_dur") or 0)
        # 阈值要对小数位取整再比：8.6-8.3 在浮点里是 0.2999999999999998，
        # 会被判成"变化太小"而跳过，于是留白一直不达标却没有任何提示
        if round(abs(new - old), 2) >= 0.3:
            shot["min_dur"] = new
            changed.append((sid, old, new))
    if changed and not dry_run:
        json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return changed


def main():
    ap = argparse.ArgumentParser(description="按实际配音收紧镜头时长")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--pad", type=float, default=1.5)
    ap.add_argument("--floor", type=float, default=4.5)
    ap.add_argument("--cap", type=float, default=8.0)
    ap.add_argument("--only", default=None, help="只处理名字以此开头的条目，如 short-")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    total = 0
    cps = sorted(glob.glob(os.path.join(args.dir, "*.content.json")))
    if getattr(args, "only", None):
        cps = [c for c in cps if os.path.basename(c).startswith(args.only)]
    for cp in cps:
        slug = os.path.basename(cp).replace(".content.json", "")
        ch = tighten(cp, args.pad, args.floor, args.cap, args.dry_run)
        if ch:
            total += len(ch)
            old_sum = sum(o for _, o, _ in ch)
            new_sum = sum(n for _, _, n in ch)
            # 符号必须能一眼看懂：变长写「+」、变短写「−」，原来一律写"共省 -0.4s"会看反
            diff = new_sum - old_sum
            verb = "共加" if diff > 0 else "共省"
            print(f"  {slug:<26}" + "  ".join(f"{s} {o:.1f}→{n:.1f}" for s, o, n in ch)
                  + f"   （这几镜{verb} {abs(diff):.1f}s）")
    print(f"\n{'[dry-run] 将' if args.dry_run else '[ok] 已'}调整 {total} 个镜头的 min_dur\n")


if __name__ == "__main__":
    main()
