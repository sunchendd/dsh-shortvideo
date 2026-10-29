# dsh-shortvideo

DeepSeek Harness（dsh）技能插件：**零 GPU 短视频制作工作流**。
参考 suncut 的 agent 流水线思路，但去掉全部生成式视觉模型（无文生图/文生视频），
画面由 Pillow 程序化逐帧渲染，任何机器可跑。

## 安装（一键）

```bash
git clone https://github.com/sunchendd/dsh-shortvideo.git
cd dsh-shortvideo
./install.sh                     # 同步到 ~/.dsh/skills/shortvideo + 建 venv + 冒烟测试
```

`install.sh` 做的事：

1. 把 `skills/shortvideo` 同步到 `${DSH_SKILLS_DIR:-~/.dsh/skills}/shortvideo`
2. 在 skill 目录里建 `.venv` 并安装 `requirements.txt`（Pillow / edge-tts / imageio-ffmpeg）
3. 检查中文字体（缺了给出 apt 命令）
4. 用 `--preview` 跑一条最小样例，几秒内确认整条链路可用

可选：`./install.sh --no-deps`（只更新代码）、`DSH_SKILLS_DIR=/path ./install.sh`（自定义位置）、
`SHORTVIDEO_PIP_INDEX=<镜像> ./install.sh`（内网 pip 源）。

dsh web（http://127.0.0.1:3080/）的 skill 目录会热加载 `~/.dsh/skills/`，无需重启。
会话中说"用 shortvideo 做一条 XX 视频"即可触发。

## 用法

对 dsh 说：
> 用 shortvideo 技能，做一条"为什么 0.999… = 1"的竖屏讲解视频

agent 会按 SKILL.md 的六步手册执行：写分镜 → 建环境 → 写题材包 → 配音 →
渲染 → 交付。

## 直接跑（不经 agent）

```bash
cd project
~/.dsh/skills/shortvideo/.venv/bin/python ~/.dsh/skills/shortvideo/tools/make.py \
    --content content.json --topic topic_mything --out out/mything.mp4
```

`make.py` 若发现当前解释器缺依赖，会自动 exec 到 skill 自带 venv，所以 `python3 make.py` 也行。

常用开关：`--preview`（秒级审片）、`--jobs N`（并行分段，默认 auto）、
`--preset veryfast`（更快）、`--clean`（清缓存重跑）、`--no-check`（跳过自检）。

## 小红书爆款能力（v2 起）

不只是"能出片"，而是按社区方法论（`docs/xhs-playbook.md`，283 行带 25 个来源）逐条落地：

| 能力 | 怎么用 | 依据 |
|---|---|---|
| **3 款人格化音色** | `"voice": {"preset": "xiaoxiao"\|"yunxi"\|"yunjian"}` | 每音色独立 5 情绪韵律表 + 响度对齐 -20dBFS |
| **逐词高亮字幕** | 自动（edge-tts WordBoundary 时间戳） | 跟着配音点亮当前词，剪映式卡拉OK |
| **不写代码出片** | content.json 里写 `deck`（9 种 kind） | `big/cards/bars/compare/formula/steps/list/bits/digits` |
| **封面三流派** | 出片自动产出 `cover_editorial/blueprint/cinematic.png`(3:4) + `cover_9x16.png` | A/B 比的是**设计语言**（编辑部/蓝图/电影感），不是新旧版式；平台支持 3:4 与竖版 |
| **脚本体检** | `make.py ... --strict` 打印 `[lint]` | 钩子≤13字、语速≤4.6字/秒、单镜≤38字、留白≥1s、红线词 |
| **信息图 / 底图** | `tools/oneimage.py` · `tools/art.py` | 一张图总结 / AI 画室 |
| **片头签名** | 封面栏目名自动做成 0.8s 片头（压暗→扫线→砸字→淡出） | 账号视觉签名，默认开 |
| **封面 A/B 台账** | `studio.py --ab-record ...` / `--ab` 汇总 | 回填真实 CTR，反推哪种封面适合这个号 |
| **程序化 BGM** | 默认自动垫，按镜头情绪分段起伏（钩子响、正文收、揭示推），`--no-bgm` 关闭 | 纯标准库合成，无版权风险 |
| **A/B 变体** | `variants.py` 只换钩子/封面出 N 条，正文共用 | 控制变量做钩子实验，配 `--ab-record` 回填 CTR |
| **爆款评分器** | `tools/score.py --content x.json --video out/x.mp4` | 六维 0~100 + 逐条扣分原因 |
| **分段进度点 / AI 标识** | 默认开启（`"chrome": false` 关） | 完播 + 平台合规（合成人声需标识） |
| **转场与动效** | `"transition": {"type":"fade|slide|slide_up|wipe","dur":0.24}` | 每条可单独 `transition_in` 覆盖 |

