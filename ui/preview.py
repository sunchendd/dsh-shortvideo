#!/usr/bin/env python3
"""片库预览台（本地 Web UI）—— 纯标准库，无第三方依赖。

## 为什么不是"生成一堆静态 HTML"

原来有个 `examples/studio-index.html`，是渲染完吐出来的静态台账：
换主题、改文案之后它不会更新，而且只能看封面，看不了成片、改不了配色。
这个服务直接读 `examples/*.content.json` + `examples/out/`，永远是当前状态。

## 能力

- 浏览 32 条片子的封面 / 成片 / 一张图 / 脚本
- 按主题、长短版、学科筛选，关键词搜索
- **换主题**：改 content.json 并即时重渲封面 + 一张图（约 2 秒）
- **重渲成片**：后台任务 + 进度轮询（约 30~40 秒/条，命中 TTS 缓存则几秒）
- 一键看门禁结果（跑 verify.sh 单条）

## 跑法

    python ui/studio.py --port 8099

只监听 127.0.0.1，不对外。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import hashlib
import urllib.parse

import preview_render as PV
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _find_root(start):
    """向上找带 examples/ 的目录当仓库根。

    装成插件后 ui/ 会被拷到 <skill>/ui，仓库根在上一层；
    开发时 ui/ 在仓库根下，根也在上一层。用"有没有 examples/"判定，两种布局都能跑。
    """
    cur = os.path.abspath(start)
    for _ in range(6):
        if os.path.isdir(os.path.join(cur, "examples")):
            return cur
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    return os.path.dirname(os.path.abspath(start))


REPO = os.environ.get("SV_ROOT") or _find_root(__file__)
EX = os.path.join(REPO, "examples")
OUT = os.path.join(EX, "out")
PY = sys.executable
def _tools_dir(root):
    """工具目录：开发布局在 skills/shortvideo/tools，装成插件后在 <skill>/tools。"""
    for cand in (os.path.join(root, "skills", "shortvideo", "tools"),
                 os.path.join(root, "tools")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(root, "skills", "shortvideo", "tools")

TOOLS = _tools_dir(REPO)

sys.path.insert(0, TOOLS)

_JOBS = {}          # id -> {status, log, started, slug}
_JOBS_LOCK = threading.Lock()
_JOB_SEQ = [0]


def _slug_list():
    out = []
    for f in sorted(os.listdir(EX)):
        if f.endswith(".content.json"):
            out.append(f[: -len(".content.json")])
    return out


def _load(slug):
    p = os.path.join(EX, f"{slug}.content.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def _subject(slug, cfg):
    try:
        from plan import subject_of
        return subject_of(slug, cfg)
    except Exception:  # noqa: BLE001
        return "其他"


def _duration(cfg):
    return round(sum(float(s.get("min_dur") or 0) for s in cfg.get("shots") or []))


def _exists(*parts):
    return all(os.path.exists(os.path.join(OUT, *p)) for p in parts)


def _card(slug):
    cfg = _load(slug)
    if cfg is None:
        return None
    d = os.path.join(OUT, slug)
    files = {}
    for name in ("cover_short.png", "cover_editorial.png", "cover_blueprint.png",
                 "cover_cinematic.png", "cover_9x16.png", "summary.png", f"{slug}.mp4"):
        if os.path.exists(os.path.join(d, name)):
            files[name] = f"/files/{slug}/{name}"
    return {
        "slug": slug,
        # 短版的 content.json 里没有 title，标题在 publish_title（成片标题）里；
        # 直接取 title 会让短版在片库里显示成 slug，实测 9 条全中。
        "title": (cfg.get("title") or cfg.get("publish_title") or cfg.get("topic")
                  or " / ".join((cfg.get("cover") or {}).get("headline") or []) or slug),
        "theme": cfg.get("theme") or "midnight",
        "cover_direction": cfg.get("cover_direction") or "",
        "format": "short" if str(cfg.get("format") or "").lower() == "short" else "long",
        "seconds": _duration(cfg),
        "subject": _subject(slug, cfg),
        "voice": (cfg.get("voice") or {}).get("preset") or "yunxi",
        "kicker": (cfg.get("cover") or {}).get("kicker") or "",
        "files": files,
        "hooks": (cfg.get("shots") or [{}])[0].get("narration") or "",
    }


def _detail(slug):
    cfg = _load(slug)
    if cfg is None:
        return None
    base = _card(slug)
    base["shots"] = [{"id": s.get("id"), "narration": s.get("narration"),
                      "dur": s.get("min_dur"), "note": s.get("note")}
                     for s in cfg.get("shots") or []]
    base["kernel"] = cfg.get("kernel")
    base["refs"] = cfg.get("refs")
    base["cover"] = cfg.get("cover")
    return base


def _rev(slug):
    """渲染版本号：内容或渲染器一改就变，给图片缓存做 cache-busting 用。"""
    try:
        return int(max(
            os.path.getmtime(os.path.join(EX, f"{slug}.content.json")),
            os.path.getmtime(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "preview_render.py")),
        ))
    except OSError:
        return 0


def _themes():
    try:
        from shortvideo import runtime as R
        from shortvideo.themes import THEMES
        return [{"key": k, "name": v.get("name"), "light": bool(v.get("light")),
                 "note": v.get("note")} for k, v in THEMES.items()]
    except Exception:  # noqa: BLE001
        return []


def _rerender_covers(slug, theme=None):
    """改主题 + 重渲封面与一张图（快，2 秒级）。返回日志。"""
    p = os.path.join(EX, f"{slug}.content.json")
    cfg = json.load(open(p, encoding="utf-8"))
    if theme:
        cfg["theme"] = theme
        json.dump(cfg, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log = []

    def _run(args):
        r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=600)
        log.append(" ".join(os.path.basename(a) for a in args[:2]) +
                   f" → rc={r.returncode}")
        if r.returncode:
            log.append((r.stderr or r.stdout or "")[-500:])
        return r.returncode == 0

    ok = _run([PY, "-c", (
        "import sys,json;sys.path.insert(0,%r);"
        "import cover;"
        "cfg=json.load(open(%r,encoding='utf-8'));"
        "cover.build_covers(cfg,%r,None)"
    ) % (TOOLS, p, os.path.join(OUT, slug))])
    spec = os.path.join(EX, f"oneimage-{slug}.json")
    if ok and os.path.exists(spec):
        try:
            scfg = json.load(open(spec, encoding="utf-8"))
            scfg["theme"] = cfg.get("theme")
            json.dump(scfg, open(spec, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass
        _run([PY, os.path.join(TOOLS, "summary2.py"), "--spec", spec,
              "--out", os.path.join(OUT, slug, "summary.png")])
    return ok, "\n".join(log)


def _next_slug(title, want=None):
    """新条目命名：沿用 NN- 前缀，标题里的 ASCII 部分做后缀（中文留空则用 draft）。"""
    if want:
        return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(want)).strip("-")[:40]
    nums = []
    for s in _slug_list():
        m = re.match(r"(\d+)-", s)
        if m:
            nums.append(int(m.group(1)))
    idx = (max(nums) + 1) if nums else 1
    ascii_part = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")
    # 中文标题没有 ASCII 可转写：原来一律退化成 "NN-draft"，33 个项目全叫 draft 分不清。
    # 用标题的短哈希做后缀，既能区分又稳定（同一标题重复建项目会撞名，正好可以被 want 覆盖）。
    if not ascii_part:
        ascii_part = hashlib.sha1(str(title or "").encode("utf-8")).hexdigest()[:6]
    return f"{idx:02d}-{ascii_part[:24]}"


def _create(slug, req):
    """新建一条：draft → 渲染 → tighten → 再渲染 → 封面 → 一张图。"""
    log = []
    content = os.path.join(EX, f"{slug}.content.json")
    req_path = os.path.join("/tmp", f"req-{slug}.json")
    json.dump(req, open(req_path, "w", encoding="utf-8"), ensure_ascii=False)

    def run(args, tag, timeout=1800):
        r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        log.append(f"[{tag}] rc={r.returncode}\n" + "\n".join(out.strip().splitlines()[-14:]))
        return r.returncode == 0

    if not run([PY, os.path.join(TOOLS, "draft.py"), "--from-json", req_path, "--out", content], "draft"):
        return False, "\n".join(log)
    mp4 = os.path.join(OUT, slug, f"{slug}.mp4")
    os.makedirs(os.path.dirname(mp4), exist_ok=True)
    if not run([PY, os.path.join(TOOLS, "make.py"), "--content", content, "--topic", "deck",
                "--out", mp4, "--jobs", "2"], "render#1"):
        return False, "\n".join(log)
    run([PY, os.path.join(TOOLS, "tighten.py"), "--dir", EX, "--only", slug.split("-")[0] + "-"], "tighten")
    run([PY, os.path.join(TOOLS, "make.py"), "--content", content, "--topic", "deck",
         "--out", mp4, "--jobs", "2"], "render#2")
    run([PY, "-c", ("import sys,json;sys.path.insert(0,%r);import cover;"
                    "cfg=json.load(open(%r,encoding='utf-8'));cover.build_covers(cfg,%r,None)")
         % (TOOLS, content, os.path.join(OUT, slug))], "covers")
    spec = os.path.join(EX, f"oneimage-{slug}.json")
    if os.path.exists(spec):
        run([PY, os.path.join(TOOLS, "summary2.py"), "--spec", spec,
             "--out", os.path.join(OUT, slug, "summary.png")], "oneimage")
    return True, "\n".join(log)


# ---------------------------------------------------------------- 制作工作台（工序）
#
# suncut 那种"制作工作台"的核心不是好看的页面，而是**一条可单独重跑的分步流水线**：
# 每一步都能看到状态、能重跑、能看日志，出错停在那一步而不是全废。
# 这里把出片拆成 5 道工序，每道对应仓库里真实存在的命令，状态由产物推导而不是记在内存里
# （服务重启后状态还在，这点很重要）。
STAGES = (
    ("script", "创意脚本", "五句话 → content.json（含五段式 deck 与一张图 spec）"),
    ("render", "配音成片", "edge-tts 配音 + Pillow 逐帧 + x264 编码"),
    ("assets", "封面物料", "三流派封面 + 9:16 + 一张图"),
    ("qa", "质检", "verify.sh 全部快门禁"),
    ("deliver", "发布包", "publishcheck 清单 + 发布标题/话题"),
)


def _brief_from_content(slug):
    """没有 brief.json 的老项目：从 content.json 反推一份可编辑的需求单。"""
    try:
        cfg = json.load(open(os.path.join(EX, f"{slug}.content.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"title": slug, "lines": [], "kicker": "", "theme": "", "voice": "",
                "short": slug.startswith("short-"), "derived": True}
    shots = cfg.get("shots") or []
    lines = [str(s.get("narration") or "").strip() for s in shots if s.get("narration")]
    return {"title": cfg.get("title") or "", "lines": lines,
            "kicker": (cfg.get("cover") or {}).get("kicker") or "",
            "theme": cfg.get("theme") or "",
            "voice": (cfg.get("voice") or {}).get("preset") or "",
            "short": slug.startswith("short-"), "derived": True}




# ---------------------------------------------------------------- 编辑器：项目读写
def _project_json(slug):
    """给编辑器用的完整项目 JSON。

    和 /api/item 的区别：这里返回**可编辑**的结构（shots 的口播/时长/deck 参数），
    编辑器改完 PATCH 回来；同时给出时间线（start/dur）供时间线组件直接用。
    """
    path = os.path.join(EX, f"{slug}.content.json")
    if not os.path.exists(path):
        return None
    cfg = json.load(open(path, encoding="utf-8"))
    shots, cursor = [], 0.0
    for sh in cfg.get("shots") or []:
        dur = float(sh.get("min_dur") or 4.0)
        deck = (cfg.get("deck") or {}).get(sh["id"]) or {}
        shots.append({
            "id": sh["id"],
            "narration": sh.get("narration") or "",
            "mood": sh.get("mood") or "",
            "duration": round(dur, 2),
            "start": round(cursor, 2),
            "kind": deck.get("kind") or "big",
            "headline": " / ".join(deck.get("lines") or []) or deck.get("text") or "",
            # deck 原样给编辑器：画面上的每一行字都要能改，不能只让改口播
            "deck": deck,
            "emphasis": sh.get("emphasis") or [],
        })
        cursor += dur
    return {
        "slug": slug,
        "title": cfg.get("title") or slug,
        "publish_title": cfg.get("publish_title") or "",
        "theme": cfg.get("theme") or "",
        "voice": (cfg.get("voice") or {}).get("preset") or "yunxi",
        "fps": int(cfg.get("fps") or 30),
        "size": cfg.get("size") or [1080, 1920],
        "total": round(cursor, 2),
        "shots": shots,
        "kicker": (cfg.get("cover") or {}).get("kicker") or "",
        "raw_shot_count": len(cfg.get("shots") or []),
    }


def _save_project(slug, body):
    """把编辑器改动写回 content.json，并留一份历史快照。

    历史快照放在 out/<slug>/history/ 下（最多保留 40 份），
    编辑器只做了客户端撤销；服务端留档才能防止"手滑改坏 + 关页面"。
    """
    path = os.path.join(EX, f"{slug}.content.json")
    if not os.path.exists(path):
        return False, "没有这个项目"
    cfg = json.load(open(path, encoding="utf-8"))
    hist = os.path.join(OUT, slug, "history")
    os.makedirs(hist, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(path, os.path.join(hist, f"content-{stamp}.json"))
    olds = sorted(os.listdir(hist))
    for old in olds[:-40]:
        os.remove(os.path.join(hist, old))

    if "title" in body and body["title"]:
        cfg["title"] = str(body["title"])
    if "theme" in body and body["theme"]:
        cfg["theme"] = str(body["theme"])
    if "voice" in body and body["voice"]:
        cfg.setdefault("voice", {})["preset"] = str(body["voice"])
    deck = cfg.setdefault("deck", {})
    by_id = {s["id"]: s for s in cfg.get("shots") or []}
    for upd in body.get("shots") or []:
        sid = upd.get("id")
        shot = by_id.get(sid)
        if not shot:
            continue
        if "narration" in upd:
            shot["narration"] = str(upd["narration"])
        if "duration" in upd:
            try:
                shot["min_dur"] = max(2.0, min(30.0, float(upd["duration"])))
            except (TypeError, ValueError):
                pass
        if "kind" in upd:
            d = deck.setdefault(sid, {})
            d["kind"] = str(upd["kind"])
        if isinstance(upd.get("deck"), dict):
            # 深合并：编辑器只回传改过的字段，其余原样保留
            d = deck.setdefault(sid, {})
            for k, v in upd["deck"].items():
                if isinstance(v, dict) and isinstance(d.get(k), dict):
                    d[k] = {**d[k], **v}
                elif isinstance(v, list) and v and isinstance(v[0], dict) \
                        and isinstance(d.get(k), list) and d[k] and isinstance(d[k][0], dict):
                    d[k] = [{**old, **new} for old, new in zip(d[k], v)]
                else:
                    d[k] = v
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    PV.invalidate(slug)
    return True, "saved"




# ---------------------------------------------------------------- 质检结果（结构化给编辑器）
_QA_NAMES = ("清单+总分", "图形事实链", "文档完整度", "发布可行性", "长短版一致",
             "响度一致", "配音情绪", "封面可读性", "前3秒留人", "画面体检", "总检(checklist)")


def _qa_items(slug):
    """把 .qa.json 里的文本尾巴解析成结构化条目。

    verify.sh 的输出是给人看的（对齐的 ✅/❌ 表格），编辑器要的是数组；
    这里只做一层薄解析，解析不出来就返回空，前端会显示"未跑"。
    """
    path = os.path.join(OUT, slug, ".qa.json")
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    tail = data.get("tail") or ""
    items, cur = [], None
    for line in tail.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        hit = next((n for n in _QA_NAMES if stripped.startswith(n)), None)
        if hit:
            ok = "✅" in stripped
            bad = "❌" in stripped
            cur = {"name": hit, "ok": ok and not bad, "detail": stripped[len(hit):].strip(" ✅❌")}
            items.append(cur)
        elif cur is not None and not items[-1]["ok"]:
            items[-1]["detail"] = (items[-1]["detail"] + " " + stripped).strip()[:220]
    return {"slug": slug, "at": data.get("at"), "ok": bool(data.get("ok")), "items": items}








# ---------------------------------------------------------------- 配音试听
VOICE_SAMPLE_TEXT = "五十倍防晒，只多挡 1.3%。真正决定晒黑的，是你涂了多少。"
_SAMPLE_CACHE = os.path.join(OUT, ".voice-samples")


def _voice_sample(voice_key):
    """合成（并缓存）一句试听音频。

    选音色这件事，看文字描述没用——必须能听。所以给每个音色预生成同一句话，
    落在 out/.voice-samples/ 下，之后秒开。网络不可用时返回 503，前端提示，
    不假装成功（edge-tts 是云端服务，离线时确实合成不了）。
    """
    os.makedirs(_SAMPLE_CACHE, exist_ok=True)
    key = "".join(c for c in str(voice_key or "yunxi") if c.isalnum() or c in "-_")[:32] or "yunxi"
    path = os.path.join(_SAMPLE_CACHE, f"{key}.mp3")
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return path
    code = (
        "import asyncio, sys;"
        "sys.path.insert(0, %r);"
        "import edge_tts;"
        "va = edge_tts.VoiceProfileMap if False else None;"
        "async def go():\n"
        "    c = edge_tts.Communicate(%r, %r)\n"
        "    await c.save(%r)\n"
        "asyncio.run(go())"
    )
    name = {"yunxi": "zh-CN-YunxiNeural",
            "xiaoxiao": "zh-CN-XiaoxiaoNeural",
            "yunjian": "zh-CN-YunjianNeural"}.get(key, "zh-CN-YunxiNeural")
    script = f"""
