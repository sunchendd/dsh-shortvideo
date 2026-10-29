"""程序化 BGM：不下载任何素材，纯合成一段不抢话的知识类背景乐。

为什么自己做：
  - 版权干净（自己生成，没有素材授权风险，平台也不会判定搬运）
  - 零依赖（只用标准库：math/array/wave），CPU 上几秒出 40 秒音乐
  - 能对齐成片时长，并按人声自动"让路"（ducking），不会盖住解说

音乐设计（知识科普向，克制为主）：
  调式  A 小调；和弦进行 Am – F – C – G（i–VI–III–VII，最常见也最"稳"）
  层次  pad（长音铺底）+ arp（八分音符琶音）+ kick（1、3 拍）+ hat（反拍轻击）
  速度  96 BPM（2.5s/小节）；结构 4 小节一循环
  情绪  intensity=low 只要 pad+arp；mid 加 kick/hat；high 再加一层高音琶音

用法：
  python bgm.py --dur 40 --out out/bgm.wav --intensity mid
  from bgm import build_bed, mix_into
  bed = build_bed(40.0, intensity="mid")          # -> array('h'), 22050Hz 单声道
  mix_into("build/audio.wav", "build/final.wav", bed, gain_db=-27)
"""
import argparse
import array
import math
import os
import random
import struct
import sys
import wave

SR = 22050
BPM = 96.0
BEAT = 60.0 / BPM
BAR = BEAT * 4

# A 小调：Am F C G，每个和弦 3 个音（MIDI 音高）
PROGRESSION = [
    (57, 60, 64),   # Am: A3 C4 E4
    (53, 57, 60),   # F : F3 A3 C4
    (48, 52, 55),   # C : C3 E3 G3
    (55, 59, 62),   # G : G3 B3 D4
]
STYLES = ("lofi", "pulse")


def _hz(midi):
    return 440.0 * 2 ** ((midi - 69) / 12.0)


def _env_ad(i, n, attack, release, curve=2.0):
    """简单 AR 包络：前 attack 秒线性起，后面按 curve 幂衰减到 0。"""
    a = max(1, int(attack * SR))
    if i < a:
        return i / a
    r = (i - a) / max(1, n - a)
    return max(0.0, (1.0 - r) ** curve)


_note_cache = {}


def _note(midi, dur, kind="pluck"):
    """生成一个音符（缓存）：pluck=拨弦感，pad=长音铺底，sub=低频。"""
    key = (midi, round(dur, 3), kind)
    hit = _note_cache.get(key)
    if hit is not None:
        return hit
    n = int(dur * SR)
    f = _hz(midi)
    out = array.array("d", bytes(8 * n))
    if kind == "pad":
        # 三个谐波叠加 + 轻微颤音，起音柔和（不刺耳、不抢人声频段）
        for i in range(n):
            t = i / SR
            vib = 1.0 + 0.0025 * math.sin(2 * math.pi * 4.5 * t)
            v = (math.sin(2 * math.pi * f * vib * t) * 0.5
                 + math.sin(2 * math.pi * f * 2 * t) * 0.18
                 + math.sin(2 * math.pi * f * 3 * t) * 0.07)
            out[i] = v * _env_ad(i, n, attack=0.35, release=0.5, curve=1.2)
    elif kind == "sub":
        for i in range(n):
            t = i / SR
            # 频率下滑 58Hz->42Hz，短促（做成"心跳感"而不是电子鼓）
            ff = 58 - 16 * min(1.0, t / 0.09)
            out[i] = math.sin(2 * math.pi * ff * t) * _env_ad(i, n, 0.004, 0.1, curve=3.0)
    else:  # pluck
        for i in range(n):
            t = i / SR
            v = math.sin(2 * math.pi * f * t) + 0.28 * math.sin(2 * math.pi * f * 2 * t)
            out[i] = v * _env_ad(i, n, attack=0.006, release=0.22, curve=2.4)
    _note_cache[key] = out
    return out


def _hat(dur=0.045, seed=7):
    key = ("hat", dur)
    hit = _note_cache.get(key)
    if hit is not None:
        return hit
    n = int(dur * SR)
    rnd = random.Random(seed)
    out = array.array("d", bytes(8 * n))
    prev = 0.0
    for i in range(n):
        x = rnd.uniform(-1, 1)
        hp = x - prev          # 一阶高通 → 沙沙的镲片感
        prev = x
        out[i] = hp * _env_ad(i, n, 0.001, 0.04, curve=3.0)
    _note_cache[key] = out
    return out


