#!/usr/bin/env python3
"""补文档时用的护栏：证明"只加了字段，没动任何会影响成片的内容"。

成片由 narration / shots / deck / voice / format 等字段决定；
kernel / refs / deck_hint 只是文档，加它们不该改变任何一个像素或一帧音频。

用法：
  pydoc_guard.py --snapshot examples/*.content.json --out /tmp/before.json
  # …别的 agent 改文件…
  pydoc_guard.py --compare /tmp/before.json examples/*.content.json
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import sys

# 这些字段变化会影响成片（或被工具读取参与判定）
RENDER_KEYS = ("shots", "deck", "voice", "format", "seconds", "bgm", "cover",
               "publish_title", "title", "kicker", "hook", "tags")
DOC_KEYS = ("kernel", "refs")


def digest(path):
    c = json.load(open(path, encoding="utf-8"))
    shots = []
    for s in c.get("shots") or []:
        shots.append({k: v for k, v in s.items() if k != "deck_hint"})
    payload = {"render": {k: c.get(k) for k in RENDER_KEYS}, "shots": shots}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    doc = {k: c.get(k) for k in DOC_KEYS}
    hints = sum(1 for s in c.get("shots") or [] if str(s.get("deck_hint") or "").strip())
    return {"render_sha": hashlib.sha256(blob).hexdigest()[:16],
            "has_kernel": bool(str(c.get("kernel") or "").strip()),
            "has_refs": bool(c.get("refs")),
            "hints": hints, "shots": len(c.get("shots") or [])}


def main():
    ap = argparse.ArgumentParser(description="补文档护栏")
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--compare", metavar="SNAP")
    ap.add_argument("--out")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    if args.snapshot:
        files = args.files or sorted(glob.glob("examples/*.content.json"))
        snap = {f: digest(f) for f in files}
        json.dump(snap, open(args.out or "/tmp/pydoc_before.json", "w",
                             encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"已快照 {len(snap)} 个文件的成片指纹 → {args.out or '/tmp/pydoc_before.json'}")
        return 0

    if args.compare:
        before = json.load(open(args.compare, encoding="utf-8"))
        files = args.files or sorted(before)
        bad, improved = 0, 0
        for f in files:
            if f not in before:
                continue
            now, old = digest(f), before[f]
            if now["render_sha"] != old["render_sha"]:
                bad += 1
                print(f"  ⛔ {f}: 成片指纹变了（说明动了 narration/deck/voice 等）")
            if now["has_kernel"] and now["has_refs"] and now["hints"] == now["shots"]:
                improved += 1
        print(f"\n成片指纹被改动的：{bad}｜文档已补全的：{improved}/{len(files)}")
        return 1 if bad else 0
    ap.error("需要 --snapshot 或 --compare")


if __name__ == "__main__":
    sys.exit(main())
