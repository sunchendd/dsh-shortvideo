#!/usr/bin/env python3
"""从 5 句大白话生成一份**合法、能过门禁**的 content.json。

## 为什么要它

手写 content.json 的门槛不在文案，在 `deck` 那一段：每个镜头要选对 `kind`
（big / stat / compare / flow / steps / cards / list / formula / timeline / bars），
还要填各 kind 自己的字段（value/unit/label、left/right、items…）。选错了要么报错，
要么渲出来是空的。

`draft.py` 只要求你给**标题 + 每镜一句口播**，剩下的它猜：
数字→stat，两个数字+对比词→compare，步骤词→steps，因果词→flow，其余→big。
猜错的代价很低（big 永远能画），猜对的收益很高（不用手写 JSON）。

## 用法

    python draft.py --title "标题" --lines "第一句" "第二句" ... \
                    --kicker 栏目名 --theme jade --voice yunxi \
                    --out examples/xx.content.json --short

也可以 `--from-json req.json`（UI 走这个）：`{"title":…, "lines":[…], "kicker":…, …}`
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shortvideo.textutil import numbers_in, syllables  # noqa: E402

SHOT_IDS_LONG = ["hook", "rule", "insight", "reveal", "solve"]
SHOT_IDS_SHORT = ["hook", "rule", "reveal", "solve"]

CMP_WORDS = ("比", "对比", "vs", "差", "高一", "低一", "多了", "少了", "相较")
STEP_WORDS = ("第一", "第二", "第三", "首先", "然后", "接着", "最后", "第一步", "步骤", "①", "②", "③")
FLOW_WORDS = ("因为", "所以", "导致", "从而", "于是", "结果", "因此",
              "而是", "但", "其实", "只是", "反而", "也就是说")
LIST_WORDS = ("、", "；", ";")
UNIT_RE = re.compile(r"(倍|%|％|度|秒|分|小时|天|年|米|厘米|毫米|公里|千克|克|毫克|分贝|Hz|赫兹|"
                     r"元|块|次|个|张|人|岁|dB|℃|瓦|伏|安|欧|焦|卡)")


# 切行时数字必须整体保留：按字符硬切会把 "1000" 切成 "10" + "00"，
# 画面上就真的写着「只有 10」「00 尼特」——图内事实链门禁抓到过（不是我瞎猜，
# 是 graphicaudit 报"钩子画面数字 ['00','10'] 口播里没说"，一查发现是切行切断了）。
_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?%?|[A-Za-z]+|\s+|.")


def _pack(tokens: list[str], max_chars: int) -> list[str]:
    """把 token 贪心打包成行，不切开任何 token。"""
    lines, cur = [], ""
    for tk in tokens:
        if len(cur) + len(tk) > max_chars and cur.strip():
            lines.append(cur.strip())
            cur = tk
        else:
            cur += tk
    if cur.strip():
        lines.append(cur.strip())
    return lines


def _big_text(ln: str, max_chars: int = 11) -> list[str]:
    """把一句话切成大字行：先按标点切段，段太长再按 token 边界折行（**不切断数字**）。"""
    parts = [p for p in re.split(r"[，。,、；：:;!?！？]", ln) if p.strip()]
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= max_chars:
            out.append(p)
            continue
        out.extend(_pack(_TOKEN_RE.findall(p), max_chars))
    # deck._big 只画 3 行。超过 3 行时不能把尾巴直接 concat——
    # 那样会粘出 "…高达5000 尼特" 这种 18 字长句，字号会被压到看不清。
    # 改成按 token 边界**均衡重排成 3 行**（数字仍然是整体）。
    # 修法：**合并最短的相邻两行**，而不是重新按字数均分——
    # 均分会把词切开（"亮/度"、"屏/幕"），中文读起来是断的；
    # 按标点产生的行合并则保住了断句，数字也不会被切。
    while len(out) > 3:
        best_i, best_len = 0, 10 ** 6
        for i in range(len(out) - 1):
            merged = len(out[i]) + len(out[i + 1])
            if merged < best_len:
                best_i, best_len = i, merged
        out[best_i:best_i + 2] = [out[best_i] + out[best_i + 1]]
    return out or [ln]


def _first_number(ln: str):
    """取句子里的第一个"数字 + 单位"，返回 (值字符串, 单位) 或 None。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|％)?", ln)
    if not m:
        return None
    val = m.group(1)
    if m.group(2):
        return val, "%"
    tail = ln[m.end():m.end() + 4]
    um = UNIT_RE.search(tail)
    return val, (um.group(1) if um else "")


