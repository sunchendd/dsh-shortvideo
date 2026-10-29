#!/bin/bash
# 启动/重启本地服务。用 pidfile 管理进程，避免 pkill -f 把自己的 shell 也匹配掉
# （这个坑已经踩过两次：命令行里同时出现 pkill 的模式和启动命令时，pgrep 会匹配到 bash -c 自己）。
set -u
cd "$(dirname "$0")/../.." || exit 1
PIDF=/tmp/dsh-sv.pid
PORT=${1:-8099}
PY=${PY:-/root/shortvideo-lab/.venv/bin/python}

if [ -f "$PIDF" ]; then
  OLD=$(cat "$PIDF" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    kill "$OLD" 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$OLD" 2>/dev/null || break; sleep 0.2; done
  fi
  rm -f "$PIDF"
fi

setsid nohup "$PY" ui/preview.py --port "$PORT" >/tmp/preview.log 2>&1 < /dev/null &
echo $! > "$PIDF"
for _ in $(seq 1 40); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/projects"; then
    echo "服务已就绪 http://127.0.0.1:$PORT  pid=$(cat $PIDF)"
    exit 0
  fi
  sleep 0.25
done
echo "启动失败，日志："; tail -5 /tmp/preview.log
exit 1
