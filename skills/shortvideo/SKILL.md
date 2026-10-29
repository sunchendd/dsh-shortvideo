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
- BGM **按镜头情绪分段起伏**（默认开）：hook=high、rule/explain=low、reveal/win=mid，
  段间 0.35s 交叉淡化、全片统一淡入淡出。实测钩子段比正文段响 ~9dB，讲完先"收"再"推"。
  覆盖：`"bgm": {"dynamics": false}` 关掉；`"bgm": {"mood_map": {"reveal": "high"}}` 改映射
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

### 元素到场窗口：短镜头按绝对时间，不按比例

`deck._seq_at()` 默认让元素在镜头前 50% 内逐个到场。长镜头没问题，但 4.9 秒的短镜头里，
第二个元素要 2.5 秒才出现 —— 前 1 秒几乎是空画面，而短版总共只有 18 秒。

所以 `_default_span()` 对 <6 秒的镜头改成**绝对时间**（1.7 秒内全部到场）。
`qa.py` 同时加了两条开场检查，都用"人为前 2 秒黑屏"的视频反向验证过：

| 检查 | 判据 | 实测分布 |
|---|---|---|
| 开场空白 | 前 1.0s 内容区无内容 | 好片 0 触发 |
| 开场偏薄 | 前 0.5s 平均画面墨量 <1.8% | 好片 3%~8%，坏例子 0.6% |

另外 `stat` 的计数**从 60% 起跳**而不是从 0：从 0 数起时前 0.5 秒屏幕上是个"0"，
钩子镜头等于空着（实测开场墨量 0.59% → 3.30%）。

### 版式实测上限（bits / digits）

`bits`（一排格子）：
- `items` 是 **dict 数组** `{"label": "中", "on": 1}`，不是字符串数组
- 列数 `cols = per_line or (n if n≤7 else 6)`；`cell = min(150 或 spec.cell, (860-16(cols-1))/cols)`，
  标签字号 = `cell × 0.46`
- **真正的限制是标签宽度不是格子数**：n=6~12 时 cell=130、字 59px，1 个汉字安全，
  2 个汉字（120px）直接溢出格框；n≥18 仍是 6 列 3~4 行，照样干净
- 想让"分组"看得懂就设 `per_line`：一个汉字占 3 字节 → `per_line: 3`，每行正好一个字
- 项数挑 12（或 18/24）是整行；8~11 会出现"6+2 / 6+5"半行，难看

`digits`（逐位数字）：
- 字段是 `{"text", "highlight", "size", "cut", "per_line", "note"}`（不是数组）
- 可用宽 880px，`0` 宽 ≈ 0.6×size：size=160 时单行最多 9 字符（`"80 80 80"` 刚好排满），
  超过 ~14 字符折第二行，最多 3 行
- `highlight` 命中**所有**重复子串（写 `"80"` 会同时点亮三处）——要分组就用空格，
  要强调就挑不重复的片段

### 竖屏构图：别让内容缩成一条横带

9:16 的坑是"横排内容只占中间一条带"，上下各空出 1/4 画面（`pace.py` 会报"画面墨量偏薄"）。
现有对策：

- `timeline`：**≤4 个节点自动走竖版**（左侧竖轨 0.26H→0.74H），≥5 个节点才横排；
  想强制某一种写 `"layout": "v"` / `"layout": "h"`
- `flow`：盒子高度上限 300px、宽度 0.84W，3 个节点能铺满 0.30H→0.76H
- `compare`：两栏高度 0.40H、数值 118px，中间加竖分隔线
- `ghost`：给 still 很薄的镜头垫一个 0.42W 的背景水印字（`ghostfill.py` 可批量补，
  只复用旁白里字面存在的数字）

> 遇到"改了没效果 / 生效了但更糟"，先看 [docs/pitfalls.md](../../docs/pitfalls.md)：
> 按"坑的形式"分类（静默失效、方向反了、口径错了、规则不一致），比逐条试错快得多。

## 改稿的正确顺序（踩过坑）

