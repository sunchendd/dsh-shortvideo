"""主流程：content.json + 题材包 -> 成片 mp4。

用法（在 project/ 下）：
  python $SKILL_DIR/tools/make.py --content content.json --topic topic_demo \
         --out out/demo.mp4            # 全量渲染（默认多进程并行）
  ... --preview                       # 每镜头 2 张关键帧（快速审片）
  ... --jobs 1                        # 关掉并行，单进程渲染（调试用）
  ... --preset veryfast --crf 23      # 调整编码速度/质量

题材包（--topic 指向的 import 名或 .py 路径）需提供：
    def build(ctx) -> {镜头id: scene(img, t, dur, ctx, S)}
镜头 id 必须与 content.json 的 shots[].id 对应。

速度：渲染是纯 CPU 的 Pillow 任务，默认按 CPU 核数开进程池；帧按顺序写入
ffmpeg 的 stdin，用「滑动窗口」控制内存（不攒帧）。
"""
import argparse
import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections import deque

from PIL import Image, ImageDraw


# ---------------------------------------------------------------- 运行环境自愈
# 依赖装在 skill 目录自带的 .venv 里；如果当前解释器缺依赖（比如直接 `python3 make.py`），
# 自动 exec 到那个 venv，用户不用记路径。
def _bootstrap_venv():
    import importlib.util as _u
    need = ["PIL", "edge_tts", "imageio_ffmpeg"]
    if all(_u.find_spec(m) for m in need):
        return
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    venv_dir = os.path.join(skill_dir, ".venv")
    for cand in (os.path.join(venv_dir, "bin", "python"),
                 os.path.join(venv_dir, "Scripts", "python.exe")):
        # 注意：venv 里的 python 是指向系统解释器的符号链接，不能用 realpath 比，
        # 要比 sys.prefix 是否已经是这个 venv。
        if os.path.exists(cand) and os.path.realpath(sys.prefix) != os.path.realpath(venv_dir):
            print(f"[env] 当前 Python 缺依赖，切到 {cand}", flush=True)
            os.execv(cand, [cand] + sys.argv)
    raise SystemExit(
        "[x] 缺少运行依赖（Pillow / edge-tts / imageio-ffmpeg）。\n"
        "    请在仓库根目录运行 ./install.sh，或：\n"
        "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_bootstrap_venv()

from shortvideo import runtime as R
from shortvideo import tts
from shortvideo.scenes import SceneAPI as S

CLAUSE = re.compile(r"[^，。；：？！,;?!]+[，。；：？！]?")


# ---------------------------------------------------------------- 题材包

def load_topic(ref):
    """ref 可以是模块名（topics 目录下）或 .py 文件路径。"""
    if ref.endswith(".py") and os.path.exists(ref):
        spec = importlib.util.spec_from_file_location("topic_user", ref)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return importlib.import_module(f"topics.{ref}")


# ---------------------------------------------------------------- 字幕

def split_clauses(text):
    """按标点切句；字幕不显示句末标点（"六个数，" -> "六个数"）。"""
    out = []
    for c in CLAUSE.findall(text):
        c = c.strip().rstrip("，。；：？！,;?!").strip()
        if c:
            out.append(c)
    return out


def build_subs(narration, t0, dur):
    """按标点切句，按字数比例分配时长。"""
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


# ---------------------------------------------------------------- 渲染核心（主进程/子进程共用）

TRANS_TYPES = ("fade", "slide", "slide_up", "wipe")


def _bg():
    fn = getattr(R, "background_rgba", None)
    return fn() if fn is not None else R.background().convert("RGBA")


def _blend_transition(prev, cur, p, mode, w, h):
    """prev/cur 都是 RGBA 帧；p=0 全 prev，p=1 全 cur。

    位移类转场用 ease_in_out（起步慢、中段快、收尾慢），ease_out 会让前两帧就"跳完"。
    """
    p = R.clamp(p)
    p = R.ease_out(p) if mode == "fade" else R.ease_in_out(p)
    if mode == "fade":
        return Image.blend(prev, cur, p)
    out = prev.copy()
    if mode == "slide":
        dx = int(round(w * (1 - p)))
        out.paste(cur, (dx, 0))
        if dx > 0:
            d = ImageDraw.Draw(out, "RGBA")
            d.rectangle((dx - 3, 0, dx, h), fill=R.ACCENT + (200,))
        return out
    if mode == "slide_up":
        dy = int(round(h * (1 - p)))
        out.paste(cur, (0, dy))
        if dy > 0:
            ImageDraw.Draw(out, "RGBA").rectangle((0, dy - 3, w, dy), fill=R.ACCENT + (200,))
        return out
    # wipe：从左向右揭开
    cut = max(1, int(round(w * p)))
    out.paste(cur.crop((0, 0, cut, h)), (0, 0))
    ImageDraw.Draw(out, "RGBA").rectangle((cut - 4, 0, cut, h), fill=R.ACCENT + (220,))
    return out


class FrameRenderer:
    """按 (镜头下标, 镜头内帧号) 出帧；负责背景、题材、转场、字幕、进度条。

    转场只做"镜头之间"，第一条镜头没有入场转场。上一镜的末帧按需渲染并缓存
    （每个进程一份），所以分段并行渲染时跨段边界也能正确出转场帧。
    """

    def __init__(self, scene_fns, ctx, total, fps, cfg, timeline):
        self.scene_fns = scene_fns
        self.ctx = ctx
        self.total = total
        self.fps = fps
        self.timeline = timeline
        self.w, self.h = cfg.get("size", [R.W, R.H])
        trans = cfg.get("transition") or {}
        self.ttype = str(trans.get("type") or "").lower()
        self.tdur = float(trans.get("dur", 0.24) or 0)
        self._prev = {}

    def _trans_for(self, idx):
        """该镜头的入场转场 (类型, 帧数)；支持每镜头 transition_in 覆盖全局。"""
        item = self.timeline[idx]
        if idx == 0:
            return "", 0
        t = item.get("transition_in") or (None if self.ttype in ("", "none") else
                                          {"type": self.ttype, "dur": self.tdur})
        if not t:
            return "", 0
        mode = str(t.get("type", self.ttype)).lower()
        if mode not in TRANS_TYPES:
            return "", 0
        dur = float(t.get("dur", self.tdur) or 0)
        return mode, max(1, int(round(dur * self.fps)))

    def _prev_scene(self, idx):
        """上一镜的最后一帧（不带字幕/进度条），用于转场。"""
        hit = self._prev.get(idx)
        if hit is not None:
            return hit
        item = self.timeline[idx]
        img = _bg()
        self.scene_fns[item["id"]](img, max(0.0, item["dur"] - 1.0 / self.fps),
                                   item["dur"], self.ctx, S)
        self._prev[idx] = img
        return img

    def scene_frame(self, idx, i):
        item = self.timeline[idx]
        img = _bg()
        self.scene_fns[item["id"]](img, i / self.fps, item["dur"], self.ctx, S)
        mode, nframes = self._trans_for(idx)
        if mode and i < nframes:
            img = _blend_transition(self._prev_scene(idx - 1), img,
                                    (i + 1) / nframes, mode, self.w, self.h)
        return img

    def frame(self, idx, i):
        item = self.timeline[idx]
        img = self.scene_frame(idx, i)
        lt = i / self.fps
        subs = item.get("subs") or []
        if subs:
            text, a = sub_at(subs, item["start"] + lt)
            S.caption(img, text, a, emphasis=item.get("emphasis"))
        S.progress_bar(img, (item["start"] + lt) / self.total)
        return img


def render_frame(scene_fn, item, lt, ctx, total, with_caption=True, emphasis=None,
                 with_progress=True):
    """单帧渲染（预览/封面用；不支持转场）。"""
    img = _bg()
    scene_fn(img, lt, item["dur"], ctx, S)
    if with_caption:
        s, a = sub_at(item["subs"], item["start"] + lt)
        S.caption(img, s, a, emphasis=emphasis)
    if with_progress:
        S.progress_bar(img, (item["start"] + lt) / total)
    return img


# --- 单进程路径的题材包缓存
_W = {}


def _scenes_for(topic_ref, ctx):
    if "_scenes" not in _W:
        _W["_scenes"] = load_topic(topic_ref).build(ctx)
    return _W["_scenes"]


def _jobs_for(timeline, fps):
    for idx, item in enumerate(timeline):
        n = max(1, int(round(item["dur"] * fps)))
        for i in range(n):
            yield (idx, i, fps)


def _frame_counts(timeline, fps):
    return [max(1, int(round(item["dur"] * fps))) for item in timeline]


# ---------------------------------------------------------------- 并行：分段编码
#
# 为什么不是「多进程渲染 + 父进程拼帧」：1080x1920 一帧 6.2MB，跨进程搬帧的
# memcpy 带宽本机约 0.5GB/s，实测把并行收益全吃掉（8 进程 85fps < 单进程 152fps）。
# 这里改成 let each worker 自己渲染+自己编码成一段 mp4（不跨进程搬像素），
# 最后用 ffmpeg concat + stream copy 无损拼接并混入音轨 —— 渲染和编码一起并行。
#
# 负载均衡：先用 1 帧/镜头测出各镜头单帧成本，再按成本做 LPT 分配。


def _chunks(timeline, fps, target_chunks):
    """把帧按镜头切成若干连续段 [(shot_idx, i0, i1), ...]（帧序）。"""
    counts = _frame_counts(timeline, fps)
    total = sum(counts) or 1
    pieces = []
    for idx, n in enumerate(counts):
        k = max(1, min(n, round(target_chunks * n / total)))
        step = n / k
        start = 0
        for j in range(k):
            end = n if j == k - 1 else int(round((j + 1) * step))
            if end > start:
                pieces.append((idx, start, end))
            start = end
    return pieces


def _sample_cost(timeline, scene_fns, ctx, total, fps):
    """每个镜头的单帧成本（秒）：渲染 2 帧取均值，用于分段负载均衡。"""
    cost = {}
    for item in timeline:
        ts = (item["dur"] * 0.45, item["dur"] * 0.9)
        t0 = time.time()
        for lt in ts:
            render_frame(scene_fns[item["id"]], item, lt, ctx, total, with_caption=False)
        cost[item["id"]] = max(1e-4, (time.time() - t0) / len(ts))
    return cost


def _assign(pieces, cost_of, jobs):
    """LPT 分配：成本大的段优先落到当前总成本最小的 worker。"""
    order = sorted(range(len(pieces)), key=lambda k: -cost_of(k))
    load, plan = [0.0] * jobs, [[] for _ in range(jobs)]
    for k in order:
        w = min(range(jobs), key=lambda j: load[j])
        plan[w].append(k)
        load[w] += cost_of(k)
    return plan


def _child_encode(worker, segs, seg_path, renderer, w, h, fps, preset, crf, threads):
    """子进程：渲染自己负责的每一段并直接编码成 mp4，最后退出。

    renderer 是父进程建好的 FrameRenderer，fork 后子进程直接用（转场帧按需自渲染）。
    """
    try:
        R.W, R.H, R.FPS = w, h, fps
        ff = tts.ffmpeg_exe()
        pieces = renderer.pieces
        for k in segs:
            idx, i0, i1 = pieces[k]
            path = seg_path(k)
            proc = subprocess.Popen(
                [ff, "-y", "-loglevel", "error",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                 "-r", str(fps), "-i", "-",
                 "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                 "-threads", str(threads), "-pix_fmt", "yuv420p",
                 "-g", str(fps * 2), "-an", path],
                stdin=subprocess.PIPE)
            try:
                for i in range(i0, i1):
                    proc.stdin.write(renderer.frame(idx, i).convert("RGB").tobytes())
            finally:
                proc.stdin.close()
                rc = proc.wait()
            if rc != 0:
                raise RuntimeError(f"分段编码失败（段 {k}, ffmpeg 退出码 {rc}）")
        os._exit(0)
    except BrokenPipeError:
        os._exit(1)
    except BaseException:  # noqa: BLE001
        traceback.print_exc()
        os._exit(1)


def encode_by_segments(timeline, topic_ref, ctx, total, w, h, fps, jobs, cfg,
                       build, preset, crf, audio, out_path, keep_segments=False):
    """分段并行渲染+编码，再无损拼接并混音。返回耗时秒数。"""
    scene_fns = _scenes_for(topic_ref, ctx)
    renderer = FrameRenderer(scene_fns, ctx, total, fps, cfg, timeline)
    cost = _sample_cost(timeline, scene_fns, ctx, total, fps)
    pieces = _chunks(timeline, fps, max(jobs * 2, jobs))
    cost_of = lambda k: (pieces[k][2] - pieces[k][1]) * cost[timeline[pieces[k][0]]["id"]]  # noqa: E731
    plan = _assign(pieces, cost_of, jobs)
    seg_path = lambda k: os.path.join(build, f"seg_{k:03d}.mp4")  # noqa: E731

    # 子进程需要用到的共同状态（fork 后共享）
    renderer.pieces = pieces
    cpu = os.cpu_count() or 1
    threads = max(1, min(8, cpu // max(1, jobs)))

    t0 = time.time()
    pids = []
    for wid, segs in enumerate(plan):
        if not segs:
            continue
        pid = os.fork()
        if pid == 0:
            _child_encode(wid, segs, seg_path, renderer, w, h, fps, preset, crf, threads)
        pids.append((wid, pid))
    failed = []
    for wid, pid in pids:
        _p, status = os.waitpid(pid, 0)
        if status != 0:
            failed.append((wid, status))
    if failed:
        raise SystemExit(f"[x] 并行编码失败: {failed}")

    # concat（stream copy，不重编码）
    list_path = os.path.join(build, "segments.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for k in range(len(pieces)):
            f.write(f"file '{os.path.basename(seg_path(k))}'\n")
    ff = tts.ffmpeg_exe()
    # cwd=build 让 list 里的相对文件名生效；路径参数统一转绝对路径
    cmd = [ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", os.path.abspath(list_path), "-i", os.path.abspath(audio),
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
           "-shortest", "-movflags", "+faststart", os.path.abspath(out_path)]
    rc = subprocess.run(cmd, cwd=build, capture_output=True, text=True)
    if rc.returncode != 0:
        raise SystemExit(f"[x] 分段拼接失败:\n{rc.stderr[-800:]}")
    if not keep_segments:                       # 中间分段占空间，默认清掉
        for k in range(len(pieces)):
            try:
                os.remove(seg_path(k))
            except OSError:
                pass
    return time.time() - t0



# ---------------------------------------------------------------- 脚本体检（依据 docs/xhs-playbook.md）

# 小红书红线：绝对化用语 / 医疗承诺 / 权威背书 / 导流
REDLINE = re.compile(
    r"(国家级|世界级|顶级|最佳|最强|第一品牌|唯一|绝对|百分之百|100%有效|永久|彻底治|"
    r"根治|包治|药到病除|无副作用|官方认证|权威认证|央视推荐|加微|私信领|扫码领|"
    r"稳赚|暴利|月入过万)")
_SPEECH_RATE = 4.2   # 普通话口播 240~260 字/分 ≈ 4.0~4.33 字/秒


def _clauses(text):
    return [c for c in re.split(r"[，。；：？！,;?!\s]+", text or "") if c.strip()]


def lint_script(cfg, timeline):
    """开工前的脚本体检：返回 [(级别, 说明)]，级别 error/warn/ok。"""
    out = []
    shots = cfg.get("shots") or []
    full = " ".join(s.get("narration", "") for s in shots)
    title = cfg.get("title") or ""
    cov = cfg.get("cover") or {}

    # 1) 钩子：前 3 秒必须交代完钩子，4.2 字/秒 × 3s ≈ 12.6 字
    if shots:
        first = _clauses(shots[0].get("narration", ""))
        head = first[0] if first else ""
        if len(head) > 13:
            out.append(("warn", f"钩子首句 {len(head)} 字（建议 ≤13 字），3 秒念不完：{head}"))

    # 2) 每镜语速：以配音实际时长算"字/秒"，超过 5.4 就是赶（人类口播舒适区 4.0~4.6）
    for shot, item in zip(shots, timeline):
        n = len(re.sub(r"\s", "", shot.get("narration", "")))
        adur = item.get("adur") or item["dur"]
        density = n / adur if adur else 0
        if density > 5.4:
            out.append(("error", f"{shot.get('id')}: {density:.1f} 字/秒（{n}字/{adur:.1f}s）"
                                  f"语速过快，建议压到 ≤4.6（删 1/6 字或加时长）"))
        elif density > 4.8:
            out.append(("warn", f"{shot.get('id')}: {density:.1f} 字/秒偏快（舒适区 4.0~4.6）"))
        if n > 38:
            out.append(("warn", f"{shot.get('id')}: {n} 字超单镜上限 38 字（字幕会顶满两行）"))
        if item["dur"] - adur < 1.0:
            out.append(("warn", f"{shot.get('id')}: 镜头留白仅 {item['dur'] - adur:.2f}s（建议 ≥1s，"
                                f"留给图形动效和呼吸）"))

    # 3) 总时长：知识类完播甜区 30~90s
    total = timeline[-1]["start"] + timeline[-1]["dur"] if timeline else 0
    if total < 30:
        out.append(("warn", f"总时长 {total:.1f}s < 30s，信息量可能不足以支撑完播"))
    elif total > 90:
        out.append(("warn", f"总时长 {total:.1f}s > 90s，完播率会明显下滑"))

    # 4) 标题/封面/口播红线词
    for where, text in (("标题", title), ("封面", " ".join(
            [str(x) for x in (cov.get("headline") or [])] + [cov.get("sub") or ""])), ("口播", full)):
        bad = set(REDLINE.findall(text or ""))
        if bad:
            out.append(("error", f"{where}含平台红线词：{sorted(bad)}"))

    # 5) 标题长度 / 封面字段
    if title and not (10 <= len(title) <= 22):
        out.append(("warn", f"标题 {len(title)} 字（建议 12~20 字）"))
    for ln in (cov.get("headline") or []):
        if len(str(ln)) > 6:
            out.append(("warn", f"封面主标题每行建议 ≤6 字，当前「{ln}」{len(str(ln))} 字"))
    if not (cov.get("tags") or []):
        out.append(("warn", "封面还没配话题标签（建议 2~5 个）"))

    # 6) 结尾互动（CES 里关注权重最高）
    tail = (shots[-1].get("narration", "") if shots else "")
    if not re.search(r"(关注|收藏|评论|留言|私信|评论区)", tail):
        out.append(("warn", "结尾没有互动引导（关注/收藏/评论区），会拉低关注转化"))

    return out or [("ok", "脚本体检通过")]


def print_lint(results, strict=False):
    bad = False
    for level, msg in results:
        tag = {"error": "[x]", "warn": "[!]", "ok": "[ok]"}[level]
        print(f"[lint] {tag} {msg}")
        bad |= level == "error"
    if bad and strict:
        raise SystemExit("[x] 脚本体检不通过（--no-strict 可强制出片）")
    return not bad


# ---------------------------------------------------------------- 校验（对应 SKILL.md 质量检查单）


def probe(out_path, expect_dur=None):
    """用 ffmpeg -i / volumedetect 自检：流、时长、峰值电平。返回 (ok, 报告行)。"""
    ff = tts.ffmpeg_exe()
    lines = []
    ok = True
    info = subprocess.run([ff, "-hide_banner", "-i", out_path],
                          capture_output=True, text=True).stderr
    has_v = "Video: h264" in info or "Video: " in info
    has_a = "Audio: aac" in info or "Audio: " in info
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info)
    dur = None
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    lines.append(f"流：视频={'有' if has_v else '无'} 音频={'有' if has_a else '无'} 时长={dur if dur else '?'}s")
    ok &= has_v and has_a
    if expect_dur and dur and abs(dur - expect_dur) > 0.75:
        lines.append(f"[!] 时长偏差 {dur - expect_dur:+.2f}s（预期 {expect_dur:.2f}s）")
        ok = False
    vol = subprocess.run([ff, "-hide_banner", "-i", out_path, "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    mm = re.search(r"max_volume: (-?[\d.]+) dB", vol)
    mn = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    if mm:
        mx = float(mm.group(1))
        lines.append(f"音量：峰值 {mx:.1f} dB / 均值 {mn.group(1) if mn else '?'} dB")
        if mx > -0.2:
            lines.append("[!] 峰值可能削波")
            ok = False
        if mx < -50:
            lines.append("[i] 接近静音（无配音或降级静音？）")
    return ok, lines


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="shortvideo 主流程")
    ap.add_argument("--content", required=True)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--out", default="out/video.mp4")
    ap.add_argument("--preview", action="store_true", help="每镜头 2 张关键帧，秒级审片")
    ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--jobs", type=int, default=None,
                    help="并行分段数（默认 auto=min(4, 核数//2)；1=单进程边渲边编）")
    ap.add_argument("--keep-segments", action="store_true", help="保留并行中间分段文件")
    ap.add_argument("--preset", default="medium", help="x264 preset（veryfast 更快）")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--build-dir", default=None)
    ap.add_argument("--clean", action="store_true", help="先清空 build 目录（含 TTS 缓存）")
    ap.add_argument("--no-check", action="store_true", help="跳过成片自检")
    ap.add_argument("--no-cover", action="store_true", help="不生成爆款封面图")
    ap.add_argument("--strict", action="store_true", help="脚本体检有 error 就中止")
    ap.add_argument("--no-lint", action="store_true", help="跳过脚本体检")
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

    out_dir = os.path.dirname(args.out) or "out"
    os.makedirs(out_dir, exist_ok=True)
    build = args.build_dir or os.path.normpath(os.path.join(out_dir, os.pardir, "build"))
    if args.clean and os.path.isdir(build):
        shutil.rmtree(build)
    os.makedirs(build, exist_ok=True)

    print(f"[font] {R.font_info()}")
    voice = tts.build_voice(cfg.get("voice", {}))
    print(f"[voice] {voice.name}")

    # ---------- 配音 + 对轴（能并行就并行） ----------
    lead, tail = cfg.get("lead_in", 0.15), cfg.get("tail", 0.55)
    items = [{"text": shot["narration"], "mood": shot.get("mood"),
              "path": os.path.join(build, f"{i:02d}_{shot['id']}.wav")}
             for i, shot in enumerate(cfg["shots"])]
    t_tts = time.time()
    if hasattr(tts, "synth_all"):
        adurs = tts.synth_all(voice, items)
    else:  # 兼容旧版 tts 模块
        adurs = [voice.synth(it["text"], it["path"], mood=it["mood"]) for it in items]
    print(f"[tts] {len(items)} 段配音用时 {time.time() - t_tts:.1f}s")
    degraded = getattr(voice, "failures", 0) or getattr(voice, "offline", False)
    if degraded:
        print(f"[!] 有 {getattr(voice, 'failures', '?')} 段配音降级为静音（网络/代理不可用），"
              f"成片仍有字幕")

    timeline, segs, cursor = [], [], 0.0
    for item, adur in zip(items, adurs):
        shot = cfg["shots"][len(timeline)]
        dur = max(shot.get("min_dur", 4.0), adur + lead + tail)
        timeline.append({**shot, "start": cursor, "dur": dur,
                         "wav": item["path"], "adur": adur})
        segs.append((cursor + lead, item["path"]))
        cursor += dur
        print(f"  {shot['id']:<10} 配音 {adur:5.2f}s -> 镜头 {dur:5.2f}s")
    total = cursor
    audio = os.path.join(build, "audio.wav")
    tts.concat(segs, total, audio)
    print(f"[time] 总时长 {total:.2f}s")

    for item in timeline:
        item["subs"] = build_subs(item["narration"], item["start"] + lead,
                                  item["dur"] - lead - tail)

    if not args.no_lint:
        print_lint(lint_script(cfg, timeline), strict=args.strict)

    # ---------- 审片模式 ----------
    if args.preview:
        pdir = os.path.join(build, "preview")
        os.makedirs(pdir, exist_ok=True)
        for item in timeline:
            for frac in (0.45, 0.9):
                lt = item["dur"] * frac
                render_frame(scene_fns[item["id"]], item, lt, ctx, total).convert("RGB").save(
                    os.path.join(pdir, f"{item['id']}_{int(frac * 100)}.png"), quality=92)
        print(f"[ok] 预览帧: {pdir}")
        return

    # ---------- 渲染 + 编码 ----------
    out_path = args.out
    counts = _frame_counts(timeline, fps)
    total_frames = sum(counts)
    jobs = args.jobs if args.jobs else min(4, max(1, (os.cpu_count() or 1) // 2))
    jobs = max(1, min(jobs, max(1, total_frames // 30)))   # 太短的片子不值得分段
    print(f"[render] {total_frames} 帧 / {fps}fps / preset={args.preset} / "
          f"{'单进程' if jobs == 1 else f'{jobs} 路分段并行'}")

    t0 = time.time()
    if jobs == 1:
        cmd = [tts.ffmpeg_exe(), "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
               "-r", str(fps), "-i", "-",
               "-i", audio,
               "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf),
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
               "-shortest", "-movflags", "+faststart", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        written = 0
        try:
            stream_renderer = FrameRenderer(scene_fns, ctx, total, fps, cfg, timeline)
            for idx, i, _fps in _jobs_for(timeline, fps):
                proc.stdin.write(stream_renderer.frame(idx, i).convert("RGB").tobytes())
                written += 1
                if written % 20 == 0 or written == total_frames:
                    _progress(written, total_frames, t0)
        except BrokenPipeError:
            err = proc.stderr.read().decode("utf-8", "replace")[-800:]
            raise SystemExit(f"[x] ffmpeg 提前退出（{written}/{total_frames} 帧已送）:\n{err}")
        finally:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        rc = proc.wait()
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        print()
        if rc != 0:
            raise SystemExit(f"[x] ffmpeg 退出码 {rc}\n{err[-800:]}")
    else:
        encode_by_segments(timeline, args.topic, ctx, total, w, h, fps, jobs, cfg,
                           build, args.preset, args.crf, audio, out_path,
                           keep_segments=args.keep_segments)
        print(f"\r  渲染 {total_frames}/{total_frames}  {total_frames / (time.time() - t0):5.1f}fps")

    elapsed = time.time() - t0
    frame_cover = os.path.join(out_dir, "cover_frame.png")
    # 封面底图：去掉字幕和进度条，画面更干净（F3 截图式封面用）
    render_frame(scene_fns[timeline[0]["id"]], timeline[0],
                 min(timeline[0]["dur"] * 0.75, timeline[0]["dur"] - 0.1), ctx, total,
                 with_caption=False, with_progress=False).convert("RGB").save(frame_cover)
    size_mb = os.path.getsize(out_path) / 1048576
    print(f"[ok] {out_path}  {size_mb:.1f}MB  {total:.1f}s  "
          f"渲染 {total_frames / elapsed:.1f}fps（{elapsed:.1f}s）")
    if not args.no_cover:
        from cover import build_covers
        for path in build_covers(cfg, out_dir, frame_path=frame_cover):
            print(f"[ok] 封面 {path}")
    print(f"[ok] 帧截图 {frame_cover}")

    if not args.no_check:
        ok, lines = probe(out_path, expect_dur=total)
        for ln in lines:
            print(f"[check] {ln}")
        print("[check] " + ("通过" if ok else "存在问题，请复核"))


def _progress(done, total, t0):
    el = time.time() - t0
    rate = done / el if el > 0 else 0
    eta = (total - done) / rate if rate > 0 else 0
    print(f"\r  渲染 {done}/{total}  {rate:5.1f}fps  ETA {eta:4.0f}s", end="", flush=True)


if __name__ == "__main__":
    main()