# 镜头角色决定"能选哪些 kind"：solve 是「怎么办」，不该出现 stat/compare
# （实测「把屏幕侧过来 30 度」被判成 stat，渲成一个大数字 30 —— 语义完全跑偏）。
ALLOWED = {
    "hook": ("big",),
    "rule": ("big", "flow", "formula"),
    "insight": ("big", "flow", "bars"),
    "reveal": ("compare", "stat", "big"),
    "solve": ("big", "steps", "list", "cards"),
}


def _ordered(shot_id, kinds):
    """按镜头角色过滤候选类型，未列出的角色允许全部。"""
    allow = ALLOWED.get(shot_id)
    return kinds if not allow else [k for k in kinds if k in allow]


def guess_deck(shot_id: str, ln: str) -> dict:
    """按"镜头角色 + 口播内容"猜一个 deck spec。猜不出退回 big（永远能画）。"""
    cands = []
    nums = numbers_in(ln)
    if len(nums) >= 2 and any(w in ln for w in CMP_WORDS):
        first = _first_number(ln)
        rest = ln.replace(first[0], "", 1) if first else ln
        second = _first_number(rest)
        if first and second:
            cands.append(({"kind": "compare",
                           "left": {"value": first[0], "unit": first[1],
                                    "label": _big_text(ln)[0][:10]},
                           "right": {"value": second[0], "unit": second[1], "label": "对照"},
                           "note": ln}, "compare"))
    if any(w in ln for w in STEP_WORDS):
        items = [p.strip() for p in re.split(r"[，。,；;]", ln) if p.strip()][:4]
        if len(items) >= 3:
            cands.append(({"kind": "steps", "items": items[:4], "note": ""}, "steps"))
            cands.append(({"kind": "list", "items": items[:4], "note": ""}, "list"))
    if any(w in ln for w in FLOW_WORDS) and len(ln) >= 14:
        items = [p.strip() for p in re.split(r"[，。,；;]", ln) if p.strip()][:3]
        if len(items) >= 2:
            cands.append(({"kind": "flow",
                           "items": [{"t": x[:9], "d": ""} for x in items], "note": ""}, "flow"))
    if len(nums) == 1 and UNIT_RE.search(ln):
        n = _first_number(ln)
        if n:
            cands.append(({"kind": "stat", "value": n[0], "unit": n[1],
                           "label": _big_text(ln)[0][:12], "note": "", "digits": 1}, "stat"))
    if any(w in ln for w in STEP_WORDS) and cands:
        items = [p.strip() for p in re.split(r"[，。,；;]", ln) if p.strip()][:3]
        if 2 <= len(items) <= 3:
            cands.append(({"kind": "cards", "items": items, "note": ""}, "cards"))
    for spec, kind in cands:
        if kind in _ordered(shot_id, [kind]):
            return spec
    # 连续三个 big 会长得一模一样（同版式同动画）。按镜头角色换入场风格：
    # 钩子要"砸"、规则要"揭幕"、原理要"上浮"，观众才不会觉得卡住了。
    style = {"hook": "slam", "rule": "wipe", "insight": "rise",
             "reveal": "pop", "solve": "slam"}.get(shot_id, "pop")
    return {"kind": "big", "lines": _big_text(ln), "style": style}


def _cut_safe(text: str, limit: int) -> str:
    """按 token 边界截断，**不切断数字**（"…只有 100|0 尼特" 这种在封面上很难看）。"""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    toks = _TOKEN_RE.findall(text)
    out = ""
    for tk in toks:
        if len(out) + len(tk) > limit:
            break
        out += tk
    return out.rstrip(" ，,。.") or text[:limit]