def _add(dst, src, at, gain):
    """把 src 按 gain 叠加到 dst 的第 at 个采样点（越界自动截断）。"""
    n = min(len(src), len(dst) - at)
    if n <= 0:
        return
    for i in range(n):
        dst[at + i] += src[i] * gain


def build_bed(dur, intensity="mid", style="lofi", seed=0, master_fade=True):
    """生成一条背景乐（array('d') -> 归一化后的 int16 array('h')）。"""
    n = int(dur * SR)
    buf = array.array("d", bytes(8 * n))
    rnd = random.Random(seed)
    beats_total = int(dur / BEAT) + 1
    bar_i = 0
    b = 0
    while b < beats_total:
        chord = PROGRESSION[bar_i % len(PROGRESSION)]
        t0 = b * BEAT
        at = int(t0 * SR)
        if at >= n:
            break
        # pad：整小节铺底
        for j, midi in enumerate(chord):
            _add(buf, _note(midi, BAR * 1.05, "pad"), at, 0.10 - j * 0.012)
        # arp：八分音符，和弦音上行 + 偶尔跳高八度
        seq = [chord[0] + 12, chord[1] + 12, chord[2] + 12, chord[1] + 12,
               chord[0] + 12, chord[2] + 12, chord[1] + 12, chord[2] + 24]
        for k in range(8):
            sa = int((t0 + k * BEAT / 2) * SR)
            if sa >= n:
                break
            g = 0.055 if style == "lofi" else 0.075
            if k % 4 == 0:
                g *= 1.25
            _add(buf, _note(seq[k], BEAT * 0.9, "pluck"), sa, g)
        if intensity in ("mid", "high"):
            # kick：1、3 拍
            for bt in (0, 2):
                _add(buf, _note(33, 0.22, "sub"), int((t0 + bt * BEAT) * SR), 0.20)
            # hat：反拍
            for k in range(4):
                _add(buf, _hat(), int((t0 + (k + 0.5) * BEAT) * SR),
                     0.03 * (1.0 if k % 2 else 0.7))
        if intensity == "high":
            # 高音琶音加密（只在"高潮/揭示"段用）
            for k in range(4):
                sa = int((t0 + k * BEAT) * SR + BEAT / 4 * SR)
                if sa < n:
                    _add(buf, _note(chord[k % 3] + 24, BEAT * 0.5, "pluck"), sa, 0.035)
        b += 4
        bar_i += 1

    # 母带：淡入淡出 + 软限幅 + 归一化（分段拼接时关掉，用段间交叉淡化代替）
    if master_fade:
        fi = int(0.35 * SR)
        fo = int(min(1.6, dur / 4) * SR)
        for i in range(fi):
            buf[i] *= i / fi
        for i in range(fo):
            buf[n - 1 - i] *= i / fo
    peak = max(1e-9, max(abs(x) for x in buf))
    # 软限幅：先按 peak 压到 0.9，再 tanh 形状避免瞬态刺耳
    norm = 0.9 / peak
    out = array.array("h", bytes(2 * n))
    for i, x in enumerate(buf):
        v = math.tanh(x * norm * 1.15) * 0.92
        out[i] = int(max(-1.0, min(1.0, v)) * 32000)
    return out


# 情绪 -> 配器强度：钩子要抓人、讲解要退后、揭示要推起来、收尾要收住
MOOD_INTENSITY = {"hook": "high", "rule": "low", "explain": "low",
                  "reveal": "mid", "win": "mid"}


# 强度 -> 电平（build_bed 内部会各自归一化，所以必须在拼接时补一次电平差，
# 否则"低强度"只是织体变稀、响度却一样，听不出起伏）
MOOD_GAIN = {"low": 0.62, "mid": 0.82, "high": 1.0}