改完 narration 之后**不能直接跑 tighten**——它读的是 `<build>/NN_shot.wav` 的实际时长，
而 wav 要等重新配音才更新。顺序错了会得到"调整 0 个镜头"的假结果：

```bash
# 1) 改 content.json 里的 narration
# 2) 先渲一遍，让配音重新生成（这一步才会写出新 wav）
$PY $TOOLS/make.py --content examples/01-x.content.json --topic deck --out examples/out/01-x/01-x.mp4
# 3) 再按新时长收紧
$PY $TOOLS/tighten.py --dir examples
# 4) 最后再渲一遍（min_dur 变了要重出片）
$PY $TOOLS/make.py --content examples/01-x.content.json --topic deck --out examples/out/01-x/01-x.mp4
```

> 注意：TTS 有缓存（按 text+voice 哈希），没改文案的镜头不会重新合成，
> 所以第 2 步通常只花几秒。

## 短版：`"format": "short"`

同一个选题可以出长短两版。长版（30~90s）做深度和收藏，短版（10~22s）搏完播和涨粉。
在 content.json 顶层写 `"format": "short"`，规则随之切换（`make.lint_script` 与
`score.py` 用的是同一套判定，改一处必须改两处）：

| | 长版 | 短版 |
|---|---|---|
| 总时长 | 30~90s | 10~22s |
| 单镜 | ≤9s | ≤6s |
| 字数 | 90~200 | 45~95 |
| 镜头数 | ≥4 | ≥4（短版也别少于 4 层） |

短版实测注意：
- **数字最占音节**。「26 个码字里，17 个是校验」这种句子念出来就是 5.7 音节/秒，
  逐镜 `mood_tuning` 调不动（实测只差 0.1s），要把 `voice.rate` 整体放慢 -15%
- 用 `tighten.py --pad 1.0 --floor 3.2 --cap 6.0 --only short-` 收紧，
  别用长版那套（pad 1.8 / cap 8.6 会把短版又撑回 20s+）
- 短版仍然要 ≥4 个镜头和互动引导，否则 `score.py` 会扣分
- 短版的**留白下限是 0.7s**（长版 1.0s）：4 个镜头要在 18 秒里说完，多留就是浪费
- 两处上限（`make.lint_script` 与 `score.SHORT_RANGE`）必须同值，曾出现 20 vs 22
  不一致导致"score 说合格、lint 说超标"

短版一镜 ≈ 12 个字。写超了就一定是 5.5+ 音节/秒，靠调 rate 是救不回来的
（实测 -12% 只多 0.2s），只能删字。

## 发布齐套：视频 + 封面 + 一张图 + 标题

第 9 道门查"齐套"，第 10 道门查"图文事实链"（`graphicaudit.py`）。实测第一次跑时 **24 条只有 2 条齐套**——
视频封面都在，但 21 条没 `summary.png`、3 条短版连 oneimage spec 都没有。
这种缺失不影响成片质量，但**发不出去**（小红书图文笔记要用一张图）。

```bash
# 补一张图（缺 spec 时先写 spec）
python3 tools/oneimage.py --spec examples/oneimage-<slug>.json \
    --out examples/out/<slug>/summary.png
```

## 图文事实链：`graphicaudit.py`

一张图里**合法地**包含推导值（SPF50 的 96.7%/98%、600cm²、0.3 克），
这些是把视频里的原始数字算出来的，不该要求旁白念一遍。
但**标题和高亮是这条图文对外的主张力**——那里出现视频从没提过的数字，
就是两条事实链对不上。

```bash
graphicaudit.py --dir examples        # 全库，退出码 = 有无标题层不一致
graphicaudit.py --spec examples/oneimage-19-spf-sunscreen.json
```

判据：标题/高亮/anchor 的数字必须在任一口播稿里出现；要点正文的数字只在
"整段一个数都没念过"时弱提示。实测 24 条里抓到 1 条
（通勤时薪的封面写「除以 176」，而旁白只说「22 天 × 8 小时」），已改成口播里有的「36 块」。
`checklist.py` 第 10 道门。

