# 目标达成审计（24h 持续任务）

> 回答一个问题：**"小红书爆款科普短视频"这个目标，哪些做到了、拿什么证明？**
> 每条都给可复现的命令或可点开的文件，不写"已完成"这种空话。

审计时间：第 35 轮 · 全部数字来自本机实跑（2026-10-29 00:0x）
**诚实声明**：下面证明的是"达到可发布的工艺水位"，**不是"必然爆款"**。
爆款取决于选题、账号权重、平台分发，这些不在本工具的证明范围内。

---

## ① 支持 3 款流畅有感情的配音音色 — ✅

三个音色都在用，不是"支持了但没使用"：

| 音色 | Azure 神经音色 | 人格定位 | 片库用量 |
|---|---|---|---|
| `xiaoxiao` | zh-CN-XiaoxiaoNeural | 活泼亲切女主 | 6 条 |
| `yunxi` | zh-CN-YunxiNeural | 阳光邻家男声 | **14 条** |
| `yunjian` | zh-CN-YunjianNeural | 浑厚沉稳男声 | 7 条 |

**"有感情"落在哪**：每音色 × 5 情绪（hook/rule/explain/reveal/win），实测参数：

```
音色        hook         rule        explain       reveal       win
xiaoxiao  +14%/+11Hz  +4%/+1Hz   -3%/-14Hz   +3%/+8Hz   +12%/+13Hz
yunxi     +16%/+9Hz   +7%/+0Hz   -1%/-13Hz   +5%/+6Hz   +13%/+11Hz
yunjian    +8%/+4Hz   -2%/-3Hz   -8%/-10Hz   -2%/-2Hz    +6%/+6Hz
```

钩子加速、讲解放慢、收尾再加速 —— 不是全片一个调子。
情绪由 shot id 自动推断（`hook→hook`、`insight→explain`…），27 条里 **扁平 0 条**、
`mood_tuning` 白写 0 处。

```bash
python skills/shortvideo/tools/voiceaudit.py --matrix       # 三音色 × 五情绪
python skills/shortvideo/tools/voiceaudit.py --dir examples # 全库扁平/白写检查
```

## ② 检索社区优秀方法案例 — ✅

`docs/xhs-playbook.md`（283 行）：口播语速甜区（正文 4.0–5.2 音节/秒、钩子 6.0/6.6）、
单镜 ≤8.6s、全片 32–46s、封面 3:4 与信息流 240px 可读性、8 类钩子公式、避坑清单。

**不是抄来的，是落地成代码并回测过的**：`score.py` / `lint_script` / `pace.py` 的阈值
都来自这份 playbook，而 27 条片子的分数分布反过来验证了阈值（全 100 [S]，无一条靠放水）。

## ③ 检索 GitHub 优秀开源代码 — ✅

`docs/oss-landscape.md`（132 行）。实际采用：

- **edge-tts**（微软神经 TTS 协议）→ 3 音色 + 情绪韵律
- **Pillow** 逐帧渲染 → 22 种 deck kind，零 GPU
- **imageio-ffmpeg** → x264/AAC 封装
- **PyDSM 的 iso226 实现** → 借它的参数表反查 ISO 226:2003，复算了 17 号的 34 dB（第 34 轮）

最后一条是新增的：不是"看过"，是"拿它的参数算出了自己的数"。

## ④ 支持生成创意并实现视频制作 — ✅

**片库 27 条**（长版 22 / 短版 5），全部 `100/100 [S]`：

```bash
python skills/shortvideo/tools/checklist.py --dir examples    # 27/27 GO（11 道门）
python skills/shortvideo/tools/plan.py --dir examples --stats # 学科分布 / 长短配比
```

**创意 → 成片的完整链路**（`brief.py` 把"选题值不值得做"变成可复算的）：

| 环节 | 工具 | 阈值 / 实测 |
|---|---|---|
| 选题打分 | `brief.py` | 选题分 ≥6.5、交付分 ≥6.0（实测中位 8.0 / **10.0**） |
| 语速预算 | `budget.py` | 按实测算：hook 4.76 / explain 4.18 音节/秒（估时 P90 误差 14%） |
| 制作 | `make.py` | lint / check / 缓存 / 静音降级 |
| 发布物料 | `coveraudit.py` `graphicaudit.py` | 27/27 封面通过、27/27 图文数字一致 |

**27 条片子全部带 `kernel`（算式）与 `refs`（依据）** —— 知识类账号的可信度底线，
每条核心数字都能照着复算。

## ⑤ Agent 角色 + skill 安装 — ✅

已安装 5 个 skill 到 `~/.dsh/skills/`：

| skill | 角色 | 关键能力 |
|---|---|---|
| `xhs-creative-director` | 创意总监 | 选题四维打分、五段式、钩子、**交付契约**（deck_hint/kernel/refs） |
| `shortvideo-master` | 短视频大师 | 镜头配方、动效规范、审片流程、**收到交付物先做三件事** |
| `one-image-summary` | 一张图总结大师 | 1080×1440 图文笔记 |
| `ai-art-studio` | AI 画室 | 5 种程序化底图风格 |
| `shortvideo` | 流水线 | 23 个工具 |

**独立作业验证 7 次**（这是对角色 skill 的真实验收）：短版 ×3、长版 ×3、跨角色 ×1。
每次都能在不问我任何问题的前提下走完全流程并过 11 道门禁 ——
而且每次都会**暴露 skill 的缺口**，这些缺口反过来修进 skill 与工具（第 34 轮一次修了 6 条）。

---

## 工艺指标总表

| 指标 | 值 | 怎么验 |
|---|---|---|
| 片库 | 27 条（长 22 / 短 5） | `checklist.py --dir examples` |
| 分数 | 27/27 = 100.0 [S] | `score.py --content <file>` |
| 门禁 | **11 道**，27/27 GO | 同上 |
| 封面体检 | 27/27 通过（字号中位 118px、缩略图对比 39） | `coveraudit.py` |
| 图文事实链 | 27/27 标题层数字都在口播里 | `graphicaudit.py` |
| 响度一致 | 27/27 在人声中位 ±1.2dB | `loudaudit.py` |
| 画面告警 | 27/27 无告警 | `qa.py --dir examples` |
| 文档完整 | 27/27 有 kernel + refs + deck_hint | `brief.py --dir examples` |
| 学科覆盖 | 10 类，最长 7 条占 26% | `plan.py --stats` |
| 音色覆盖 | 3 种，扁平 0 条 | `voiceaudit.py --dir examples` |
| 排期 | 27 条 / 9 周，同题长短同周 | `plan.py --out docs/publish-plan.md` |

## 还差什么（诚实清单）

1. **没上过真实平台**：全部是工艺指标，完播率/涨粉率要真发出去才知道。
2. **21 号的"可听下限 20 Hz"**仍是经验值（17 号的 34 dB 已按 ISO 226:2003 复算成立）。
3. **薄弱学科**：航空/皮肤/视光各只有 1 条。
4. **素材单一**：纯程序化视觉，没有人像、没有实拍；适合知识类，不适合生活方式类。
5. **配音是 TTS**：情绪靠韵律参数模拟，真人语感、气口、笑声做不到。
6. **选题没有数据闭环**：没有"从评论区/搜索词反推选题"的能力。
