#!/bin/bash
# 启动/重启本地服务。
#
# 踩过的坑（两个）：
# 1) 用 `pkill -f "ui/preview.py"` 会匹配到我自己这条 bash -c（命令行里同时含有启动命令），把自己杀掉两次；
# 2) 只用 pidfile 判断存活不够 —— 如果端口被"不是 pidfile 里那个"的旧进程占着，
#    新进程绑定失败，而健康检查又被旧进程满足，就会以为重启成功、其实一直在跑旧代码。
# 所以这里：先按端口找占用者并杀掉 → 等端口真空出来 → 启动后核对监听者 PID 是不是新进程。
set -u
cd "$(dirname "$0")/../.." || exit 1
PIDF=/tmp/dsh-sv.pid
PORT=${1:-8099}
PY=${PY:-/root/shortvideo-lab/.venv/bin/python}

listener_pid() {
  ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p' | grep -oP 'pid=\K[0-9]+' | head -1
}

OLD=$(listener_pid)
if [ -z "${OLD:-}" ] && [ -f "$PIDF" ]; then OLD=$(cat "$PIDF" 2>/dev/null || true); fi
if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
  kill "$OLD" 2>/dev/null
  for _ in $(seq 1 25); do kill -0 "$OLD" 2>/dev/null || break; sleep 0.2; done
  kill -9 "$OLD" 2>/dev/null || true
fi
rm -f "$PIDF"

for _ in $(seq 1 25); do [ -z "$(listener_pid)" ] && break; sleep 0.2; done
if [ -n "$(listener_pid)" ]; then
  echo "端口 $PORT 仍被占用（pid=$(listener_pid)），放弃"
  exit 1
fi

setsid nohup "$PY" ui/preview.py --port "$PORT" >/tmp/preview.log 2>&1 < /dev/null &
NEW=$!
echo "$NEW" > "$PIDF"
for _ in $(seq 1 60); do
  if curl -s -o /dev/null "http://127.0.0.1:$PORT/api/projects"; then
    LIVE=$(listener_pid)
    if [ -n "${LIVE:-}" ] && [ "$LIVE" != "$NEW" ]; then
      echo "启动异常：监听者是 $LIVE，但新进程是 $NEW（可能在跑旧代码）"
      exit 1
    fi
    echo "服务已就绪 http://127.0.0.1:$PORT  pid=$NEW"
    exit 0
  fi
  sleep 0.25
done
echo "启动失败，日志："; tail -5 /tmp/preview.log
exit 1