## 选题评分：`brief.py`（选题分 / 交付分，别混成一个数）

```bash
brief.py --dir examples                                   # 全库分布
brief.py --content examples/19-spf-sunscreen.content.json  # 单条
brief.py --title "彩虹其实是圆的"                           # 还没做的选题先估一下
```

- **选题分**（反常识 + 痛点，/10）：标题有没有数字对比/反转；命中几类痛点
- **交付分**（可验证 + 可视化，/10）：有没有 `kernel`+`refs`；每镜有没有 `deck_hint`

混成一个 20 分制会得出荒谬结论：好选题因为没写 kernel 被判"不该做"。
实测全库 **选题分中位 7.5、交付分中位 1.5**——选题不差，是文档没落盘。

## 补文档的护栏：`pydoc_guard.py`

`kernel` / `refs` / `deck_hint` 是纯文档，加它们**不该改变任何一帧**。
给一批老片子补文档时用指纹护栏证明这一点：

```bash
pydoc_guard.py --snapshot --out /tmp/before.json    # 改之前
pydoc_guard.py --compare /tmp/before.json           # 改之后：成片指纹必须 0 变动
```

指纹只算会影响成片的字段（shots 去掉 deck_hint / deck / voice / cover / format…），
所以"动了 narration"会被立刻抓出来。

## 封面体检：`coveraudit.py`（按 240px 缩略图量）

信息流里封面只有约 240px 宽，很多"看着很满"的封面到流里就糊了。
所以这个工具**先把封面缩到 240 宽再量**，四项指标：缩略图对比度 / 标题字号 /
安全边距 / 标题带密度。

```bash
coveraudit.py --dir examples/out --glob 'cover_short.png'
coveraudit.py --file examples/out/19-spf-sunscreen/cover_f1.png
```

**校准数据（25 张实测，别再拍脑袋）**：字号 110~190px、缩略图 RMS 33~56、
边距 45~47、密度 1.6%~11.2%。阈值取 `字号≥96 / RMS≥26 / 边距≥36 / 密度 1.2%~18%`。

**两个量错的坑**：

1. **量的是墨迹高度，不是字号**。CJK 字形墨迹只占 em 框约 78%，86px 墨迹 ≈ 112px 字号。
   第一版阈值按 90px 墨迹设，把 8 张 112px 的封面判成"字小"——全是假警报。
   现在报的是换算后的字号（`墨迹 / 0.78`）。
2. **对比度只看标题带会漏**。副标题/标签这些"小字"在最容易糊，但它们不在标题带里；
   而单独量小字带又不具备区分度（实测好封面 27.8、差的 39.5）。

**长版也要出大字封面**：实测同一条片子（08 冰面摩擦），short 版式综合分 **103.5** vs f1 的 **80.8**
（字号 159 vs 131、缩略图对比 53.0 vs 40.4）。信息流是 240px 缩略图，**字大就是硬道理**。
所以 `build_covers` 现在对长版也生成 `cover_short.png`（带「N 秒速通」徽标），
`plan.py` 与 `coveraudit.py` 都优先用它。全库 27 张发布封面中位数：字号 118→**159**、
缩略图对比 39.0→**51.9**。

**选哪张不是靠眼睛**：`coveraudit.py --pick --dir examples/out` 按综合分给每条片子排序，
输出推荐版式与其它候选分数。

**顺带发现的排版杠杆**：封面字号是被**行长**撑小的，不是被行数。`_fit` 只会缩字，
所以一条 12 字的长标题会被缩到 84px，而"拆成两行"能拿到 112px。
`_variant_f1` 现在**拆行优先于缩字**（`size < 100` 且不超过 4 行时尝试拆行）。

## 排期：`plan.py` 把片库排成发布日历

```bash
python3 tools/plan.py --dir examples --per-week 3 --out docs/publish-plan.md
python3 tools/plan.py --dir examples --start 2026-11-01 --json
```