### 角色技能（多 agent 协作）

| 技能 | 角色 | 干什么 |
|---|---|---|
| `xhs-creative-director` | 创意总监 | 选题打分、角度、钩子、标题、`content.json` |
| `shortvideo-master` | 短视频大师 | 分镜节奏、视觉配方、动效、审片 |
| `one-image-summary` | 一张图总结大师 | 1080x1440 信息图 |
| `ai-art-studio` | AI 画室 | 程序化底图/主视觉（5 风格 4 配色） |

`./install.sh` 会把这 4 个角色技能一起装到 `~/.dsh/skills/`。

### 看看效果

`examples/` 里有 3 条按爆款阈值写的完整脚本（题材内核都跑数验证过）：

| 条目 | 题材 | 音色 | 成片 |
|---|---|---|---|
| `01-monty-hall` | 蒙提霍尔（换门翻倍，20 万次模拟 0.6666 vs 0.3334） | yunxi | `out/01-monty-hall.mp4` 36.8s |
| `02-rayleigh-blue` | 瑞利散射（(700/450)⁴ = 5.86 倍） | xiaoxiao | `out/02-rayleigh-blue.mp4` 36.0s |
| `03-float-point` | 浮点数（repr(0.1+0.2) 第 17 位） | yunjian | `out/03-float-point.mp4` 37.2s |

```bash
cd examples
$PY ../skills/shortvideo/tools/make.py --content 01-monty-hall.content.json --topic deck --out out/01.mp4
$PY ../skills/shortvideo/tools/oneimage.py --spec oneimage-01-monty-hall.json --out out/01.png
```

## 内置题材包（examples，也是开发模板）

| 题材 | content | topic 模块 | 讲什么 |
|------|---------|-----------|--------|
| 九宫格拆弹 | topics-content/lightsout.content.json | tools/topics/lightsout.py | 熄灯问题→GF(2) 矩阵求逆 |
| 排序对决 | topics-content/sorting.content.json | tools/topics/sorting.py | 冒泡 vs 选择，真实 trace 驱动柱状图 |

## 目录

```
skills/shortvideo/
├── SKILL.md                  # agent 执行手册（dsh 技能入口）
├── requirements.txt          # 运行依赖（含可选 numpy/certifi）
├── tools/
│   ├── make.py               # 主流程 CLI（配音→渲染→编码→自检）
│   ├── shortvideo/           # 运行时引擎
│   │   ├── runtime.py        # 主题/缓动/背景/字体自适应/发光
│   │   ├── scenes.py         # SceneAPI 绘图基元（带图层缓存）
│   │   └── tts.py            # edge-tts + 代理/CA + mood + 缓存 + 静音兜底
│   └── topics/               # 内置题材包（开发模板）
└── topics-content/           # 内置题材的 content.json
```

## 性能优化记录（相对初版）

同一台 24 核机器、同一条 35s 成片（1055 帧）实测：

| 环节 | 初版 | 现在 | 提升 |
|------|------|------|------|
| 纯渲染（sorting 题材） | 33.6 ms/帧（29.8 fps） | 5.96 ms/帧（167.7 fps） | 5.6x |
| 纯渲染（lightsout 题材） | 18.0 ms/帧（55.7 fps） | 7.76 ms/帧（128.8 fps） | 2.3x |
| 配音（5 镜，真网络） | 13.3 s（串行） | 3.3 s（并发；命中缓存 0 s） | ~4x |
| 音频拼接（60s 时间轴） | 49.6 ms | 3.4 ms | ~15x |
| **端到端出片** | **37.3 s（25.9 fps）** | **7.9 s（139.5 fps）** | **~4.7x** |

