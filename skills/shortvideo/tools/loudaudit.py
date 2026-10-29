"""响度一致性审计：16 条片子之间是不是"一样响"。

单条检查（make.py 的 [check] 音量）只能保证一条片子不爆不哑，
但刷到一个账号的连播时，观众感觉到的是**条与条之间的落差**：
一条人声 -22dB、下一条 -25dB，第二条就像"声音突然小了"。

这里做横向比对：
  · peak     真峰值（dBFS），超过 -1.0 有削波风险
  · voice    人声段 RMS（只统计有话音的帧），这是"听起来多响"的主指标
  · gap      静音段（人声停顿）的 RMS，反映 BGM 床有多高
  · range    人声帧 RMS 的 10%~90% 区间，反映"抑扬顿挫"幅度
所有指标都与全库中位数比，偏差超阈值就报警。

用法：
  python loudaudit.py --dir examples
  python loudaudit.py --dir examples --tol 1.0
"""
import argparse
import array
import glob
import json
import math
import os
import wave

VOICE_TH = 300          # 认定"有话音"的采样阈值
GAP_TH = 200            # 认定"静音"的阈值
PEAK_LIMIT = -1.0       # 峰值上限
TOL = 1.2               # 与中位数的允许偏差（dB）


def db(v):
    return 20 * math.log10(max(v, 1e-9))


def gated_voice_db(a, sr):
    """与 tts.speech_levels 同口径：按峰值 -20dB 门限挑帧，再算 RMS。"""
    n = len(a)
    if n < sr // 4:
        return None
    win = max(1, int(sr * 0.02))
    frames = []
    for s in range(0, n - win, win):
        seg = a[s:s + win]
        m = max(abs(min(seg)), abs(max(seg))) / 32768.0
        frames.append((m, math.sqrt(sum((x / 32768.0) ** 2 for x in seg) / len(seg))))
    if not frames:
        return None
    peak = max(f[0] for f in frames)
    gate = peak * (10 ** (-20 / 20.0))
    keep = [r for m, r in frames if m >= gate]
    if not keep:
        return None
    return db(sum(keep) / len(keep))


def stats_of(wav_path, mix_path=None):
    with wave.open(wav_path) as w:
        sr, n = w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    a = array.array("h")
    a.frombytes(raw)
    if len(a) < sr:
        return None
    win = max(1, sr // 20)                 # 50ms 一帧
    peak = max(abs(min(a)), abs(max(a))) / 32768.0
    voice, gap = [], []
    for s in range(0, len(a) - win, win):
        seg = a[s:s + win]
        m = max(abs(min(seg)), abs(max(seg))) / 32768.0
        if m > VOICE_TH / 32768.0:
            rms = math.sqrt(sum((x / 32768.0) ** 2 for x in seg) / len(seg))
            voice.append(db(rms))
        elif m < GAP_TH / 32768.0:
            rms = math.sqrt(sum((x / 32768.0) ** 2 for x in seg) / len(seg))
            gap.append(db(rms))
    if not voice:
        return None
    gv = gated_voice_db(a, sr)
    # 停顿处 BGM 电平要从"混音后"的文件量：build/audio.wav 只有人声，
    # 它的停顿是真静音（量出来 -148dB，会让人以为 BGM 没了）
    gap_mix = None
    if mix_path and os.path.exists(mix_path):
        with wave.open(mix_path) as w2:
            a2 = array.array("h")
            a2.frombytes(w2.readframes(w2.getnframes()))
        if len(a2) >= len(a):
            a2 = a2[:len(a)]
        segs = []
        for s in range(0, min(len(a), len(a2)) - win, win):
            if max(abs(min(a[s:s + win])), abs(max(a[s:s + win]))) < GAP_TH:
                seg = a2[s:s + win]
                segs.append(db(math.sqrt(sum((x / 32768.0) ** 2 for x in seg) / len(seg))))
        if segs and len(segs) >= 8:     # 样本太少时 p90 没意义（17 秒的短版只有几个停顿）
            segs.sort()
            gap_mix = segs[int(len(segs) * 0.9)]   # 90 分位，代表"打击乐的响度"
    voice.sort()
    def pct(q):
        return voice[min(len(voice) - 1, int(len(voice) * q))]
    return {"peak": db(peak),
            "voice": gv if gv is not None else sum(voice) / len(voice),
            "gap": gap_mix if gap_mix is not None else ((sum(gap) / len(gap)) if gap else None),
            "range": pct(0.9) - pct(0.1),
            "frames": len(voice)}


def main():
    ap = argparse.ArgumentParser(description="全库响度一致性审计")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--tol", type=float, default=TOL, help="与中位数的允许偏差 dB")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = []
    for d in sorted(glob.glob(os.path.join(args.dir, "out", "*"))):
        slug = os.path.basename(d)
        wav = os.path.join(d, "build", "audio.wav")
        if not os.path.exists(wav):
            continue
        st = stats_of(wav, os.path.join(d, "build", "audio_bgm.wav"))
        if st:
            rows.append((slug, st))
    if not rows:
        print("[loud] 没找到 audio.wav（先 render）")
        return 1

    def median(key):
        vals = sorted(r[1][key] for r in rows if r[1][key] is not None)
        return vals[len(vals) // 2]

    med = {"voice": median("voice"), "gap": median("gap"), "range": median("range")}
    payload, issues = [], []
    for slug, st in rows:
        flags = []
        if st["peak"] > PEAK_LIMIT:
            flags.append(f"峰值 {st['peak']:.1f}dB 偏高（有削波风险）")
        # 动态范围本来就会因题材/音色不同而差几个 dB（叙事片起伏大、公式片平），
        # 容差给宽；人声和 BGM 是全账号"一样响"的硬指标，卡紧。
        for key, label, tol in (("voice", "人声", args.tol), ("gap", "BGM", 2.0),
                                ("range", "动态范围", 4.5)):
            v = st[key]
            if v is None:
                continue
            dv = v - med[key]
            if abs(dv) > tol:
                flags.append(f"{label}比全库中位{'高' if dv > 0 else '低'} {abs(dv):.1f}dB")
        payload.append({"slug": slug, **{k: (round(v, 2) if v is not None else None)
                                         for k, v in st.items()}, "flags": flags})
        issues += [f"{slug}: {f}" for f in flags]

    if args.json:
        print(json.dumps({"median": {k: round(v, 2) for k, v in med.items() if v is not None},
                          "rows": payload, "issues": issues}, ensure_ascii=False, indent=2))
        return 0 if not issues else 1

    print(f"\n■ 响度一致性（{len(rows)} 条；中位：人声 {med['voice']:.1f}dB（门限 RMS）/ "
          f"BGM {med['gap']:.1f}dB / 动态 {med['range']:.1f}dB；容差 ±{args.tol}dB）\n")
    print(f"  {'条目':<26}{'峰值':>7}{'人声':>8}{'Δ人声':>8}{'BGMp90':>8}{'动态':>7}")
    for r in payload:
        dv = r["voice"] - med["voice"]
        mark = "" if abs(dv) <= args.tol else ("  ←" if dv > 0 else "  ←")
        print(f"  {r['slug']:<26}{r['peak']:>7.1f}{r['voice']:>8.1f}{dv:>+8.1f}"
              f"{(r['gap'] if r['gap'] is not None else 0):>8.1f}{r['range']:>7.1f}{mark}")
    if issues:
        print(f"\n  {len(issues)} 处需要调整：")
        for i in issues:
            print(f"    - {i}")
    else:
        print(f"\n  ✅ {len(rows)} 条响度一致（人声与 BGM 都在中位数 ±{args.tol}dB 内）")
    print()
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