`plan.py --stats` 先看结构（学科分布 / 长短配比 / 薄弱学科 / 单一学科是否超 25%），
确认多样性之后再排期。

排序不是随便排，四条硬规则：

1. **同题不连发**：按学科轮转取件，相邻两条尽量不同学科
2. **长短配对**：同一选题的长版和短版**必须在同一周**（互相导流，否则自相竞争）
3. **强弱交替**：短的、钩子强的先发（新号前几条决定推不推）
4. **首尾周不满就并入相邻周**，避免"发一周停一周"

同题识别靠 `TOPIC_KEYS`（二维码/咖啡因/视觉盲点/微波/近视…），新选题要加一行。

## 工具约定与口径（不看会踩的"隐性规则"）

### 1. 成片路径是硬约定

```bash
--out examples/out/<slug>/<slug>.mp4     # ✅ 唯一正确写法
```

`tighten.py` / `pace.py` / `qa.py` 都只认 `out/<slug>/build/NN_<shot>.wav` 与
`out/<slug>/<slug>.mp4`。写成 `out/<slug>.mp4` 时 tighten 读不到 wav，
**静默跳过**（只显示"调整 0 个镜头"），时长永远收不紧。现在它会打一行提示。

### 2. `tighten.py --pad` 不是统一余量

每个 kind 有固定余量：`bars/timeline 2.1`、`flow/bits 2.0`、`compare 1.9`、
`stat/formula/digits 1.6`；只有 `big/cards/list/steps` 才吃 `--pad`。
另外 `--floor` 会兜住短钩子（默认 4.5，短版用 3.2），变化 <0.3s 不写盘。

### 3. 三套语速口径，单位不同

| 工具 | 单位 | 钩子阈值 | 正文阈值 |
|---|---|---|---|
| `lint_script` | 音节/秒 | 6.0 / 6.6（错） | 5.2 / 5.8（错） |
| `score.py` | 字 ÷ (dur−0.7) | 6.2 | 5.4 |
| `pace.py` | 音节/秒 | 6.6 / 7.2 | 5.2 / 5.8 |

**以 lint 的"音节/秒"为准**写文案，另两套是复核。

### 4. 留白与 cap 夹出"单镜配音上限"

长版：`dur − adur ≥ 1.0s`，且 `min_dur` 上限 8.6 ⇒ **单镜配音最长约 7.6 秒**
（≈38 字）。短版：留白 0.7s、cap 6.0 ⇒ 单镜配音约 5.3 秒。

### 5. 时长阈值多处不同，取交集

`lint`/`score` 放行 30~90s，playbook 甜区 32~46s，`score` 平均镜长 4~9.5s。
**能同时满足的只有 32~46 秒且单镜 <8.6 秒**，按这个写。

### 5.5 两道容易漏跑的门（物料与图文）

| 门 | 查什么 | 漏跑后果 |
|---|---|---|
| 齐套（第 9） | 视频 + 封面 + 一张图 + 标题 | 成片全绿但**发不出去**（实测 24 条只有 2 条齐） |
| 图文（第 10） | 一张图**标题层**数字是否在口播里出现过 | 图里写「除以 176」，视频只说「22 天×8 小时」，两条事实链对不上 |

```bash
python3 tools/graphicaudit.py --dir examples     # 退出码 1 = 有标题层不一致
```

要点正文里的推导值（96.7%、600cm²）**不算违规**，只有标题/高亮/anchor 这一层要求念过。

### 6. 阿拉伯数字会顶爆语速判据，改中文数字更稳

同一句「度数就是100除以焦距的米数」13 音节：

| 写法 | 实测 | 音节/秒 |
|---|---|---|
| `100` | 1.90s | 6.85（lint 判 [x]） |
| `一百` | 3.14s | 4.15（通过） |

**不用改一处参数，只把数字写成中文，语速就降 40%**。这是比调 rate 更稳的降密度手段，
且字数不变。反过来，画面要显示阿拉伯数字时，旁白可写中文、deck 里写数字，两者不冲突。

