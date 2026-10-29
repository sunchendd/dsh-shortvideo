---
name: shortvideo
description: 零 GPU 竖屏短视频制作工作流：从任意知识题材生成"钩子-规则-原理-高潮-实战"五段式讲解视频（1080x1920, H.264+AAC, 微软神经配音, 中文字幕）。纯程序化视觉（Pillow 逐帧渲染），无文生图、无文生视频。
whenToUse: 用户想制作知识讲解/数学/科普类竖屏短视频，或要求"生成短视频/做一条视频/短视频工作流"且本机无 GPU 时。已知范例：九宫格熄灯（矩阵求逆）、排序算法可视化、单位换算推导。
---

# shortvideo —— 零 GPU 短视频工作流

把"烧脑知识"做成 60 秒竖屏讲解视频。整条流水线只依赖 **Python + Pillow + edge-tts + ffmpeg**，
不碰任何生成式视觉模型（无文生图/无文生视频），因此任何机器都能跑。

## 工作流总览（六步）

```
①选题脚本 → ②环境引导 → ③题材包 → ④配音对轴 → ⑤逐帧渲染 → ⑥合成交付
```

| 步骤 | 做什么 | 产物 |
|------|--------|------|
| ①选题脚本 | 按"五段式"写分镜台词（见下） | `project/content.json` |
| ②环境引导 | 跑 `install.sh`（一次性），建 skill 自带 venv | `<SKILL_DIR>/.venv/` |
| ③题材包 | 写一个 Python 镜头绘制模块 + 数学内核 | `project/topic_<name>.py` |
| ④配音对轴 | edge-tts 逐镜合成（按 mood 调语速音调，多镜并发）→ 裁静音 → 时长对轴 | `build/*.wav` |
| ⑤逐帧渲染 | Pillow 画 1080x1920 帧（约 130-200fps，多路并行） | rawvideo → ffmpeg |
| ⑥合成交付 | H.264+AAC 合成 + 封面 + 自检报告 | `out/<name>.mp4` + `cover.png` |

## 五段式叙事模板（分镜铁律）

每个镜头一句话台词（口语化、20~45 字），情绪标记驱动配音 prosody：

| 镜头 | mood | 情绪 | 台词要领 |
|------|------|------|----------|
| hook | hook | 快而亮，制造悬念 | 一个抓人的问题，不给答案 |
| rule | rule | 平稳陈述 | 把核心规则/定义讲清楚 |
| insight | explain | 放慢，讲道理 | 关键观察/引理，为什么成立 |
| reveal | reveal | 提起来，高潮 | 结论/公式/反转，情绪最高点 |
| solve | win | 自信收尾 | 实战演示 + 一句方法论总结 |

镜头数可增删，但**钩子必须 3 秒内抛出问题**，**高潮前必须有铺垫**。

## 具体操作步骤（agent 执行手册）

### 第 0 步：定位工具与题材目录

```
SKILL_DIR  = 本 SKILL.md 所在目录
TOOLS      = $SKILL_DIR/tools          # make.py + 引擎
TOPICS     = $SKILL_DIR/tools/topics   # 题材包（开发模板，可复制改写）
CONTENTS   = $SKILL_DIR/topics-content # 内置题材的 content.json
PY         = $SKILL_DIR/.venv/bin/python
```

`make.py` 若发现当前解释器缺依赖，会自动 exec 到 `$SKILL_DIR/.venv/bin/python`，
所以直接 `python3 $TOOLS/make.py ...` 也不会报 ImportError。

### 第 1 步：选题脚本（你写，不跑代码）

向用户确认题材与核心知识点后，直接写出 `content.json`（放 `project/` 下）：

```json
{
  "title": "标题（大字）",
  "subtitle": "副标题",
  "voice": {"engine": "edge", "name": "zh-CN-XiaoxiaoNeural", "rate": "+0%", "pitch": "+0Hz"},
  "fps": 30, "size": [1080, 1920],
  "lead_in": 0.15, "tail": 0.55,
  "shots": [
    {"id": "hook",    "mood": "hook",    "min_dur": 5.2, "narration": "……"},
    {"id": "rule",    "mood": "rule",    "min_dur": 5.4, "narration": "……"},
    {"id": "insight", "mood": "explain", "min_dur": 7.0, "narration": "……"},
    {"id": "reveal",  "mood": "reveal",  "min_dur": 6.6, "narration": "……"},
    {"id": "solve",   "mood": "win",     "min_dur": 8.0, "narration": "……"}
  ]
}
```

音色可换：女声 `zh-CN-XiaoxiaoNeural`（活泼）/ `zh-CN-XiaoyiNeural`（甜美），
男声 `zh-CN-YunxiNeural`（阳光）/ `zh-CN-YunjianNeural`（浑厚）/ `zh-CN-YunyangNeural`（播音）。

