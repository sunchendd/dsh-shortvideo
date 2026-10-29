---
name: ai-art-studio
description: 「AI 画室」：零模型依赖的程序化视觉资产工厂——生成底图/主视觉/片头卡/几何插画（aurora / mesh / rays / grid / paper 五种风格），供封面、视频片头、图文海报复用。
whenToUse: 用户说"做个底图/背景图/片头/主视觉/氛围图/封面背景/配色方案"，或需要比纯色渐变更有质感的视觉资产时。
---

# AI 画室（Art Studio）

这里**不用任何生成式模型**（无 Stable Diffusion / 无 API），全靠 Pillow 程序化合成：
秒级出图、可复现（seed 决定结果）、无版权风险 —— 这也是能跑在任何 CPU 机器上的原因。

```bash
SV=${SHORTVIDEO_DIR:-$HOME/.dsh/skills/shortvideo}
PY=$SV/.venv/bin/python
$PY $SV/tools/art.py --list                                   # 看风格与配色
$PY $SV/tools/art.py --style mesh --w 1080 --h 1440 --out out/bg.png
$PY $SV/tools/art.py --style rays --palette warm --seed 7 --out out/hero.png
```

## 一、风格选择表（按内容气质选，别按喜好选）

| 风格 | 观感 | 适合题材 | 不适合 |
|---|---|---|---|
| `mesh` | 深底 + 3 团柔光色盘，高级克制 | 数学/算法/硬核科普 | 儿童/生活类 |
| `aurora` | 极光带 + 强光晕，戏剧性 | 悬念、重磅结论、宇宙/物理 | 需要冷静的推导过程 |
| `rays` | 放射光束 + 底光，聚光感 | 封面、"揭秘"、对比冲突 | 长图文（光斑会抢字） |
| `grid` | 细网格 + 扫描线，科技感 | 计算机/工程/数据 | 人文类 |
| `paper` | 米白纸张 + 横线 + 页边红，手账感 | 学习笔记、清单、方法论 | 夜景/科技 |

配色（`--palette`）：`default`（青/粉/琥珀）、`cool`（蓝紫）、`warm`（橙红）、`paper`（浅底深字）。

## 二、四种标准产出

**1. 封面底图**（交给 cover.py 用，或直接当 3:4 底）
```bash
$PY $SV/tools/art.py --style mesh --w 1080 --h 1440 --palette default --seed 3 --out out/cover_bg.png
```

**2. 视频片头 0.8s 主视觉卡**（1080x1920 全屏，配一行大字）
```bash
$PY $SV/tools/art.py --style rays --w 1080 --h 1920 --seed 11 --out out/hero_9x16.png
# 再用 scenes 的 kinetic_text 叠标题（见 shortvideo-master 的配方库）
```

**3. 图文海报底**（配 oneimage.py 的 info 版式）
```bash
$PY $SV/tools/art.py --style paper --palette paper --w 1080 --h 1440 --out out/note_bg.png
```

**4. 系列视觉一致性**：固定 `--style` + `--palette`，只换 `--seed` → 同一账号五条视频的底图
看起来是"一个系列"（这是涨粉的关键，比每期换风格更重要）。

## 三、把底图接进视频（不用改代码）

在题材包或 content.json 的 `ctx` 里给图片路径，题材的 scene_fn 里：
```python
from PIL import Image
bg = Image.open(ctx["bg_path"]).convert("RGBA")   # prepare() 里加载一次
img.alpha_composite(bg)                            # 每帧贴底
```

## 四、质量判据（不达标就换 seed / 换风格）

- [ ] 文字放上去**对比度够**：深底字白度 ≥230，浅底字亮度 ≤60
- [ ] 没有大面积纯黑（OLED/缩略图会显得"空"）
- [ ] 光斑不在文字中心区（会糊字）
- [ ] 颗粒感适中（`_grain` 默认 0.05~0.12，太重像噪点故障）
- [ ] 同系列底图风格统一

## 五、别做的事

- 不要用彩虹渐变（廉价感最强的一种视觉）
- 不要把三种风格混在一张图里
- 不要在深底上再叠深色文字
- 不要为了"高级"把画面压到全黑（手机自动亮度下会看不清）
