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
import subprocess
import sys
import threading
import time
import hashlib
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EX = os.path.join(REPO, "examples")
OUT = os.path.join(EX, "out")
PY = sys.executable
TOOLS = os.path.join(REPO, "skills", "shortvideo", "tools")

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
        ascii_part = "topic-" + hashlib.sha1(str(title or "").encode("utf-8")).hexdigest()[:6]
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


def _run_stage(slug, stage):
    """跑一道工序，返回 (ok, 日志尾巴)。每道工序都是幂等的，可以反复重跑。"""
    d = os.path.join(OUT, slug)
    content = os.path.join(EX, f"{slug}.content.json")
    mp4 = os.path.join(d, f"{slug}.mp4")
    if stage == "script":
        brief = os.path.join(d, "brief.json")
        if not os.path.exists(brief):
            return False, f"没有 {brief}：脚本工序需要先有 brief（新建项目时写入）"
        r = subprocess.run([PY, os.path.join(TOOLS, "draft.py"), "--from-json", brief,
                            "--out", content], cwd=REPO, capture_output=True, text=True, timeout=600)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-1500:]
    if stage == "render":
        r = subprocess.run([PY, os.path.join(TOOLS, "make.py"), "--content", content,
                            "--topic", "deck", "--out", mp4, "--jobs", "2"],
                           cwd=REPO, capture_output=True, text=True, timeout=3600)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or ""))[-1500:]
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
                args = [PY, os.path.join(TOOLS, "make.py"), "--content", p,
                        "--topic", "deck", "--out", os.path.join(OUT, slug, f"{slug}.mp4"),
                        "--jobs", "2"]
                r = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=1800)
                tail = (r.stdout or "")[-400:] + (r.stderr or "")[-400:]
                ok = r.returncode == 0
            elif kind == "new":
                ok, tail = _create(slug, (extra or {}).get("req") or {})
            elif kind == "stage":
                ok, tail = _run_stage(slug, (extra or {}).get("stage") or "script")
            elif kind == "verify":
                r = subprocess.run(["./verify.sh", slug], cwd=REPO,
                                   capture_output=True, text=True, timeout=1800)
                tail = (r.stdout or "")[-1200:]
                ok = r.returncode == 0
            else:
                ok, tail = _rerender_covers(slug, (extra or {}).get("theme"))
            with _JOBS_LOCK:
                _JOBS[jid].update(status="done" if ok else "fail",
                                  log=[tail], elapsed=round(time.time() - _JOBS[jid]["started"], 1))
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
            return self._file(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))
        if path == "/api/library":
            items = [c for c in (_card(s) for s in _slug_list()) if c]
            return self._send(200, {"items": items, "themes": _themes(),
                                    "root": REPO})
        if path == "/api/item":
            return self._send(200, _detail(q.get("slug", [""])[0]) or {"error": "unknown"})
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
                rows.append({"slug": slug, "title": _title_of(slug), "stages": st,
                             "detail": detail,
                             "done": sum(1 for v in st.values() if v)})
            # 工作台是待办清单：没走完的排前面，走完的沉底
            rows.sort(key=lambda r: (r["done"] == len(STAGES), r["done"], r["slug"]))
            return self._send(200, {"stages": [{"id": a, "name": b, "desc": c} for a, b, c in STAGES],
                                    "projects": rows})
        if path == "/api/job":
            j = _job(q.get("id", [""])[0])
            return self._send(200, j or {"status": "unknown"})
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
        if u.path == "/api/project":
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
            return self._send(200, {"job": _start_job("stage", slug, {"stage": "script"}),
                                    "slug": slug})
        if u.path == "/api/stage":
            slug, stage = body.get("slug"), body.get("stage")
            if not slug or stage not in [k for k, _, _ in STAGES]:
                return self._send(400, {"error": "slug/stage 不对"})
            return self._send(200, {"job": _start_job("stage", slug, {"stage": stage}),
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
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"[preview] http://{a.host}:{a.port}  ({len(_slug_list())} 条片子)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
