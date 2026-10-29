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

## 角色分工（多 skill 协作）

本技能只管"把稿子变成片子"。上游创意与下游图文由同仓库的 4 个角色技能负责：

| 技能 | 角色 | 产出 |
|---|---|---|
| `xhs-creative-director` | 创意总监 | 选题打分、角度、钩子、`content.json`（含 cover/deck）、标题 5 条 |
| `shortvideo-master` | 短视频大师（导演/后期） | 分镜节奏、视觉配方、动效、审片清单、性能预算 |
| `one-image-summary` | 一张图总结大师 | 1080x1440 信息图（`tools/oneimage.py`） |
| `ai-art-studio` | AI 画室 | 程序化底图/主视觉（`tools/art.py`，5 风格 4 配色） |

推荐流水线：创意总监出稿 → `make.py --preview --strict` 体检+审片 → 短视频大师调画面 →
全量出片 → 一张图总结出配套图文 → 三版封面拿去做 A/B。

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

### 第 2.5 步：先跑脚本体检（30 秒，省一轮返工）

```bash
$PY $TOOLS/make.py --content content.json --topic deck --out out/x.mp4 --preview --strict
```

`[lint]` 会按小红书爆款阈值逐条判：钩子首句 ≤13 字、每镜配音**音节/秒**
（正文 4.0~5.2，>5.8 error；钩子放到 6.0~6.6）、单镜 ≤38 字、镜头留白 ≥1s、
总长 30~90s、标题 10~22 字、封面每行 ≤6 字宽、红线词（绝对化/医疗/带联系方式）、
结尾互动引导，以及**钩子镜画面数字 vs 口播数字**是否自相矛盾（中文数字会先归一成数值，
"零点一" 等于 "0.1"，不会假报警）。
依据与出处见 `docs/xhs-playbook.md`。

### 第 3 步：选题材包 —— 先想清楚"要不要写代码"

**80% 的科普题材用内置 `deck` 就够了**：在 content.json 顶层写 `deck`，不写一行 Python：

```json
"deck": {
  "hook":    {"kind": "big", "lines": ["0.1 + 0.2", "≠ 0.3"], "sub": "电脑算错了？"},
  "rule":    {"kind": "digits", "text": "0.1 = 0.0001100110011…", "highlight": "0011"},
  "insight": {"kind": "cards", "items": [{"t": "52 位尾数", "d": "再多装不下"}]},
  "reveal":  {"kind": "formula", "parts": ["0.1 + 0.2", "=", "0.30000000000000004"]},
  "solve":   {"kind": "steps", "items": [{"t": "别用等号比浮点"}]}
}
```

| kind | 画什么 | 适合 |
|---|---|---|
| `big` | 1~3 行大字错峰砸入（描边+阴影） | 钩子、结论 |
| `cards` | 1~4 张卡片，序号+标题+说明依次点亮 | 规则、要点、清单 |
| `bars` | 横向条形对比（数值右对齐成列、峰值强调） | 数据、倍数 |
| `compare` | 左右两栏大数值 + VS | 反常识、翻倍 |
| `formula` | 公式块，自动缩字号/自动断行 | 定理、等式 |
| `steps` | 竖排序号步骤，当前步高亮 | 流程、实战 |
| `list` | 逐条出现的清单 | 三条建议 |
| `bits` | 格子阵（0/1、开/关） | 二进制、开关 |
| `digits` | 数字串逐字排，可高亮重复段/截断点 | 浮点、进制、哈希 |
| `stat` | 巨型数字滚动计数 + 单位 + 说明 | 倍数、增长率、反差数据 |
| `timeline` | 横向时间轴，节点依次点亮 | 过程、历史、因果链 |
| `flow` | 竖向因果链 A→B→C，逐级点亮 | 讲机制、讲推理 |

要更"专属"的视觉（真实数据结构动画、几何推导）再写题材包，参照 `$TOPICS/` 里的
`sorting.py`（真实跑排序记录 trace）和 `lightsout.py`（矩阵求逆）。

### 第 3b 步：题材包（需要专属视觉时才写）

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
| `--strict` | 脚本体检有 error 就中止（CI/批量生成建议开） |
| `--no-lint` | 跳过脚本体检 |
| `--no-cover` | 不生成封面图 |

产出物（默认全出）：`out/x.mp4` 成片、`cover_f1/f3/f6.png` 三版 3:4 爆款封面
（平台支持封面 A/B，最多 4 张，10 小时后按点击率选优）、`cover_9x16.png`
（首帧/主页用）、`cover_frame.png`（F3 用的净帧截图）。

### 配音音色（3 款人格化音色）

```json
"voice": {"preset": "xiaoxiao"}     // 或 yunxi / yunjian；也可只写 name
```

| preset | 音色 | 人格 | 推荐题材 |
|---|---|---|---|
| `xiaoxiao` | zh-CN-XiaoxiaoNeural | 活泼亲切女主（248Hz） | 科普讲解、生活好奇心 |
| `yunxi` | zh-CN-YunxiNeural | 阳光邻家男声（175Hz） | 反常识、冷知识 |
| `yunjian` | zh-CN-YunjianNeural | 浑厚沉稳男声（119Hz） | 悬念揭秘、重磅结论 |

每个音色有独立的 5 情绪韵律表（hook/rule/explain/reveal/win 的 rate/pitch/volume 各不相同，
例如浑厚男声不会用 +15% 语速），并做了响度对齐（-20dBFS 语音电平，避免五段连播电平跳变）。
逐镜微调：`"mood_tuning": {"hook": {"rate": "+15%"}}`。试听：`$PY $TOOLS/voice_lab.py --preset all`。

### 片头签名（默认开）