### 7. 短版总长 10~22s，平均镜头 2.6~6.0s（第 38 轮改）

旧规则"平均镜头 ≥4s"曾逼出"4 镜必须 ≥16s"，而 4×12 字只有 11~12.5s 配音，
只能靠镜头空转凑时长——**留白比快切更伤完播**。现在 `score.py` 对短版按
2.6~6.0s 判平均镜头，`tighten` 对短版留白压到长版的 45%。
9 条短版实测由 13.1~20.4s 收到 13.1~17.8s，每条省 2.5~3.6s 空转，分数仍是 100。

### 8. 其它

- `"format"` 不写 = 长版；只认 `"short" / "15s" / "quick"`（写 `"long"` 也是长版）
- 数字一致性**只查钩子镜**：其余镜画面上的数字没念出来不会告警，自己核对
- 封面宽度：空格与半角字符都算 **0.5 字宽**（「低音少 34 分贝」= 7.0 超限，
  要写「低音少34分贝」= 6.0）；tags 只取前 3 个；末行 ≤2 字会自动并上一行
- 配音降级成静音时该 wav **没有 `.words.json`**，且必须 `--clean` 重跑，
  否则 tighten 会按静音时长写坏 `min_dur`

## ⚠️ 情绪表只在 shot 写了 mood 时生效（现在会自动推断）

`make.py` 原来用 `shot.get("mood")`，**不写 mood 就是 None** → 韵律恒为扁平 `+2%`，
于是 preset 的五情绪表、`mood_tuning`、`VOICE_MOODS` **全部静默失效**。
早期 4 条短版就是这样：听起来"没感情"，且 `mood_tuning` 怎么写都没反应。

现在 `mood` 缺省时按镜头 id **自动推断**：

| shot id | 自动 mood |
|---|---|
| hook | hook（快、上扬） |
| rule | rule（稳） |
| insight / explain | explain（慢，讲道理） |
| reveal | reveal（加强） |
| solve / win | win（收尾饱满） |

要扁平化就显式写 `"mood": "flat"` 或 `voice.mood_auto: false`。

**写 mood_tuning 的前提**：确认该镜有 mood（自动推断也算），否则 tuning 不生效。

### 配音缓存是共享的：`.audiocache/`

`make.py` 默认把配音缓存放到 skill 根目录的 `.audiocache/`（可用 `voice.cache_dir` 覆盖）。
原因：edge-tts 是**外部依赖**，实测会因代理 407 / 连接被重置整条降级成静音，
而放在 `build/tts_cache/` 里的缓存一 `--clean` 就没了。

实测：网络断掉 + `--clean` 重渲 → 仍能用共享缓存产出真实配音（3.02s/2.60s/4.44s/4.66s）。
同一句话在不同片子里也只合成一次。

### 检查情绪到底有没有生效：`voiceaudit.py`

```bash
python3 tools/voiceaudit.py --dir examples          # 全库：扁平/白写
python3 tools/voiceaudit.py --content examples/x.content.json   # 单条逐镜明细
python3 tools/voiceaudit.py --matrix                # 三音色 × 五情绪对照表
```

输出会标出：哪几镜是"推断"来的情绪、`mood_tuning` 里哪些 key 白写、整条是否扁平。
`checklist.py` 已把它接成第 8 道门（严格模式下白写直接 NO-GO）。

### 情绪的实测强度（同一句话，yunxi）

| 情绪 | 语速 | 时长 | 过零率（亮度代理） |
|---|---|---|---|
| hook | +16% | 3.95s | 988 |
| win | +13% | 4.05s | 938 |
| rule | +7% | 4.25s | 900 |
| reveal | +5% | 4.34s | 862 |
| explain | -1% | 4.58s | 800 |

时长跨度 1.16×，亮度跨度 1.23×。**早先音高只给 ±5~9Hz（≈3%），实测亮度跨度仅 1.18×、
基本听不出差别**；现在拉到 ±6~15Hz 才有"在讲"的感觉。

