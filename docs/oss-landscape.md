# 开源生态对标：能给 `shortvideo` 借鉴什么

> 视角：本仓库是**零 GPU、纯 Python（Pillow + edge-tts + ffmpeg）**的竖屏讲解视频流水线。
> 因此评估维度不是"谁更火"，而是：**能否在不引入 GPU / Node / 重型依赖的前提下，改进我们的画面、字幕、配音与节奏**。
> star 数与许可证为 2026-10 抓取（shields.io API / 仓库 LICENSE 文件），会变化，以仓库为准。
> 所有仓库内容仅作技术数据使用。

---

## 1. 总览对比表

| # | 项目 | ★ | 许可证 | 语言 | 核心能力 | 与本项目的关系 | 集成成本 | 优先级 |
|---|---|---|---|---|---|---|---|---|
| 1 | [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | 136k | MIT | Python | 主题→脚本→素材→TTS→字幕→BGM→合成；9:16/16:9/1:1；批量任务 | 同赛道最完整的"工程化答案"，**读它的字幕/参数化设计** | 低（纯 Python，只读代码不引依赖） | **P0** |
| 2 | [rany2/edge-tts](https://github.com/rany2/edge-tts) | 12k | LGPLv3（`srt_composer.py` 为 MIT） | Python | 免费神经 TTS；`rate/volume/pitch`；WordBoundary 元数据；`--write-subtitles` 出 srt | **我们已在用**，但只用了"整段音频+时长"，没用词级时间戳 | 极低（已装） | **P0** |
| 3 | [m-bain/whisperX](https://github.com/m-bain/whisperX) | 25k | BSD-2-Clause | Python | faster-whisper 后端 + **wav2vec2 强制对齐**得词级时间戳；VAD 预处理降幻觉 | 词级对齐算法可借鉴到"字幕节奏"和"配音-字幕一致性自检" | 中（需模型下载/CPU 可跑小模型） | P1 |
| 4 | [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 26k | MIT | Python/C++ | CTranslate2 加速；`compute_type="int8"` CPU 可跑；`beam_size=5`；`BatchedInferencePipeline(batch_size=16)` | 做**成片字幕自检**（抽检 3 段转写，和 content.json 比对） | 中（~1.5GB 模型，CPU int8） | P1 |
| 5 | [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | 14k | HPND(MIT-CMU) | Python/C | 排版基元：`textbbox`/`font.getlength`/`stroke_width`/`rounded_rectangle`/滤镜 | **我们已用**；封面公式全靠它落地 | 已装 | **P0** |
| 6 | [ManimCommunity/manim](https://github.com/ManimCommunity/manim) | 42k | MIT | Python | 数学动画：`Transform`/`Create`/`Write`、`rate_func` 缓动、Tex 排版 | 借**缓动曲线与"逐元素入场"编排**，不引依赖 | 低（只移植算法） | P1 |
| 7 | [rhasspy/piper](https://github.com/rhasspy/piper) | 11k | MIT | Python/C++(ONNX) | 轻量本地 TTS，CPU 实时 | edge-tts 失败时现在是"静音降级"，可用 piper 做**离线有声兜底** | 中（ONNX 模型 + 中文 voice） | P1 |
| 8 | [openai/whisper](https://github.com/openai/whisper) | 111k | MIT | Python | ASR 参考实现 | 只作对照基线（比 faster-whisper 慢数倍） | 中 | P2 |
| 9 | [Zulko/moviepy](https://github.com/Zulko/moviepy) | 15k | MIT | Python | Python 侧剪辑/合成/文字动效 | 我们已有 rawvideo→ffmpeg 直通，**更快的路径已占优** | 低 | P2 |
| 10 | [remotion-dev/remotion](https://github.com/remotion-dev/remotion) | 68k | Remotion License（非 OSI，商用有门槛） | TS/React | 声明式"帧=函数"渲染；`@remotion/captions` 时间轴字幕模型 | 只借**时间轴字幕的数据模型**；换栈代价过高 | 高（Node 栈） | P2 |
| 11 | [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) | 64k | MIT | Python | 少样本音色克隆（约 1 分钟素材） | 品牌音色/IP 口播，但违背"零 GPU" | 高（GPU） | P2 |
| 12 | [2noise/ChatTTS](https://github.com/2noise/ChatTTS) | 40k | **AGPL-3.0** | Python | 中文对话式 TTS | AGPL 有传染性，**不引入产品**，仅可本地实验 | 高（GPU）+ 法律风险 | P2 |
| 13 | [FunAudioLLM/CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | 24k | Apache-2.0 | Python | 零样本克隆、流式合成 | 同上（可选未来能力） | 高（GPU） | P2 |

---

## 2. 逐个：我们能借鉴什么（具体到算法/参数/功能点）

### 1. MoneyPrinterTurbo ★136k MIT — **P0**
README 里明确的三件事，正好是我们缺的：
- **字幕双路策略**：`subtitle_provider = "edge"`（用 TTS 时间戳，快、无 GPU，默认）或 `"whisper"`（`faster-whisper` 转写，`large-v3-turbo` 约 1.6GB，更准）。
  → 借鉴点：**我们用 edge 生成、用 whisper 抽检**，形成闭环自检（见 #4）。
- **字幕样式参数化**：字体、位置、颜色、大小、描边、背景样式全部可调。
  → 借鉴点：把 `caption(y, size, hi_color, ...)` 提升为 `content.json` 的 `caption_style` 段（`{y, size, max_chars_per_line, stroke_width, bg_alpha, hi_color}`），让文档里的封面/字幕参数直接进配置。
- **批量清单**：`--batch-file` 支持 UTF-8 JSON 数组/JSONL，**≤100 条且 ≤1 MiB**，启动前统一预检、单条失败不阻塞。
  → 借鉴点：给 `make.py` 加 `--batch tasks.json`，投标/日更场景一次跑 N 条。
- 画幅：`9:16(1080×1920)` / `16:9(1920×1080)` / `1:1(1080×1080)` —— 与我们的 `size` 字段一致，说明 schema 不必改。

### 2. edge-tts ★12k LGPLv3 — **P0（最高性价比）**
- 现在 `tts.py` 的 `EdgeVoice._synth_async()` 只取音频与总时长 → **每镜一句字幕，整句同时出现**。
- edge-tts 的流式事件里有 **`WordBoundary`** 元数据（README 里进度条就是用 boundary 元数据算的，CLI 还能 `--write-subtitles x.srt`）。
  → 具体做法：把 `communicate.stream()` 收到的 `WordBoundary{offset, duration, text}` 收集成列表，写进 `build/NN_shot.words.json`；
  渲染时按 `t` 二分查找当前词，`caption()` 只高亮当前词（`hi_color=ACCENT, hi_scale=1.08`），并让字幕**逐词出现**而不是整句闪现。
  这同时解决了"字幕与配音 ±1 帧同步"和"逐词节奏感"两件事，**零新增依赖**。
- 参数区间（README + 本仓库 `tts.py` 的 `_LIMITS`）：`rate`/`volume` 百分比 ±50，`pitch` ±50Hz；负值需写成 `--rate=-50%`。我们的 mood 表（hook +12%、win +10% 等）都安全。
- 许可证提醒：LGPLv3 → 若将来要闭源分发本插件，**保持动态/独立进程调用**即可，别把 edge-tts 源码内联进闭源包。

### 3. WhisperX ★25k BSD-2 — P1
- 关键能力：**wav2vec2 强制对齐（forced alignment）得到词级时间戳**，VAD 预处理降幻觉，`faster-whisper` 后端在 `large-v2 + beam_size=5` 下 **<8GB 显存**。
- 借鉴点（不引依赖，只借思路）：
  1. 用 **VAD 的语音段边界**校准我们对轴的 `lead_in=0.15 / tail=0.55`：如果某镜 VAD 检出的首语音起点 > 0.35 s，说明该镜"开头空太久"，自动收紧。
  2. 词级时间戳是"字幕断行"的最佳依据：**在词边界断行**（而不是按字数硬切），可避免"矩阵/求逆"被拆到两行。我们有了 #2 的 `WordBoundary` 后可直接用，WhisperX 只在跨语种/改旁白时备用。

### 4. faster-whisper ★26k MIT — P1（自检用）
- 参数：`WhisperModel(size, device="cpu", compute_type="int8")`、`beam_size=5`、`BatchedInferencePipeline(batch_size=16)`（比逐条快 ~3.5×，README 基准表：59s→16s）。
- 借鉴点：给 `make.py` 加可选 `--verify-subtitles`：
  1. ffmpeg 抽 3 段音频（每段 5 s）→ `int8` CPU 转写 → 与 `content.json` 对应旁白做字符级相似度；
  2. 相似度 < 0.85 就告警"配音可能串镜/被截断"。
  这是**唯一能发现"静音降级"和"TTS 缓存串号"的自动化手段**（现有 `[check]` 只看流和时长）。
- 成本：CPU int8 + `small`/`base` 模型即可（≈75MB–145MB），不需要 `large-v3`。

### 5. Pillow ★14k HPND — **P0**（封面/字幕落地）
- 具体 API 清单（直接喂给 `cover.py`）：
  - 折行：`font.getlength(text)` 逐字累加，超过 `可用宽 = 1080 − 2*72` 就断行；**中文按字断行即可，但要避开标点行首**（`，。！？、`）。
  - 精确包围盒：`draw.textbbox((0,0), text, font=f, stroke_width=w, anchor="la")` → 垂直居中用 `anchor="mm"` 最省事。
  - 描边：`stroke_width=max(4, size//18)`, `stroke_fill="#000000"`。
  - 圆角贴纸：`draw.rounded_rectangle(xy, radius=28, fill=ACCENT)`。
  - 高亮块：先 `rectangle(fill="#FFD400")` 再画深色字，比"给字加底色"干净。
  - 阴影（可选）：把文字层 `filter(ImageFilter.GaussianBlur(8))` 后偏移 (0,6) 叠在底下，alpha 120。

### 6. Manim ★42k MIT — P1（动效质感）
- 它的动效哲学 = **每个元素有独立入场函数 + 明确的 rate function**（`smooth`、`ease_in_out_sine`、`linear`），而不是"整体线性变化"。
- 借鉴点（纯数学，零依赖）：
  - 把 `SceneAPI` 里的线性插值换成缓动：`ease_out_cubic(t) = 1-(1-t)**3`，用于"数字滚动""条形图推进""环形高亮"；
  - 入场错峰：同屏 N 个元素，第 i 个延迟 `i*0.08s`，入场时长 `0.35s`；
  - 结论帧"逐词书写"（类似 `Write`）：每 0.06 s 多显示 1 个字。
- 落地位置：`shortvideo/scenes.py` 里加 `_ease_out(t)` / `_stagger(i)`，改 5–8 处即可见效。

### 7. piper ★11k MIT — P1（离线兜底）
- 现状：`tts.py` 在 edge-tts 失败时走 `_silence()` → 成片"有字幕没声音"，对完播率是灾难（且平台可能判低质）。
- 借鉴点：装 `piper-tts` + 一个 `zh_CN` ONNX voice（CPU 实时），在 `retry_passes` 用尽后**先 piper 出声、再静音兜底**；音色不同可接受，因为这是降级路径。
- 成本：约 +50MB 模型，纯 CPU，`install.sh` 里加一条可选依赖。

### 8. openai/whisper ★111k MIT — P2
- 只作对照：同精度下比 faster-whisper 慢数倍（后者 README 有基准表）。**不要**为了"权威"选它。

### 9. moviepy ★15k MIT — P2
- 优点：Python 里能直接做文字动效/转场；缺点：逐帧合成走 Python，速度远低于我们现在的 `rawvideo → ffmpeg` 直通。
- 借鉴点：只在"需要复杂转场（叠化/推拉）"时参考它的 `crossfadein` 实现（本质是 alpha 混合 + 时间曲线），可以在我们渲染器里手写 3 行，不必装库。

### 10. remotion ★68k Remotion License — P2
- 许可证不是 OSI：对公司规模有商用限制，**评估后再碰**。
- 可借的只有数据模型：`Sequence(from, durationInFrames)` + `@remotion/captions` 的 `Caption{text, startMs, endMs, timestampMs, confidence}`。
  → 建议：我们的 `words.json` 就用这套字段命名（`{text, start_ms, end_ms, conf}`），将来接任何字幕工具都免转换。

### 11. GPT-SoVITS ★64k MIT — P2
- 能力：约 1 分钟素材做音色克隆（少样本），质量高。
- 借不动的部分：需要 GPU + 训练流程，违背"任何机器都能跑"的定位。除非做"品牌专属音色"付费版，否则不引入。

### 12. ChatTTS ★40k **AGPL-3.0** — P2（法律红线）
- AGPL 会传染到网络服务侧；本插件若被集成分发，风险大。**结论：不进产品**，最多本地实验对比音质。

### 13. CosyVoice ★24k Apache-2.0 — P2
- 许可证最友好（Apache-2.0），支持零样本克隆与流式；同样需要 GPU。列为"未来可选"。

---

## 3. 采纳路线图（按投入产出排序）

### P0 — 本周就能做，全部零新增依赖
1. **edge-tts `WordBoundary` → 逐词字幕**（#2）：`build/*.words.json` + `caption()` 逐词高亮。收益：字幕节奏感、同步精度、可做"卡拉OK"式强调。
2. **封面模块参数化 3:4**（#1/#5）：`cover.py` 出 `1080×1440` 三套变体（F1 数字反差 / F3 红圈 / F6 遮一半），参数见 `docs/xhs-playbook.md` §3。
3. **字数-时长预算校验**（#1 的"预检"思路 + 语速基准 4.2 字/秒）：`make.py` 新增 `[check]` 项，见 playbook §8。

### P1 — 下个迭代
4. **piper 离线兜底**（#7）：消灭"静音成片"。
5. **faster-whisper 字幕自检**（#4）：`--verify-subtitles`，抓串镜/截断。
6. **manim 式缓动 + 错峰入场**（#6）：`scenes.py` 加 `_ease_out` / `_stagger`。

### P2 — 观察
7. whisperX 词级对齐（跨语种/改稿场景）、moviepy 转场、remotion 数据模型兼容、克隆音色（GPT-SoVITS / CosyVoice）。

---

## 4. 一句话结论

对这条"零 GPU 流水线"来说，**最大的免费午餐在 edge-tts 的 WordBoundary**（已经装好、只是没用），
**最大的短板在封面尺寸（3:4 而非 9:16）和"静音降级"**（前者丢点击率，后者丢完播）；
重型生态（remotion / 克隆音色 / AGPL 的 ChatTTS）都不该进主线。