import asyncio, sys
sys.path.insert(0, {TOOLS!r})
import edge_tts
async def go():
    c = edge_tts.Communicate({VOICE_SAMPLE_TEXT!r}, {name!r}, rate="+2%")
    await c.save({path!r})
asyncio.run(go())
"""
    r = subprocess.run([PY, "-c", script], cwd=REPO, capture_output=True,
                       text=True, timeout=180)
    if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) >= 2000:
        return path
    # 云端 TTS 不通（离线/被墙）时，退到"从成片里剪一段"：
    # 那本来就是同一个音色、同一套韵律参数读出来的，比放不出声诚实。
    fallback = _sample_from_video(key, path)
    if fallback:
        return fallback
    raise RuntimeError(((r.stdout or "") + (r.stderr or ""))[-300:] or "合成失败且没有可用成片")


def _sample_from_video(voice_key, out_path):
    """截取一条使用该音色的成片的 1.2s~4.6s，作为试听。"""
    import imageio_ffmpeg
    for slug in _slug_list():
        try:
            cfg = json.load(open(os.path.join(EX, f"{slug}.content.json"), encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        preset = (cfg.get("voice") or {}).get("preset") or ""
        if preset != voice_key:
            continue
        mp4 = os.path.join(OUT, slug, f"{slug}.mp4")
        if not os.path.exists(mp4):
            continue
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        r = subprocess.run([ff, "-y", "-v", "error", "-ss", "1.2", "-t", "3.4",
                            "-i", mp4, "-vn", "-c:a", "libmp3lame", "-b:a", "96k",
                            out_path], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 2000:
            return out_path
    return None


def _publish_meta(slug):
    """发布元数据（选中的封面、改过的发布标题）存在 out/<slug>/publish.json。"""
    path = os.path.join(OUT, slug, "publish.json")
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_publish(slug, patch):
    d = os.path.join(OUT, slug)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "publish.json")
    cur = _publish_meta(slug)
    cur.update({k: v for k, v in (patch or {}).items() if v is not None})
    cur["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    json.dump(cur, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return cur


def _deliver_info(slug):
    """导出完成后"交付"界面要的全部东西。

    用户拿到的不该只是一个 mp4：小红书发布要的是视频 + 封面 + 标题 + 话题。
    这里一次性把四样凑齐，缺什么就如实标出来。
    """
    d = os.path.join(OUT, slug)
    content = os.path.join(EX, f"{slug}.content.json")
    info = {"slug": slug, "video": None, "covers": [], "summary": None,
            "picked_cover": _publish_meta(slug).get("cover") or "",
            "publish_title": "", "topics": [], "tags": [], "duration": 0.0,
            "size_mb": 0.0, "qa": _qa_items(slug)}
    cfg = {}
    try:
        cfg = json.load(open(content, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    info["publish_title"] = cfg.get("publish_title") or cfg.get("title") or slug
    info["topics"] = cfg.get("topics") or []
    info["tags"] = (cfg.get("cover") or {}).get("tags") or []
    info["duration"] = round(sum(float(x.get("min_dur") or 0) for x in cfg.get("shots") or []), 1)
    info["size"] = "x".join(str(v) for v in (cfg.get("size") or [1080, 1920]))
    info["fps"] = int(cfg.get("fps") or 30)
    rj = os.path.join(d, ".render.json")
    if os.path.exists(rj):
        try:
            meta = json.load(open(rj, encoding="utf-8"))
            info["size"] = meta.get("size") or info["size"]
            info["fps"] = meta.get("fps") or info["fps"]
            info["preset"] = meta.get("preset")
            info["rendered_at"] = meta.get("at")
        except Exception:  # noqa: BLE001
            pass

    mp4 = os.path.join(d, f"{slug}.mp4")
    if os.path.exists(mp4):
        info["video"] = f"/files/{slug}/{slug}.mp4"
        info["size_mb"] = round(os.path.getsize(mp4) / 1048576, 1)
    for name, label in (("cover_short.png", "主封面 3:4"),
                        ("cover_cinematic.png", "电影感"),
                        ("cover_editorial.png", "编辑风"),
                        ("cover_blueprint.png", "蓝图"),
                        ("cover_9x16.png", "竖版 9:16")):
        fp = os.path.join(d, name)
        if os.path.exists(fp):
            info["covers"].append({"name": name, "label": label, "url": f"/files/{slug}/{name}"})
    if os.path.exists(os.path.join(d, "summary.png")):
        info["summary"] = f"/files/{slug}/summary.png"
    dlv = os.path.join(d, ".deliver.json")
    if os.path.exists(dlv):
        try:
            info["deliver"] = json.load(open(dlv, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return info




# ---------------------------------------------------------------- 结构模板
_TEMPLATES = None


def _templates():
    """读结构模板（决定"怎么讲"，不是"长什么样"）。"""
    global _TEMPLATES
    if _TEMPLATES is None:
        path = os.path.join(TOOLS, "templates.json")
        try:
            _TEMPLATES = json.load(open(path, encoding="utf-8")).get("templates") or []
        except Exception:  # noqa: BLE001
            _TEMPLATES = []
    return _TEMPLATES


def _apply_template(slug, tid):
    """把模板的镜头类型序列写进 content.json（新建项目的第二步）。"""
    tpl = next((t for t in _templates() if t["id"] == tid), None)
    if not tpl:
        return False
    path = os.path.join(EX, f"{slug}.content.json")
    if not os.path.exists(path):
        return False
    cfg = json.load(open(path, encoding="utf-8"))
    deck = cfg.setdefault("deck", {})
    for i, kind in enumerate(tpl.get("kinds") or []):
        shots = cfg.get("shots") or []
        if i >= len(shots):
            break
        deck.setdefault(shots[i]["id"], {})["kind"] = kind
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    PV.invalidate(slug)
    return True


def _project_meta(slug):
    """片库卡片要的最少信息：时长、镜数、主题、音色、有没有成片。

    这些都能从 content.json 直接读，不额外算 —— 片库 33 张卡片要瞬时出。
    """
    path = os.path.join(EX, f"{slug}.content.json")
    meta = {"duration": 0.0, "shots": 0, "theme": "", "kind": "long", "has_video": False,
            "publish_title": ""}
    try:
        cfg = json.load(open(path, encoding="utf-8"))
        shots = cfg.get("shots") or []
        meta.update({
            "duration": round(sum(float(s.get("min_dur") or 0) for s in shots), 1),
            "shots": len(shots),
            "theme": cfg.get("theme") or "",
            "kind": "short" if slug.startswith("short-") else "long",
            "publish_title": cfg.get("publish_title") or "",
        })
    except Exception:  # noqa: BLE001
        pass
    mp4 = os.path.join(OUT, slug, f"{slug}.mp4")
    meta["has_video"] = os.path.exists(mp4)
    if meta["has_video"]:
        meta["size_mb"] = round(os.path.getsize(mp4) / 1048576, 1)
    return meta


def _title_of(slug):
    try:
        cfg = json.load(open(os.path.join(EX, f"{slug}.content.json"), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return slug
    return (cfg.get("title") or cfg.get("publish_title") or cfg.get("topic")
            or slug)


def _stage_status(slug):
    d = os.path.join(OUT, slug)
    content = os.path.join(EX, f"{slug}.content.json")
    have_content = os.path.exists(content)
    mp4 = os.path.join(d, f"{slug}.mp4")
    covers = all(os.path.exists(os.path.join(d, n)) for n in
                 ("cover_short.png", "cover_9x16.png", "summary.png"))
    qa_mark = os.path.join(d, ".qa.json")
    deliver_mark = os.path.join(d, ".deliver.json")
    st = {
        "script": bool(have_content),
        "render": os.path.exists(mp4),
        "assets": covers,
        "qa": os.path.exists(qa_mark),
        "deliver": os.path.exists(deliver_mark),
    }
    detail = {}
    if os.path.exists(qa_mark):
        try:
            detail["qa"] = json.load(open(qa_mark, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            detail["qa"] = {}
    if os.path.exists(deliver_mark):
        try:
            detail["deliver"] = json.load(open(deliver_mark, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            detail["deliver"] = {}
    return st, detail


def _run_stage(slug, stage, opts=None, jid=None):
    """跑一道工序，返回 (ok, 日志尾巴)。每道工序都是幂等的，可以反复重跑。

    jid 给了就把子进程输出**实时**推进 job log（导出进度条要动就得靠这个）。
    """
    def run(cmd, timeout=3600):
        if jid:
            return _stream_subprocess(cmd, REPO, jid, timeout=timeout)
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-1500:]
    d = os.path.join(OUT, slug)
    content = os.path.join(EX, f"{slug}.content.json")
    mp4 = os.path.join(d, f"{slug}.mp4")
    if stage == "script":
        brief = os.path.join(d, "brief.json")
        if not os.path.exists(brief):
            return False, f"没有 {brief}：脚本工序需要先有 brief（新建项目时写入）"
        return run([PY, os.path.join(TOOLS, "draft.py"), "--from-json", brief,
                    "--out", content], timeout=600)
    if stage == "render":
        preset = (opts or {}).get("preset") or "xhs"
        cmd = [PY, os.path.join(TOOLS, "make.py"), "--content", content,
               "--topic", "deck", "--out", mp4, "--jobs", "2"]
        if preset == "draft":
            # 快速预览：720x1280 / 24fps / veryfast，约 1/3 耗时。
            # 注意 make.py 原来没有 --size，第一次接的时候直接 argparse 报错，
            # 而前端把"失败"当成了"完成"——两个都修了。
            cmd += ["--size", "720x1280", "--fps", "24", "--preset", "veryfast", "--crf", "26"]
        ok_run, tail_run = run(cmd)
        if ok_run and os.path.exists(mp4):
            # 快速预览是 720x1280，交付界面不能还写着 1080x1920 —— 以实际出片为准
            try:
                json.dump({"preset": preset, "size": "720x1280" if preset == "draft" else "1080x1920",
                           "fps": 24 if preset == "draft" else 30,
                           "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                          open(os.path.join(d, ".render.json"), "w", encoding="utf-8"))
            except OSError:
                pass
        return ok_run, tail_run
    if stage == "assets":
        code = ("import sys,json;sys.path.insert(0,%r);import cover;"
                "cfg=json.load(open(%r,encoding='utf-8'));cover.build_covers(cfg,%r,None)")
        r1 = subprocess.run([PY, "-c", code % (TOOLS, content, d)], cwd=REPO,
                            capture_output=True, text=True, timeout=900)
        spec = os.path.join(EX, f"oneimage-{slug}.json")
        r2 = subprocess.run([PY, os.path.join(TOOLS, "summary2.py"), "--spec", spec,
                             "--out", os.path.join(d, "summary.png")], cwd=REPO,
                            capture_output=True, text=True, timeout=600) if os.path.exists(spec) else r1
        ok = r1.returncode == 0 and r2.returncode == 0
        return ok, ((r1.stdout or "") + (r1.stderr or "") + (r2.stderr or ""))[-1500:]
    if stage == "qa":
        r = subprocess.run(["./verify.sh", slug], cwd=REPO, capture_output=True,
                           text=True, timeout=3600)
        out = (r.stdout or "") + (r.stderr or "")
        json.dump({"ok": r.returncode == 0, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "tail": out[-1200:]}, open(os.path.join(d, ".qa.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return r.returncode == 0, out[-1500:]
    if stage == "deliver":
        # publishcheck 的 --dir 是"父目录"口径，传单条目录会得到 0 条；
        # 用 --content 才是单条检查（实测 --dir 单条目录输出「0/0 条可直接发」）。
        r = subprocess.run([PY, os.path.join(TOOLS, "publishcheck.py"), "--content", content],
                           cwd=REPO, capture_output=True, text=True, timeout=600)
        out = (r.stdout or "") + (r.stderr or "")
        try:
            cfg = json.load(open(content, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cfg = {}
        json.dump({"ok": r.returncode == 0, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "publish_title": cfg.get("publish_title") or cfg.get("title"),
                   "topics": cfg.get("topics") or [], "tail": out[-1200:]},
                  open(os.path.join(d, ".deliver.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        return r.returncode == 0, out[-1500:]
    return False, f"未知工序 {stage}"


def _stream_subprocess(cmd, cwd, jid, timeout=1800):
    """跑子进程并把输出**实时**写进 job log。

    原来用 subprocess.run(capture_output=True)：日志和进度要等进程结束才一次性出现，
    界面上的进度条等于摆设（看起来"卡在启动渲染…"十几秒然后突然 100%）。
    make.py 的进度是用 \r 刷同一行的，所以要按 \r 也切行，否则一行都读不到。
    """
    t0 = time.time()
    try:
        # PYTHONUNBUFFERED：子进程 stdout 接的是管道，默认块缓冲 8KB，
        # 不写这个的话进度行要攒满一块才出来（表现为"日志卡着不动，最后一次性刷完"）
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1,
                             errors="replace", env=env)
    except Exception as e:  # noqa: BLE001
        return False, f"启动失败：{e}"
    tail = []
    for chunk in iter(lambda: p.stdout.readline(), ""):
        for line in chunk.replace("\r", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            tail = tail[-40:]
            with _JOBS_LOCK:
                if jid in _JOBS:
                    _JOBS[jid]["log"].append(line)
                    if len(_JOBS[jid]["log"]) > 400:
                        del _JOBS[jid]["log"][:-400]
        if time.time() - t0 > timeout:
            p.kill()
            return False, "超时"
    rc = p.wait()
    return rc == 0, "\n".join(tail)[-1500:]


def _start_job(kind, slug, extra=None):
    with _JOBS_LOCK:
        _JOB_SEQ[0] += 1
        jid = f"job{_JOB_SEQ[0]}"
        _JOBS[jid] = {"status": "running", "log": [], "slug": slug, "kind": kind,
                      "started": time.time()}

    def worker():
        try:
            if kind == "render":
                p = os.path.join(EX, f"{slug}.content.json")
                preset = ((extra or {}).get("opts") or {}).get("preset") or "xhs"
                args = [PY, os.path.join(TOOLS, "make.py"), "--content", p,
                        "--topic", "deck", "--out", os.path.join(OUT, slug, f"{slug}.mp4"),
                        "--jobs", "2"]
                if preset == "draft":
                    args += ["--size", "720x1280", "--fps", "24",
                             "--preset", "veryfast", "--crf", "26"]
                ok, tail = _stream_subprocess(args, REPO, jid)
            elif kind == "new":
                ok, tail = _create(slug, (extra or {}).get("req") or {})
            elif kind == "stage":
                ok, tail = _run_stage(slug, (extra or {}).get("stage") or "script",
                                      (extra or {}).get("opts") or {}, jid=jid)
                tpl = (extra or {}).get("template")
                if ok and tpl:
                    _apply_template(slug, tpl)
            elif kind == "verify":
                r = subprocess.run(["./verify.sh", slug], cwd=REPO,
                                   capture_output=True, text=True, timeout=1800)
                tail = (r.stdout or "")[-1200:]
                ok = r.returncode == 0
            else:
                ok, tail = _rerender_covers(slug, (extra or {}).get("theme"))
            with _JOBS_LOCK:
                # 结束时不覆盖日志：流式阶段攒下的进度行要留着，只把尾巴接上去
                cur = _JOBS[jid].get("log") or []
                extra_lines = [l for l in (tail or "").split("\n") if l.strip()]
                _JOBS[jid].update(status="done" if ok else "fail",
                                  log=(cur + extra_lines)[-400:],
                                  elapsed=round(time.time() - _JOBS[jid]["started"], 1))
        except Exception as e:  # noqa: BLE001
            with _JOBS_LOCK:
                _JOBS[jid].update(status="fail", log=[f"{type(e).__name__}: {e}"])

    threading.Thread(target=worker, daemon=True).start()
    return jid


def _job(jid):
    with _JOBS_LOCK:
        return _JOBS.get(jid)


class Handler(BaseHTTPRequestHandler):
    server_version = "shortvideo-preview/1.0"

    def log_message(self, fmt, *args):     # 静音访问日志
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path):
        if not os.path.isfile(path):
            return self._send(404, {"error": "not found"})
        ext = os.path.splitext(path)[1].lower()
        ctype = {".png": "image/png", ".mp4": "video/mp4", ".jpg": "image/jpeg",
                 ".json": "application/json", ".html": "text/html; charset=utf-8"}.get(
            ext, "application/octet-stream")
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        if rng and ext == ".mp4":          # <video> 需要 Range 才能拖动
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                with open(path, "rb") as f:
                    f.seek(start)
                    data = f.read(end - start + 1)
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        path = u.path
        if path in ("/", "/index.html"):
            # 首页是片库（和剪辑台同一套视觉）；批量表格页在 /batch
            return self._file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app", "library.html"))
        if path == "/api/library":
            items = [c for c in (_card(s) for s in _slug_list()) if c]
            return self._send(200, {"items": items, "themes": _themes(),
                                    "root": REPO})
        if path == "/api/item":
            return self._send(200, _detail(q.get("slug", [""])[0]) or {"error": "unknown"})
        if path == "/api/theme-preview":
            slug = q.get("slug", [""])[0]
            theme = q.get("theme", ["porcelain"])[0]
            try:
                data = PV.theme_frame(slug, theme)
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "public, max-age=600")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/voice-sample":
            vk = q.get("voice", ["yunxi"])[0]
            try:
                fp = _voice_sample(vk)
            except Exception as e:  # noqa: BLE001
                return self._send(503, {"error": f"试听合成失败（{type(e).__name__}）：{e}"})
            data = open(fp, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/templates":
            return self._send(200, {"templates": _templates()})
        if path == "/api/deliver":
            return self._send(200, _deliver_info(q.get("slug", [""])[0]))
        if path == "/api/stage-status":
            slug = q.get("slug", [""])[0]
            return self._send(200, _qa_items(slug) or {"slug": slug, "items": [], "ok": None})
        if path == "/api/project":
            slug = q.get("slug", [""])[0]
            data = _project_json(slug)
            if data:
                data["rev"] = _rev(slug)
            return self._send(200, data) if data else self._send(404, {"error": "unknown"})
        if path == "/api/frame":
            slug = q.get("slug", [""])[0]
            try:
                t = float(q.get("t", ["0"])[0])
            except ValueError:
                t = 0.0
            try:
                data = PV.frame_png(slug, t)
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/brief":
            slug = q.get("slug", [""])[0]
            d = os.path.join(OUT, slug, "brief.json")
            if os.path.exists(d):
                return self._send(200, json.load(open(d, encoding="utf-8")))
            return self._send(200, _brief_from_content(slug))
        if path == "/api/projects":
            rows = []
            for slug in _slug_list():
                st, detail = _stage_status(slug)
                meta = _project_meta(slug)
                rows.append({"slug": slug, "title": _title_of(slug), "stages": st,
                             "detail": detail, "done": sum(1 for v in st.values() if v),
                             **meta})
            # 工作台是待办清单：没走完的排前面，走完的沉底
            rows.sort(key=lambda r: (r["done"] == len(STAGES), r["done"], r["slug"]))
            return self._send(200, {"stages": [{"id": a, "name": b, "desc": c} for a, b, c in STAGES],
                                    "projects": rows})
        if path == "/api/job":
            j = _job(q.get("id", [""])[0])
            return self._send(200, j or {"status": "unknown"})
        if path == "/batch":
            path = "/app/batch.html"
        if path in ("/app", "/app/"):
            path = "/app/editor.html"
        if path.startswith("/app/") or path == "/batch.html":
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), path.lstrip("/"))
            if os.path.isfile(fp):
                ext = os.path.splitext(fp)[1]
                ctype = {".html": "text/html; charset=utf-8",
                         ".css": "text/css; charset=utf-8",
                         ".js": "application/javascript; charset=utf-8",
                         ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
                data = open(fp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
        if path.startswith("/files/"):
            rel = path[len("/files/"):]
            target = os.path.normpath(os.path.join(OUT, rel))
            if not target.startswith(OUT + os.sep):
                return self._send(403, {"error": "forbidden"})
            return self._file(target)
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            body = {}
        if u.path == "/api/new":
            title = str(body.get("title") or "").strip()
            lines = [str(x).strip() for x in (body.get("lines") or []) if str(x).strip()]
            if not title or not lines:
                return self._send(400, {"error": "title 和 lines 都要有"})
            slug = _next_slug(title, body.get("slug"))
            req = {"title": title, "lines": lines, "kicker": body.get("kicker"),
                   "theme": body.get("theme"), "voice": body.get("voice"),
                   "short": bool(body.get("short"))}
            return self._send(200, {"job": _start_job("new", slug, {"req": req}), "slug": slug})
        if u.path == "/api/theme":
            slug, theme = body.get("slug"), body.get("theme")
            if not slug:
                return self._send(400, {"error": "slug required"})
            return self._send(200, {"job": _start_job("theme", slug, {"theme": theme})})
        if u.path == "/api/project":
            # 同一个路径两种语义：带 slug 且项目存在 = 保存改动，否则 = 新建项目。
            # （之前是两个 if 并列，保存那段永远先命中，新建直接 400 slug required。）
            slug = str(body.get("slug") or "").strip()
            exists = bool(slug) and os.path.exists(os.path.join(EX, f"{slug}.content.json"))
            if slug and exists:
                ok, msg = _save_project(slug, body)
                return self._send(200 if ok else 400, {"ok": ok, "message": msg})
            if slug and not exists and not body.get("lines"):
                return self._send(404, {"error": f"没有这个项目：{slug}"})
        if u.path == "/api/brief":
            slug = str(body.get("slug") or "").strip()
            if not slug:
                return self._send(400, {"error": "slug required"})
            lines = [str(x).strip() for x in (body.get("lines") or []) if str(x).strip()]
            title = str(body.get("title") or "").strip()
            if not title or len(lines) < 3:
                return self._send(400, {"error": "标题必填，句子至少 3 句"})
            d = os.path.join(OUT, slug)
            os.makedirs(d, exist_ok=True)
            brief = {"title": title, "lines": lines, "kicker": body.get("kicker"),
                     "theme": body.get("theme"), "voice": body.get("voice"),
                     "short": bool(body.get("short")),
                     "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            json.dump(brief, open(os.path.join(d, "brief.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            job = _start_job("stage", slug, {"stage": "script"}) if body.get("rerun", True) else None
            return self._send(200, {"ok": True, "job": job, "slug": slug})
        if u.path in ("/api/project/new",):
            title = str(body.get("title") or "").strip()
            lines = [str(x).strip() for x in (body.get("lines") or []) if str(x).strip()]
            if not title or len(lines) < 3:
                return self._send(400, {"error": "标题必填，句子至少 3 句（推荐 5 句）"})
            slug = _next_slug(title, body.get("slug"))
            d = os.path.join(OUT, slug)
            os.makedirs(d, exist_ok=True)
            # brief.json 是这条项目的"需求单"：脚本工序以后每次重跑都读它，
            # 这样改一句话只需改 brief 再点重跑，不必记住命令行。
            brief = {"title": title, "lines": lines, "kicker": body.get("kicker"),
                     "theme": body.get("theme"), "voice": body.get("voice"),
                     "short": bool(body.get("short")), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
            json.dump(brief, open(os.path.join(d, "brief.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            tpl = str(body.get("template") or "").strip()
            return self._send(200, {"job": _start_job("stage", slug, {
                "stage": "script", "template": tpl}), "slug": slug})
        if u.path == "/api/project" and not slug:
            return self._send(400, {"error": "slug required"})
        if u.path == "/api/publish":
            slug = str(body.get("slug") or "").strip()
            if not slug:
                return self._send(400, {"error": "slug required"})
            cur = _save_publish(slug, {"cover": body.get("cover"),
                                       "title": body.get("title")})
            return self._send(200, {"ok": True, "publish": cur})
        if u.path == "/api/stage":
            slug, stage = body.get("slug"), body.get("stage")
            if not slug or stage not in [k for k, _, _ in STAGES]:
                return self._send(400, {"error": "slug/stage 不对"})
            return self._send(200, {"job": _start_job("stage", slug, {
                "stage": stage, "opts": body.get("opts") or {}}),
                                    "slug": slug, "stage": stage})
        if u.path == "/api/render":
            slug = body.get("slug")
            if not slug:
                return self._send(400, {"error": "slug required"})
            return self._send(200, {"job": _start_job("render", slug)})
        if u.path == "/api/verify":
            slug = body.get("slug")
            if not slug:
                return self._send(400, {"error": "slug required"})
            return self._send(200, {"job": _start_job("verify", slug)})
        return self._send(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description="片库预览台")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--export", default=None, metavar="SLUG",
                    help="不开服务器，直接导出这条片子（sv export）")
    a = ap.parse_args()
    if a.export:
        ok, tail = _run_stage(a.export, "render")
        print(tail)
        raise SystemExit(0 if ok else 1)
    try:
        srv = ThreadingHTTPServer((a.host, a.port), Handler)
    except OSError as e:
        raise SystemExit(f"[x] 端口 {a.port} 被占用（{e}）。换一个：sv app --port 8100")
    print(f"[preview] http://{a.host}:{a.port}  ({len(_slug_list())} 条片子)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