（同一台 24 核机器、同一套 `bench.py` 口径；机器负载不同会有 ±15% 波动。画面像素级等价：
把题材代码固定后，优化前后 `diff.py --tol 0.02` 最大通道均值差 **0.000**。）

关键改动：

- **绘图层**：`tag/caption/ring/arrow` 不再为每次调用分配整帧 1080x1920 RGBA 图层 + 全帧
  alpha_composite，改成按包围盒合成；文字走"覆盖率蒙版 + 纯色贴图"缓存（重复中文串几乎零成本）；
  `glow()` 的径向蒙版按半径缓存、强度用 256 级 LUT；新增 `background_rgba()` 复用 RGBA 背景。
  像素级等价（`diff.py --tol 0.02` 仍通过）。
- **编码层**：默认按段并行（每路自己渲染 + 自己编码成 mp4，最后 `concat -c copy` 拼接 + 混音），
  避开"跨进程搬 6.2MB/帧"的带宽陷阱（试过：多进程渲染 + 父进程拼帧只有 85 fps，不如单进程 152 fps）。
  负载按镜头实测单帧成本做 LPT 分配。
- **配音层**：修好了两个真问题——企业 MITM 网关下 edge-tts 只认 certifi 不认系统 CA、
  aiohttp 不读 `https_proxy`（本机直连必挂）；断网时按文档承诺降级静音而不是让流水线崩掉；
  多镜并发合成；wav 按内容哈希缓存，重渲 0 秒；`concat/_trim` 换成 `array('h')` 流式写入。
- **健壮性**：成片自动自检（视频/音频流、时长偏差、峰值电平），`ffmpeg` 早退有明确报错，
  `--clean/--keep-segments/--no-check` 等运维开关，缺依赖时自动切 venv。

## 设计边界（与 suncut 对比）

| suncut | 本插件 | 原因 |
|--------|--------|------|
| 8 agent + 网页工作台 | 1 个 skill（SKILL.md 即流程手册） | 无 GPU，砍掉选角/服化道/导演/审片 |
| 文生图（Qwen-Image） | ❌ 不做 | 无算力 |
| 文生视频（Sol-H3 Spark） | ❌ 不做 | 无算力 |
| VLM 审片 | `--preview` 关键帧 + ffmpeg 自检（流/时长/电平） | 程序化画面可控性高 |
| edge-tts 配音 | ✅ 相同（含 mood 情绪 prosody），+ 代理/CA 兼容与静音兜底 | 云端服务，无需 GPU |
| ffmpeg 合成 | ✅ 相同（imageio-ffmpeg 内置），+ 分段并行编码 | CPU 即可 |

## 片库预览台（本地 Web UI）

这套工具默认是 CLI，没有界面。要看**界面**就跑这个：

```bash
/root/shortvideo-lab/.venv/bin/python ui/preview.py --port 8099
# 打开 http://127.0.0.1:8099
```

纯标准库（无 Flask/无 npm），只监听 127.0.0.1。能做的事：

| 功能 | 说明 |
|---|---|
| 浏览 | 32 条片子的封面 / 成片 / 一张图，卡片按主题着色 |
| 筛选 | 按主题、长短版、关键词（标题/钩子/学科）过滤 |
| 详情 | 播放成片、看 5 版封面、看一张图、读脚本与算式 |
| 换主题 | 下拉选主题 → 改 `content.json` 并即时重渲封面 + 一张图（约 2 秒） |
| 重渲成片 | 后台任务 + 进度日志（命中 TTS 缓存时几秒） |
| 跑门禁 | 对单条跑 `verify.sh`，日志直接显示在抽屉里 |
| 深链 | `http://127.0.0.1:8099/#19-spf-sunscreen` 直接打开某一条 |
| **新建** | 点「＋ 新建」，填标题 + 5 句口播 + 主题/音色 → 自动出片（draft → 渲染 → 收紧 → 封面 → 一张图，约 2 分钟） |

注意区分两个同名概念：
- `ui/preview.py` —— **动态**预览台（就是这个）
- `tools/studio.py` —— **静态**封面 A/B 台账生成器（产出 `examples/studio-index.html`，用于挑封面上传）