def build_dynamic(segments, style="lofi", seed=0, xfade=0.35):
    """按镜头分段生成 BGM 再拼接（段间交叉淡化），让配器跟着叙事起伏。

    segments: [(dur_seconds, intensity), ...]
    一段一段建床再交替淡化，比"整片一个 intensity"明显更有起伏，
    而且每段的和弦进行从循环里接着走，不会每次都从 Am 重新开始。
    """
    if not segments:
        return array.array("h")
    total = sum(d for d, _ in segments)
    n = int(total * SR)
    out = array.array("d", bytes(8 * n))
    bar_offset = 0.0
    pos = 0.0
    for dur, intensity in segments:
        seg = build_bed(dur + xfade, intensity=intensity, style=style, seed=seed,
                        master_fade=False)
        seg_gain = MOOD_GAIN.get(intensity, 0.82)
        # 去掉开头 / 结尾的淡入淡出（拼接时用交叉淡化代替，避免段间"抽一下"）
        i0 = int(0.12 * SR)
        seg = seg[i0:]
        at = int(pos * SR)
        xf = int(xfade * SR)
        for i in range(len(seg)):
            j = at + i
            if j >= n:
                break
            g = 1.0
            if i < xf and pos > 0:      # 与前一段重叠的上升沿
                g = i / max(1, xf)
            out[j] += (seg[i] / 32768.0) * g * seg_gain
        pos += dur
        bar_offset += dur
    # 全片淡入淡出（分段时各自不淡，避免段间"抽一下"）
    total_sec = n / SR
    fi = int(min(0.4, total_sec / 8) * SR)
    fo = int(min(1.6, total_sec / 4) * SR)
    for i in range(fi):
        out[i] *= i / max(1, fi)
    for i in range(fo):
        out[n - 1 - i] *= i / max(1, fo)
    peak = max(1e-9, max(abs(x) for x in out))
    norm = 0.9 / peak
    res = array.array("h", bytes(2 * n))
    for i, x in enumerate(out):
        v = math.tanh(x * norm * 1.15) * 0.92
        res[i] = int(max(-1.0, min(1.0, v)) * 32000)
    return res


def _read_wav(path):
    with wave.open(path, "rb") as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path} 不是 {SR}Hz/单声道/16bit（先经 tts.concat 归一）")
        return array.array("h", w.readframes(w.getnframes()))


def _write_wav(path, samples):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def mix_into(voice_wav, out_wav, bed, gain_db=-27.0, duck=0.55, music_only=False):
    """把人声与 BGM 混合。duck>0 时按人声音量自动压低 BGM（侧链压缩的简化版）。

    返回 (峰值 dBFS, 人声段平均压低量)。
    """
    voice = _read_wav(voice_wav)
    n = len(voice)
    if len(bed) < n:
        bed = bed + array.array("h", bytes(2 * (n - len(bed))))
    else:
        bed = bed[:n]
    g = 10 ** (gain_db / 20.0)
    # 人声包络：50ms 窗口取峰值，再做 200ms 平滑（够粗但不会抖）
    win = int(0.05 * SR)
    env = array.array("d", bytes(8 * n))
    for s in range(0, n, win):
        seg = voice[s:s + win]
        pk = max((abs(x) for x in seg), default=0) / 32768.0
        for i in range(s, min(n, s + win)):
            env[i] = pk
    smooth = int(0.2 * SR)
    acc = 0.0
    for i in range(n):
        acc += (env[i] - acc) / smooth
        env[i] = acc
    out = array.array("h", bytes(2 * n))
    peak = 0
    ducked = 0.0
    for i in range(n):
        v = voice[i] / 32768.0
        m = bed[i] / 32768.0 * g
        if duck > 0 and not music_only:
            m *= max(0.0, 1.0 - duck * min(1.0, env[i] * 6.0))
            ducked += (1.0 - max(0.0, 1.0 - duck * min(1.0, env[i] * 6.0)))
        x = v + m
        if x > 1.0:
            x = math.tanh(x)
        elif x < -1.0:
            x = -math.tanh(-x)
        iv = int(x * 32000)
        out[i] = iv
        a = abs(iv)
        if a > peak:
            peak = a
    _write_wav(out_wav, out)
    db = 20 * math.log10(max(1e-9, peak / 32768.0))
    return db, ducked / max(1, n)


def main():
    ap = argparse.ArgumentParser(description="程序化背景乐（知识类，轻鼓点+琶音）")
    ap.add_argument("--dur", type=float, default=40.0)
    ap.add_argument("--out", default="out/bgm.wav")
    ap.add_argument("--intensity", default="mid", choices=("low", "mid", "high"))
    ap.add_argument("--style", default="lofi", choices=STYLES)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mix", default=None, help="可选：与这个人声 wav 混合后输出")
    ap.add_argument("--dynamics", default=None,
                    help="按镜头强度分段，如 hook:high,rule:low,reveal:mid")
    args = ap.parse_args()
    if args.dynamics:
        moods = [x.split(":") for x in args.dynamics.split(",") if ":" in x]
        per = args.dur / max(1, len(moods))
        bed = build_dynamic([(per, m[1]) for m in moods], args.style, args.seed)
    else:
        bed = build_bed(args.dur, args.intensity, args.style, args.seed)
    if args.mix:
        db, duck = mix_into(args.mix, args.out, bed)
        print(f"[ok] {args.out}  混合完成 峰值 {db:.1f} dBFS  BGM 平均压低 {duck * 100:.0f}%")
    else:
        _write_wav(args.out, bed)
        print(f"[ok] {args.out}  {args.dur:.1f}s  intensity={args.intensity} style={args.style}")


if __name__ == "__main__":
    main()
