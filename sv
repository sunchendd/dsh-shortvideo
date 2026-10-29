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
    shift || true
    tmp=$(mktemp)
    if [ $# -gt 0 ]; then
      printf '%s\n' "$@" > "$tmp"
    elif [ -t 0 ]; then
      echo "输入 3~5 句话（每行一句，空行结束）："
      n=0
      while [ $n -lt 5 ]; do
        read -r -p "  $((n+1)). " line || break
        [ -z "$line" ] && break
        printf '%s\n' "$line" >> "$tmp"
        n=$((n+1))
      done
    else
      cat > "$tmp"
    fi
    "$PY" - "$ROOT" "$title" "$(cat "$tmp")" <<'PY'
import json, os, sys, urllib.request
root, title, raw = sys.argv[1], sys.argv[2], sys.argv[3]
lines = [l.strip() for l in raw.splitlines() if l.strip()]
if len(lines) < 3:
    raise SystemExit(f"[x] 至少 3 句口播，现在只有 {len(lines)} 句。"
                     "（每行一句：钩子 / 规则 / 原理 / 高潮 / 实战）")
port = os.environ.get("SV_PORT", "8099")
body = json.dumps({"title": title, "lines": lines}).encode()
try:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/project/new",
                                 data=body, headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    slug = d.get("slug")
    print(f"已建项目 {slug}（{len(lines)} 句）")
    print(f"  打开 http://127.0.0.1:{port}/app/#{slug}")
except Exception as e:
    msg = getattr(e, "read", lambda: b"")()
    raise SystemExit(f"[x] 创建失败：{msg.decode()[:200] if msg else e}\n"
                     "    需要先启动剪辑台：sv app")
PY
    rm -f "$tmp"
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
