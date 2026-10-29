#!/usr/bin/env bash
# shortvideo 一键安装/更新脚本（WSL / Linux / macOS）
#
#   ./install.sh              # 安装到 dsh 的 skill 目录（默认 ~/.dsh/skills/shortvideo）
#   ./install.sh --no-deps    # 只同步代码，不建 venv / 不装依赖
#   DSH_SKILLS_DIR=/path ./install.sh   # 自定义 skill 根目录
#
# 做的事：
#   1) 把 skills/shortvideo 同步到 <skills_dir>/shortvideo（保留 .venv 与 tts_cache）
#   1b) 同步 4 个角色技能（创意总监/短视频大师/一张图总结/AI 画室），纯提示词无依赖
#   2) 在该目录建 .venv 并装 requirements.txt（Pillow / edge-tts / imageio-ffmpeg）
#   3) 检查中文字体（缺了给 apt 命令）
#   4) 用 --preview 跑一条最小样例，确认整条链路可用
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$REPO_DIR/skills/shortvideo"
SKILLS_DIR="${DSH_SKILLS_DIR:-$HOME/.dsh/skills}"
DEST_DIR="$SKILLS_DIR/shortvideo"
# 角色技能（纯提示词，无依赖）：创意总监 / 短视频大师 / 一张图总结 / AI 画室
ROLE_SKILLS=(xhs-creative-director xhs-art-director shortvideo-master one-image-summary ai-art-studio ui-ux-pro-max brand banner-design design-system)
VENV="$DEST_DIR/.venv"
PIP_INDEX="${SHORTVIDEO_PIP_INDEX:-}"
DO_DEPS=1
DO_SMOKE=1

while [ $# -gt 0 ]; do
  case "$1" in
    --no-deps) DO_DEPS=0; DO_SMOKE=0 ;;
    --no-smoke) DO_SMOKE=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
  shift
done

echo "==> 同步技能代码：$SRC_DIR -> $DEST_DIR"
mkdir -p "$DEST_DIR"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude 'tts_cache' \
        "$SRC_DIR/" "$DEST_DIR/"
else
  (cd "$SRC_DIR" && tar -cf - --exclude '.venv' --exclude '__pycache__' .) \
    | (cd "$DEST_DIR" && tar -xf -)
fi

echo "==> 同步角色技能：${ROLE_SKILLS[*]}"
DRIFT=0
for role in "${ROLE_SKILLS[@]}"; do
  [ -d "$REPO_DIR/skills/$role" ] || continue
  # 漂移检测：装好的 skill 若被直接改过（而不是改仓库副本），同步会把它冲掉。
  # 实测代价：第 19 轮写的「短版」一节就这样静默消失了一整轮。
  if [ -f "$SKILLS_DIR/$role/SKILL.md" ] && [ -f "$REPO_DIR/skills/$role/SKILL.md" ] \
     && ! cmp -s "$SKILLS_DIR/$role/SKILL.md" "$REPO_DIR/skills/$role/SKILL.md"; then
    if grep -qxF "$(cat "$REPO_DIR/skills/$role/SKILL.md")" "$SKILLS_DIR/$role/SKILL.md" 2>/dev/null; then
      :
    else
      DRIFT=1
      echo "    [!] $role/SKILL.md 与仓库不一致，已备份为 SKILL.md.bak（今后请改仓库副本）"
      cp -f "$SKILLS_DIR/$role/SKILL.md" "$SKILLS_DIR/$role/SKILL.md.bak"
    fi
  fi
  mkdir -p "$SKILLS_DIR/$role"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude '__pycache__' "$REPO_DIR/skills/$role/" "$SKILLS_DIR/$role/"
  else
    cp -r "$REPO_DIR/skills/$role/." "$SKILLS_DIR/$role/"
  fi
  echo "    $SKILLS_DIR/$role"
done
if [ "$DRIFT" = "1" ]; then
  echo "    注意：检测到直接改装的副本，已覆盖。角色 skill 的正确改法是"
  echo "          编辑仓库 skills/<role>/SKILL.md 后重跑 install.sh"
fi

if [ "$DO_DEPS" = "1" ]; then
  if [ ! -x "$VENV/bin/python" ]; then
    echo "==> 建 venv：$VENV"
    python3 -m venv "$VENV"
  fi
  echo "==> 安装依赖"
  if [ -n "$PIP_INDEX" ]; then
    "$VENV/bin/pip" install -q -i "$PIP_INDEX" -r "$DEST_DIR/requirements.txt"
  else
    "$VENV/bin/pip" install -q -r "$DEST_DIR/requirements.txt"
  fi
  "$VENV/bin/python" - <<'PY'
import PIL, edge_tts, imageio_ffmpeg
print(f"    Pillow {PIL.__version__} / edge-tts {getattr(edge_tts,'__version__','?')} / "
      f"ffmpeg {imageio_ffmpeg.get_ffmpeg_exe()}")
PY
fi

echo "==> 准备配音缓存目录"
# edge-tts 是唯一的外部依赖。网络/代理不可用时，没有缓存就只能出静音片，
# 所以缓存默认放在 skill 根目录的 .audiocache/，安装时建好并检查可用性。
AUDIO_CACHE="$REPO_DIR/.audiocache"
mkdir -p "$AUDIO_CACHE"
if ls "$AUDIO_CACHE"/*.wav >/dev/null 2>&1; then
  n=$(ls "$AUDIO_CACHE"/*.wav 2>/dev/null | wc -l)
  echo "    已有 $n 条配音缓存（断网也能重渲）"
else
  echo "    缓存为空：首次出片需要能连 speech.platform.bing.com"
fi

echo "==> 检查中文字体"
if fc-list :lang=zh 2>/dev/null | grep -q .; then
  fc-list :lang=zh 2>/dev/null | head -2 | sed 's/^/    /'
else
  echo "    [!] 没找到中文字体，字幕会变豆腐块。建议："
  echo "        sudo apt-get install -y fonts-wqy-zenhei   # 或 fonts-noto-cjk"
fi

if [ "$DO_SMOKE" = "1" ] && [ -x "$VENV/bin/python" ]; then
  echo "==> 冒烟测试（--preview，数秒）"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  cp "$DEST_DIR/topics-content/sorting.content.json" "$TMP/content.json"
  ( cd "$TMP" && "$VENV/bin/python" "$DEST_DIR/tools/make.py" \
      --content content.json --topic sorting --out out/smoke.mp4 --preview ) \
      | sed 's/^/    /'
  ls "$TMP"/build/preview/*.png | sed 's/^/    预览帧: /'
fi

cat <<EOF

✅ 安装完成

  skill 目录 : $DEST_DIR
  角色技能   : ${ROLE_SKILLS[*]}（$SKILLS_DIR/<name>）
  Python     : $VENV/bin/python   （make.py 会自动切到这个 venv）

直接出片：
  cd <你的 project 目录>
  $VENV/bin/python $DEST_DIR/tools/make.py \\
      --content content.json --topic topic_<名字> --out out/<名字>.mp4

在 dsh 会话里说「用 shortvideo 做一条 XX 视频」也能触发（skill 目录热加载，无需重启）。
EOF
