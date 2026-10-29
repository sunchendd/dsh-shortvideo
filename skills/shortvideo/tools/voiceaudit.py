#!/usr/bin/env python3
"""配音审计：每条片子的每个镜头，最终用的是什么情绪、什么语速、实际多长。

解决三类看不见的问题：
  1. `mood_tuning` 写了某个情绪，但片子里没有这个情绪的镜头 → 白写（静默失效）
  2. 镜头没写 mood 时，情绪是按 id 推断的 → 必须让人看见"实际生效的是什么"
  3. 三个音色 × 五个情绪的语速/音高差异，需要一张可对比的表

用法：
  python3 voiceaudit.py --dir examples              # 全库检查
  python3 voiceaudit.py --content examples/x.json   # 单条细节
  python3 voiceaudit.py --matrix                    # 三音色 × 五情绪对比表
  python3 voiceaudit.py --dir examples --json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo import tts  # noqa: E402

_MOOD_BY_ID = {"hook": "hook", "rule": "rule", "insight": "explain", "explain": "explain",
               "reveal": "reveal", "solve": "win", "win": "win", "flat": "flat"}
MOOD_KEYS = ("hook", "rule", "explain", "reveal", "win")


def resolve_moods(content):
    """返回 [(shot_id, mood_or_None, 来源)]，来源 = explicit / inferred / flat。"""
    voice = content.get("voice") or {}
    auto = voice.get("mood_auto", True)
    out = []
    for shot in content.get("shots") or []:
        sid = str(shot.get("id") or "")
        if shot.get("mood"):
            out.append((sid, shot["mood"], "explicit"))
        elif auto and _MOOD_BY_ID.get(sid.lower()):
            out.append((sid, _MOOD_BY_ID[sid.lower()], "inferred"))
        else:
            out.append((sid, None, "flat"))
    return out


def audit_one(path):
    content = json.load(open(path, encoding="utf-8"))
    voice = tts.build_voice(content.get("voice") or {})
    moods = resolve_moods(content)
    used = {m for _, m, _ in moods if m}
    tuning = (content.get("voice") or {}).get("mood_tuning") or {}
    dead = sorted(set(tuning) - used)
    rows = []
    for sid, mood, src in moods:
        pros = voice.mood_prosody(mood)
        rows.append({"shot": sid, "mood": mood or "-", "source": src,
                     "rate": pros.get("rate"), "pitch": pros.get("pitch"),
                     "volume": pros.get("volume")})
    return {"file": os.path.basename(path), "voice": voice.name,
            "preset": (content.get("voice") or {}).get("preset"),
            "shots": rows, "dead_tuning": dead,
            "has_mood_field": any(s.get("mood") for s in content.get("shots") or [])}


def matrix():
    """三音色 × 五情绪：同一个情绪不同音色的语速/音高，以及同音色不同情绪的差异。"""
    print(f"{'音色':<10}" + "".join(f"{m:>14}" for m in MOOD_KEYS))
    for preset in ("xiaoxiao", "yunxi", "yunjian"):
        try:
            v = tts.build_voice({"preset": preset})
        except Exception:  # noqa: BLE001
            continue
        cells = []
        for m in MOOD_KEYS:
            pros = v.mood_prosody(m)
            cells.append(f"{pros['rate']:>6}/{pros['pitch']:>6}")
        print(f"{preset:<10}" + "".join(f"{c:>14}" for c in cells))
    print("\n格式：语速/音高（rate/pitch），五个情绪依次为 hook rule explain reveal win")


def main():
    ap = argparse.ArgumentParser(description="配音审计：情绪是否真的生效")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--content")
    ap.add_argument("--matrix", action="store_true", help="三音色 × 五情绪对比表")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.matrix:
        matrix()
        return 0

    paths = [args.content] if args.content else sorted(
        p for p in glob.glob(os.path.join(args.dir, "*.content.json"))
        if not os.path.basename(p).startswith("oneimage"))
    reports = [audit_one(p) for p in paths]

    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 1 if any(r["dead_tuning"] for r in reports) else 0

    print(f"■ 配音审计（{len(reports)} 条）")
    if args.content:
        r = reports[0]
        print(f"\n{r['file']}  音色 {r['preset']}（{r['voice']}）")
        print(f"  {'镜头':<10}{'情绪':<9}{'来源':<10}{'语速':>7}{'音高':>8}")
        for row in r["shots"]:
            print(f"  {row['shot']:<10}{row['mood']:<9}{row['source']:<10}"
                  f"{row['rate']:>7}{row['pitch']:>8}")
        if r["dead_tuning"]:
            print(f"  [!] mood_tuning 里这些情绪没有对应镜头，白写：{', '.join(r['dead_tuning'])}")
        return 1 if r["dead_tuning"] else 0

    flat = [r for r in reports if all(s["mood"] == "-" for s in r["shots"])]
    dead = [(r["file"], r["dead_tuning"]) for r in reports if r["dead_tuning"]]
    mixed = [(r["file"], [s["shot"] for s in r["shots"] if s["source"] == "inferred"])
             for r in reports if any(s["source"] == "inferred" for s in r["shots"])]

    print(f"  全片扁平（无任何情绪）：{len(flat)} 条")
    print(f"  靠 id 推断情绪：{len(mixed)} 条（这些片子的 shot 里没写 mood）")
    if dead:
        print(f"\n  [!] mood_tuning 白写的 {len(dead)} 条：")
        for f, keys in dead:
            print(f"      {f}: {', '.join(keys)}")
    else:
        print("  mood_tuning 全部有对应镜头 ✅")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