封面上的栏目名会自动做成**顶部栏目条**：从左滑入 → 停留 1 秒 → 滑出（共 1.7s），
**不遮挡钩子画面**。同一个名字出现在封面和片头，就是账号的视觉签名。

```json
"intro": {"title": "反常识概率", "sub": "", "dur": 1.7, "style": "bar"}  // 默认，取 cover.kicker
"intro": {"style": "takeover"}        // 全屏版：压暗→扫线→砸字→淡出，会挡住钩子前 0.8s
"intro": false                        // 关掉
```

> 默认为什么不是全屏：前 3 秒是完播率的生死线，全屏片头会白吃掉 1/4 的黄金时间。

### 封面 A/B 台账（发布后回填，越攒越准）

```bash
# 发布后记一行（ctr 是百分比数字，impressions 用来算互动率）
$PY $TOOLS/studio.py --dir examples --ab-record "slug=01-x,cover=f1,impressions=12000,ctr=9.1,likes=310,saves=120,comments=40,follows=26"
# 看汇总：哪种版式在这个账号上真的更吃香
$PY $TOOLS/studio.py --dir examples --ab
```

数据落在 `<dir>/ab-log.csv`（slug,cover,posted_at,impressions,ctr,likes,saves,comments,follows,note）。
样本 <3 时脚本会明说"结论不稳"，别拿一条数据下判断。

### 背景乐（默认开）与合规标识

- 成片自动垫一层**程序化 BGM**（`tools/bgm.py`，A 小调 Am–F–C–G、96BPM、pad+琶音+轻鼓点，
  纯标准库合成、无版权风险），并做人声侧链压低（实测压低 28~39%），默认 gain=-27dB
- `--no-bgm` 关掉；`--bgm-gain -32` 更轻；content.json 里 `"bgm": {"intensity": "high",
  "style": "pulse", "gain": -30, "duck": 0.6}` 可细调
- 画面常驻两件"小道具"：顶部分段进度点（让观众知道还剩几段）+ 右下 `AI 配音` 标识
  （合成人声需显著标识，平台合规）；`"chrome": false` / `"disclosure": false` 可关

字幕是**逐词高亮**的（卡拉OK 式）：用 edge-tts 的 WordBoundary 时间戳，跟着配音点亮当前词，
缓存命中时不重新合成也能复用（`<wav>.words.json`）。

## 出片前先算分：`score.py`

```bash
$PY $TOOLS/score.py --content content.json --video out/x.mp4
# 爆款要素完成度 94/100  [S]  + 六个维度的扣分原因
```

六维：钩子 20 / 节奏 20 / 信息密度 15 / 视觉 15 / 封面标题 15 / 互动合规 15。
它把本文件与 `docs/xhs-playbook.md` 的清单变成数字，改一版看一次分数即可知道有没有变好。
**注意口径**：满分只代表"完全符合 playbook 清单"，不等于必爆——真实分发还看选题时机与账号权重。

## 批量干活：`studio.py`

```bash
$PY $TOOLS/studio.py --dir examples                      # 只评分，秒级出报告
$PY $TOOLS/studio.py --dir examples --render             # 评分 + 出片 + 封面 + 信息图
$PY $TOOLS/studio.py --dir examples --render --from-score 90   # 只渲达标的
```

产物都在 `--dir` 下：
- `studio-report.md` / `.json`：生产台账（分数 / 时长 / 分辨率 / 体积 / 峰值 / 缺什么）
- `studio-index.html`：**封面挑选页**，三版 3:4 封面并排，点开大图挑一张去发（也能当 A/B 记录页）
- `out/<slug>/`：成片 + `cover_f1/f3/f6/9x16` + `cover_frame` + `<slug>-summary.png`

选题一多就走这条路：先把 N 个 content.json 写完 → `--dir` 评分排序 → 只渲 ≥90 的 → 用封面页挑图发。

### 语速为什么要按"音节"算

中文数字串读出来比看到的字少：`8000` 写 4 字符、读"八千"只有 2 音节；`33%` 读"百分之三十三"6 音节。
按字符算会把带数字的稿子误判成"语速过快"，所以体检统一用 `shortvideo/textutil.py` 的
`syllables()`（内含整数转中文读法，`int_to_cn` / `cn_to_num` 也一起提供，可正反换算）。

## 质量检查单（交付前必过）

- [ ] `[lint]` 全绿（或只留 `[!]` 且已确认可接受）
- [ ] `--preview` 关键帧无文字溢出、无重叠、无豆腐块（缺字形）
- [ ] 成片时长 = 各镜头配音+0.7s 之和（`make.py` 的 `[check] 时长` 行应无偏差警告）
- [ ] `[check] 流：视频=有 音频=有`，峰值 ≤ -0.2dB 不削波（自动打印）
- [ ] 数学/逻辑结论有代码验证（不是抄来的数字）
- [ ] 字幕与配音同源同序（都来自 content.json，天然一致）

## 已知边界

- 免费 edge-tts 端点不支持 SSML 情感标签（express-as），情感=音色自带表现力+mood prosody
- 企业代理会重置部分 WSS 连接：已做重试 + 两轮补采，仍失败则该镜降级为等长静音占位
  （字幕照常、片子照出）。**这种降级会让该镜冒出一条假的"音节/秒偏快"**——
  已加自动识别（全零静音检测），体检会改报"配音降级为静音占位：hook, rule（重跑一次通常就好）"
  并跳过这几镜的语速项，不要再当成文案问题去删字。
- 分段并行编码会让文件略大（每段一个首帧关键帧，约 +30~50%）；要最小体积用 `--jobs 1`
- 无 BGM；需要时后期在剪映里垫低音量鼓点
- 配音是合成语音：平台要求显著标识"AI 合成"，片尾/简介里要写明
- 复杂 3D/真实照片类题材不适合（程序化视觉的表达边界）