## 语速的三个开关（按优先级）

| 写法 | 作用 | 实测（同一句话、同一音色） |
|---|---|---|
| `voice.mood_tuning.rule.rate = "-15%"` | **直接替换**该情绪的语速（需该镜有 mood） | 4.38s → 5.49s |
| `voice.rate = "-15%"` | **全局偏移**，叠加到每个情绪上 | 4.38s → 5.08s |
| 删字 | 最有效，且不拉长总时长 | 见各条 |

⚠️ 历史坑：`voice.rate` 曾经**完全无效**——被 preset 的情绪表（yunxi 的 rule 恒 +7%）整个覆盖，
写 -15% 和写 +0% 出来音频一模一样（4.38s vs 4.38s）。现在改成叠加（-15% + rule +7% = -8%），
才真正生效。如果你在旧版本里"调了 rate 没反应"，就是这个原因。

## 节奏：`tighten.py` + `viz.py`

镜头时长别手工写死。`min_dur` 越往后面越长（钩子 6.4s、收尾 9.0s）是最常见的节奏病，
画成心电图一眼可见：

```bash
$PY $TOOLS/viz.py --dir examples          # → examples/rhythm.png 节奏心电图
$PY $TOOLS/tighten.py --dir examples      # min_dur = clamp(实配音 + 1.8s, 4.5, 9.0)
$PY $TOOLS/tighten.py --dir examples --dry-run   # 先看要改哪些
```

`tighten.py` 只动 `min_dur`（不动文案、不动配音），改完记得重渲。

## BGM 两种风格

```bash
# lofi（默认）：pad 铺底 + 稀疏琶音 + 1/3 拍底鼓，适合叙事/生活类
# [!] 别为"迁就某条片子"去改 bgm.gain。loudaudit 判的是**相对全库中位**的偏差，
#     你把一条从 -28 调到 -25，中位会跟着漂，下一条又要再调——追自己尾巴。
#     实测同一条片子 -28 被报"低 2.6dB"、-25 又被报"高 2.6dB"，两端都是中位。
#     正确做法：先用项目默认 -27（lofi）/ -26（pulse），跑 loudaudit 看偏差，
#     只在 |Δ| > 1.2dB 时微调 1~2dB，且一次只动一条，动完重跑。
# pulse      ：四拍底鼓 + 八分贝斯 + 十六分踩镲，适合机制/实验/科技类
$PY $TOOLS/bgm.py --dur 40 --styles lofi,pulse --out /tmp/bed.wav
$PY $TOOLS/viz.py --bgm /tmp/bed.lofi.wav /tmp/bed.pulse.wav --out /tmp/cmp.png
```

实测起音密度：lofi 1.9 次/秒、pulse 3.1 次/秒（pulse 明显更"推"，但别配慢节奏讲解）。
内容里用 `"bgm": {"style": "pulse"}` 指定。

## 成片画面体检：`qa.py`

```bash
$PY $TOOLS/pace.py --dir examples --order # 完播友好度 + 建议发布顺序
$PY $TOOLS/qa.py --video out/x.mp4        # 单条
$PY $TOOLS/qa.py --dir examples           # 批量扫 out/*/*.mp4
```

抽帧算四个指标，抓"人眼要一帧帧看才能发现"的问题：
`画面墨量`（内容区像素占比，太低=空镜）、`越界`（72px 安全区内的高亮像素，太高=内容贴边）、
`亮度`（死黑帧）、`字幕`（连续 ≥1.5s 字幕带空 = 漏字幕）。
`studio.py --render` 会自动跑一遍并把结果写进台账。

阈值是**双向校准**过的：拿一条故意做成两镜全空的片子验证能抓（"内容区连续 8.0s 无内容"），
再跑 9 条正常片验证不误报。调阈值前请照这个流程重做一次。

## 封面一致性体检：`coveraudit.py`

```bash
$PY $TOOLS/coveraudit.py --dir examples    # 字段检查 + 拼一张 covers-f1.png
```

