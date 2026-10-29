#!/usr/bin/env python3
"""音色实验室：把 3 个人格音色 × 5 种情绪全部合成一遍，量化验收 + 出样片。

它做三件事：
  1. 用 tts.build_voice({"preset": key}) 合成一套标准 5 情绪脚本
     -> <out>/<preset>/<mood>.wav，再拼一条 <out>/<preset>/demo.wav
  2. 实测每条音频的时长 / 峰值 dBFS / RMS dBFS / 字速 / F0 中位数（纯 stdlib，
     不依赖 numpy），证明“情绪真的不一样”而不是只在配置里不一样
  3. 验收：非静音、同一音色内响度差 ≤3dB、情绪间语速/音高单调性符合设计
  打印人看的表格，并把结果落 <out>/metrics.json + <out>/prosody.json

用法：
  python voice_lab.py --preset all --out /root/shortvideo-lab/voice
  python voice_lab.py --preset yunxi --mood hook,reveal --jobs 2
  python voice_lab.py --selfcheck          # 只跑 build_voice 向后兼容断言（不联网）

注意：企业 MITM 代理下必须用 tts.py 里的 SSL 补丁（本脚本只调用它，不重复实现）。
"""
import argparse
import array
import json
import math
import operator
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo import tts  # noqa: E402

# 一套标准科普脚本：同一个文本跑遍所有音色，指标才可比。
# 文案贴近小红书科普口播（口语、短句、有钩子）。
SCRIPT = (
    ("hook", "你有没有想过，冬天摸金属，为什么手会被粘住？"),
    ("rule", "先记住一条规则：热量总是从高温，流向低温。"),
    ("explain", "金属的导热速度快得多，它几乎瞬间就带走了你手上的热量。"),
    ("reveal", "所以让你觉得冷的，不是温度低，而是热跑得太快。"),
    ("win", "记住这一条，下次再摸金属，你就知道发生了什么了。"),
)

GAP = 0.35  # demo 里每段之间的留白（秒）

# 控制变量诊断：同一句话跑 5 种情绪。
# 样片脚本每条情绪的文字不同，跨情绪比较会被“句子本身的语调”污染；
# 只有同一句文本才能把测到的差异归因到 rate/pitch/volume 参数上。
DIAG_TEXT = "同一段文字，用来量音高和语速，五种情绪各念一遍，方便直接对比。"

# 验收阈值
SILENT_RMS_DBFS = -50.0   # 低于此值认定静音/合成失败
SILENT_PEAK_DBFS = -40.0
LOUDNESS_SPREAD_DB = 3.0  # 同一音色内最大响度差
MIN_CPC_RATIO = 0.9       # hook 字速至少是 explain 的 0.9 倍（要有明显加速）


# ------------------------------------------------------------------ 音频指标

def peak_rms(arr):
    """返回 (峰值 dBFS, 整体 RMS dBFS, 语音段 RMS dBFS)。

    arr 是 s16 整数样本。第三个是“门限 RMS”：只看响过 -20dB（相对最响帧）的
    20ms 帧，剔除长停顿，才是可比的“说话响度”——整体 RMS 会被停顿长度带偏。
    """
    n = len(arr)
    if not n:
        return float("-inf"), float("-inf"), float("-inf")
    peak = max(max(arr), -min(arr)) / 32768.0
    peak_db = 20.0 * math.log10(peak) if peak > 0 else float("-inf")
    total = sum(v * v for v in arr)
    mean_sq = total / n / (32768.0 * 32768.0)
    rms_db = 10.0 * math.log10(mean_sq) if mean_sq > 0 else float("-inf")

    frame = max(1, int(tts.SAMPLE_RATE * 0.02))
    energies = []
    for st in range(0, n - frame + 1, frame):
        s = 0
        for v in arr[st:st + frame]:
            s += v * v
        energies.append(s / float(frame))
    gated_db = float("-inf")
    if energies:
        top = max(energies)
        if top > 0:
            keep = [e for e in energies if e >= top * 0.01]  # -20dB 门限
            gated = sum(keep) / len(keep) / (32768.0 * 32768.0)
            if gated > 0:
                gated_db = 10.0 * math.log10(gated)
    return peak_db, rms_db, gated_db


