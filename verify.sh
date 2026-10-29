#!/usr/bin/env bash
# 一键全验：把 13 道门禁串起来跑，任何一道红就非 0 退出。
#
# 为什么需要它：单条片子要验的项太多，靠记忆逐条敲命令必然漏；
# 而且漏掉的那道恰好是最容易出问题的那道（静默裁字、缺 AI 标注都是这么漏的）。
#
#   ./verify.sh                    # 全库
#   ./verify.sh short-09           # 只验名字以 short-09 开头的
#   ./verify.sh --no-slow          # 跳过慢的（抽帧类）
#
# 退出码：0 = 全部通过；1 = 有门禁失败（末尾会列出失败的项）

set -uo pipefail
cd "$(dirname "$0")"

PY=${PY:-/root/shortvideo-lab/.venv/bin/python}
TOOLS=skills/shortvideo/tools
DIR=examples
ONLY=""
SLOW=1

for a in "$@"; do
  case "$a" in
    --no-slow) SLOW=0 ;;
    -*) echo "未知参数 $a"; exit 2 ;;
    *) ONLY="$a" ;;
  esac
done

FAILED=()
run() {  # run <名字> <命令...>
  local name="$1"; shift
  printf '%-22s' "$name"
  local out
  if out=$("$@" 2>&1); then
    echo "✅"
    [[ -n "${VERBOSE:-}" ]] && echo "$out"
  else
    echo "❌"
    echo "$out" | tail -12 | sed 's/^/    /'
    FAILED+=("$name")
  fi
}

# 只验单条时，给支持 --only 的工具传参
if [[ -n "$ONLY" ]]; then
  run "清单+总分"    $PY $TOOLS/score.py --content "$(ls $DIR/$ONLY*.content.json | head -1)"
else
  run "清单+总分"    bash -c "for f in $DIR/*.content.json; do $PY $TOOLS/score.py --content \"\$f\" >/dev/null || { echo \"FAIL \$f\"; exit 1; }; done"
fi
run "图形事实链"    $PY $TOOLS/graphicaudit.py --dir $DIR
run "文档完整度"    $PY $TOOLS/brief.py --dir $DIR
run "发布可行性"    $PY $TOOLS/publishcheck.py --dir $DIR
run "响度一致"      $PY $TOOLS/loudaudit.py --dir $DIR
run "配音情绪"      $PY $TOOLS/voiceaudit.py --dir $DIR
run "封面可读性"    $PY $TOOLS/coveraudit.py --dir $DIR/out

if [[ "$SLOW" == "1" ]]; then
  # 单条模式必须把 --video 传进去，否则 `./verify.sh short-09` 会扫全库跑 3 分钟
  if [[ -n "$ONLY" ]]; then
    MP4=$(ls $DIR/out/$ONLY*/"$ONLY"*.mp4 2>/dev/null | head -1)
    run "前3秒留人" $PY $TOOLS/hookaudit.py --video "${MP4:-/dev/null}"
    run "画面体检"  $PY $TOOLS/qa.py --video "${MP4:-/dev/null}" --fps 4
  else
    run "前3秒留人" $PY $TOOLS/hookaudit.py --dir $DIR
    run "画面体检"  $PY $TOOLS/qa.py --dir $DIR --fps 4
  fi
fi

if [[ -n "$ONLY" ]]; then
  run "总检(checklist)" $PY $TOOLS/checklist.py --dir $DIR --only "$ONLY"
else
  run "总检(checklist)" $PY $TOOLS/checklist.py --dir $DIR
fi

echo
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "全部通过 ✅"
  exit 0
fi
echo "未通过 ${#FAILED[@]} 项：${FAILED[*]}"
exit 1
