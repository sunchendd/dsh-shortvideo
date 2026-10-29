---
name: ui-ux-pro-max
description: 设计情报库（离线）：192 套配色、74 组字体搭配、88 种视觉风格、119 条 UX 准则。当需要"选配色/选字体/查某种风格该怎么做/封面或海报该用什么色板"时用本地 CLI 检索，不要凭感觉拍。
license: MIT
metadata:
  upstream: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
  vendored_data: data/ scripts/ templates/（3.6MB，纯 CSV + Python，可离线跑）
---

# UI/UX 设计情报（本地检索）

> 这是一份**数据 + 检索器**，不是让模型"凭审美发挥"。
> 上游是 MIT 授权的 [ui-ux-pro-max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)，
> 本项目只取 `data/ scripts/ templates/`（去掉 web 框架相关部分），全部离线可用。

## 什么时候用

- 要给一个新品类定配色 → 先搜同类产品在用哪套色
- 想知道某个视觉风格（glassmorphism / brutalist / editorial…）的具体做法
- 需要字体搭配（中文字体本项目用 wqy-zenhei 兜底，但字号/字重/行距可查）
- 排版/可读性/交互的通用准则

## 用法

```bash
PY=/root/shortvideo-lab/.venv/bin/python
SK=skills/ui-ux-pro-max/scripts/search.py

$PY $SK "short video dark" --domain color -n 3          # 配色（含 hex + on-色，直接能用）
$PY $SK "science poster" --domain typography -n 3       # 字体搭配 + 情绪关键词
$PY $SK "editorial bold" --domain style -n 3            # 视觉风格 + CSS 关键词
$PY $SK "mobile vertical" --domain ux -n 5              # UX 准则/反模式
# 全部 domain（--list-domains 上游未实现，直接看这里）：
#   style color chart landing product ux typography icons gsap react web google-fonts
# 参数：--domain <d> -n/--max-results 1~20  --json  --full  --format ascii|markdown
```

常用 domain：`product / style / typography / color / landing / chart / ux / icons / motion`。

## 输出怎么用

`--domain color` 返回的是**一套完整语义色**（primary / on-primary / secondary / accent /
background / card / muted-foreground / border / destructive / ring），不是一堆散色。
拿到后**不要直接抄**，要做三件事：

1. 用 `(L1+0.05)/(L2+0.05)` 验对比度（本项目`docs/visual-system.md` §6 有公式）
2. 把 hex 转成项目 token（`runtime.py` 的 INK/BRAND/STATE/AMBER 层级），别新增散色
3. 在 **240px 缩略图**口径下复核可读性（信息流真实尺寸，缩放系数 0.222）

## 硬约束（本项目踩过的坑）

- **一个色只干一件事**：品牌青只标结构，琥珀只标数字，玫红只标状态。
  同一色兼任两种语义，层级一定塌。
- **不要"什么都想亮"**：前景亮度要拉出台阶（本项目的四层：白 / 琥珀 / 浅灰 / 青）。
- 中文封面不要用细体：笔画宽度 < 1px 在 240px 下会糊成一团。

## 数据来源与许可

`data/` 里是上游整理的 CSV（含 `data-provenance.json` 记录来源）。上游 LICENSE 见同目录 `LICENSE`（MIT）。
本项目未改动数据，只加了这份说明。