def _emphasis(ln: str) -> list[str]:
    """每镜挑 1~2 个要高亮的词：优先"数字+单位"，没有再取最长的实义词块。

    score 的视觉分有一条"关键词高亮 0 个（建议每镜 1~2 个）"——空着就扣分，
    而高亮的本质就是"让观众眼睛停一下"，数字和判断词是最自然的落脚点。
    """
    out = []
    for m in re.finditer(r"\d+(?:\.\d+)?\s*(?:%|％|倍|度|秒|分|小时|天|年|米|厘米|毫米|公里|"
                         r"千克|克|毫克|尼特|分贝|元|次|个|张|人|岁)?", ln):
        tok = m.group(0).strip()
        if tok and len(tok) <= 8:
            out.append(tok)
        if len(out) >= 2:
            break
    if not out:
        chunks = [c for c in re.split(r"[，。,、；：]", ln) if len(c.strip()) >= 3]
        if chunks:
            c = max(chunks, key=len).strip()
            out.append(c[:6])
    return out[:2]


def build_draft(req: dict) -> dict:
    title = str(req.get("title") or "").strip() or "未命名选题"
    lines = [str(x).strip() for x in (req.get("lines") or []) if str(x).strip()]
    if not lines:
        raise SystemExit("至少要给一句口播")
    short = bool(req.get("short")) or len(lines) <= 4 and req.get("short")
    ids = SHOT_IDS_SHORT if short else SHOT_IDS_LONG
    lines = lines[: len(ids)]
    while len(lines) < len(ids):
        lines.append(lines[-1])
    # 首句只有陈述（"亮度只有 1000 尼特"）而没有反常识/疑问信号时，score 会扣 5 分。
    # 科普钩子的标准写法就是"摆数字 → 抛疑问"，补一句最短的"为什么？"即可，
    # 不改变事实、不改变观点，只把话说成问句。req 里给 hook_signal:false 可关掉。
    if req.get("hook_signal", True):
        sig = ("错", "不", "为什么", "居然", "竟然", "其实", "真相", "别再", "别用",
               "陷阱", "坑", "冷知识", "反常识", "？", "?")
        if not any(w in lines[0] for w in sig):
            lines[0] = lines[0].rstrip("。.！!") + "。为什么？"
    # 结尾没互动引导会拉低关注转化（lint 有这条规则）。用户没写就补一句最短的，
    # 补在口播里而不是只写在封面上——口播才是转化的位置。
    if not any(w in lines[-1] for w in ("评论", "关注", "收藏", "点赞", "你怎么看", "留言")):
        lines[-1] = lines[-1].rstrip("。.！!") + "。你怎么看？评论区聊聊。"

    # 时长按**音节数**估，不拍一个统一值：统一给 6.6s 时，
    # 短句镜头留白 0.8s（lint 报"留白不足"）、长句镜头语速 5.4 音节/秒（报"偏快"）。
    # 口播舒适区 4.0~5.2 音节/秒，取 4.45 做预估，再交给 tighten.py 按真实配音收口。
    rate = float(req.get("rate") or 4.45)
    pad = float(req.get("pad") or 1.55)          # 图形动效要的呼吸
    floor, cap = (2.6, 6.0) if short else (4.2, 8.4)
    shots, deck = [], {}
    for sid, ln in zip(ids, lines):
        need = syllables(ln) / rate + pad
        dur = round(min(cap, max(floor, need)), 2)
        shots.append({"id": sid, "min_dur": dur, "narration": ln,
                      "emphasis": _emphasis(ln), "deck_hint": ln})
        deck[sid] = guess_deck(sid, ln)

    headline = req.get("headline") or _big_text(title, 6)
    if isinstance(headline, str):
        headline = _big_text(headline, 6)
    nums = sorted(numbers_in(lines[0] + " " + title))
    highlight = [m.group(0) for m in re.finditer(r"\d+(?:\.\d+)?%?", title)][:1]
    if not highlight:
        # 标题里没有数字时不能就空着：score 会报"封面没有 highlight"。
        # 科普标题的判断词通常在结尾（"看不清""不等于 0.3""为什么不冷"），取末 3 字。
        tail = re.sub(r"[，。！？、,.!?\s]", "", title)[-3:]
        if len(tail) >= 2:
            highlight = [tail]
    cfg = {
        "title": title,
        "subtitle": str(req.get("sub") or "").strip(),
        "theme": req.get("theme") or "midnight",
        "voice": {"preset": req.get("voice") or "yunxi"},
        "fps": 30,
        "size": [1080, 1920],
        "lead_in": 0.0,
        "tail": 0.4,
        "disclosure": "AI 语音合成配音；画面为程序化渲染",
        "bgm": req.get("bgm") or None,
        "cover": {
            "kicker": str(req.get("kicker") or "硬核科普").strip(),
            "serial": str(req.get("serial") or ""),
            "headline": headline[:3],
            "highlight": highlight,
            "sub": _cut_safe(str(req.get("cover_sub") or lines[0]), 18),
            "tags": (req.get("tags") or ["#科普", "#冷知识", "#涨知识"])[:3],
            "cta": "看到最后 →",
        },
        "shots": shots,
        "deck": deck,
        "transition": {"type": "fade", "dur": 0.24},
        "publish_title": title[:20],
        "kernel": str(req.get("kernel") or ""),
        "refs": req.get("refs") or [],
    }
    if short:
        cfg["format"] = "short"
    return cfg


