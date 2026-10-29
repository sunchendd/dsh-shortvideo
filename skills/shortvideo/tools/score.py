"""爆款潜力评分器：把 content.json（+ 可选成片）按 playbook 阈值打分，给出可执行的修复项。

为什么需要它：主观"感觉不错"没法迭代。这里把 docs/xhs-playbook.md 的阈值变成 0~100 分，
每次改稿/改画面都能看到分数变化，也知道下一步该改哪个字段。

六维（权重按 CES 与完播贡献排）：
  钩子 20 · 节奏 20 · 信息密度 15 · 视觉 15 · 封面标题 15 · 互动合规 15

用法：
  python score.py --content content.json
  python score.py --content content.json --video out/x.mp4
  python score.py --content content.json --json     # 机器可读
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

RATE = 4.2          # 普通话口播字/秒（规划用）
HOOK_MAX = 13
SHOT_CHARS_MAX = 38
TOTAL_RANGE = (30, 90)
REDLINE = re.compile(
    r"(国家级|世界级|顶级|最佳|最强|第一品牌|唯一|绝对|百分之百|100%有效|永久|彻底治|"
    r"根治|包治|药到病除|无副作用|官方认证|权威认证|央视推荐|加微|私信领|扫码领|稳赚|暴利|月入过万)")
NUM_RE = re.compile(
    r"\d+(?:\.\d+)?"
    r"|零点[一二三四五六七八九]"
    r"|十的负[一二三四五六七八九]次方"
    r"|[一二三四五六七八九十百千万亿两]{1,6}(?=[扇个位倍秒年天分厘米度种层成色])")
_TAG = re.compile(r"<[^>]+>")


def _width(s):
    """视觉字宽：全角计 1，半角计 0.5（封面每行 ≤6 字按这个口径）。"""
    return sum(1.0 if ord(ch) > 0x2E80 else 0.5 for ch in str(s))
HOOK_SIGNALS = ("错", "不", "为什么", "居然", "竟然", "其实", "真相", "别再", "别用", "陷阱",
                "坑", "秒", "%", "倍", "冷知识", "反常识")


def _clauses(text):
    return [c for c in re.split(r"[，。；：？！,;?!\s]+", text or "") if c.strip()]


def _chars(text):
    return len(re.sub(r"\s", "", text or ""))


def _shot_secs(shot):
    """估时：配音（按 4.2 字/秒）× 情绪修正 + 留白 0.7s。"""
    mood = (shot.get("mood") or "").lower()
    rate = {"hook": 5.0, "reveal": 4.6, "win": 4.4}.get(mood, RATE)
    return max(shot.get("min_dur", 4.0), _chars(shot["narration"]) / rate + 0.7)


# ---------------------------------------------------------------- 各维度

def score_hook(cfg, shots, miss, ok=lambda *a: None):
    pts, mx = 0.0, 20.0
    if not shots:
        miss("钩子", 0, mx, "没有镜头")
        return 0, mx
    first = _clauses(shots[0]["narration"])
    head = first[0] if first else ""
    if len(head) <= HOOK_MAX:
        pts += 10
    else:
        miss("钩子", 0, 10, f"首句 {len(head)} 字（≤{HOOK_MAX}），3 秒念不完：{head}")
    hook_text = shots[0]["narration"]
    has_signal = any(sig in hook_text for sig in HOOK_SIGNALS) or re.search(r"[？?]|[吗呢][。？?]?$", hook_text)
    if has_signal:
        pts += 5
    else:
        miss("钩子", 0, 5, "首镜没有反常识/数字/疑问等钩子信号词")
    if NUM_RE.search(shots[0]["narration"]):
        pts += 5
    else:
        miss("钩子", 0, 5, "首镜没有具体数字（数字是点击率最稳的钩子）")
    if pts == mx:
        ok("钩子", "首句短、有钩子信号、含具体数字")
    return pts, mx


def score_rhythm(cfg, shots, miss, ok=lambda *a: None):
    pts, mx = 0.0, 20.0
    secs = [_shot_secs(s) for s in shots]
    total = sum(secs)
    worst = 0.0
    for s, d in zip(shots, secs):
        n = _chars(s["narration"])
        dens = n / max(0.1, d - 0.7)
        limit = 6.2 if s.get("id") == "hook" else 5.4
        worst = max(worst, dens / limit)
        if dens > limit:
            miss("节奏", 0, 5, f"{s.get('id')} 语速 {dens:.1f} 字/秒 > {limit}（会赶）")
    if worst <= 1.0:
        pts += 8
    elif worst <= 1.1:
        pts += 5
        miss("节奏", 0, 3, "有镜头的语速略超上限")
    if TOTAL_RANGE[0] <= total <= TOTAL_RANGE[1]:
        pts += 6
    else:
        miss("节奏", 0, 6, f"总时长 {total:.0f}s 不在 {TOTAL_RANGE[0]}~{TOTAL_RANGE[1]}s 甜区")
    avg = total / max(1, len(shots))
    if 4.0 <= avg <= 9.5:
        pts += 6
    else:
        miss("节奏", 0, 6, f"平均镜头 {avg:.1f}s（建议 4~9.5s，太碎或太拖都掉完播）")
    if pts == mx:
        ok("节奏", f"总长 {total:.0f}s，平均镜头 {avg:.1f}s，语速都在舒适区")
    return pts, mx


def _ngram_repeat(text, n=4):
    """4 字重复率：口播词高度重复会被平台判"非原创"（playbook §6 红线）。"""
    clean = re.sub(r"[^\u4e00-\u9fa5]", "", text)
    grams = [clean[i:i + n] for i in range(max(0, len(clean) - n + 1))]
    if not grams:
        return 0.0, []
    seen = {}
    for g in grams:
        seen[g] = seen.get(g, 0) + 1
    dup = {g: c for g, c in seen.items() if c >= 3}
    return len(dup) / len(grams), sorted(dup, key=lambda g: -dup[g])[:5]


def score_info(cfg, shots, miss, ok=lambda *a: None):
    pts, mx = 0.0, 15.0
    text = " ".join(s["narration"] for s in shots)
    n = _chars(text)
    if 90 <= n <= 200:
        pts += 6
    else:
        miss("信息密度", 0, 6, f"总字数 {n}（90~200 较合适）")
    nums = len(NUM_RE.findall(text))
    if nums >= 3:
        pts += 5
    else:
        miss("信息密度", 0, 5, f"具体数字只有 {nums} 个（≥3 个才有实感）")
    if len(shots) >= 4:
        pts += 4
    else:
        miss("信息密度", 0, 4, "镜头数 <4，信息层次不够")
    ratio, dups = _ngram_repeat(text)
    if ratio <= 0.02:
        pass                      # 不单独给分，只在超标时扣
    else:
        pts = max(0.0, pts - 3)
        miss("信息密度", 0, 3, f"4 字重复率 {ratio * 100:.1f}%（{dups}），批量生成易被判非原创")
    if pts == mx:
        ok("信息密度", f"{n} 字 / {nums} 个数字 / {len(shots)} 个镜头 / 无明显重复")
    return pts, mx


def score_visual(cfg, shots, miss, ok=lambda *a: None):
    pts, mx = 0.0, 15.0
    deck = cfg.get("deck") or {}
    kinds = {str((v or {}).get("kind") or "") for v in deck.values()}
    if len(kinds) >= 3:
        pts += 6
    elif len(kinds) == 2:
        pts += 4
        miss("视觉", 0, 2, f"只用了 {len(kinds)} 种画面类型，容易一个样子到底")
    else:
        miss("视觉", 0, 6, "deck 画面类型不足（建议 ≥3 种 kind 交替）")
    emph = sum(len(s.get("emphasis") or []) for s in shots)
    if 3 <= emph <= 10:
        pts += 4
    else:
        miss("视觉", 0, 4, f"关键词高亮 {emph} 个（建议每镜 1~2 个，共 3~10 个）")
    trans = (cfg.get("transition") or {}).get("type")
    per_shot = any(s.get("transition_in") for s in shots)
    if trans or per_shot:
        pts += 3
    else:
        miss("视觉", 0, 3, "没配转场（镜头硬切会显得糙）")
    if cfg.get("chrome", True):
        pts += 2
    else:
        miss("视觉", 0, 2, "关掉了顶部分段进度点（chorme）：观众不知道还剩几段")
    if pts == mx:
        ok("视觉", f"{len(kinds)} 种画面 kind，{emph} 处关键词高亮，有转场")
    return pts, mx


def score_cover(cfg, shots, miss, ok=lambda *a: None):
    pts, mx = 0.0, 15.0
    cov = cfg.get("cover") or {}
    head = cov.get("headline") or []
    if head and all(_width(x) <= 6 for x in head) and len(head) <= 3:
        pts += 5
    else:
        miss("封面标题", 0, 5, f"封面主标题每行需 ≤6 字宽且 ≤3 行，当前 {head}")
    if cov.get("highlight"):
        pts += 3
    else:
        miss("封面标题", 0, 3, "封面没有 highlight（关键词没有对比色强调）")
    if cov.get("sub") and len(str(cov["sub"])) <= 20:
        pts += 3
    else:
        miss("封面标题", 0, 3, "封面 sub 缺失或 >20 字")
    tags = cov.get("tags") or []
    if 2 <= len(tags) <= 5:
        pts += 2
    else:
        miss("封面标题", 0, 2, f"话题标签 {len(tags)} 个（建议 2~5）")
    pub = cfg.get("publish_title") or cfg.get("title") or ""
    if 10 <= len(pub) <= 22:
        pts += 2
    else:
        miss("封面标题", 0, 2, f"标题 {len(pub)} 字（建议 12~20）")
    if pts == mx:
        ok("封面标题", "封面三行内、有高亮、有副标、有标签，标题长度达标")
    return pts, mx


def score_engage(cfg, shots, miss, ok=lambda *a: None):
    pts, mx = 0.0, 15.0
    text = " ".join(s["narration"] for s in shots) + " " + str(cfg.get("title") or "")
    tail = shots[-1]["narration"] if shots else ""
    if re.search(r"(关注|收藏|评论|留言|评论区)", tail):
        pts += 6
    else:
        miss("互动合规", 0, 6, "结尾没有互动引导（CES 里关注的权重是点赞的 8 倍）")
    bad = set(REDLINE.findall(text))
    if bad:
        miss("互动合规", 0, 6, f"出现平台红线词：{sorted(bad)}")
    else:
        pts += 6
    if cfg.get("disclosure", "AI 配音"):
        pts += 3
    else:
        miss("互动合规", 0, 3, "没有 AI 合成语音标识（合成人声需显著标识）")
    if pts == mx:
        ok("互动合规", "有互动引导、无红线词、有 AI 标识")
    return pts, mx


# ---------------------------------------------------------------- 成片技术分

def video_facts(path):
    """读成片技术参数（用 imageio-ffmpeg 自带 ffmpeg，不依赖系统装没装）。"""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        ff = "ffmpeg"
    info = subprocess.run([ff, "-hide_banner", "-i", path],
                          capture_output=True, text=True).stderr
    vol = subprocess.run([ff, "-hide_banner", "-i", path, "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    out = {"size_mb": round(os.path.getsize(path) / 1048576, 2)}
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info)
    if m:
        out["dur"] = round(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)), 2)
    m = re.search(r"Stream #0:0.*?(\d{3,4})x(\d{3,4})", info, re.S)
    if m:
        out["size"] = f"{m.group(1)}x{m.group(2)}"
    m = re.search(r"(\d+(?:\.\d+)?) fps", info)
    if m:
        out["fps"] = float(m.group(1))
    m = re.search(r"max_volume: (-?[\d.]+) dB", vol)
    if m:
        out["peak_db"] = float(m.group(1))
    m = re.search(r"mean_volume: (-?[\d.]+) dB", vol)
    if m:
        out["mean_db"] = float(m.group(1))
    return out


GRADE = ((85, "S 爆款潜力"), (75, "A 可发"), (65, "B 需打磨"), (50, "C 有硬伤"), (0, "D 别发"))


def run(cfg, video=None):
    shots = cfg.get("shots") or []
    rows = []
    def miss(dim, got, mx, msg):
        rows.append({"dim": dim, "msg": msg, "pass": False})

    def good(dim, msg):
        rows.append({"dim": dim, "msg": msg, "pass": True})
    total = 0.0
    for fn in (score_hook, score_rhythm, score_info, score_visual, score_cover, score_engage):
        got, mx = fn(cfg, shots, miss, good)
        total += got
    res = {"score": round(total, 1), "grade": next(g for s, g in GRADE if total >= s),
           "extra": rows}
    if video:
        res["video"] = video_facts(video)
    return res


def main():
    ap = argparse.ArgumentParser(description="爆款潜力评分（0~100）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--video", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    cfg = json.load(open(args.content, encoding="utf-8"))
    res = run(cfg, args.video)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print(f"\n爆款潜力 {res['score']}/100  [{res['grade']}]\n" + "─" * 52)
    dims = {}
    for row in res["extra"]:
        dims.setdefault(row["dim"], []).append(row)
    for dim, rows_ in dims.items():
        bad = [r for r in rows_ if not r["pass"]]
        print(f"\n■ {dim}" + ("  ✅" if not bad else f"  ⚠ {len(bad)} 项"))
        for r in rows_:
            print(("   - " if not r["pass"] else "   ✔ ") + r["msg"])
    if "video" in res:
        v = res["video"]
        print(f"\n■ 成片  {v.get('size', '?')} / {v.get('fps', '?')}fps / {v.get('dur', '?')}s / "
              f"{v.get('size_mb', '?')}MB / 峰值 {v.get('peak_db', '?')}dB")
    print()


if __name__ == "__main__":
    main()