`voice` 里其它可用键（除 `name` 外都可省略）：
`engine`(`edge`|`silent`)、`rate`、`pitch`、`volume`、`retries`、`retry_delay`、
`proxy`(`auto`|`""`|`http://host:port`)、`cache`(true|false|目录)、`jobs`（并发合成数）。

### 第 2 步：环境引导（一次性）

```bash
cd dsh-shortvideo && ./install.sh          # 同步到 ~/.dsh/skills/shortvideo + 建 venv + 冒烟测试
./install.sh --no-deps                     # 只更新代码
DSH_SKILLS_DIR=/path ./install.sh          # 自定义 skill 根目录
```

install.sh 会：建 `$SKILL_DIR/.venv` 并装 `requirements.txt`、检查中文字体、跑一次 `--preview` 冒烟。
公司内网可用 `SHORTVIDEO_PIP_INDEX=<镜像>` 指定 pip 源。

**配音在企业网/代理环境**：`tts.py` 会自动把系统 CA（`/etc/ssl/certs/ca-certificates.crt` 等）
补进 edge-tts 的证书链，并把 `https_proxy/HTTPS_PROXY/http_proxy` 显式传给 aiohttp
（edge-tts 本身既不读代理也不认系统 CA，直连会卡住）。建议在 `content.json` 里把
`"proxy": "http://<代理>:<端口>"` 固定下来，避免 cron/无环境变量的场景退化成静音。

### 第 3 步：题材包（核心创作环节）

参照 `$TOPICS/` 里现成的题材（熄灯矩阵、排序可视化），写 `project/topic_<name>.py`：

```python
from shortvideo.runtime import SceneAPI   # 由 tools/shortvideo/runtime.py 提供

def build(ctx):
    """返回 {镜头id: 绘制函数}。绘制函数签名 scene(img, t, dur, ctx, S)。"""
```

`SceneAPI` 提供全部绘图基元（不需要自己写 Pillow）：

- `grid(lights, baseline=…, press=…)` 九宫格/矩阵网格（按压动画、发光、高亮）
- `matrix(cells, n, reveal=…)` 矩阵逐格浮现 + 行列高亮
- `formula(parts)` 公式拼接（手动上标支持）；`text/title/tag/arrow/ring`
- `caption(text)` 底部字幕卡（自动换行，make.py 每帧自动调用）
- `bar_chart(bars)` / 横向条形；`progress_bar(ratio)` 进度条

**数学/逻辑内核必须先算对再画**：题材包里先写纯计算函数（如矩阵求逆、排序步骤序列），
画面直接消费计算结果，杜绝讲解错误。

### 第 4~6 步：一条命令跑完

```bash
cd project
$PY $TOOLS/make.py --content content.json --topic topic_<name> --out out/<name>.mp4
```

`make.py` 自动完成：配音（mood prosody + 并发 + 缓存）→ 静音裁剪 → 时长对轴 →
逐帧渲染（多路并行分段编码）→ ffmpeg 合成 → 封面 PNG → 成片自检。

常用开关：

| 开关 | 作用 |
|------|------|
| `--preview` | 每镜头只出 2 张关键帧（秒级审片），先看版式再全量渲染 |
| `--jobs N` | 并行分段数（默认 auto=`min(4, 核数//2)`；`1`=单进程边渲边编） |
| `--preset` / `--crf` | x264 档位：`veryfast` 更快、`slow` 更小，默认 `medium`/`20` |
| `--clean` | 清空 `build/`（含 TTS 缓存），用于换音色/改台词后重跑 |
| `--no-check` | 跳过成片自检（默认会用 ffmpeg 校验流/时长/峰值电平） |
| `--fps` | 覆盖 content.json 的帧率（24 更省，30 更顺） |

## 质量检查单（交付前必过）

- [ ] `--preview` 关键帧无文字溢出、无重叠、无豆腐块（缺字形）
- [ ] 成片时长 = 各镜头配音+0.7s 之和（`make.py` 的 `[check] 时长` 行应无偏差警告）
- [ ] `[check] 流：视频=有 音频=有`，峰值 ≤ -0.2dB 不削波（自动打印）
- [ ] 数学/逻辑结论有代码验证（不是抄来的数字）
- [ ] 字幕与配音同源同序（都来自 content.json，天然一致）

## 已知边界

- 免费 edge-tts 端点不支持 SSML 情感标签（express-as），情感=音色自带表现力+mood prosody
- 企业代理会重置部分 WSS 连接：已做重试 + 两轮补采，仍失败则该镜降级静音（字幕照常）
- 分段并行编码会让文件略大（每段一个首帧关键帧，约 +30~50%）；要最小体积用 `--jobs 1`
- 无 BGM；需要时后期在剪映里垫低音量鼓点
- 复杂 3D/真实照片类题材不适合（程序化视觉的表达边界）