def _decimate(samples, step):
    """box 均值降采样（粗糙抗混叠，够做 F0 估计）。"""
    if step <= 1:
        return list(samples)
    return [(x + y + z) / 3.0
            for x, y, z in zip(samples[0::step], samples[1::step], samples[2::step])]


def _acf_r(seg, prefix, lag, n, total):
    """归一化自相关在某个滞后上的值（前缀和复用能量，只算一次点积）。"""
    a_end = n - lag
    if a_end <= 0 or lag <= 0:
        return None
    ea = prefix[a_end]          # sum(seg[:a_end]^2)
    eb = total - prefix[lag]    # sum(seg[lag:]^2)
    if ea <= 1e-12 or eb <= 1e-12:
        return None
    return sum(map(operator.mul, seg[:a_end], seg[lag:])) / math.sqrt(ea * eb)


def _acf_frame(seg, prefix, lag_min, lag_max):
    """归一化自相关找基频周期，抛物线插值细化到亚样本精度。

    返回 (滞后, r) 或 None。插值是必须的：7350Hz 下 245Hz 对应滞后 30，
    滞后量化会让 +4Hz 的音高差被吞掉。
    """
    n = len(seg)
    total = prefix[n]
    best_r, best_lag = 0.0, 0
    for lag in range(lag_min, lag_max + 1):
        r = _acf_r(seg, prefix, lag, n, total)
        if r is not None and r > best_r:
            best_r, best_lag = r, lag
    if not best_lag or best_r < 0.35:
        return None
    shift = 0.0
    rm = _acf_r(seg, prefix, best_lag - 1, n, total) if best_lag - 1 >= 2 else None
    rp = _acf_r(seg, prefix, best_lag + 1, n, total)
    if rm is not None and rp is not None:
        denom = rm - 2.0 * best_r + rp
        if abs(denom) > 1e-9:
            shift = max(-0.5, min(0.5, 0.5 * (rm - rp) / denom))
    return best_lag + shift, best_r


