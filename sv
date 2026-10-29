#!/usr/bin/env bash
# shortvideo 命令行入口
#
#   sv                 启动剪辑台（默认 8099）
#   sv app --port 8100  指定端口
#   sv list            列出所有项目
#   sv new "标题"       新建一条（五句话可留空，进剪辑台再写）
#   sv export <slug>   导出成片
#   sv qa <slug>       跑质检
#   sv check           跑全仓门禁（verify.sh）
#
# 设计原则：这个脚本只做"入口"，所有逻辑都在 python 里，
# 免得命令行和界面各写一套规则。
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${SV_ROOT:-$SELF}"
PY="${SV_PY:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="${SV_PY:-python3}"

cmd="${1:-app}"
shift || true

case "$cmd" in
  app|serve|ui)
    port=8099
    while [ $# -gt 0 ]; do
      case "$1" in
        --port) port="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    echo "剪辑台： http://127.0.0.1:$port"
    exec "$PY" "$ROOT/ui/preview.py" --port "$port"
    ;;
  list)
    "$PY" - "$ROOT" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
rows = []
for p in sorted(glob.glob(os.path.join(root, "examples", "short-*.content.json"))) + \
        sorted(glob.glob(os.path.join(root, "examples", "*.content.json"))):
    slug = os.path.basename(p).replace(".content.json", "")
    if slug.startswith("short-") and slug in [r[0] for r in rows]:
        continue
    try:
        cfg = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    dur = sum(float(s.get("min_dur") or 0) for s in cfg.get("shots") or [])
    mp4 = os.path.join(root, "examples", "out", slug, f"{slug}.mp4")
    title = cfg.get("title") or cfg.get("publish_title") or slug
    rows.append((slug, title, round(dur, 1), os.path.exists(mp4)))
seen, out = set(), []
for r in rows:
    if r[0] in seen:
        continue
    seen.add(r[0]); out.append(r)
print(f"{len(out)} 条项目")
for slug, title, dur, has in out:
    print(f"  {'●' if has else '○'} {slug:<28} {dur:>5.1f}s  {title}")
PY
    ;;
  new)
    title="${1:-}"
    if [ -z "$title" ]; then
      read -r -p "标题: " title
    fi
    "$PY" - "$ROOT" "$title" <<'PY'
import json, os, sys, urllib.request
root, title = sys.argv[1], sys.argv[2]
port = os.environ.get("SV_PORT", "8099")
body = json.dumps({"title": title, "lines": [], "kicker": "硬核科普"}).encode()
try:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/project/new",
                                 data=body, headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    print(f"已建项目 {d.get('slug')} —— 打开 http://127.0.0.1:{port}/app/#{d.get('slug')} 写五句话")
except Exception as e:
    print(f"需要先启动剪辑台（sv app）：{e}")
PY
    ;;
  export)
    slug="${1:?用法: sv export <slug>}"
    "$PY" "$ROOT/ui/preview.py" --export "$slug"
    ;;
  qa)
    slug="${1:?用法: sv qa <slug>}"
    ( cd "$ROOT" && ./verify.sh "$slug" )
    ;;
  check)
    ( cd "$ROOT" && ./verify.sh --no-slow )
    ;;
  *)
    sed -n '2,13p' "$0"
    exit 1
    ;;
esac