def build_oneimage(cfg: dict, req: dict) -> dict:
    """顺手出一份"一张图"spec：把五段口播压成 4~6 个要点。

    一张图是图文笔记的主图，缺了它这条片子只能发视频不能发图文。
    要点不做二次创作（那需要人/大模型），直接用口播原文，长度交给 oneimage 折行。
    """
    pts = []
    for sh in cfg["shots"]:
        ln = str(sh.get("narration") or "").strip()
        if not ln:
            continue
        head, _, tail = ln.partition("，")
        pts.append({"t": head[:12], "d": tail[:40] if tail else ""})
    return {
        "kicker": cfg["cover"]["kicker"],
        "title": cfg["cover"]["headline"][:3],
        "highlight": cfg["cover"]["highlight"],
        "points": pts[:6],
        "big": None,
        "tags": cfg["cover"]["tags"],
        "footer": "@你的账号 · 每条一个反常识",
        "style": req.get("style") or "mesh",
        "palette": req.get("palette") or "default",
        "seed": int(req.get("seed") or 7),
        "theme": cfg.get("theme"),
    }


def main():
    ap = argparse.ArgumentParser(description="5 句话 → content.json")
    ap.add_argument("--from-json", default=None, help="需求 JSON（UI 用这个）")
    ap.add_argument("--title", default=None)
    ap.add_argument("--lines", nargs="*", default=None)
    ap.add_argument("--kicker", default=None)
    ap.add_argument("--theme", default=None)
    ap.add_argument("--voice", default=None)
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--no-oneimage", action="store_true", help="不生成一张图 spec")
    a = ap.parse_args()
    if a.from_json:
        req = json.load(open(a.from_json, encoding="utf-8"))
    else:
        req = {"title": a.title, "lines": a.lines, "kicker": a.kicker,
               "theme": a.theme, "voice": a.voice, "short": a.short}
    cfg = build_draft(req)
    out = a.out or os.path.join("examples", "draft.content.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(cfg, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    if not a.no_oneimage:
        slug = os.path.basename(out).replace(".content.json", "")
        spec_path = os.path.join(os.path.dirname(out) or ".", f"oneimage-{slug}.json")
        json.dump(build_oneimage(cfg, req), open(spec_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    kinds = ", ".join(f"{k}:{v['kind']}" for k, v in cfg["deck"].items())
    print(f"[ok] {out}  {len(cfg['shots'])} 镜  {kinds}")


if __name__ == "__main__":
    main()
