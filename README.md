# dsh-shortvideo

DeepSeek Harness（dsh）技能插件：**零 GPU 短视频制作工作流**。
参考 suncut 的 agent 流水线思路，但去掉全部生成式视觉模型（无文生图/文生视频），
画面由 Pillow 程序化逐帧渲染，任何机器可跑。

## 安装（WSL 内）

```bash
# skill 本体
mkdir -p ~/.dsh/skills
cp -r dsh-shortvideo/skills/shortvideo ~/.dsh/skills/

# 运行依赖（每个 project 一次）
python3 -m venv project/.venv
project/.venv/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple \
    pillow edge-tts imageio-ffmpeg

# 中文字体（WSL 通常没有）
sudo apt-get install -y fonts-wqy-zenhei   # 或 fonts-noto-cjk
```

dsh web（http://127.0.0.1:3080/）的 skill 目录会热加载 `~/.dsh/skills/`，
无需重启。会话中说"用 shortvideo 做一条 XX 视频"即可触发。

## 用法

对 dsh 说：
> 用 shortvideo 技能，做一条"为什么 0.999… = 1"的竖屏讲解视频

agent 会按 SKILL.md 的六步手册执行：写分镜 → 建环境 → 写题材包 → 配音 →
渲染 → 交付。

## 直接跑（不经 agent）

```bash
cd project
.venv/bin/python ~/.dsh/skills/shortvideo/tools/make.py \
    --content content.json --topic topic_mything --out out/mything.mp4
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
├── tools/
│   ├── make.py               # 主流程 CLI
│   ├── shortvideo/           # 运行时引擎
│   │   ├── runtime.py        # 主题/缓动/背景/字体自适应
│   │   ├── scenes.py         # SceneAPI 绘图基元
│   │   └── tts.py            # edge-tts + mood 情绪 + 静音兜底
│   └── topics/               # 内置题材包（开发模板）
└── topics-content/           # 内置题材的 content.json
```

## 设计边界（与 suncut 对比）

| suncut | 本插件 | 原因 |
|--------|--------|------|
| 8 agent + 网页工作台 | 1 个 skill（SKILL.md 即流程手册） | 无 GPU，砍掉选角/服化道/导演/审片 |
| 文生图（Qwen-Image） | ❌ 不做 | 无算力 |
| 文生视频（Sol-H3 Spark） | ❌ 不做 | 无算力 |
| VLM 审片 | --preview 关键帧 + 亮像素统计自检 | 程序化画面可控性高 |
| edge-tts 配音 | ✅ 相同（含 mood 情绪 prosody） | 云端服务，无需 GPU |
| ffmpeg 合成 | ✅ 相同（imageio-ffmpeg 内置） | CPU 即可 |
