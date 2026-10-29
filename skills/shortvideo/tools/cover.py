"""爆款封面生成（小红书视频封面只有 3:4 / 1:1 / 4:3，官方建议竖版 3:4）。

依据 docs/xhs-playbook.md §3 + docs/visual-system.md：
  - 画布 1080x1440（3:4），MARGIN=72，主标题 100~144px（刻度 h3/h2/h1），描边 max(4, size/18)，
    行距 1.14；底 INK.850→INK.950（与视频同一条中性阶），品牌青 #3DE8FF，
    高亮块琥珀 #FFC22E + 深字 #04060E，状态玫 #FF3D63（序号徽标/标注圈）
  - 颜色与字号一律取 runtime.token（INK/BRAND/STATE/AMBER_SCALE/TS/LEADING），本文件不写死
  - 平台支持封面 A/B（最多 4 张，10 小时后按点击率选优）→ 一次产出 3 个差异化版式：
      F1 数字反差大字报（结论狠）/ F3 红圈标注截图式（有画面主体）/ F6 遮一半（制造信息缺口）
  - 再额外出 1 张 9:16，供视频首帧/主页头图复用

用法：
  python cover.py --content content.json --out out/ [--frame out/cover_frame.png]
  from cover import build_covers; build_covers(cfg, out_dir, frame_path=...)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFilter

from shortvideo import runtime as R
from shortvideo import scenes as _scenes

# 3:4 封面基线（playbook §3.1）—— 颜色/字号全部取自 runtime 的 token
# （唯一出处 docs/visual-system.md；这里不许再出现字面色值）
W34, H34 = 1080, 1440
MARGIN = R.GUTTER                          # 72
TS = R.TS                                  # 排版刻度
LEAD = R.LEADING
SPACE = R.SPACE
SIZE_MAX, SIZE_MIN = TS["h1"], TS["h3"]    # 主标题 144 → 100（缩略图 32px → 22px）
SUB_SIZE = TS["lead"]                      # 副标 68（240px 缩略图 ≈ 15px）
# 副标色：中性浅灰（≈INK.250）。青色留给"结构"语义（栏目/时长/标签），
# 不压主标题——青的亮度 0.659 是标题白 0.886 的 0.74 倍，台阶太浅会抢层级。
LINE_SPACING = LEAD["title"]               # 1.14
_PROBE = ImageDraw.Draw(Image.new("RGB", (1, 1)))


def sync_theme():
    """把 runtime 当前主题的 token 吸进来。

    这些曾经是 import 期常量，换肤就换不动（值被冻在模块上）。
    现在由 `runtime.set_theme()` 之后调用一次即可。
    """
    global BG, FG, ACCENT, ACCENT2, GREY, STATE, DARK, SUB_INK, ON_BLOCK
    BG = R.INK["950"]              # 底：深色主题=近黑，浅色主题=近白
    FG = R.INK["0"]                # 主标题：深色主题=白，浅色主题=近黑
    ACCENT = R.BRAND["500"]        # 结构：栏目名/时长胶囊/序号环/标签
    ACCENT2 = R.AMBER_SCALE["500"]  # 高亮块：每张封面唯一一处强调色
    GREY = R.INK["300"]            # 标签/次级文字
    STATE = R.STATE["500"]         # 状态：序号徽标、F3 标注圈
    DARK = R.INK["950"]            # 亮底上的"反色字"
    ON_BLOCK = getattr(R, "ON_BLOCK", INK_950 if False else R.INK["950"])  # 压在高亮块上的字
    SUB_INK = getattr(R, "SUB_INK", (206, 216, 232))


sync_theme()


def _paste(img, layer, ox, oy):
    """把 RGBA 小图层混合到画布（RGB 画布 paste(mask)；RGBA 画布 alpha_composite）。

    RGBA 画布上用 ImageDraw 写半透明颜色不会混合 RGB，只写 alpha —— 会把半透明胶囊
    变成实心色块，所以一律画在小图层再合成。
    """
    ox, oy = int(round(ox)), int(round(oy))
    lw, lh = layer.size
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(img.size[0], ox + lw), min(img.size[1], oy + lh)
    if x1 <= x0 or y1 <= y0:
        return
    if (x0 - ox, y0 - oy, x1 - ox, y1 - oy) != (0, 0, lw, lh):
        layer = layer.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy))
    if img.mode == "RGBA":
        img.alpha_composite(layer, (x0, y0))
    else:
        img.paste(layer, (x0, y0), layer)


def _fit(text, max_w, hi=120, lo=SIZE_MIN, bold=True):
    """自适应字号：从 hi 往下压到 lo，返回 (size, font)。"""
    s = hi
    while s > lo:
        f = R.font(s, bold=bold)
        if _PROBE.textlength(text, font=f) <= max_w:
            return s, f
        s -= 2
    return lo, R.font(lo, bold=bold)


def _apply_theme(content):
    """封面用**它自己**的主题（浅色主题照用，不跟着视频回退），渲染前同步 token。"""
    th = content.get("theme") if isinstance(content, dict) else None
    R.set_theme(th if isinstance(th, str) else None)
    try:
        import art as _art
        _art.refresh_theme()
    except Exception:  # noqa: BLE001
        pass
    sync_theme()


def _cfg(content):
    c = dict(content.get("cover") or {})
    san = lambda x: R.sanitize(str(x), bold=True)  # noqa: E731
    headline = c.get("headline") or ([content.get("title")] if content.get("title") else [])
    if isinstance(headline, str):
        headline = [headline]
    headline = [san(x) for x in headline if x and str(x).strip()]
    if len(headline) > 1 and len(headline[-1]) <= 2:   # 末行 1~2 字头重脚轻 → 并到上一行
        headline = headline[:-2] + [headline[-2] + headline[-1]]
    highlight = c.get("highlight") or []
    if isinstance(highlight, str):
        highlight = [highlight]
    # 注意：`theme` 现在通常是**主题名字符串**（如 "porcelain"），旧代码里它是个
    # 可以带 accent/accent2 覆盖的 dict。两种都接住，别让字符串走进 .get()。
    theme = content.get("theme")
    theme = theme if isinstance(theme, dict) else {}
    accent = c.get("accent") or theme.get("accent") or ACCENT
    accent2 = c.get("accent2") or theme.get("accent2") or ACCENT2
    return {
        "kicker": san(c.get("kicker") or content.get("badge") or "硬核科普"),
        "headline": headline[:3] or ["硬核科普"],
        "highlight": [san(x) for x in highlight],
        "sub": san(c.get("sub") or content.get("subtitle") or ""),
        "tags": [san(x) for x in (c.get("tags") or [])[:3]],
        "cta": san(c.get("cta") or ""),
        "serial": c.get("serial") or "",
        "accent": tuple(accent),
        "accent2": tuple(accent2),
        "ring": c.get("ring"),          # [cx, cy, r] 红圈中心与半径（F3）
        "ring_label": san(c.get("ring_label") or ""),
        "f3_title": san(c.get("f3_title") or ""),
    }


# ---------------------------------------------------------------- 基础件

def _canvas(w, h, accent):
    """深底 + 强调色柔光 + 斜向细纹（缩略图里也有质感）。

    底色用与视频同一套（INK.850→INK.950 竖向渐变 + 60px 点阵），
    这样"视频里的一帧"和"同一条片子的封面"在信息流里是同一张脸，
    而不是两种设计（旧封面是纯色 #0E1116，跟视频底一比就露馅）。
    """
    grad = Image.linear_gradient("L").resize((w, h))
    img = Image.composite(Image.new("RGB", (w, h), BG),
                          Image.new("RGB", (w, h), R.BG_TOP), grad)
    d = ImageDraw.Draw(img)
    for y in range(0, h, 60):
        for x in range(0, w, 60):
            d.point((x, y), fill=R.INK["700"])
    glow = R._glow_mask(int(min(w, h) * 0.62))  # noqa: SLF001
    # 光斑压到品牌色的 62% 亮度：两件事同时成立——
    # ① 与视频底"深底 + 局部辉光"同一套做法；② 辉光不越过年检的"亮墨"阈值(L>140)，
    #    否则它会被算成标题墨迹，把安全边距从 72 判成 27（青色辉光 L≈183 就踩过这个坑）。
    # 光斑配色按**极性**分：深底是"把品牌色压暗"（62% 亮度），浅底必须反过来
    # "把品牌色往底里推"——否则浅色封面右上角会出现一块深蓝（钴蓝 × 0.62 = 深海军蓝）。
    if R.LIGHT:
        k = 0.82
        glow_rgb = tuple(int(a + (b - a) * k) for a, b in zip(accent, BG))
    else:
        glow_rgb = tuple(int(v * 0.62) for v in accent)
    sprite = Image.new("RGBA", glow.size, glow_rgb + (255,))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.paste(sprite, (int(w * 0.78 - glow.size[0] / 2), int(h * 0.06 - glow.size[1] / 2)), glow)
    layer = layer.filter(ImageFilter.GaussianBlur(int(min(w, h) * 0.02)))
    _paste(img, layer, 0, 0)
    d = ImageDraw.Draw(img, "RGBA")
    step = max(56, w // 15)
    for i in range(-h, w, step):
        d.line([(i, 0), (i + h // 2, h)], fill=(255, 255, 255, 8), width=1)
    return img


def _kicker(img, cx, y, text, accent, size=TS["label"]):
    f = R.font(size, bold=True)
    w = _PROBE.textlength(text, font=f) + 60
    pad = int(size * 0.66)                   # 胶囊内边距随字号走，不写死 32
    layer, ox, oy = _scenes._layer((cx - w / 2, y - pad, cx + w / 2, y + pad), pad=2)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((cx - w / 2 - ox, y - pad - oy, cx + w / 2 - ox, y + pad - oy),
                        radius=pad, fill=accent + (34,), outline=accent + (210,), width=2)
    d.text((cx - ox, y - oy), text, font=f, fill=accent + (255,), anchor="mm")
    _paste(img, layer, ox, oy)


def _tags(img, cx, y, tags, accent, size=TS["label"]):
    if not tags:
        return
    f = R.font(size, bold=True)
    pad, gap = int(size * 0.94), SPACE["sm"]
    widths = [_PROBE.textlength(t, font=f) + pad for t in tags]
    x = cx - (sum(widths) + gap * (len(tags) - 1)) / 2
    half = int(size * 0.62)
    for t, w in zip(tags, widths):
        layer, ox, oy = _scenes._layer((x, y - half, x + w, y + half), pad=2)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((x - ox, y - half - oy, x + w - ox, y + half - oy), radius=half,
                            fill=(255, 255, 255, 18), outline=(255, 255, 255, 64), width=1)
        d.text((x + w / 2 - ox, y - oy), t, font=f, fill=GREY + (255,), anchor="mm")
        _paste(img, layer, ox, oy)
        x += w + gap


def _serial(img, w, n, accent, r=None):
    """右上角序号徽标（状态玫底 + 深字）。

    三个曾经的真问题（都在实测里被抓到过）：
      ① 字号写死成 r，而序号是「短05」这种 3 个字符——直接撑出圆外；
      ② 黑字压在洋红圆上，缩略图里糊成一团，读不出来；
      ③ 白字压 #FF2D55 只有 3.65:1（<4.5）—— 现在改成深字压玫底 = 5.88:1。
    所以：底色只用 STATE（品牌青不做"状态"），字色固定 INK.950，字号按实测宽度缩。
    """
    r = r or int(w * 0.052)
    d = ImageDraw.Draw(img, "RGBA")
    cx, cy = w - MARGIN - r, MARGIN + r
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=STATE + (255,))
    txt = str(n)
    size = int(r * 1.05)
    box = r * 2 * 0.68                       # 留足内边距（字形比 advance 宽一点）
    while size > 9 and R.font(size, bold=True).getlength(txt) > box:
        size -= 2
    d.text((cx, cy), txt, font=R.font(size, bold=True), fill=ON_BLOCK + (255,), anchor="mm")


def _text_outline(img, xy, s, size, fill, stroke=None, anchor="mm", stroke_w=None, font=None):
    """带描边的文字（RGB 画布会正确混合 alpha）。"""
    f = font or R.font(size, bold=True)
    sw = max(4, size // 18) if stroke_w is None else stroke_w
    ImageDraw.Draw(img, "RGBA").text(
        xy, s, font=f, fill=tuple(fill) + (255,), anchor=anchor,
        stroke_width=sw, stroke_fill=(tuple(stroke) if stroke else (0, 0, 0)) + (255,))


def highlight_box(cy, size, x0, x1, pad_x=10):
    """高亮块的纵向范围：必须包住整个 CJK 字形。

    旧写法用 line_h*0.14 ~ line_h*0.86，块只有 ~0.72 行高，
    而中文字形几乎占满 em 框——结果字的上下被切掉，
    深色字落到深色底上直接看不见（实测「三成」只剩中间一横）。
    """
    half = size * 0.60
    return (x0, cy - half, x1, cy + half)


def _highlight_block(img, box, color):
    """关键词高亮色块（F6 用：色块 + 深色字）。"""
    layer, ox, oy = _scenes._layer(box, pad=1)
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy),
                        radius=int((box[3] - box[1]) * 0.22), fill=tuple(color) + (255,))
    _paste(img, layer, ox, oy)


# ---------------------------------------------------------------- 三个版式

def _split_point(text):
    """找一个适合断行的位置：优先标点后，其次中点附近。返回切分下标或 0。"""
    cands = [i + 1 for i, ch in enumerate(text)
             if ch in "，、：；。 " and 0 < i + 1 < len(text)]
    if not cands:
        mid = len(text) // 2
        if len(text) < 6:
            return 0
        return mid
    return min(cands, key=lambda i: abs(i - len(text) / 2))


def _variant_f1(c, w=W34, h=H34):
    """F1 数字反差大字报：主标题 1~3 行居中，副标一行强调色。"""
    accent, accent2 = c["accent"], c["accent2"]
    img = _canvas(w, h, accent)
    _kicker(img, w / 2, MARGIN + 34, c["kicker"], accent, size=TS["label"])
    if c["serial"]:
        _serial(img, w, c["serial"], accent)

    lines = c["headline"]
    max_w = w - MARGIN * 2
    hi = TS["h1"] if len(lines) <= 1 else (TS["h2"] if len(lines) == 2 else TS["h3"])
    size = min(_fit(ln, max_w, hi=hi, lo=SIZE_MIN)[0] for ln in lines)
    # 拆行优先于缩字：字被行长撑小时（实测 8 张封面掉到 86~88px，
    # 缩略图上明显偏小），把最长的一行拆成两行重新取字号，能大就大。
    if size < TS["h3"] and len(lines) < 4:
        worst = max(lines, key=lambda ln: R.font(size, bold=True).getlength(ln))
        pivot = _split_point(worst)
        if pivot:
            i = lines.index(worst)
            trial = lines[:i] + [worst[:pivot], worst[pivot:]] + lines[i + 1:]
            t_hi = TS["h2"] if len(trial) == 2 else TS["h3"]
            t_size = min(_fit(ln, max_w, hi=t_hi, lo=SIZE_MIN)[0] for ln in trial)
            if t_size > size + 4:
                lines, size = trial, t_size
    f = R.font(size, bold=True)
    line_h = int(size * LINE_SPACING)
    block_h = line_h * len(lines)
    # 垂直位置：把主标题块居中在"栏目名以下、标签以上"的可用区里。
    # 旧写法按 h*0.245 / h*0.44 两档拍，2 行标题会在下半页留 500px 空底——
    # 缩略图里那一片纯黑就是"这图没做完"的观感。
    y = int(R.clamp(
        ((MARGIN + 150) + (h - MARGIN - 140)) / 2 - block_h / 2,
        MARGIN + 120, max(MARGIN + 120, h - MARGIN - 200 - block_h)))
    for ln in lines:
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        tw = f.getlength(ln)
        x0 = (w - tw) / 2
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            _highlight_block(img, highlight_box(
                y + line_h / 2, size,
                max(MARGIN - 4, x0 + pre - 10),
                min(w - MARGIN + 4, x0 + pre + hw + 10)), accent2)
            d = ImageDraw.Draw(img, "RGBA")
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x0, y + line_h / 2), before, font=f, fill=FG + (255,), anchor="lm")
            d.text((x0 + pre, y + line_h / 2), hit, font=f, fill=ON_BLOCK + (255,), anchor="lm")
            if after:
                d.text((x0 + pre + hw, y + line_h / 2), after, font=f,
                       fill=FG + (255,), anchor="lm")
        else:
            _text_outline(img, (w / 2, y + line_h / 2), ln, size, FG, anchor="mm")
        y += line_h

    if c["sub"]:
        sub_size, fsub = _fit(c["sub"], w - MARGIN * 2, hi=SUB_SIZE, lo=TS["body"])
        # 副标用中性浅灰而不是品牌青：青的亮度 0.659 是标题白的 0.74 倍，
        # 台阶太浅——副标会和主标题抢层级（这正是设计系统自己诊断过的"无亮度台阶"）。
        # 改后封面四层分明：白=主信息 / 琥珀=数字 / 浅灰=解释 / 青=结构（栏目·时长·标签）。
        _text_outline(img, (w / 2, y + int(h * 0.035)), c["sub"], sub_size, SUB_INK,
                      anchor="mm", stroke_w=0, font=fsub)
    _tags(img, w / 2, h - MARGIN - 58, c["tags"], accent, size=TS["label"])
    if c["cta"]:
        _text_outline(img, (w / 2, h - MARGIN + 4), c["cta"], TS["label"], FG,
                      anchor="mm", stroke_w=0)
    return img


def _variant_short(c, w=W34, h=H34):
    """短版封面：字数更少、字号更大、右上角带时长徽标。

    短版靠"一眼看懂"抢点击，3:4 里能放的字比长版更少，
    所以标题最多 2 行、副标一行、其余全部让位给字号。
    """
    accent, accent2 = c["accent"], c["accent2"]
    img = _canvas(w, h, accent)
    secs = c.get("seconds")
    _kicker(img, w / 2, MARGIN + 34, c["kicker"], accent, size=TS["label"])
    if c["serial"]:                 # 系列序号保留：短版也要能"认出是同一个号"
        _serial(img, w, c["serial"], accent)
    # 时长徽标放在栏目名下方左侧：短版的卖点就是"几十秒看完"，
    # 但不能顶掉右上角的系列序号（那是账号识别）
    if secs:
        d = ImageDraw.Draw(img, "RGBA")
        f = R.font(TS["label"], bold=True)
        label = f"{secs:g} 秒速通"
        tw = f.getlength(label)
        bh = int(TS["label"] * 1.55)
        bx0, by0 = MARGIN, MARGIN + 84
        d.rounded_rectangle([bx0, by0, bx0 + tw + 52, by0 + bh], radius=bh // 2,
                            fill=accent + (40,), outline=accent + (190,), width=3)
        d.text((bx0 + 26, by0 + bh // 2), label, font=f, fill=accent + (255,), anchor="lm")

    # 曾经写死 [:2]，把第 3 行**静默丢掉**：19/18/02/22 等 9 条片子的封面
    # 变成「五十倍防晒 / 只多挡」——核心数字整行消失，读不通。
    # 根因是 short 版式原本只给短版用，后来长版也用它（大字版缩略图对比高 25%），
    # 于是任何 3 行标题的长版都中招。现在 3 行也画，基准字号相应下调。
    lines = c["headline"][:3]
    c["_headline_drawn"] = len(lines)      # 供 lint 比对"配置了几行 vs 画了几行"
    max_w = w - MARGIN * 2
    hi = TS["display"] if len(lines) <= 2 else TS["mega"] - 40
    size = min(_fit(ln, max_w, hi=hi, lo=TS["h3"])[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * LINE_SPACING)
    block_h = line_h * len(lines)
    y = int(h * 0.46 - block_h / 2)
    for ln in lines:
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        tw = f.getlength(ln)
        x0 = (w - tw) / 2
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            _highlight_block(img, highlight_box(
                y + line_h / 2, size,
                max(MARGIN - 6, x0 + pre - 14),
                min(w - MARGIN + 6, x0 + pre + hw + 14)), accent2)
            d = ImageDraw.Draw(img, "RGBA")
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x0, y + line_h / 2), before, font=f, fill=FG + (255,), anchor="lm")
            d.text((x0 + pre, y + line_h / 2), hit, font=f, fill=ON_BLOCK + (255,), anchor="lm")
            if after:
                d.text((x0 + pre + hw, y + line_h / 2), after, font=f,
                       fill=FG + (255,), anchor="lm")
        else:
            _text_outline(img, (w / 2, y + line_h / 2), ln, size, FG, anchor="mm")
        y += line_h

    if c["sub"]:
        sub_size, fsub = _fit(c["sub"], w - MARGIN * 2, hi=SUB_SIZE, lo=TS["body"])
        _text_outline(img, (w / 2, y + int(h * 0.045)), c["sub"], sub_size, SUB_INK,
                      anchor="mm", stroke_w=0, font=fsub)
    _tags(img, w / 2, h - MARGIN - 58, c["tags"], accent, size=TS["label"])
    if c["cta"]:
        _text_outline(img, (w / 2, h - MARGIN + 4), c["cta"], TS["label"], FG,
                      anchor="mm", stroke_w=0)
    return img


def _variant_f3(c, frame_path=None, w=W34, h=H34):
    """F3 红圈标注（截图式）：hook 镜头画面当底 + 红圈 + 标签。"""
    accent = c["accent"]
    if frame_path and os.path.exists(frame_path):
        src = Image.open(frame_path).convert("RGB")
        sw, sh = src.size
        need = w / h
        if sw / sh > need:                       # 太宽：左右裁
            nw = int(sh * need)
            src = src.crop(((sw - nw) // 2, 0, (sw + nw) // 2, sh))
        elif sh / sw > need:                     # 太高：按上部 25% 对齐裁
            nh = int(sw / need)
            top = max(0, int((sh - nh) * 0.25))
            src = src.crop((0, top, sw, top + nh))
        img = src.resize((w, h), Image.LANCZOS)
        # 顶部压暗，保证标题可读
        shade = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        band = int(h * 0.18)
        for i in range(band):
            sd.line([(0, i), (w, i)], fill=(8, 10, 16, int(150 * (1 - i / band) ** 1.4)))
        _paste(img, shade, 0, 0)
    else:
        img = _canvas(w, h, accent)

    d = ImageDraw.Draw(img, "RGBA")
    # 截图式封面：画面自己会说话。只有显式给了 f3_title 才叠标题，
    # 否则双标题会互相打架（视频里已经有自己的大字）。
    title = c.get("f3_title") or ""
    if title:
        size, f = _fit(title, w - MARGIN * 2, hi=TS["h2"], lo=SIZE_MIN)
        d.text((MARGIN, MARGIN + 6), title, font=f, fill=FG + (255,), anchor="la",
               stroke_width=max(4, size // 18), stroke_fill=(0, 0, 0, 255))
    _kicker(img, MARGIN + 170, MARGIN + 42, c["kicker"], accent, size=TS["label"])

    # 标注圈：默认落在画面中部偏右的"差异点"（可用 cover.ring 覆盖）
    # 圈用 STATE（状态玫）而不是品牌青——视频里"被指出来/被点亮"就是这个色，
    # 圈标签改深字压玫底：白字压 #FF2D55 只有 3.65:1，深字有 5.88:1。
    cx, cy, r = c["ring"] or (int(w * 0.62), int(h * 0.52), 170)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=STATE + (255,), width=10)
    if c["ring_label"]:
        lab = c["ring_label"][:6]
        f2 = R.font(TS["lead"], bold=True)
        tw = d.textlength(lab, font=f2)
        bh = int(TS["lead"] * 1.5)
        bx, by = cx + r * 0.5, cy + r * 0.15
        d.rounded_rectangle((bx, by, bx + tw + 44, by + bh), radius=16, fill=STATE + (255,))
        d.text((bx + 22, by + bh // 2), lab, font=f2, fill=ON_BLOCK + (255,), anchor="lm")

    if c["sub"]:
        _text_outline(img, (w / 2, h - MARGIN - 104), c["sub"], TS["body"], FG,
                      anchor="mm", stroke_w=0)
    _tags(img, w / 2, h - MARGIN - 24, c["tags"], accent, size=TS["label"])
    return img


def _variant_f6(c, w=W34, h=H34):
    """F6 知识性好奇（遮一半）：关键字高亮黄块 + 其余 70% 白，制造"信息在封面外"。"""
    accent, accent2 = c["accent"], c["accent2"]
    img = _canvas(w, h, accent)
    _kicker(img, w / 2, MARGIN + 34, c["kicker"], accent, size=TS["label"])
    if c["serial"]:
        _serial(img, w, c["serial"], accent)

    lines = c["headline"]
    max_w = w - MARGIN * 2
    hi = TS["h1"] if len(lines) <= 1 else TS["h2"]
    size = min(_fit(ln, max_w, hi=hi, lo=SIZE_MIN)[0] for ln in lines)
    f = R.font(size, bold=True)
    line_h = int(size * LINE_SPACING)
    block_h = line_h * len(lines)
    y = max(MARGIN + 120, int(h * 0.44 - block_h / 2))
    d = ImageDraw.Draw(img, "RGBA")
    for ln in lines:
        tw = f.getlength(ln)
        x = (w - tw) / 2
        hit = next((wd for wd in c["highlight"] if wd and ln.find(wd) >= 0), None)
        if hit:
            k = ln.find(hit)
            pre, hw = f.getlength(ln[:k]), f.getlength(hit)
            _highlight_block(img, highlight_box(
                y + line_h / 2, size,
                max(MARGIN - 4, x + pre - 10),
                min(w - MARGIN + 4, x + pre + hw + 10)), accent2)
            before, after = ln[:k], ln[k + len(hit):]
            if before:
                d.text((x, y + line_h / 2), before, font=f, fill=FG + (178,), anchor="lm")
            d.text((x + pre, y + line_h / 2), hit, font=f, fill=DARK + (255,), anchor="lm")
            if after:
                d.text((x + pre + hw, y + line_h / 2), after, font=f, fill=FG + (178,), anchor="lm")
        else:
            d.text((x, y + line_h / 2), ln, font=f, fill=FG + (178,), anchor="lm")
        y += line_h

    if c["sub"]:
        _text_outline(img, (w / 2, y + int(h * 0.045)), c["sub"], TS["body"], accent,
                      anchor="mm", stroke_w=0)
    band_y = h - MARGIN - 160
    d.rounded_rectangle((MARGIN, band_y, w - MARGIN, band_y + 8), radius=4, fill=accent + (255,))
    _text_outline(img, (w / 2, band_y + 74), c["cta"] or "点开看完整推导", TS["label"], FG,
                  anchor="mm", stroke_w=0)
    _tags(img, w / 2, h - MARGIN + 6, c["tags"], accent, size=TS["label"])
    return img


_BUILDERS = {"short": lambda c, fp: _variant_short(c),
             "f1": lambda c, fp: _variant_f1(c),
             "f3": lambda c, fp: _variant_f3(c, fp),
             "f6": lambda c, fp: _variant_f6(c)}


def build_covers(content, out_dir, frame_path=None, variants=None):
    """生成封面合集，返回文件路径列表（3:4 三个版式 + 一张 9:16）。

    短版（content 里 "format": "short"）默认出 short + f1 + 9x16：
    短版封面字更大、带时长徽标，与长版形成两种视觉层级。
    """
    os.makedirs(out_dir, exist_ok=True)
    _apply_theme(content)          # 封面用自己的主题（浅色主题照用，不跟着视频回退）
    c = _cfg(content)
    if variants is None:
        short = str((content or {}).get("format") or "").lower() in ("short", "15s", "quick")
        variants = ("short", "f1", "9x16") if short else ("short", "f1", "f3", "f6", "9x16")
    # 长版也出一张 short 版式（大字 + 时长徽标）：实测同一片子
    # short 版式综合分 103.5 vs f1 的 80.8——信息流是 240px 缩略图，字大就是硬道理。
    # seconds 必须**无条件**算好：以前它在 `variants is None` 分支里，
    # 调用方一旦显式传 variants（比如只重渲 short 版式），时长徽标就静默消失。
    secs = float((content or {}).get("seconds") or 0)
    if not secs:
        secs = round(sum(float(sh.get("min_dur") or 0) for sh in (content or {}).get("shots") or []))
    c["seconds"] = secs
    made = []
    direction = str((content or {}).get("cover_direction") or "off")
    for v in variants:
        if v == "short" and direction != "off":
            # 主封面走 v2（编辑部 / 蓝图 / 电影感三流派之一，存在 content.cover_direction）。
            # f1/f3/f6 仍是 v1 版式，留作 A/B 备选，所以两种设计语言不会在同一张图上混。
            import cover2
            img, _man = cover2.render(content, direction)
            path = os.path.join(out_dir, "cover_short.png")
            img.save(path, quality=95)
            made.append(path)
            continue
        if v == "9x16":
            img, name = _variant_f1(c, 1080, 1920), "cover_9x16.png"
        elif v in _BUILDERS:
            img, name = _BUILDERS[v](c, frame_path), f"cover_{v}.png"
        else:
            continue
        path = os.path.join(out_dir, name)
        img.save(path, quality=95)
        made.append(path)
    return made


def main():
    ap = argparse.ArgumentParser(description="生成小红书爆款封面（3:4 三版式 + 9:16）")
    ap.add_argument("--content", required=True)
    ap.add_argument("--out", default="out")
    ap.add_argument("--frame", default=None, help="F3 用的画面底图（如 cover_frame.png）")
    ap.add_argument("--text", default=None, help="直接给主标题（覆盖 cover.headline）")
    args = ap.parse_args()
    content = json.load(open(args.content, encoding="utf-8"))
    if args.text:
        content.setdefault("cover", {})["headline"] = [args.text]
    for p in build_covers(content, args.out, frame_path=args.frame):
        print(f"[ok] {p}")


if __name__ == "__main__":
    main()
