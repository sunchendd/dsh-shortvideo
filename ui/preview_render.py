#!/usr/bin/env python3
"""单帧预览渲染器：给定 slug + 时间 t，立刻出一帧 1080x1920 的 PNG。

## 为什么单独写一个

出片管线 `make.py` 要先跑 TTS 才知道每镜真实时长，一次要几十秒；
但编辑时"拖动播放头看画面"必须 **150ms 内出帧**，否则时间线就是假的。

所以这里用**配置里的 min_dur** 拼一条近似时间线（不跑 TTS），
字幕按句子均分时长近似（真实配音时长在导出时才精确）。
预览只要"构图对、字对、节奏大致对"，不要求逐帧等于成片。

## 缓存

- 项目对象（cfg + deck scene_fns + timeline）按 slug + content.json mtime 缓存；
- 帧按 (slug, 量化后的 t) 缓存，LRU 300 张（约 60MB），
  拖动播放头时来回扫同一段不会重复渲染。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from collections import OrderedDict

def _find_root(start):
    """向上找带 examples/ 的目录（开发布局与插件布局都能命中）。"""
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


def _tools_dir(root):
    for cand in (os.path.join(root, "skills", "shortvideo", "tools"),
                 os.path.join(root, "tools")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(root, "skills", "shortvideo", "tools")


TOOLS = _tools_dir(REPO)
EX = os.path.join(REPO, "examples")
for p in (TOOLS,):
    if p not in sys.path:
        sys.path.insert(0, p)

_LOCK = threading.Lock()
_PROJECTS = {}                       # slug -> (mtime, project)
_FRAMES = OrderedDict()              # key -> png bytes
_FRAME_CAP = 300
_WARM = {}                           # slug -> 预热状态


def _q(t, fps=30):
    return round(max(0.0, float(t)) * fps) / fps


class Preview:
    """一条片子的预览渲染器。"""

    def __init__(self, slug, theme_override=None):
        import make as M
        from shortvideo import runtime as R
        from shortvideo import themes
        from shortvideo.scenes import SceneAPI as S

        self.slug = slug
        self.M, self.R, self.S = M, R, S
        content_path = os.path.join(EX, f"{slug}.content.json")
        if not os.path.exists(content_path):
            raise FileNotFoundError(content_path)
        cfg = json.load(open(content_path, encoding="utf-8"))
        self.cfg = cfg
        themes_name = theme_override or cfg.get("theme")
        R.set_theme(themes.theme_for_video(themes_name) if themes_name else None)
        try:
            import art as _art
            _art.refresh_theme()
        except Exception:  # noqa: BLE001
            pass
        R.W, R.H = (cfg.get("size") or [R.W, R.H])[:2]
        self.fps = int(cfg.get("fps") or 30)

        # theme 是全局的：多个项目交替预览时必须每次重设，否则会串色
        self.theme = themes_name
        self.theme_override = theme_override

        topic = M.load_topic("deck")
        ctx = dict(cfg.get("ctx", {}))
        ctx["content"] = cfg
        ctx.update(topic.prepare(ctx) if hasattr(topic, "prepare") else {})
        self.ctx = ctx
        self.scene_fns = topic.build(ctx)

        # 近似时间线：min_dur 当镜头时长，字幕按句子均分（真值要跑 TTS）
        self.timeline, self.total = self._timeline()
        self.n = len(self.timeline)

    def _timeline(self):
        tl, cursor = [], 0.0
        for shot in self.cfg.get("shots") or []:
            dur = float(shot.get("min_dur") or 4.0)
            item = dict(shot)
            item["start"] = cursor
            item["dur"] = dur
            narration = str(shot.get("narration") or "")
            item["subs"] = self._subs(narration, cursor, dur)
            tl.append(item)
            cursor += dur
        return tl, cursor

    def _subs(self, narration, start, dur):
        """按标点切句、按字数分配时长——只为预览贴近成片，导出仍走真实对齐。"""
        import re
        parts = [s for s in re.split(r"(?<=[。！？；])", narration) if s.strip()]
        if not parts:
            return []
        total_chars = sum(len(p) for p in parts) or 1
        subs, t = [], start
        for p in parts:
            d = max(0.6, dur * len(p) / total_chars)
            subs.append((t, t + d, p))
            t += d
        return subs

    def shot_at(self, t):
        for idx, item in enumerate(self.timeline):
            if item["start"] <= t < item["start"] + item["dur"]:
                return idx
        return max(0, len(self.timeline) - 1)

    def frame(self, t, theme="__default__"):
        """渲染一帧。theme 传具体名字可以渲染"换主题之后"的样子（主题面板预览用）。"""
        from shortvideo import runtime as R, themes
        if theme == "__default__":
            R.set_theme(self.theme_for_video())
        else:
            R.set_theme(themes.theme_for_video(theme) if theme else None)
        idx = self.shot_at(t)
        item = self.timeline[idx]
        lt = max(0.0, min(t - item["start"], item["dur"] - 1e-3))
        fn = self.scene_fns.get(item["id"])
        if fn is None:
            raise KeyError(item["id"])
        img = self.M.render_frame(
            fn, item, lt, self.ctx, self.total,
            with_caption=True, emphasis=item.get("emphasis"),
            with_progress=True, idx=idx, n_shots=self.n,
            disclosure=self.cfg.get("disclosure"),
        )
        return img.convert("RGB")

    def theme_for_video(self):
        from shortvideo import themes
        name = self.theme_override or self.cfg.get("theme")
        return themes.theme_for_video(name) if name else None


def project(slug):
    """取（并缓存）某条片子的预览器，按 content.json 的 mtime 失效。"""
    path = os.path.join(EX, f"{slug}.content.json")
    mtime = os.path.getmtime(path) if os.path.exists(path) else 0
    with _LOCK:
        hit = _PROJECTS.get(slug)
        if hit and hit[0] == mtime:
            return hit[1]
    pv = Preview(slug)
    with _LOCK:
        _PROJECTS[slug] = (mtime, pv)
    return pv


def frame_png(slug, t, quality=88):
    """返回 PNG bytes（带 LRU 缓存）。"""
    key = (slug, _q(t))
    with _LOCK:
        if key in _FRAMES:
            _FRAMES.move_to_end(key)
            return _FRAMES[key]
    pv = project(slug)
    img = pv.frame(key[1])
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    data = buf.getvalue()
    with _LOCK:
        _FRAMES[key] = data
        while len(_FRAMES) > _FRAME_CAP:
            _FRAMES.popitem(last=False)
    return data


def invalidate(slug=None):
    """配置改动后清缓存。"""
    with _LOCK:
        if slug is None:
            _PROJECTS.clear()
            _FRAMES.clear()
        else:
            _PROJECTS.pop(slug, None)
            for k in [k for k in _FRAMES if k[0] == slug]:
                _FRAMES.pop(k, None)


_THEME_PROJECTS = {}


def _themed_project(slug, theme):
    key = (slug, theme)
    if key not in _THEME_PROJECTS:
        _THEME_PROJECTS[key] = Preview(slug, theme_override=theme)
    return _THEME_PROJECTS[key]


def theme_frame(slug, theme, t=1.0, width=240):
    """某条片子换某个主题后的一帧（带磁盘缓存）。"""
    import io, os
    # 缓存放在 out/ 下（和 .voice-samples 一致），别散落在 examples/ 根上
    d = os.path.join(EX, "out", ".theme-previews")
    os.makedirs(d, exist_ok=True)
    key = "".join(c for c in f"{slug}--{theme}" if c.isalnum() or c in "-_")[:80]
    path = os.path.join(d, key + ".jpg")
    if os.path.exists(path) and os.path.getsize(path) > 500:
        return open(path, "rb").read()
    pv = _themed_project(slug, theme)
    img = pv.frame(t)
    w, h = img.size
    img = img.resize((width, int(h * width / w)), )
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80, optimize=True)
    data = buf.getvalue()
    open(path, "wb").write(data)
    return data


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="预览单帧")
    ap.add_argument("slug")
    ap.add_argument("--t", type=float, default=1.0)
    ap.add_argument("--out", default="/tmp/frame.jpg")
    a = ap.parse_args()
    t0 = time.time()
    data = frame_png(a.slug, a.t)
    open(a.out, "wb").write(data)
    print(f"{a.out}  {len(data) // 1024}KB  {1000 * (time.time() - t0):.0f}ms")