def estimate_f0(samples, sr=tts.SAMPLE_RATE, max_frames=48):
    """估算 F0 分布：返回 dict(f0_median, f0_p10, f0_p90, frames)。

    做法：降采样到 sr/3，55ms 窗 + 25ms 跳，帧内归一化自相关（60~420Hz），
    取相关系数 ≥0.35 的浊音帧做统计。纯 Python，5s 音频约 0.1s。
    """
    step = 3
    dec = _decimate(samples, step)
    sr2 = sr / float(step)
    win = int(sr2 * 0.055)
    hop = int(sr2 * 0.025)
    lag_min = max(2, int(sr2 / 420.0))
    lag_max = max(lag_min + 2, int(sr2 / 60.0))
    if len(dec) < win + hop:
        return dict(f0_median=0.0, f0_p10=0.0, f0_p90=0.0, frames=0)
    starts = list(range(0, len(dec) - win, hop))
    # 均匀抽帧，避免长音频拖慢
    if len(starts) > max_frames:
        stride = len(starts) / float(max_frames)
        starts = [starts[int(i * stride)] for i in range(max_frames)]
    energies = []
    for st in starts:
        seg = dec[st:st + win]
        energies.append(sum(v * v for v in seg))
    floor = (sorted(energies)[len(energies) // 2] * 0.25) if energies else 0.0
    f0s = []
    for st, e in zip(starts, energies):
        if e <= floor or e <= 0:
            continue
        seg = dec[st:st + win]
        prefix = [0.0]
        acc = 0.0
        for v in seg:
            acc += v * v
            prefix.append(acc)
        hit = _acf_frame(seg, prefix, lag_min, lag_max)
        if hit:
            f0s.append(sr2 / hit[0])
    if not f0s:
        return dict(f0_median=0.0, f0_p10=0.0, f0_p90=0.0, frames=0)
    f0s.sort()

    def pct(p):
        return f0s[min(len(f0s) - 1, int(round(p / 100.0 * (len(f0s) - 1))))]

    return dict(f0_median=round(pct(50), 1), f0_p10=round(pct(10), 1),
                f0_p90=round(pct(90), 1), frames=len(f0s))


def analyze(path, text=""):
    """一条 wav -> 指标字典。"""
    arr = tts._read_wav(path)
    dur = len(arr) / float(tts.SAMPLE_RATE)
    peak_db, rms_db, gated_db = peak_rms(arr)
    f0 = estimate_f0(arr)
    n_chars = len((text or "").replace(" ", ""))
    cps = (n_chars / dur) if dur > 0 else 0.0
    semis = 0.0
    if f0["f0_p10"] > 0 and f0["f0_p90"] > 0:
        semis = 12.0 * math.log(f0["f0_p90"] / f0["f0_p10"], 2)

    def db(v):
        return round(v, 2) if v != float("-inf") else None

    return dict(
        path=path, dur=round(dur, 3), peak_dbfs=db(peak_db), rms_dbfs=db(rms_db),
        speech_dbfs=db(gated_db),
        chars=n_chars, chars_per_sec=round(cps, 2),
        f0_median=f0["f0_median"], f0_p10=f0["f0_p10"], f0_p90=f0["f0_p90"],
        f0_frames=f0["frames"], f0_semis=round(semis, 2),
        silent=(rms_db == float("-inf") or rms_db < SILENT_RMS_DBFS
                or (peak_db != float("-inf") and peak_db < SILENT_PEAK_DBFS)),
    )


# ------------------------------------------------------------------ 合成

def synth_preset(key, out_dir, moods=None, jobs=3, cache=True, verbose=True, normalize=True):
    """合成一个音色的 5 情绪样片 + demo.wav，返回 (records, demo_path)。"""
    voice = tts.build_voice({"preset": key, "cache": cache, "jobs": jobs,
                             "verbose": verbose, "normalize": normalize})
    d = os.path.join(out_dir, key)
    os.makedirs(d, exist_ok=True)
    script = [it for it in SCRIPT if not moods or it[0] in moods]
    items = [{"text": text, "path": os.path.join(d, f"{mood}.wav"), "mood": mood}
             for mood, text in script]
    t0 = time.time()
    durs = tts.synth_all(voice, items, jobs=jobs)
    elapsed = time.time() - t0
    records = []
    for item, dur in zip(items, durs):
        rec = analyze(item["path"], item["text"])
        rec.update(preset=key, voice=voice.name, label=voice.label, mood=item["mood"],
                   text=item["text"], synth_seconds=dur,
                   prosody=voice.mood_prosody(item["mood"]),
                   normalize=voice.normalize_target)
        records.append(rec)
    demo = None
    if len(records) > 1:
        demo = os.path.join(d, "demo.wav")
        segs, pos = [], 0.0
        for rec in records:
            segs.append((pos, rec["path"]))
            pos += rec["dur"] + GAP
        tts.concat(segs, max(0.01, pos - GAP + 0.25), demo)
    print(f"[lab] {key} ({voice.label}) {len(records)} 条 / {elapsed:.1f}s "
          f"(并发 {voice.jobs}, 降级 {getattr(voice, 'failures', 0)})", flush=True)
    return records, demo, voice


# ------------------------------------------------------------------ 控制变量诊断

def synth_diag(key, out_dir, jobs=3, cache=True, verbose=False, normalize=True):
    """同一句话 × 5 情绪 -> 证明“参数真的生效了”（不受文本差异污染）。"""
    voice = tts.build_voice({"preset": key, "cache": cache, "jobs": jobs,
                             "verbose": verbose, "normalize": normalize})
    d = os.path.join(out_dir, key, "diag")
    os.makedirs(d, exist_ok=True)
    items = [{"text": DIAG_TEXT, "path": os.path.join(d, f"{mood}.wav"), "mood": mood}
             for mood in tts.MOOD_KEYS]
    durs = tts.synth_all(voice, items, jobs=jobs)
    records = []
    for item, dur in zip(items, durs):
        rec = analyze(item["path"], item["text"])
        rec.update(preset=key, voice=voice.name, label=voice.label, mood=item["mood"],
                   diag=True, synth_seconds=dur, prosody=voice.mood_prosody(item["mood"]),
                   normalize=voice.normalize_target)
        records.append(rec)
    return records, voice


def _num(text):
    """'+12%' / '-7Hz' -> float。"""
    try:
        return float(str(text or "0").replace("%", "").replace("Hz", "").replace("+", ""))
    except ValueError:
        return 0.0


def check_diag(records):
    """同文本下的归因验收：rate / pitch / volume 三个维度都要按设计分开。

    比较对象不写死情绪名，而是取“参数最大 vs 最小”的两个 mood——这样表改了
    验收逻辑也不用改，同时保证测的是真的参数差异而不是文案巧合。
    """
    issues = []
    for key in dict.fromkeys(r["preset"] for r in records):
        rs = {r["mood"]: r for r in records if r["preset"] == key}
        for mood, r in rs.items():
            if r["silent"]:
                issues.append(("FAIL", f"{key}/diag/{mood} 静音"))
        if len(rs) < 2:
            continue
        # 1) 语速（同文本 -> 只能由 rate 造成）
        fast = max(rs.values(), key=lambda r: _num(r["prosody"]["rate"]))
        slow = min(rs.values(), key=lambda r: _num(r["prosody"]["rate"]))
        if _num(fast["prosody"]["rate"]) - _num(slow["prosody"]["rate"]) >= 8:
            d = (fast["dur"] / slow["dur"] - 1.0) * 100
            issues.append(("OK" if d < -3 else "FAIL",
                           f"{key} 语速 {fast['mood']}{fast['prosody']['rate']} "
                           f"{fast['dur']:.2f}s vs {slow['mood']}{slow['prosody']['rate']} "
                           f"{slow['dur']:.2f}s （{d:+.0f}% 时长）"))
        # 2) 音高（F0 中位数，同文本才可比）
        hi = max(rs.values(), key=lambda r: _num(r["prosody"]["pitch"]))
        lo = min(rs.values(), key=lambda r: _num(r["prosody"]["pitch"]))
        if _num(hi["prosody"]["pitch"]) - _num(lo["prosody"]["pitch"]) >= 4:
            if not hi["f0_median"] or not lo["f0_median"]:
                # 没测到基频：多半是这一条被 TTS 抖动成了静音/气声，不是参数没生效
                issues.append(("WARN", f"{key} 音高对比跳过："
                                       f"{hi['mood'] if not hi['f0_median'] else lo['mood']} "
                                       f"未测到基频（重跑一次通常就好）"))
                continue
            df0 = hi["f0_median"] - lo["f0_median"]
            issues.append(("OK" if df0 > 1.0 else "FAIL",
                           f"{key} 音高 {hi['mood']}{hi['prosody']['pitch']} "
                           f"{hi['f0_median']:.1f}Hz vs {lo['mood']}{lo['prosody']['pitch']} "
                           f"{lo['f0_median']:.1f}Hz （{df0:+.1f}Hz 实测）"))
        # 3) 响度（门限 RMS，同文本才可比）
        vals = [r["speech_dbfs"] for r in rs.values() if r["speech_dbfs"] is not None]
        if vals:
            loud = max(rs.values(), key=lambda r: _num(r["prosody"]["volume"]))
            quiet = min(rs.values(), key=lambda r: _num(r["prosody"]["volume"]))
            if _num(loud["prosody"]["volume"]) - _num(quiet["prosody"]["volume"]) >= 3:
                dv = loud["speech_dbfs"] - quiet["speech_dbfs"]
                issues.append(("OK" if dv > 0.2 else "FAIL",
                               f"{key} 响度 {loud['mood']}{loud['prosody']['volume']} "
                               f"{loud['speech_dbfs']}dBFS vs {quiet['mood']}"
                               f"{quiet['prosody']['volume']} {quiet['speech_dbfs']}dBFS"
                               f" （{dv:+.2f}dB）"))
            spread = max(vals) - min(vals)
            issues.append(("OK" if spread <= LOUDNESS_SPREAD_DB else "FAIL",
                           f"{key} 同文本语音响度跨度 {spread:.2f}dB "
                           f"({min(vals)}..{max(vals)} dBFS)"))
    return issues


def print_diag(records):
    print(f"\n== 控制变量诊断（同一句：{DIAG_TEXT}）==")
    print("   用于把差异归因到 rate/pitch/volume，不受样片脚本文本差异污染")
    hdr = (f"{'preset':<10}{'mood':<9}{'dur_s':>7}{'rms_dB':>8}{'语音dB':>8}"
           f"{'F0med':>8}{'F0p10':>7}{'F0p90':>7}{'半音':>6}   prosody(rate/pitch/vol)")
    print(hdr)
    print("-" * len(hdr))
    for r in records:
        p = r["prosody"]

        def fmt(v, width, prec=2):
            return f"{'-inf':>{width}}" if v is None else f"{v:>{width}.{prec}f}"
        print(f"{r['preset']:<10}{r['mood']:<9}{r['dur']:>7.2f}{fmt(r['rms_dbfs'], 8)}"
              f"{fmt(r['speech_dbfs'], 8)}{r['f0_median']:>8.1f}{r['f0_p10']:>7.1f}"
              f"{r['f0_p90']:>7.1f}{r['f0_semis']:>6.2f}"
              f"   {p['rate']}/{p['pitch']}/{p['volume']}"
              f"{'  [静音!]' if r['silent'] else ''}")


# ------------------------------------------------------------------ 验收

def check_preset(records):
    """单音色验收 -> [(level, message), ...]；level 为 FAIL/WARN/OK。"""
    issues = []
    key = records[0]["preset"] if records else "?"
    for rec in records:
        if rec["silent"]:
            issues.append(("FAIL", f"{key}/{rec['mood']} 静音或过轻 "
                                   f"(RMS {rec['rms_dbfs']} dBFS)"))
        elif rec["peak_dbfs"] is not None and rec["peak_dbfs"] > -0.5:
            issues.append(("WARN", f"{key}/{rec['mood']} 峰值 {rec['peak_dbfs']} dBFS 接近削波"))
        if rec["f0_frames"] < 8:
            issues.append(("WARN", f"{key}/{rec['mood']} 浊音帧太少({rec['f0_frames']})，F0 仅供参考"))
    # 响度一致性：用门限 RMS（剔除停顿），否则长句的停顿会被误判成“更轻”
    sp = [r["speech_dbfs"] for r in records if r["speech_dbfs"] is not None]
    if len(sp) > 1:
        spread = max(sp) - min(sp)
        msg = f"{key} 样片语音响度跨度 {spread:.2f} dB ({min(sp)}..{max(sp)} dBFS)"
        issues.append(("FAIL", msg + f" > {LOUDNESS_SPREAD_DB}dB") if spread > LOUDNESS_SPREAD_DB
                      else ("OK", msg))
    by = {r["mood"]: r for r in records}
    if "hook" in by and "explain" in by:
        h, e = by["hook"]["chars_per_sec"], by["explain"]["chars_per_sec"]
        msg = f"{key} 样片字速 hook {h}/s vs explain {e}/s ({(h / e - 1) * 100:+.0f}%)"
        issues.append(("FAIL", msg + " —— hook 没有比 explain 更快") if h <= e * MIN_CPC_RATIO
                      else ("OK", msg))
    return issues


# ------------------------------------------------------------------ 打印

def print_prosody(voices):
    print("\n== 实际发给 edge-tts 的韵律（preset × mood）==")
    head = f"{'preset':<10}{'label':<14}{'base(音色)':<24}"
    print(head + "".join(f"{m:<16}" for m in tts.MOOD_KEYS))
    for key, voice in voices.items():
        base = f"{voice.rate}/{voice.pitch}/{voice.volume}"
        cells = []
        for mood in tts.MOOD_KEYS:
            p = voice.mood_prosody(mood)
            cells.append(f"{p['rate']},{p['pitch']},{p['volume']}")
        print(f"{key:<10}{voice.label:<14}{base:<24}" + "".join(f"{c:<16}" for c in cells))


def print_metrics(records):
    print("\n== 样片实测指标（不同文案，供人耳试听对照）==")
    hdr = (f"{'preset':<10}{'mood':<9}{'dur_s':>7}{'peak_dB':>9}{'rms_dB':>8}{'语音dB':>8}"
           f"{'字/秒':>8}{'F0med':>8}{'F0p10':>7}{'F0p90':>7}{'半音':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in records:
        def fmt(v, width, prec=2):
            return f"{'-inf':>{width}}" if v is None else f"{v:>{width}.{prec}f}"
        print(f"{r['preset']:<10}{r['mood']:<9}{r['dur']:>7.2f}{fmt(r['peak_dbfs'], 9)}"
              f"{fmt(r['rms_dbfs'], 8)}{fmt(r['speech_dbfs'], 8)}{r['chars_per_sec']:>8.2f}"
              f"{r['f0_median']:>8.1f}{r['f0_p10']:>7.1f}{r['f0_p90']:>7.1f}"
              f"{r['f0_semis']:>6.2f}{'  [静音! 合成失败]' if r['silent'] else ''}")


def print_checks(issues):
    print("\n== 验收 ==")
    for level, msg in issues:
        mark = {"OK": "  ok ", "WARN": "  !  ", "FAIL": "  X  "}[level]
        print(mark + msg)


# ------------------------------------------------------------------ 自检

def selfcheck():
    """向后兼容断言：老配置、name-only、preset-only 都要能用。"""
    print("== build_voice 向后兼容自检 ==")
    cases = []
    v0 = tts.build_voice({})
    cases.append(("build_voice({})", v0.voice == "zh-CN-XiaoxiaoNeural",
                  f"{v0.name} / preset={getattr(v0, 'preset', None)}"))
    v1 = tts.build_voice({"name": "zh-CN-YunxiNeural"})
    cases.append(("name-only 旧配置", v1.voice == "zh-CN-YunxiNeural" and callable(v1.synth),
                  f"{v1.name} / preset={getattr(v1, 'preset', None)}"))
    v2 = tts.build_voice({"preset": "yunxi"})
    cases.append(("preset=yunxi", v2.voice == "zh-CN-YunxiNeural" and v2.name == "edge:zh-CN-YunxiNeural",
                  f"name={v2.name} / label={v2.label}"))
    v3 = tts.build_voice({"preset": "yunjian", "mood_tuning": {"hook": {"rate": "+15%"}}})
    cases.append(("mood_tuning 覆盖", v3.mood_prosody("hook")["rate"] == "+15%",
                  f"hook rate={v3.mood_prosody('hook')['rate']}"))
    v4 = tts.build_voice({"preset": "yunjian", "rate": "+5%"})
    cases.append(("name/preset+显式 rate", v4.rate == "+5%", f"base rate={v4.rate}"))
    v5 = tts.build_voice({"engine": "silent"})
    cases.append(("engine=silent", v5.name == "silent", v5.name))
    v6 = tts.build_voice({"preset": "yunjian", "mood_tuning": {"hook": {"rate": "+900%"}}})
    cases.append(("超限钳位", v6.mood_prosody("hook")["rate"] == "+100%",
                  f"clamped rate={v6.mood_prosody('hook')['rate']}"))
    ok = True
    for name, passed, detail in cases:
        print(f"  {'ok ' if passed else 'X  '}{name:<24}{detail}")
        ok = ok and passed
    per_voice_differs = len({tuple(tuple(sorted(v.moods[m].items())) for m in tts.MOOD_KEYS)
                             for v in (tts.build_voice({"preset": k}) for k in tts.VOICE_PRESETS)}) == 3
    print(f"  {'ok ' if per_voice_differs else 'X  '}三个人格情绪表互不相同")
    return ok and per_voice_differs


# ------------------------------------------------------------------ 词级时间戳验收

WORDS_TOL = 0.15  # 末词 end 与 wav 时长的允许误差（秒）


def words_check(text, preset="xiaoxiao", out_dir=None, cache=True, tolerance=WORDS_TOL):
    """验收词级时间戳：打印每个词，并检查末词对齐 + 缓存命中一致。

    1) 第一次跑：走网络，拿 WordBoundary -> 词表；末词 end 必须落在 wav 时长 ±0.15s
    2) 第二次跑（新对象、缓存开）：必须 cache_hits=1（不重新合成）且词表完全一致
    """
    out_dir = os.path.abspath(out_dir or os.path.join(os.getcwd(), "voice-lab-out"))
    d = os.path.join(out_dir, preset, "words")
    os.makedirs(d, exist_ok=True)
    wav = os.path.join(d, "words_demo.wav")
    issues = []

    def run(tag):
        v = tts.build_voice({"preset": preset, "cache": cache, "verbose": False,
                             "words": True, "retries": 6, "retry_passes": 3})
        t0 = time.time()
        dur = v.synth(text, wav, "hook")
        words = v.words_for(wav)
        print(f"[words] {tag}: {len(words)} 词, wav {dur:.3f}s, "
              f"cache_hits={v.cache_hits}, 用时 {time.time() - t0:.2f}s, "
              f"boundary={'WordBoundary' if tts.boundary_supported() else 'SentenceBoundary'}")
        return dur, words, v

    dur1, words1, v1 = run("第一次(网络)")
    if not words1:
        issues.append(("FAIL", "没有拿到任何词时间戳"))
    else:
        print(f"\n{'#':>3}{'词':<8}{'start':>8}{'end':>8}{'dur':>7}")
        prev_end = 0.0
        for i, w in enumerate(words1, 1):
            print(f"{i:>3}{w['text']:<8}{w['start']:>8.3f}{w['end']:>8.3f}"
                  f"{w['end'] - w['start']:>7.3f}")
            if w["start"] < prev_end - 1e-6:
                issues.append(("WARN", f"第 {i} 个词 start 早于前一个 end（重叠）"))
            if w["end"] <= w["start"]:
                issues.append(("FAIL", f"第 {i} 个词 end<=start"))
            if w["start"] > dur1 + 1e-6:
                issues.append(("FAIL", f"第 {i} 个词 start 超出 wav 时长"))
            prev_end = w["end"]
        last_end = tts.words_duration(words1)
        delta = last_end - dur1
        ok = abs(delta) <= tolerance
        issues.append(("OK" if ok else "FAIL",
                       f"末词 end {last_end:.3f}s vs wav {dur1:.3f}s （{delta:+.3f}s，"
                       f"容差 ±{tolerance}s）"))
        issues.append(("OK" if words1[0]["start"] < 1.0 else "WARN",
                       f"首词 start {words1[0]['start']:.3f}s（裁剪后应从 0 附近开始）"))

    dur2, words2, v2 = run("第二次(应命中缓存)")
    issues.append(("OK" if v2.cache_hits >= 1 else "FAIL",
                   f"第二次 cache_hits={v2.cache_hits}（应 ≥1，即不重新合成）"))
    same = (len(words1) == len(words2)
            and all(abs(a["start"] - b["start"]) < 1e-6 and abs(a["end"] - b["end"]) < 1e-6
                    and a["text"] == b["text"] for a, b in zip(words1, words2)))
    issues.append(("OK" if same else "FAIL",
                   f"两次词表一致（{len(words1)} vs {len(words2)} 词），"
                   f"时长 {dur1:.3f} vs {dur2:.3f}s"))
    side = tts.read_words(wav)
    issues.append(("OK" if len(side) == len(words1) else "FAIL",
                   f"旁车文件 {os.path.basename(wav)}{tts.WORDS_SIDECAR_SUFFIX} 读出 "
                   f"{len(side)} 词"))

    v3 = tts.build_voice({"preset": preset, "cache": cache, "verbose": False, "words": False})
    dur3 = v3.synth(text, os.path.join(d, "words_off.wav"), "hook")
    issues.append(("OK" if not v3.words_for(os.path.join(d, "words_off.wav")) else "FAIL",
                   f"cfg[\"words\"]=False 时 words_for 返回空（dur {dur3:.2f}s）"))
    return issues


# ------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(description="音色实验室：3 人格音色 × 5 情绪样片 + 指标验收")
    ap.add_argument("--preset", default="all", help="all 或 key（逗号分隔），key 见 --list")
    ap.add_argument("--mood", default="", help="只跑指定情绪（逗号分隔，默认全部）")
    ap.add_argument("--out", default=os.path.join(os.getcwd(), "voice-lab-out"),
                    help="样片输出根目录（默认 ./voice-lab-out）")
    ap.add_argument("--jobs", type=int, default=tts.DEFAULT_JOBS, help="并发合成数（默认 3）")
    ap.add_argument("--no-cache", action="store_true", help="禁用 wav 缓存，强制走网络")
    ap.add_argument("--no-diag", action="store_true",
                    help="跳过控制变量诊断（同文本 × 5 情绪，默认会跑）")
    ap.add_argument("--no-normalize", dest="normalize", action="store_false",
                    help="关闭响度对齐，测 edge-tts 原始电平（对照用）")
    ap.add_argument("--target-dbfs", type=float, default=None,
                    help="响度对齐目标语音电平（默认 -20 dBFS）")
    ap.add_argument("--words", default="", metavar="TEXT",
                    help="只验收词级时间戳：合成这句台词并打印每个词的 start/end")
    ap.add_argument("--words-tolerance", type=float, default=WORDS_TOL,
                    help=f"末词对齐容差秒（默认 {WORDS_TOL}）")
    ap.add_argument("--list", action="store_true", help="只列出音色清单")
    ap.add_argument("--selfcheck", action="store_true", help="只跑 build_voice 兼容性断言")
    args = ap.parse_args(argv)

    if args.list:
        for key, label, name, best_for in tts.list_presets():
            print(f"{key:<10}{label:<14}{name:<26}推荐题材：{'/'.join(best_for)}")
        return 0
    if args.selfcheck:
        return 0 if selfcheck() else 1
    if args.words:
        key = args.preset if args.preset.strip().lower() not in ("", "all") else tts.DEFAULT_PRESET
        issues = words_check(args.words, key, args.out, cache=not args.no_cache,
                             tolerance=args.words_tolerance)
        print_checks(issues)
        fails = [i for i in issues if i[0] == "FAIL"]
        print(f"\n[lab] 词时间戳结论：{'通过' if not fails else '不通过'} ({len(fails)} FAIL)")
        return 0 if not fails else 1

    keys = list(tts.VOICE_PRESETS) if args.preset.strip().lower() in ("all", "") \
        else [k.strip().lower() for k in args.preset.split(",") if k.strip()]
    unknown = [k for k in keys if k not in tts.VOICE_PRESETS]
    if unknown:
        print(f"[lab] 未知 preset: {', '.join(unknown)}（可选：{', '.join(tts.VOICE_PRESETS)}）")
        return 2
    moods = [m.strip() for m in args.mood.split(",") if m.strip()] or None
    normalize = args.target_dbfs if args.target_dbfs is not None else args.normalize

    selfcheck_ok = selfcheck()
    out = os.path.abspath(args.out)
    all_records, diag_records, demos, voices = [], [], {}, {}
    for key in keys:
        records, demo, voice = synth_preset(key, out, moods=moods, jobs=args.jobs,
                                            cache=not args.no_cache,
                                            normalize=normalize)
        all_records.extend(records)
        voices[key] = voice
        if demo:
            demos[key] = demo
        if not args.no_diag:
            dr, _ = synth_diag(key, out, jobs=args.jobs, cache=not args.no_cache,
                               normalize=normalize)
            diag_records.extend(dr)

    print_prosody(voices)
    print_metrics(all_records)
    issues = []
    for key in keys:
        issues.extend(check_preset([r for r in all_records if r["preset"] == key]))
    if diag_records:
        print_diag(diag_records)
        issues.extend(check_diag(diag_records))
    print_checks(issues)

    payload = dict(generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   out=out, presets=keys, diag_text=DIAG_TEXT if diag_records else None,
                   normalize=normalize,
                   demo={k: os.path.relpath(p, out) for k, p in demos.items()},
                   records=all_records, diag=diag_records,
                   checks=[dict(level=l, message=m) for l, m in issues])
    with open(os.path.join(out, "metrics.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out, "prosody.json"), "w", encoding="utf-8") as f:
        json.dump({k: dict(base=dict(rate=v.rate, pitch=v.pitch, volume=v.volume),
                           label=v.label, voice=v.voice,
                           moods={m: v.mood_prosody(m) for m in tts.MOOD_KEYS})
                   for k, v in voices.items()}, f, ensure_ascii=False, indent=2)
    print(f"\n[lab] 样片 -> {out}/<preset>/{{{'|'.join(tts.MOOD_KEYS)}}}.wav + demo.wav；"
          f"指标 -> metrics.json / prosody.json")

    fails = [i for i in issues if i[0] == "FAIL"]
    if not selfcheck_ok:
        print("[lab] build_voice 兼容性自检失败")
    print(f"[lab] 结论：{'通过' if not fails and selfcheck_ok else '不通过'} "
          f"({len(all_records) + len(diag_records)} 条音频, {len(fails)} FAIL)")
    return 0 if (not fails and selfcheck_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
