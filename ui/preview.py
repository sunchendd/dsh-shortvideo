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
    return f"{idx:02d}-{ascii_part[:24] or 'draft'}"


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
