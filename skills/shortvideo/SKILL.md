---
name: shortvideo
description: 零 GPU 竖屏短视频制作工作流：从任意知识题材生成"钩子-规则-原理-高潮-实战"五段式讲解视频（1080x1920, H.264+AAC, 微软神经配音, 中文字幕）。纯程序化视觉（Pillow 逐帧渲染），无文生图、无文生视频。
whenToUse: 用户想制作知识讲解/数学/科普类竖屏短视频，或要求"生成短视频/做一条视频/短视频工作流"且本机无 GPU 时。已知范例：九宫格熄灯（矩阵求逆）、排序算法可视化、单位换算推导。
---

# shortvideo —— 零 GPU 短视频工作流

把"烧脑知识"做成 60 秒竖屏讲解视频。整条流水线只依赖 **Python + Pillow + edge-tts + ffmpeg**，
不碰任何生成式视觉模型（无文生图/文生视频），因此任何机器都能跑。

## 工作流总览（六步）

```
①选题脚本 → ②环境引导 → ③题材包 → ④配音对轴 → ⑤逐帧渲染 → ⑥合成交付
```

每一步的职责与产物：

| 步骤 | 做什么 | 产物 |
|------|--------|------|
| ①选题脚本 | 按"五段式"写分镜台词（见下） | `project/content.json` |
| ②环境引导 | 建 venv，装 pillow/edge-tts/imageio-ffmpeg | `project/.venv/` |
| ③题材包 | 写一个 Python 镜头绘制模块 + 数学内核 | `project/topic_<name>.py` |
| ④配音对轴 | edge-tts 逐镜合成（按 mood 调语速音调）→ 裁静音 → 镜头时长自动对齐 | `build/*.wav` |
| ⑤逐帧渲染 | Pillow 画 1080x1920 帧（约 20fps 渲染速度） | rawvideo → ffmpeg |
| ⑥合成交付 | H.264+AAC 合成 + 封面 | `out/<name>.mp4` + `cover.png` |

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

### 第 0 步：定位本 skill 的工具目录

```
SKILL_DIR = 本 SKILL.md 所在目录
```

工具都在 `$SKILL_DIR/tools/`，题材范例在 `$SKILL_DIR/topics/`。

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

音色可换（样张试听后改 `voice.name`）：女声 `zh-CN-XiaoxiaoNeural`（活泼）/ `zh-CN-XiaoyiNeural`（甜美），
男声 `zh-CN-YunxiNeural`（阳光）/ `zh-CN-YunjianNeural`（浑厚）/ `zh-CN-YunyangNeural`（播音）。

### 第 2 步：环境引导（一次性）

```bash
python3 -m venv project/.venv
project/.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pillow edge-tts imageio-ffmpeg
```

- 公司内网代理：先 `export https_proxy=http://172.16.67.168:8080`（若在 VPN 环境）；
  直连镜像可达则不需要。
- ffmpeg 由 `imageio-ffmpeg` wheel 内置，无需系统安装。
- 字体：用系统自带 CJK 字体（`fc-list :lang=zh` 找一个，优先 Noto Sans CJK / WenQuanYi；
  都没有则 `apt-get install -y fonts-noto-cjk`）。

### 第 3 步：题材包（核心创作环节）

参照 `$SKILL_DIR/topics/` 里现成的题材（熄灯矩阵、排序可视化等），写 `project/topic_<name>.py`：

```python
from shortvideo.runtime import SceneAPI   # 由 tools/runtime.py 提供

def build(ctx):
    """返回 {镜头id: 绘制函数}。绘制函数签名 scene(img, t, dur, ctx)。"""
```

`SceneAPI` 提供全部绘图基元（不需要自己写 Pillow）：

- `grid(lights, press=…)` 九宫格/矩阵网格（按压动画、发光、高亮）
- `matrix(cells, reveal=…)` 矩阵逐格浮现 + 行列高亮
- `formula(parts)` 公式拼接（手动上标支持）
- `caption(text)` 底部字幕卡（自动换行）
- `title(kicker, title)` 页眉；`tag(text)` 胶囊标签；`arrow(p0, p1)` 箭头
- `bar_chart(bars)` / `number_flow(…)` 等数据图形

**数学/逻辑内核必须先算对再画**：题材包里先写纯计算函数（如矩阵求逆、排序步骤序列），
画面直接消费计算结果，杜绝讲解错误。

### 第 4~6 步：一条命令跑完

```bash
cd project
../.venv/bin/python $SKILL_DIR/tools/make.py \
    --content content.json --topic topic_<name> --out out/<name>.mp4
```

`make.py` 自动完成：配音（含 mood prosody + 断线重试）→ 静音裁剪 → 时长对轴 →
字幕按标点切句 → 逐帧渲染（打印进度与 ETA）→ ffmpeg 合成 → 封面 PNG。

审片模式（每镜头只出 2 张关键帧，几秒完成）：

```bash
... --preview   # 产物在 build/preview/*.png，用图像查看确认版式后再全量渲染
```

## 质量检查单（交付前必过）

- [ ] `--preview` 关键帧无文字溢出、无重叠、无豆腐块（缺字形）
- [ ] 成片时长 = 各镜头配音+0.7s 之和（对轴正确）
- [ ] ffmpeg 校验：`ffprobe` 有视频+音频流；`volumedetect` max ≥ -3dB 不削波
- [ ] 数学/逻辑结论有代码验证（不是抄来的数字）
- [ ] 字幕与配音同源同序（都来自 content.json，天然一致）

## 已知边界

- 免费 edge-tts 端点不支持 SSML 情感标签（express-as），情感=音色自带表现力+mood prosody
- 渲染速度约 15-20fps（1080x1920）；60s 视频 ≈ 3 分钟出片
- 无 BGM；需要时后期在剪映里垫低音量鼓点
- 复杂 3D/真实照片类题材不适合（程序化视觉的表达边界）
