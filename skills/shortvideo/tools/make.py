"""主流程：content.json + 题材包 -> 成片 mp4。

用法（在 project/ 下）：
  python $SKILL_DIR/tools/make.py --content content.json --topic topic_demo \
         --out out/demo.mp4            # 全量渲染
  ... --preview                       # 每镜头 2 张关键帧（快速审片）

题材包（--topic 指向的 import 名或 .py 路径）需提供：
    def build(ctx) -> {镜头id: scene(img, t, dur, ctx)}
镜头 id 必须与 content.json 的 shots[].id 对应。
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo import runtime as R
from shortvideo import tts
from shortvideo.scenes import SceneAPI as S

CLAUSE = re.compile(r"[^，。；：？！,;?!]+[，。；：？！]?")


def load_topic(ref):
    """ref 可以是模块名（topics 目录下）或 .py 文件路径。"""
    if ref.endswith(".py") and os.path.exists(ref):
        spec = importlib.util.spec_from_file_location("topic_user", ref)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return importlib.import_module(f"topics.{ref}")


def split_clauses(text):
    return [c.strip() for c in CLAUSE.findall(text) if c.strip()]


def build_subs(narration, t0, dur):
    clauses = split_clauses(narration)
    if not clauses:
        return []
    total = sum(len(c) for c in clauses)
    subs, cur = [], t0
    for c in clauses:
        d = dur * len(c) / total
        subs.append((cur, cur + d, c))
        cur += d
    return subs


def sub_at(subs, t):
    for a, b, s in subs:
        if a <= t < b:
            return s, min(1.0, (t - a) / 0.12, (b - t) / 0.12)
    return "", 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--content", required=True)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--out", default="out/video.mp4")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--fps", type=int, default=None)
    args = ap.parse_args()

    cfg = json.load(open(args.content, encoding="utf-8"))
    fps = args.fps or cfg.get("fps", 30)
    w, h = cfg.get("size", [R.W, R.H])
    R.W, R.H, R.FPS = w, h, fps

    topic = load_topic(args.topic)
    ctx = dict(cfg.get("ctx", {}))
    ctx.update(topic.prepare(ctx) if hasattr(topic, "prepare") else {})
    scene_fns = topic.build(ctx)
    missing = [s["id"] for s in cfg["shots"] if s["id"] not in scene_fns]
    if missing:
        raise SystemExit(f"[x] 题材包缺镜头: {missing}（content.json 与 topic 的 id 需一致）")

    build = os.path.join(os.path.dirname(args.out) or "build", os.pardir, "build")
    build = os.path.normpath(build)
    os.makedirs(build, exist_ok=True)
    out_dir = os.path.dirname(args.out) or "out"
    os.makedirs(out_dir, exist_ok=True)

    print(f"[font] {R.font_info()}")
    voice = tts.build_voice(cfg.get("voice", {}))
    print(f"[voice] {voice.name}")

    # ---------- 配音 + 对轴 ----------
    timeline, segs, cursor = [], [], 0.0
    lead, tail = cfg.get("lead_in", 0.15), cfg.get("tail", 0.55)
    for i, shot in enumerate(cfg["shots"]):
        wav = os.path.join(build, f"{i:02d}_{shot['id']}.wav")
        adur = voice.synth(shot["narration"], wav, mood=shot.get("mood"))
        dur = max(shot.get("min_dur", 4.0), adur + lead + tail)
        timeline.append({**shot, "start": cursor, "dur": dur, "wav": wav})
        segs.append((cursor + lead, wav))
        cursor += dur
        print(f"  {shot['id']:<10} 配音 {adur:5.2f}s -> 镜头 {dur:5.2f}s")
    total = cursor
    audio = os.path.join(build, "audio.wav")
    tts.concat(segs, total, audio)
    print(f"[time] 总时长 {total:.2f}s")

    for item in timeline:
        item["subs"] = build_subs(item["narration"], item["start"] + lead, item["dur"] - lead - tail)

    def render_frame(item, lt):
        from PIL import Image
        img = R.background().convert("RGBA")
        scene_fns[item["id"]](img, lt, item["dur"], ctx, S)
        s, a = sub_at(item["subs"], item["start"] + lt)
        S.caption(img, s, a)
        S.progress_bar(img, (item["start"] + lt) / total)
        return img

    if args.preview:
        pdir = os.path.join(build, "preview")
        os.makedirs(pdir, exist_ok=True)
        for item in timeline:
            for frac in (0.45, 0.9):
                t = item["dur"] * frac
                render_frame(item, t).convert("RGB").save(
                    os.path.join(pdir, f"{item['id']}_{int(frac*100)}.png"), quality=92)
        print(f"[ok] 预览帧: {pdir}")
        return

    # ---------- 逐帧渲染 -> ffmpeg ----------
    import imageio_ffmpeg
    out_path = args.out
    proc = subprocess.Popen(
        [tts.ffmpeg_exe(), "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
         "-i", audio,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
         out_path],
        stdin=subprocess.PIPE)
    total_frames = int(total * fps)
    done, t0 = 0, time.time()
    for item in timeline:
        n_frames = max(1, int(round(item["dur"] * fps)))
        fn = scene_fns[item["id"]]
        for i in range(n_frames):
            img = render_frame(item, i / fps)
            proc.stdin.write(img.convert("RGB").tobytes())
            done += 1
            if done % 60 == 0 or done == total_frames:
                el = time.time() - t0
                print(f"\r  渲染 {done}/{total_frames}  {done/el:5.1f}fps  "
                      f"ETA {(total_frames-done)/max(done/el,.1):4.0f}s", end="", flush=True)
    proc.stdin.close()
    proc.wait()
    print()
    if proc.returncode != 0:
        raise SystemExit(f"[x] ffmpeg 退出码 {proc.returncode}")

    # 封面：hook 镜头后段一帧
    cover = os.path.join(out_dir, "cover.png")
    render_frame(timeline[0], min(timeline[0]["dur"] * 0.75, timeline[0]["dur"] - 0.1)
                 ).convert("RGB").save(cover)
    print(f"[ok] {out_path}  {os.path.getsize(out_path)/1048576:.1f}MB  {total:.1f}s")
    print(f"[ok] 封面 {cover}")


if __name__ == "__main__":
    main()