单条封面好看 ≠ 像同一个账号。这个工具横向比对栏目名、主标题行数、标签数、行动号召，
并拼出一张 3 列封面墙（`examples/covers-f1.png`），一眼看出系列感有没有崩。

## A/B 变体：`variants.py`

小红书的分发由「封面 + 标题 + 前 3 秒」决定进不进下一个流量池，正文通常不变——
所以做 A/B 要**控制变量**：只换钩子和封面，正文原样。

```bash
# 1) 写变体清单（例：examples/variants-01-monty-hall.json，三种钩子风格）
$PY $TOOLS/variants.py --spec examples/variants-01-monty-hall.json --out examples/variants
# 2) 出片（正文共用，只有开场/封面不同）
$PY $TOOLS/studio.py --dir examples/variants --render
# 3) 发布后回填 CTR，看哪种钩子在你这儿真的好使
$PY $TOOLS/studio.py --dir examples --ab-record "slug=01-monty-hall-vnum,cover=f1,impressions=..,ctr=.."
```

钩子风格建议至少覆盖三类：**数字冲击**、**提问式**、**代价恐惧**（三种都能进池子，但人群不同）。

## 响度一致性：`loudaudit.py`

单条检查只能保证"不爆不哑"，但连播时观众感觉到的是**条与条之间的落差**。

```bash
$PY $TOOLS/loudaudit.py --dir examples        # 横向比对，偏差超阈值报警
$PY $TOOLS/loudaudit.py --dir examples --json
```

四个指标：峰值 / 人声（门限 RMS）/ BGM（停顿段 p90）/ 动态范围。
16 条实测人声 -20.8~-21.8dB（±0.6dB 内），BGM -40.9~-42.8dB。

两个量法上的坑（都踩过）：
- **人声要用"相对峰值的 -20dB 门限 RMS"**（和 tts 归一化同口径）。用绝对门限会把
  停顿比例混进来，同一批音频能算出 ±2.7dB 的假差异
- **BGM 要用停顿段的 90 分位**，不能用均值：`pulse` 是密集打击乐，间隔里均值很低、
  脉冲很高，用均值会误判成"BGM 小了 12dB"

## 发布前总检：`checklist.py`

```bash
$PY $TOOLS/checklist.py --dir examples                  # 全部条目 go / no-go
$PY $TOOLS/checklist.py --dir examples --publish-order   # 附投放顺序（风险低的先发）
$PY $TOOLS/checklist.py --dir examples --strict          # 警告也算 NO-GO
$PY $TOOLS/checklist.py --dir examples --json            # 机器可读，接 CI
```

四道门禁：清单完成度（score）→ 脚本体检（lint）→ 画面体检（qa）→ 节奏风险（pace）。
任一 error 级问题即 NO-GO，退出码非 0，可直接串进 CI 或 pre-commit。

## 发布可行性：`publishcheck.py`（**内容好 ≠ 发得出去**）

```bash
$PY $TOOLS/publishcheck.py --dir examples
$PY $TOOLS/publishcheck.py --content examples/19-spf-sunscreen.content.json
```

前面 12 道门禁验的是"内容好不好"，这一道验"文件会不会被平台卡住"：

| 检查项 | 上限 | 依据 |
|---|---|---|
| 标题 | **≤20 字** | 超了平台直接截断 |
| 正文 | ≤1000 字 | — |
| 单文件 | ≤1GB | — |
| 时长 | ≤15min | — |
| 编码 | h264 + aac | 平台推荐 |
| 画面 | 竖屏 9:16 或 3:4 | 否则加黑边 |
| AI 标注 | `disclosure` 必有 | 平台合规要求 |
| 导流红线 | 微信号/链接/扫码加… | 出现即限流 |

第一次跑就抓出两个真问题：**21 条没有 `disclosure`（AI 标注）**、
**2 条标题 21 字超限**（SPF 那两条）。全部补齐后 31/31 可直接发。

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
